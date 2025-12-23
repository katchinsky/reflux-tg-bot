from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaxonomyCandidate:
    category_id: str
    label: str
    score: int  # 0-100 (RapidFuzz score)
    level: int
    parent_ids: list[str]


def _repo_root() -> Path:
    # app/services/taxonomy_index.py -> parents[0]=services, [1]=app, [2]=repo root
    return Path(__file__).resolve().parents[2]

def _default_categories_path() -> Path:
    """
    Prefer `./categories.json` when running from a repo checkout.
    Fallback to repo-root-relative path when available.
    """
    cwd_candidate = Path.cwd() / "categories.json"
    if cwd_candidate.exists():
        return cwd_candidate
    return _repo_root() / "categories.json"


def _load_categories(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected top-level dict in {path}, got {type(obj).__name__}")
    return obj


def _iter_parents(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    parents = payload.get("parents")
    if isinstance(parents, list):
        return [p for p in parents if isinstance(p, str) and p]
    return []


def _label_for(payload: Any, *, prefer_lang: str | None) -> str:
    """
    Returns a best-effort human label.
    Typical schema: {"name": {"en": "...", "ru": "...", ...}}
    """
    if not isinstance(payload, dict):
        return ""
    name = payload.get("name")
    if not isinstance(name, dict):
        return ""
    if prefer_lang:
        v = name.get(prefer_lang)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # prefer English as a stable fallback
    en = name.get("en")
    if isinstance(en, str) and en.strip():
        return en.strip()
    for v in name.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _compute_levels(tax: dict[str, Any]) -> dict[str, int]:
    """
    Compute level as minimum distance from any root (node with no in-taxonomy parents).
    """
    nodes = set(tax.keys())
    child_map: dict[str, set[str]] = {n: set() for n in nodes}
    parent_map: dict[str, set[str]] = {n: set() for n in nodes}

    for n, payload in tax.items():
        for p in _iter_parents(payload):
            if p in nodes:
                parent_map[n].add(p)
                child_map[p].add(n)

    roots = {n for n, parents in parent_map.items() if not parents}

    from collections import deque

    level: dict[str, int] = {}
    q: deque[str] = deque()
    for r in roots:
        level[r] = 0
        q.append(r)
    while q:
        u = q.popleft()
        base = level[u]
        for v in child_map.get(u, ()):
            cand = base + 1
            prev = level.get(v)
            if prev is None or cand < prev:
                level[v] = cand
                q.append(v)

    # If disconnected (shouldn't happen in your file), assign remaining nodes level 0.
    for n in nodes:
        level.setdefault(n, 0)
    return level


class TaxonomyIndex:
    def __init__(self, *, categories_path: Path | None = None) -> None:
        self._path = categories_path or _default_categories_path()
        self._tax = _load_categories(self._path)
        self._levels = _compute_levels(self._tax)

        # Build RapidFuzz choices: many label strings -> one category id.
        self._choice_texts: list[str] = []
        self._choice_cat_ids: list[str] = []
        self._choice_primary_label: list[str] = []
        self._choice_parent_ids: list[list[str]] = []

        for cid, payload in self._tax.items():
            parents = _iter_parents(payload)
            names = []
            if isinstance(payload, dict) and isinstance(payload.get("name"), dict):
                for v in payload["name"].values():
                    if isinstance(v, str) and v.strip():
                        names.append(v.strip())
            # Always include ID suffix (after colon) as a weak string.
            if ":" in cid:
                names.append(cid.split(":", 1)[1].replace("-", " ").strip())
            # de-dup, keep small
            uniq = sorted({n for n in names if n})
            primary = _label_for(payload, prefer_lang="en") or (uniq[0] if uniq else cid)
            for nm in uniq[:10]:
                self._choice_texts.append(nm)
                self._choice_cat_ids.append(cid)
                self._choice_primary_label.append(primary)
                self._choice_parent_ids.append(parents)

    @property
    def categories_path(self) -> Path:
        return self._path

    def get_label(self, category_id: str, *, prefer_lang: str | None = None) -> str:
        payload = self._tax.get(category_id)
        return _label_for(payload, prefer_lang=prefer_lang) or category_id

    def get_parent_ids(self, category_id: str) -> list[str]:
        return _iter_parents(self._tax.get(category_id))

    def get_level(self, category_id: str) -> int:
        return int(self._levels.get(category_id, 0))

    def search(self, query: str, *, limit: int = 30) -> list[TaxonomyCandidate]:
        """
        Returns top categories by fuzzy match score (0-100).
        """
        q = (query or "").strip()
        if not q:
            return []

        try:
            from rapidfuzz import fuzz, process  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("rapidfuzz package is not installed. Add it to dependencies.") from e

        # Pull more to allow per-category aggregation.
        raw = process.extract(
            q,
            self._choice_texts,
            scorer=fuzz.WRatio,
            limit=max(limit * 6, limit),
        )

        best: dict[str, tuple[int, str, list[str]]] = {}
        for _text, score, idx in raw:
            cid = self._choice_cat_ids[idx]
            prev = best.get(cid)
            if prev is None or score > prev[0]:
                best[cid] = (int(score), self._choice_primary_label[idx], self._choice_parent_ids[idx])

        out: list[TaxonomyCandidate] = []
        for cid, (score, label, parents) in best.items():
            out.append(
                TaxonomyCandidate(
                    category_id=cid,
                    label=label,
                    score=int(score),
                    level=int(self._levels.get(cid, 0)),
                    parent_ids=list(parents),
                )
            )

        out.sort(key=lambda c: (c.score, c.level), reverse=True)
        return out[: max(0, limit)]

    @staticmethod
    def diversify_by_level(
        candidates: list[TaxonomyCandidate],
        *,
        limit: int,
        max_per_level: int = 6,
        prefer_broader: bool = True,
        always_include_best: bool = True,
    ) -> list[TaxonomyCandidate]:
        """
        Pick candidates across different levels.

        - prefer_broader=True prioritizes lower numeric levels (more broad).
        - Round-robin across levels, capping max_per_level to avoid domination by a single depth.
        - always_include_best=True ensures the top overall (by score) isn't lost.
        """
        if limit <= 0 or not candidates:
            return []

        # Sort global best by retrieval score first, then broader.
        sorted_all = sorted(candidates, key=lambda c: (c.score, -c.level), reverse=True)
        forced: list[TaxonomyCandidate] = []
        forced_ids: set[str] = set()
        if always_include_best and sorted_all:
            forced.append(sorted_all[0])
            forced_ids.add(sorted_all[0].category_id)

        by_level: dict[int, list[TaxonomyCandidate]] = {}
        for c in sorted_all:
            if c.category_id in forced_ids:
                continue
            by_level.setdefault(int(c.level), []).append(c)

        # Sort within each level by score desc.
        for lvl in by_level:
            by_level[lvl].sort(key=lambda c: c.score, reverse=True)

        levels = sorted(by_level.keys(), reverse=not prefer_broader)

        picked: list[TaxonomyCandidate] = list(forced)
        picked_per_level: dict[int, int] = {}

        progressed = True
        while progressed and len(picked) < limit:
            progressed = False
            for lvl in levels:
                if len(picked) >= limit:
                    break
                if picked_per_level.get(lvl, 0) >= max_per_level:
                    continue
                pool = by_level.get(lvl)
                if not pool:
                    continue
                picked.append(pool.pop(0))
                picked_per_level[lvl] = picked_per_level.get(lvl, 0) + 1
                progressed = True

        return picked[:limit]


@lru_cache(maxsize=1)
def get_taxonomy_index() -> TaxonomyIndex:
    return TaxonomyIndex()


