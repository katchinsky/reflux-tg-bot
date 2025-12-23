from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, select

from app.db.models import EventAudit, MealItem, MealItemCategory
from app.db.session import get_session
from app.services.openai_client import OpenAIChat, OpenAIClientError
from app.services.taxonomy_index import TaxonomyCandidate, get_taxonomy_index

logger = logging.getLogger("reflux-bot")

ItemType = Literal["dish", "ingredient", "drink", "product"]


@dataclass(frozen=True)
class ExtractedItem:
    item_type: ItemType
    text_span: str
    normalized: str
    normalized_en: str | None
    modifiers: list[str]
    confidence: float  # 0..1


@dataclass(frozen=True)
class LinkedCategory:
    category_id: str
    label: str
    score: float  # 0..1
    level: int


@dataclass(frozen=True)
class ItemLinkResult:
    item: ExtractedItem
    top3: list[LinkedCategory]
    abstain: bool
    abstain_reason: str | None


PROMPT_VERSION = "v3"


def _audit(user_id: str, *, event_type: str, payload: dict) -> None:
    with get_session() as session:
        session.add(
            EventAudit(
                user_id=user_id,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )


def _clamp01(x: Any, *, default: float = 0.0) -> float:
    try:
        v = float(x)
    except Exception:  # noqa: BLE001
        return default
    if v != v:  # NaN
        return default
    return max(0.0, min(1.0, v))


def _as_list_str(x: Any) -> list[str]:
    if isinstance(x, list):
        out: list[str] = []
        for v in x:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


def extract_items(
    *,
    notes_text: str,
    lang: str,
    openai_api_key: str,
    model: str,
) -> tuple[list[ExtractedItem], str]:
    chat = OpenAIChat(api_key=openai_api_key)
    # system = (
    #     "You are an information extraction system.\n"
    #     "Extract food/drink items from messy meal descriptions.\n"
    #     "Return ONLY valid JSON object with key 'items'.\n"
    #     "Each item must have: text_span, normalized_ru, normalized_en, type, modifiers, confidence.\n"
    #     "type must be one of: dish, ingredient, drink, product.\n"
    #     "confidence is 0..1.\n"
    #     "Do not invent quantities.\n"
    # )

    system = ('''
You are an information extraction system.
Extract all food and drink items from unstructured or messy meal descriptions. Strictly avoid inferring, inventing, or including any quantities—if any quantity information appears in your output, detect and correct it before returning results.
Normalize and translate item names into Russian and English.
Return only a single valid JSON object with a top-level key 'items'.

After extracting items, validate your output in the following manner:
    - Ensure every field is present in each item, in the specified order.
    - Confirm all field types are correct.
    - Verify that no quantities are present in any field.
If any validation fails, self-correct your output before returning results.

If no food or drink items are identified, return a JSON object with an empty 'items' array in the following format:
    {
    "items": []
    }

# Output Format
Return a single valid JSON object with a top-level key 'items'. The 'items' array must contain objects with the following fields in order: text_span, normalized_ru, normalized_en, type, modifiers, confidence. Field types must match the allowed values. Confidence must be a float between 0 and 1 (not a string).

Example output:
{
  "items": [
    {
      "text_span": "chicken soup",
      "normalized_ru": "куриный суп",
      "normalized_en": "chicken soup",
      "type": "dish",
      "modifiers": ["hot"],
      "confidence": 0.98
    },
    {
      "text_span": "green tea",
      "normalized_ru": "зеленый чай",
      "normalized_en": "green tea",
      "type": "drink",
      "modifiers": [],
      "confidence": 0.95
    }
  ]
}
After producing your output, explicitly verify that it conforms to the required JSON schema and output format before finalizing your response.''')
    user = f"Language: {lang}\nMeal text:\n{notes_text}"
    res = chat.chat_json(model=model, system=system, user=user)
    obj = res.json_obj
    if not isinstance(obj, dict):
        raise OpenAIClientError("Extractor response must be a JSON object")

    raw_items = obj.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    items: list[ExtractedItem] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        t = (it.get("type") or "").strip().lower()
        if t not in ("dish", "ingredient", "drink", "product"):
            continue
        text_span = str(it.get("text_span") or "").strip()
        normalized = str(it.get("normalized_ru") or "").strip()
        normalized_en = (it.get("normalized_en") if isinstance(it.get("normalized_en"), str) else None)
        normalized_en = (normalized_en or "").strip() or None
        modifiers = _as_list_str(it.get("modifiers"))
        conf = _clamp01(it.get("confidence"), default=0.5)
        if not normalized and not text_span:
            continue
        items.append(
            ExtractedItem(
                item_type=t,  # type: ignore[arg-type]
                text_span=text_span,
                normalized=normalized or text_span,
                normalized_en=normalized_en,
                modifiers=modifiers,
                confidence=conf,
            )
        )

    return items, res.model


def _candidate_pack(
    cand: TaxonomyCandidate, *, prefer_lang: str
) -> dict[str, Any]:
    idx = get_taxonomy_index()
    parents = cand.parent_ids[:3]
    return {
        "id": cand.category_id,
        "label": idx.get_label(cand.category_id, prefer_lang=prefer_lang),
        "level": cand.level,
        "parents": [{"id": p, "label": idx.get_label(p, prefer_lang=prefer_lang)} for p in parents],
        "retrieval_score": cand.score / 100.0,
    }


def link_item_top3(
    *,
    item: ExtractedItem,
    notes_text: str,
    lang: str,
    openai_api_key: str,
    model: str,
    candidate_limit: int = 30,
) -> tuple[ItemLinkResult, str]:
    idx = get_taxonomy_index()

    # Candidate generation (lexical/fuzzy).
    queries = [item.normalized]
    if item.normalized_en:
        queries.append(item.normalized_en)
    # Avoid duplicates while preserving order.
    seen_q: set[str] = set()
    uniq_queries: list[str] = []
    for q in queries:
        qq = q.strip()
        if qq and qq not in seen_q:
            seen_q.add(qq)
            uniq_queries.append(qq)

    best: dict[str, TaxonomyCandidate] = {}
    for q in uniq_queries:
        for c in idx.search(q, limit=candidate_limit):
            prev = best.get(c.category_id)
            if prev is None or c.score > prev.score:
                best[c.category_id] = c

    candidates_raw = sorted(best.values(), key=lambda c: (c.score, c.level), reverse=True)
    # Diversify across levels, favoring broader categories (smaller level numbers).
    candidates = idx.diversify_by_level(
        candidates_raw,
        limit=candidate_limit,
        max_per_level=6,
        prefer_broader=True,
        always_include_best=True,
    )
    packed = [_candidate_pack(c, prefer_lang=lang) for c in candidates]

    # Rerank with LLM.
    chat = OpenAIChat(api_key=openai_api_key)
    # system = (
    #     "You map an item to taxonomy categories.\n"
    #     "Return ONLY JSON object with keys: top_k, abstain, abstain_reason.\n"
    #     "top_k must be an array of up to 3 items, each: id, score (0..1), reason.\n"
    #     "Choose ONLY from the provided candidate list.\n"
    #     "If none match, set abstain=true.\n"
    # )
    system = ('''# Role and Objective
Map a given item to taxonomy categories, selecting from a provided list of candidate categories.

# Instructions
- Return ONLY a JSON object containing these keys: `top_k`, `abstain`, and `abstain_reason`.
- Select candidates strictly from the provided candidate list; do not generate new categories.
- If no candidates are appropriate, abstain.
- Prioritize broader categories over more specific ones when the item description is not specific.

## Output Structure
- `top_k`: An array with up to 3 objects, each representing a candidate category, listed in descending order by confidence score. Each object must include:
  - `id` (string): The candidate category ID (must match one from the provided candidate list).
  - `score` (number, 0..1): Confidence score for this candidate.
  - `reason` (string): A brief justification for why this category was chosen.
- `abstain` (boolean): Set to `true` if no candidates are appropriate; set to `false` if one or more suitable candidates are found.
- `abstain_reason` (string or null): If `abstain` is `true`, specify the reason for abstaining; if `abstain` is `false`, set this to `null`.

# Output Format
- Ensure strict adherence to the specified JSON structure; include only the required keys with correctly typed values.

Example when confident matches exist:
```json
{
  "top_k": [
    {"id": "en:fresh-strawberry", "score": 0.93, "reason": "Matches item description."},
    {"id": "en:strawberry-jelly", "score": 0.78, "reason": "Related to strawberries, but the form was not specified in the description."}
  ],
  "abstain": false,
  "abstain_reason": null
}
```

Example when abstaining:
```json
{
  "top_k": [],
  "abstain": true,
  "abstain_reason": "No candidates are sufficiently relevant to the item."
}
```

# Constraints
- Only choose from the provided candidate list.
- Set `abstain` to `true` if no suitable candidates exist.

# Stop Conditions
- Return the JSON object when mapping is complete.
- Do not return any output other than the specified JSON.

# Validation
- After generating the JSON object, briefly verify that all fields conform to the Output Structure requirements and make corrections if needed before returning.
''')
    user = json.dumps(
        {
            "item": {
                "type": item.item_type,
                "normalized": item.normalized,
                "normalized_en": item.normalized_en,
                "modifiers": item.modifiers,
            },
            "meal_text": notes_text,
            "candidates": packed,
        },
        ensure_ascii=False,
    )
    res = chat.chat_json(model=model, system=system, user=user)
    obj = res.json_obj
    if not isinstance(obj, dict):
        raise OpenAIClientError("Rerank response must be a JSON object")

    raw_top = obj.get("top_k", [])
    abstain = bool(obj.get("abstain", False))
    abstain_reason = obj.get("abstain_reason")
    abstain_reason = abstain_reason if isinstance(abstain_reason, str) and abstain_reason.strip() else None

    top3: list[LinkedCategory] = []
    if isinstance(raw_top, list):
        for it2 in raw_top[:3]:
            if not isinstance(it2, dict):
                continue
            cid = str(it2.get("id") or "").strip()
            if not cid:
                continue
            score = _clamp01(it2.get("score"), default=0.0)
            top3.append(
                LinkedCategory(
                    category_id=cid,
                    label=idx.get_label(cid, prefer_lang=lang),
                    score=score,
                    level=idx.get_level(cid),
                )
            )

    return (
        ItemLinkResult(
            item=item,
            top3=top3,
            abstain=abstain,
            abstain_reason=abstain_reason,
        ),
        res.model,
    )


def persist_results(
    *,
    user_id: str,
    meal_id: str,
    results: list[ItemLinkResult],
    extract_model: str,
    rerank_model: str,
    replace_existing: bool = True,
) -> None:
    with get_session() as session:
        if replace_existing:
            # If re-running pipeline, clear old records for this meal.
            meal_item_ids = [
                r[0]
                for r in session.execute(select(MealItem.id).where(MealItem.meal_id == meal_id)).all()
            ]
            if meal_item_ids:
                session.execute(
                    delete(MealItemCategory).where(MealItemCategory.meal_item_id.in_(meal_item_ids))
                )
            session.execute(delete(MealItem).where(MealItem.meal_id == meal_id))

        for r in results:
            mi = MealItem(
                meal_id=meal_id,
                user_id=user_id,
                item_type=r.item.item_type,
                text_span=r.item.text_span,
                normalized=r.item.normalized,
                modifiers_json=json.dumps(
                    {
                        "modifiers": r.item.modifiers,
                        "normalized_en": r.item.normalized_en,
                        "abstain": r.abstain,
                        "abstain_reason": r.abstain_reason,
                    },
                    ensure_ascii=False,
                ),
                confidence=int(round(100.0 * r.item.confidence)),
                llm_model=f"{extract_model}|{rerank_model}",
                prompt_version=PROMPT_VERSION,
                created_at=datetime.utcnow(),
            )
            session.add(mi)
            session.flush()
            for i, c in enumerate(r.top3[:3], start=1):
                session.add(
                    MealItemCategory(
                        meal_item_id=mi.id,
                        category_id=c.category_id,
                        rank=i,
                        score=int(round(100.0 * c.score)),
                        meta_json=json.dumps(
                            {"label": c.label, "level": c.level},
                            ensure_ascii=False,
                        ),
                        created_at=datetime.utcnow(),
                    )
                )


def process_meal(
    *,
    user_id: str,
    meal_id: str,
    notes_text: str,
    lang: str,
    openai_api_key: str | None,
    openai_model_extract: str,
    openai_model_rerank: str,
) -> list[ItemLinkResult]:
    """
    Full pipeline: extract -> candidate retrieval -> rerank -> persist.
    Returns the in-memory results for UI.
    """
    if not openai_api_key:
        return []
    if not (notes_text or "").strip():
        return []

    try:
        items, extract_model = extract_items(
            notes_text=notes_text, lang=lang, openai_api_key=openai_api_key, model=openai_model_extract
        )
        _audit(
            user_id,
            event_type="meal_taxonomy_extracted",
            payload={"meal_id": meal_id, "items": len(items), "prompt_version": PROMPT_VERSION},
        )
        results: list[ItemLinkResult] = []
        rerank_model_used = openai_model_rerank
        for it in items:
            r, rerank_model = link_item_top3(
                item=it,
                notes_text=notes_text,
                lang=lang,
                openai_api_key=openai_api_key,
                model=openai_model_rerank,
            )
            rerank_model_used = rerank_model
            results.append(r)

        persist_results(
            user_id=user_id,
            meal_id=meal_id,
            results=results,
            extract_model=extract_model,
            rerank_model=rerank_model_used,
        )
        _audit(
            user_id,
            event_type="meal_taxonomy_linked",
            payload={
                "meal_id": meal_id,
                "items": len(results),
                "top1_scores": [r.top3[0].score if r.top3 else 0.0 for r in results],
                "prompt_version": PROMPT_VERSION,
            },
        )
        return results
    except Exception as e:  # noqa: BLE001
        logger.exception("Meal taxonomy pipeline failed")
        _audit(
            user_id,
            event_type="meal_taxonomy_failed",
            payload={"meal_id": meal_id, "error": str(e), "prompt_version": PROMPT_VERSION},
        )
        return []


