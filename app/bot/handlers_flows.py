from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.flow_common import fmt_dt_user, nav_kb, now_utc, one_hour_ago, parse_cb
from app.bot.keyboards import main_menu_keyboard
from app.core.i18n import (
    activity_level_label,
    fat_label,
    portion_label,
    posture_label,
    sleep_position_label,
    symptom_type_label,
    t,
)
from app.core.state import StateStore
from app.core.timeparse import parse_user_time
from app.services.logging import create_meal, create_medication, create_morning_check, create_symptom
from app.services.medications import top_medication_names
from app.services.meal_taxonomy import process_meal
from app.services.users import ensure_user


_store = StateStore(ttl_hours=24)


def _draft_key(flow: str) -> str:
    return f"{flow}:draft"


def _hist_key(flow: str) -> str:
    return f"{flow}:hist"


def _step_key(flow: str) -> str:
    return f"{flow}:step"


def _push_state(context: ContextTypes.DEFAULT_TYPE, flow: str, state: int) -> None:
    hist = context.user_data.setdefault(_hist_key(flow), [])
    if isinstance(hist, list):
        hist.append(state)


def _pop_state(context: ContextTypes.DEFAULT_TYPE, flow: str) -> int | None:
    hist = context.user_data.get(_hist_key(flow))
    if isinstance(hist, list) and hist:
        return hist.pop()
    return None


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, *, flow: str) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    _store.clear(user, flow=flow)
    context.user_data.pop(_draft_key(flow), None)
    context.user_data.pop(_hist_key(flow), None)
    context.user_data.pop(_step_key(flow), None)

    if update.callback_query:
        await update.callback_query.answer()
        # Can't attach a reply keyboard to an edited inline-keyboard message.
        await update.callback_query.edit_message_text(t(lang, "common.cancelled"))
        if update.callback_query.message:
            await update.callback_query.message.reply_text(
                t(lang, "common.menu"), reply_markup=main_menu_keyboard()
            )
    elif update.message:
        await update.message.reply_text(t(lang, "common.cancelled"), reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def _not_implemented(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = "en"
    if update.effective_user:
        user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
        lang = getattr(user, "language", "en")
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(t(lang, "common.not_implemented"))
    elif update.message:
        await update.message.reply_text(t(lang, "common.not_implemented"))
    return ConversationHandler.END


# -----------------------
# Meal flow
# -----------------------

MEAL_FLOW = "meal"
MEAL_RESUME, MEAL_TIME, MEAL_TIME_CUSTOM, MEAL_INPUT, MEAL_PORTION, MEAL_FAT, MEAL_POSTURE, MEAL_CONFIRM = range(
    8
)

async def meal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel(update, context, flow=MEAL_FLOW)


async def meal_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    loaded = _store.load(user, flow=MEAL_FLOW, now_utc=now_utc())
    if loaded:
        context.user_data[_draft_key(MEAL_FLOW)] = loaded.draft
        context.user_data[_step_key(MEAL_FLOW)] = loaded.step
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(t(lang, "common.resume_draft"), callback_data="meal:resume:yes"),
                    InlineKeyboardButton(t(lang, "common.discard"), callback_data="meal:resume:no"),
                ]
            ]
        )
        if update.message:
            await update.message.reply_text(t(lang, "meal.unfinished_resume"), reply_markup=kb)
        return MEAL_RESUME

    context.user_data[_draft_key(MEAL_FLOW)] = {}
    context.user_data[_hist_key(MEAL_FLOW)] = []
    return await meal_prompt_time(update, context, edit_message=False, fresh=True)


async def meal_resume_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "resume":
        return MEAL_RESUME

    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    if cb.value == "no":
        _store.clear(user, flow=MEAL_FLOW)
        context.user_data[_draft_key(MEAL_FLOW)] = {}
        context.user_data[_hist_key(MEAL_FLOW)] = []
        return await meal_prompt_time(update, context, edit_message=True, fresh=True)

    step = str(context.user_data.get(_step_key(MEAL_FLOW), "time"))
    mapping = {
        "time": MEAL_TIME,
        "time_custom": MEAL_TIME_CUSTOM,
        "input": MEAL_INPUT,
        "portion": MEAL_PORTION,
        "fat": MEAL_FAT,
        "posture": MEAL_POSTURE,
        "confirm": MEAL_CONFIRM,
    }
    return await meal_render_state(update, context, mapping.get(step, MEAL_TIME), edit_message=True)


async def meal_render_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, *, edit_message: bool) -> int:
    if state == MEAL_TIME:
        return await meal_prompt_time(update, context, edit_message=edit_message, fresh=False)
    if state == MEAL_TIME_CUSTOM:
        return await meal_prompt_custom_time(update, context, edit_message=edit_message)
    if state == MEAL_INPUT:
        return await meal_prompt_input(update, context, edit_message=edit_message)
    if state == MEAL_PORTION:
        return await meal_prompt_portion(update, context, edit_message=edit_message)
    if state == MEAL_FAT:
        return await meal_prompt_fat(update, context, edit_message=edit_message)
    if state == MEAL_POSTURE:
        return await meal_prompt_posture(update, context, edit_message=edit_message)
    if state == MEAL_CONFIRM:
        return await meal_prompt_confirm(update, context, edit_message=edit_message)
    return ConversationHandler.END


async def meal_prompt_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    edit_message: bool,
    fresh: bool,
) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    if fresh:
        draft.clear()
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "meal.time.now"), callback_data="meal:time:now"),
                InlineKeyboardButton(t(lang, "meal.time.one_hour_ago"), callback_data="meal:time:1h"),
                InlineKeyboardButton(t(lang, "meal.time.custom"), callback_data="meal:time:custom"),
            ],
            [InlineKeyboardButton(t(lang, "common.cancel"), callback_data="meal:nav:cancel")],
        ]
    )
    text = t(lang, "meal.time.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MEAL_FLOW)] = "time"
    _push_state(context, MEAL_FLOW, MEAL_TIME)
    _store.save(user, flow=MEAL_FLOW, step="time", draft=draft, now_utc=now_utc())
    return MEAL_TIME


async def meal_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "time":
        return MEAL_TIME

    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    now = now_utc()
    if cb.value == "now":
        draft["occurred_at_utc"] = now.isoformat()
        _store.save(user, flow=MEAL_FLOW, step="input", draft=draft, now_utc=now)
        return await meal_prompt_input(update, context, edit_message=True)
    if cb.value == "1h":
        draft["occurred_at_utc"] = one_hour_ago(now).isoformat()
        _store.save(user, flow=MEAL_FLOW, step="input", draft=draft, now_utc=now)
        return await meal_prompt_input(update, context, edit_message=True)
    if cb.value == "custom":
        _store.save(user, flow=MEAL_FLOW, step="time_custom", draft=draft, now_utc=now)
        return await meal_prompt_custom_time(update, context, edit_message=True)
    return MEAL_TIME


async def meal_prompt_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    text = t(lang, "meal.time.custom_help")
    kb = nav_kb(flow=MEAL_FLOW, lang=lang, show_back=True, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MEAL_FLOW)] = "time_custom"
    _push_state(context, MEAL_FLOW, MEAL_TIME_CUSTOM)
    _store.save(user, flow=MEAL_FLOW, step="time_custom", draft=draft, now_utc=now_utc())
    return MEAL_TIME_CUSTOM


async def meal_custom_time_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    parsed = parse_user_time(update.message.text or "", user_tz=user.timezone, now_utc=now_utc())
    if not parsed:
        await update.message.reply_text(t(getattr(user, "language", "en"), "meal.time.parse_fail"))
        return MEAL_TIME_CUSTOM
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    draft["occurred_at_utc"] = parsed.isoformat()
    _store.save(user, flow=MEAL_FLOW, step="input", draft=draft, now_utc=now_utc())
    return await meal_prompt_input(update, context, edit_message=False)


async def meal_prompt_input(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    kb = nav_kb(flow=MEAL_FLOW, lang=lang, show_back=True, show_skip=False)
    text = t(lang, "meal.input.help")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MEAL_FLOW)] = "input"
    _push_state(context, MEAL_FLOW, MEAL_INPUT)
    _store.save(user, flow=MEAL_FLOW, step="input", draft=draft, now_utc=now_utc())
    return MEAL_INPUT


async def meal_input_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    if update.message.photo:
        draft["photo_file_id"] = update.message.photo[-1].file_id
        draft["notes_text"] = (update.message.caption or "").strip()
    else:
        draft["photo_file_id"] = None
        draft["notes_text"] = (update.message.text or "").strip()
    _store.save(user, flow=MEAL_FLOW, step="portion", draft=draft, now_utc=now_utc())
    return await meal_prompt_portion(update, context, edit_message=False)


async def meal_prompt_portion(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("S", callback_data="meal:portion:small"),
                InlineKeyboardButton("M", callback_data="meal:portion:medium"),
                InlineKeyboardButton("L", callback_data="meal:portion:large"),
            ],
            nav_kb(flow=MEAL_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "meal.portion.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MEAL_FLOW)] = "portion"
    _push_state(context, MEAL_FLOW, MEAL_PORTION)
    _store.save(user, flow=MEAL_FLOW, step="portion", draft=draft, now_utc=now_utc())
    return MEAL_PORTION


async def meal_portion_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "portion":
        return MEAL_PORTION
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    draft["portion_size"] = cb.value
    _store.save(user, flow=MEAL_FLOW, step="fat", draft=draft, now_utc=now_utc())
    return await meal_prompt_fat(update, context, edit_message=True)


async def meal_prompt_fat(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(fat_label(lang, "low"), callback_data="meal:fat:low"),
                InlineKeyboardButton(fat_label(lang, "medium"), callback_data="meal:fat:medium"),
                InlineKeyboardButton(fat_label(lang, "high"), callback_data="meal:fat:high"),
                InlineKeyboardButton(t(lang, "common.skip"), callback_data="meal:fat:unknown"),
            ],
            nav_kb(flow=MEAL_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "meal.fat.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MEAL_FLOW)] = "fat"
    _push_state(context, MEAL_FLOW, MEAL_FAT)
    _store.save(user, flow=MEAL_FLOW, step="fat", draft=draft, now_utc=now_utc())
    return MEAL_FAT


async def meal_fat_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "fat":
        return MEAL_FAT
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    draft["fat_level"] = cb.value
    _store.save(user, flow=MEAL_FLOW, step="posture", draft=draft, now_utc=now_utc())
    return await meal_prompt_posture(update, context, edit_message=True)


async def meal_prompt_posture(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(posture_label(lang, "laying"), callback_data="meal:posture:laying"),
                InlineKeyboardButton(posture_label(lang, "sitting"), callback_data="meal:posture:sitting"),
            ],
            [
                InlineKeyboardButton(posture_label(lang, "walking"), callback_data="meal:posture:walking"),
                InlineKeyboardButton(posture_label(lang, "standing"), callback_data="meal:posture:standing"),
            ],
            [InlineKeyboardButton(t(lang, "common.skip"), callback_data="meal:posture:unknown")],
            nav_kb(flow=MEAL_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "meal.posture.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MEAL_FLOW)] = "posture"
    _push_state(context, MEAL_FLOW, MEAL_POSTURE)
    _store.save(user, flow=MEAL_FLOW, step="posture", draft=draft, now_utc=now_utc())
    return MEAL_POSTURE


async def meal_posture_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "posture":
        return MEAL_POSTURE
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    draft["posture_after"] = cb.value
    _store.save(user, flow=MEAL_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return await meal_prompt_confirm(update, context, edit_message=True)


async def meal_prompt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MEAL_FLOW), {})
    occurred_at = (
        datetime.fromisoformat(draft["occurred_at_utc"]) if draft.get("occurred_at_utc") else now_utc()
    )
    notes = draft.get("notes_text") or t(lang, "common.none")
    text = (
        f"{t(lang, 'meal.confirm.title')}\n"
        f"{t(lang, 'meal.confirm.time')}: {fmt_dt_user(occurred_at, user_tz=user.timezone)}\n"
        f"{t(lang, 'meal.confirm.portion')}: {portion_label(lang, str(draft.get('portion_size', 'medium')))}\n"
        f"{t(lang, 'meal.confirm.fat')}: {fat_label(lang, str(draft.get('fat_level', 'unknown')))}\n"
        f"{t(lang, 'meal.confirm.posture')}: {posture_label(lang, str(draft.get('posture_after', 'unknown')))}\n"
        f"{t(lang, 'meal.confirm.notes')}: {notes}\n\n"
        f"{t(lang, 'meal.confirm.save_q')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "common.save"), callback_data="meal:confirm:save"),
                InlineKeyboardButton(t(lang, "common.back"), callback_data="meal:nav:back"),
                InlineKeyboardButton(t(lang, "common.cancel"), callback_data="meal:nav:cancel"),
            ]
        ]
    )
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MEAL_FLOW)] = "confirm"
    _push_state(context, MEAL_FLOW, MEAL_CONFIRM)
    _store.save(user, flow=MEAL_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return MEAL_CONFIRM


async def meal_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "confirm" or cb.value != "save":
        return MEAL_CONFIRM
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.get(_draft_key(MEAL_FLOW), {})
    meal = create_meal(
        user,
        occurred_at_utc=datetime.fromisoformat(draft.get("occurred_at_utc")) if draft.get("occurred_at_utc") else now_utc(),
        notes_text=str(draft.get("notes_text", "")),
        photo_file_id=draft.get("photo_file_id"),
        portion_size=str(draft.get("portion_size", "medium")),
        fat_level=str(draft.get("fat_level", "unknown")),
        posture_after=str(draft.get("posture_after", "unknown")),
    )

    # Fire-and-forget: run LLM meal parsing + taxonomy linking after saving.
    chat_id = None
    if q.message:
        chat_id = q.message.chat_id
    openai_api_key = context.bot_data.get("openai_api_key")
    openai_model_extract = context.bot_data.get("openai_model_extract", "gpt-4o-mini")
    openai_model_rerank = context.bot_data.get("openai_model_rerank", "gpt-4o-mini")
    notes_text = str(draft.get("notes_text", "")).strip()

    async def _run_and_notify() -> None:
        if not chat_id or not openai_api_key or not notes_text:
            return
        results = await asyncio.to_thread(
            process_meal,
            user_id=user.id,
            meal_id=meal.id,
            notes_text=notes_text,
            lang=lang,
            openai_api_key=openai_api_key,
            openai_model_extract=str(openai_model_extract),
            openai_model_rerank=str(openai_model_rerank),
        )
        if not results:
            return
        header = "Detected items and category suggestions:" if lang != "ru" else "Распознанные продукты и категории:"
        lines = [header]
        for r in results:
            item = r.item.normalized
            if r.item.item_type:
                item = f"{item} ({r.item.item_type})"
            lines.append(f"- {item}")
            if r.top3:
                for c in r.top3[:3]:
                    lines.append(f"  - {c.label} ({c.category_id}) — {c.score:.0%}")
            elif r.abstain:
                reason = r.abstain_reason or "no good match"
                lines.append(f"  - (no match) {reason}")
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))

    asyncio.create_task(_run_and_notify())
    _store.clear(user, flow=MEAL_FLOW)
    context.user_data.pop(_draft_key(MEAL_FLOW), None)
    context.user_data.pop(_hist_key(MEAL_FLOW), None)
    context.user_data.pop(_step_key(MEAL_FLOW), None)
    # Editing an inline-keyboard message cannot attach a reply keyboard.
    await q.edit_message_text(t(lang, "meal.logged", disclaimer=t(lang, "disclaimer.text")))
    if q.message:
        await q.message.reply_text(t(lang, "common.what_next"), reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def meal_nav_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MEAL_FLOW or cb.kind != "nav":
        return ConversationHandler.END
    if cb.value == "cancel":
        return await _cancel(update, context, flow=MEAL_FLOW)
    if cb.value == "back":
        _pop_state(context, MEAL_FLOW)
        prev = _pop_state(context, MEAL_FLOW) or MEAL_TIME
        return await meal_render_state(update, context, prev, edit_message=True)
    return ConversationHandler.END


# -----------------------
# Symptom flow
# -----------------------

SYM_FLOW = "symptom"
SYM_RESUME, SYM_TYPE, SYM_INTENSITY, SYM_TIME, SYM_TIME_CUSTOM, SYM_DURATION, SYM_DURATION_CUSTOM, SYM_NOTES, SYM_CONFIRM = range(
    9
)

async def symptom_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel(update, context, flow=SYM_FLOW)

SYMPTOM_TYPES = [
    "reflux",
    "heartburn",
    "regurgitation",
    # "burping",
    "nausea",
    "cough_hoarseness",
    # "chest_discomfort",
    # "throat_burn",
    "bloating",
    "stomach_pain",
    "other",
]


async def symptom_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    loaded = _store.load(user, flow=SYM_FLOW, now_utc=now_utc())
    if loaded:
        context.user_data[_draft_key(SYM_FLOW)] = loaded.draft
        context.user_data[_step_key(SYM_FLOW)] = loaded.step
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(t(lang, "common.resume_draft"), callback_data="symptom:resume:yes"),
                    InlineKeyboardButton(t(lang, "common.discard"), callback_data="symptom:resume:no"),
                ]
            ]
        )
        if update.message:
            await update.message.reply_text(t(lang, "symptom.unfinished_resume"), reply_markup=kb)
        return SYM_RESUME
    context.user_data[_draft_key(SYM_FLOW)] = {}
    context.user_data[_hist_key(SYM_FLOW)] = []
    return await symptom_prompt_type(update, context, edit_message=False)


async def symptom_resume_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "resume":
        return SYM_RESUME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    if cb.value == "no":
        _store.clear(user, flow=SYM_FLOW)
        context.user_data[_draft_key(SYM_FLOW)] = {}
        context.user_data[_hist_key(SYM_FLOW)] = []
        return await symptom_prompt_type(update, context, edit_message=True)

    step = str(context.user_data.get(_step_key(SYM_FLOW), "type"))
    mapping = {
        "type": SYM_TYPE,
        "intensity": SYM_INTENSITY,
        "time": SYM_TIME,
        "time_custom": SYM_TIME_CUSTOM,
        "duration": SYM_DURATION,
        "duration_custom": SYM_DURATION_CUSTOM,
        "notes": SYM_NOTES,
        "confirm": SYM_CONFIRM,
    }
    return await symptom_render_state(update, context, mapping.get(step, SYM_TYPE), edit_message=True)


async def symptom_render_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, *, edit_message: bool) -> int:
    if state == SYM_TYPE:
        return await symptom_prompt_type(update, context, edit_message=edit_message)
    if state == SYM_INTENSITY:
        return await symptom_prompt_intensity(update, context, edit_message=edit_message)
    if state == SYM_TIME:
        return await symptom_prompt_time(update, context, edit_message=edit_message)
    if state == SYM_TIME_CUSTOM:
        return await symptom_prompt_time_custom(update, context, edit_message=edit_message)
    if state == SYM_DURATION:
        return await symptom_prompt_duration(update, context, edit_message=edit_message)
    if state == SYM_DURATION_CUSTOM:
        return await symptom_prompt_duration_custom(update, context, edit_message=edit_message)
    if state == SYM_NOTES:
        return await symptom_prompt_notes(update, context, edit_message=edit_message)
    if state == SYM_CONFIRM:
        return await symptom_prompt_confirm(update, context, edit_message=edit_message)
    return ConversationHandler.END


async def symptom_prompt_type(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    rows: list[list[InlineKeyboardButton]] = []
    for val in SYMPTOM_TYPES:
        rows.append([InlineKeyboardButton(symptom_type_label(lang, val), callback_data=f"symptom:type:{val}")])
    rows.append(nav_kb(flow=SYM_FLOW, lang=lang, show_back=False, show_skip=False).inline_keyboard[0])
    kb = InlineKeyboardMarkup(rows)
    text = t(lang, "symptom.type.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "type"
    _push_state(context, SYM_FLOW, SYM_TYPE)
    _store.save(user, flow=SYM_FLOW, step="type", draft=draft, now_utc=now_utc())
    return SYM_TYPE


async def symptom_type_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "type":
        return SYM_TYPE
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    draft["symptom_type"] = cb.value
    _store.save(user, flow=SYM_FLOW, step="intensity", draft=draft, now_utc=now_utc())
    return await symptom_prompt_intensity(update, context, edit_message=True)


async def symptom_prompt_intensity(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    text = t(lang, "symptom.intensity.help")
    kb = nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "intensity"
    _push_state(context, SYM_FLOW, SYM_INTENSITY)
    _store.save(user, flow=SYM_FLOW, step="intensity", draft=draft, now_utc=now_utc())
    return SYM_INTENSITY


async def symptom_intensity_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    try:
        val = int((update.message.text or "").strip())
    except Exception:
        user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
        await update.message.reply_text(t(getattr(user, "language", "en"), "symptom.intensity.bad"))
        return SYM_INTENSITY
    if not (0 <= val <= 10):
        user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
        await update.message.reply_text(t(getattr(user, "language", "en"), "symptom.intensity.range"))
        return SYM_INTENSITY
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    draft["intensity"] = val
    _store.save(user, flow=SYM_FLOW, step="time", draft=draft, now_utc=now_utc())
    return await symptom_prompt_time(update, context, edit_message=False)


async def symptom_prompt_time(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "meal.time.now"), callback_data="symptom:time:now"),
                InlineKeyboardButton(t(lang, "meal.time.custom"), callback_data="symptom:time:custom"),
            ],
            nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "symptom.time.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "time"
    _push_state(context, SYM_FLOW, SYM_TIME)
    _store.save(user, flow=SYM_FLOW, step="time", draft=draft, now_utc=now_utc())
    return SYM_TIME


async def symptom_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "time":
        return SYM_TIME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    if cb.value == "now":
        draft["started_at_utc"] = now_utc().isoformat()
        _store.save(user, flow=SYM_FLOW, step="duration", draft=draft, now_utc=now_utc())
        return await symptom_prompt_duration(update, context, edit_message=True)
    if cb.value == "custom":
        _store.save(user, flow=SYM_FLOW, step="time_custom", draft=draft, now_utc=now_utc())
        return await symptom_prompt_time_custom(update, context, edit_message=True)
    return SYM_TIME


async def symptom_prompt_time_custom(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    text = t(lang, "symptom.time.custom_help")
    kb = nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(SYM_FLOW)] = "time_custom"
    _push_state(context, SYM_FLOW, SYM_TIME_CUSTOM)
    _store.save(user, flow=SYM_FLOW, step="time_custom", draft=draft, now_utc=now_utc())
    return SYM_TIME_CUSTOM


async def symptom_time_custom_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    parsed = parse_user_time(update.message.text or "", user_tz=user.timezone, now_utc=now_utc())
    if not parsed:
        await update.message.reply_text(t(getattr(user, "language", "en"), "meal.time.parse_fail"))
        return SYM_TIME_CUSTOM
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    draft["started_at_utc"] = parsed.isoformat()
    _store.save(user, flow=SYM_FLOW, step="duration", draft=draft, now_utc=now_utc())
    return await symptom_prompt_duration(update, context, edit_message=False)


async def symptom_prompt_duration(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "symptom.duration.ongoing"), callback_data="symptom:duration:ongoing"),
                InlineKeyboardButton("15m", callback_data="symptom:duration:15"),
                InlineKeyboardButton("30m", callback_data="symptom:duration:30"),
                InlineKeyboardButton("60m", callback_data="symptom:duration:60"),
            ],
            [InlineKeyboardButton(t(lang, "symptom.duration.custom_btn"), callback_data="symptom:duration:custom")],
            nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "symptom.duration.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "duration"
    _push_state(context, SYM_FLOW, SYM_DURATION)
    _store.save(user, flow=SYM_FLOW, step="duration", draft=draft, now_utc=now_utc())
    return SYM_DURATION


async def symptom_duration_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "duration":
        return SYM_DURATION
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    if cb.value == "ongoing":
        draft["duration_minutes"] = None
        _store.save(user, flow=SYM_FLOW, step="notes", draft=draft, now_utc=now_utc())
        return await symptom_prompt_notes(update, context, edit_message=True)
    if cb.value == "custom":
        _store.save(user, flow=SYM_FLOW, step="duration_custom", draft=draft, now_utc=now_utc())
        return await symptom_prompt_duration_custom(update, context, edit_message=True)
    try:
        mins = int(cb.value)
    except Exception:
        return SYM_DURATION
    draft["duration_minutes"] = mins
    _store.save(user, flow=SYM_FLOW, step="notes", draft=draft, now_utc=now_utc())
    return await symptom_prompt_notes(update, context, edit_message=True)


async def symptom_prompt_duration_custom(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    text = t(lang, "symptom.duration.custom_help")
    kb = nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "duration_custom"
    _push_state(context, SYM_FLOW, SYM_DURATION_CUSTOM)
    _store.save(user, flow=SYM_FLOW, step="duration_custom", draft=draft, now_utc=now_utc())
    return SYM_DURATION_CUSTOM


async def symptom_duration_custom_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    raw = (update.message.text or "").strip().lower()
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    if raw == "ongoing":
        draft["duration_minutes"] = None
        _store.save(user, flow=SYM_FLOW, step="notes", draft=draft, now_utc=now_utc())
        return await symptom_prompt_notes(update, context, edit_message=False)
    try:
        mins = int(raw)
    except Exception:
        await update.message.reply_text(t(getattr(user, "language", "en"), "symptom.duration.bad"))
        return SYM_DURATION_CUSTOM
    if mins <= 0 or mins > 24 * 60:
        await update.message.reply_text(t(getattr(user, "language", "en"), "symptom.duration.range"))
        return SYM_DURATION_CUSTOM
    draft["duration_minutes"] = mins
    _store.save(user, flow=SYM_FLOW, step="notes", draft=draft, now_utc=now_utc())
    return await symptom_prompt_notes(update, context, edit_message=False)


async def symptom_prompt_notes(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    text = t(lang, "symptom.notes.help")
    kb = nav_kb(flow=SYM_FLOW, lang=lang, show_back=True, show_skip=True)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(SYM_FLOW)] = "notes"
    _push_state(context, SYM_FLOW, SYM_NOTES)
    _store.save(user, flow=SYM_FLOW, step="notes", draft=draft, now_utc=now_utc())
    return SYM_NOTES


async def symptom_notes_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    draft["notes"] = (update.message.text or "").strip()
    _store.save(user, flow=SYM_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return await symptom_prompt_confirm(update, context, edit_message=False)


async def symptom_prompt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(SYM_FLOW), {})
    started_at = datetime.fromisoformat(draft.get("started_at_utc")) if draft.get("started_at_utc") else now_utc()
    duration = draft.get("duration_minutes", None)
    notes = draft.get("notes") or t(lang, "common.none")
    duration_text = (
        t(lang, "symptom.duration.ongoing") if duration is None else f"{duration} min"
    )
    text = (
        f"{t(lang, 'symptom.confirm.title')}\n"
        f"{t(lang, 'symptom.confirm.type')}: {symptom_type_label(lang, draft.get('symptom_type'))}\n"
        f"{t(lang, 'symptom.confirm.intensity')}: {draft.get('intensity')}/10\n"
        f"{t(lang, 'symptom.confirm.started')}: {fmt_dt_user(started_at, user_tz=user.timezone)}\n"
        f"{t(lang, 'symptom.confirm.duration')}: {duration_text}\n"
        f"{t(lang, 'symptom.confirm.notes')}: {notes}\n\n"
        f"{t(lang, 'symptom.confirm.save_q')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "common.save"), callback_data="symptom:confirm:save"),
                InlineKeyboardButton(t(lang, "common.back"), callback_data="symptom:nav:back"),
                InlineKeyboardButton(t(lang, "common.cancel"), callback_data="symptom:nav:cancel"),
            ]
        ]
    )
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(SYM_FLOW)] = "confirm"
    _push_state(context, SYM_FLOW, SYM_CONFIRM)
    _store.save(user, flow=SYM_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return SYM_CONFIRM


async def symptom_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "confirm" or cb.value != "save":
        return SYM_CONFIRM
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.get(_draft_key(SYM_FLOW), {})
    create_symptom(
        user,
        symptom_type=str(draft.get("symptom_type", "other")),
        intensity=int(draft.get("intensity", 0)),
        started_at_utc=datetime.fromisoformat(draft.get("started_at_utc")) if draft.get("started_at_utc") else now_utc(),
        duration_minutes=draft.get("duration_minutes"),
        notes=draft.get("notes"),
    )
    _store.clear(user, flow=SYM_FLOW)
    context.user_data.pop(_draft_key(SYM_FLOW), None)
    context.user_data.pop(_hist_key(SYM_FLOW), None)
    context.user_data.pop(_step_key(SYM_FLOW), None)
    await q.edit_message_text(t(lang, "symptom.logged", disclaimer=t(lang, "disclaimer.text")))
    if q.message:
        await q.message.reply_text(t(lang, "common.what_next"), reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def symptom_nav_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != SYM_FLOW or cb.kind != "nav":
        return ConversationHandler.END
    if cb.value == "cancel":
        return await _cancel(update, context, flow=SYM_FLOW)
    if cb.value == "skip":
        return await symptom_prompt_confirm(update, context, edit_message=True)
    if cb.value == "back":
        _pop_state(context, SYM_FLOW)
        prev = _pop_state(context, SYM_FLOW) or SYM_TYPE
        return await symptom_render_state(update, context, prev, edit_message=True)
    return ConversationHandler.END


# -----------------------
# Medicine flow
# -----------------------

MED_FLOW = "med"
MED_RESUME, MED_NAME, MED_DOSAGE, MED_TIME, MED_TIME_CUSTOM, MED_CONFIRM = range(6)
_MED_TOP_NAMES_KEY = "med:top_names"
_MED_NAME_NO_SUGGEST_KEY = "med:name_no_suggest"

async def med_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel(update, context, flow=MED_FLOW)


async def med_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    # Reset name-step UI mode for a fresh entry into the flow.
    context.user_data[_MED_NAME_NO_SUGGEST_KEY] = False
    loaded = _store.load(user, flow=MED_FLOW, now_utc=now_utc())
    if loaded:
        context.user_data[_draft_key(MED_FLOW)] = loaded.draft
        context.user_data[_step_key(MED_FLOW)] = loaded.step
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(t(lang, "common.resume_draft"), callback_data="med:resume:yes"),
                    InlineKeyboardButton(t(lang, "common.discard"), callback_data="med:resume:no"),
                ]
            ]
        )
        if update.message:
            await update.message.reply_text(t(lang, "med.unfinished_resume"), reply_markup=kb)
        return MED_RESUME
    context.user_data[_draft_key(MED_FLOW)] = {}
    context.user_data[_hist_key(MED_FLOW)] = []
    return await med_prompt_name(update, context, edit_message=False)


async def med_resume_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "resume":
        return MED_RESUME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    if cb.value == "no":
        _store.clear(user, flow=MED_FLOW)
        context.user_data[_draft_key(MED_FLOW)] = {}
        context.user_data[_hist_key(MED_FLOW)] = []
        return await med_prompt_name(update, context, edit_message=True)

    step = str(context.user_data.get(_step_key(MED_FLOW), "name"))
    mapping = {"name": MED_NAME, "dosage": MED_DOSAGE, "time": MED_TIME, "time_custom": MED_TIME_CUSTOM, "confirm": MED_CONFIRM}
    return await med_render_state(update, context, mapping.get(step, MED_NAME), edit_message=True)


async def med_render_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, *, edit_message: bool) -> int:
    if state == MED_NAME:
        return await med_prompt_name(update, context, edit_message=edit_message)
    if state == MED_DOSAGE:
        return await med_prompt_dosage(update, context, edit_message=edit_message)
    if state == MED_TIME:
        return await med_prompt_time(update, context, edit_message=edit_message)
    if state == MED_TIME_CUSTOM:
        return await med_prompt_time_custom(update, context, edit_message=edit_message)
    if state == MED_CONFIRM:
        return await med_prompt_confirm(update, context, edit_message=edit_message)
    return ConversationHandler.END


async def med_prompt_name(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    text = t(lang, "med.name.prompt")
    no_suggest = bool(context.user_data.get(_MED_NAME_NO_SUGGEST_KEY))
    top_names = top_medication_names(user, limit=3) if not no_suggest else []
    context.user_data[_MED_TOP_NAMES_KEY] = top_names
    if top_names:
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for i, name in enumerate(top_names):
            row.append(InlineKeyboardButton(name, callback_data=f"med:namepick:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(t(lang, "med.name.other_btn"), callback_data="med:nameother:1")])
        rows.append(nav_kb(flow=MED_FLOW, lang=lang, show_back=False, show_skip=False).inline_keyboard[0])
        kb = InlineKeyboardMarkup(rows)
    else:
        kb = nav_kb(flow=MED_FLOW, lang=lang, show_back=False, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MED_FLOW)] = "name"
    _push_state(context, MED_FLOW, MED_NAME)
    _store.save(user, flow=MED_FLOW, step="name", draft=draft, now_utc=now_utc())
    return MED_NAME


async def med_namepick_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "namepick":
        return MED_NAME
    try:
        idx = int(cb.value)
    except Exception:
        return MED_NAME

    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    top_names = context.user_data.get(_MED_TOP_NAMES_KEY) or []
    if not isinstance(top_names, list) or idx < 0 or idx >= len(top_names):
        return MED_NAME
    picked = str(top_names[idx]).strip()
    if not picked:
        return MED_NAME

    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    draft["name"] = picked
    _store.save(user, flow=MED_FLOW, step="dosage", draft=draft, now_utc=now_utc())
    return await med_prompt_dosage(update, context, edit_message=True)


async def med_nameother_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "nameother":
        return MED_NAME
    # Switch to plain free-text input by hiding suggestion buttons.
    context.user_data[_MED_NAME_NO_SUGGEST_KEY] = True
    return await med_prompt_name(update, context, edit_message=True)


async def med_name_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    name = (update.message.text or "").strip()
    if not name:
        user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
        await update.message.reply_text(t(getattr(user, "language", "en"), "med.name.bad"))
        return MED_NAME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    draft["name"] = name
    _store.save(user, flow=MED_FLOW, step="dosage", draft=draft, now_utc=now_utc())
    return await med_prompt_dosage(update, context, edit_message=False)


async def med_prompt_dosage(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    text = t(lang, "med.dosage.prompt")
    kb = nav_kb(flow=MED_FLOW, lang=lang, show_back=True, show_skip=True)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MED_FLOW)] = "dosage"
    _push_state(context, MED_FLOW, MED_DOSAGE)
    _store.save(user, flow=MED_FLOW, step="dosage", draft=draft, now_utc=now_utc())
    return MED_DOSAGE


async def med_dosage_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    draft["dosage"] = (update.message.text or "").strip()
    _store.save(user, flow=MED_FLOW, step="time", draft=draft, now_utc=now_utc())
    return await med_prompt_time(update, context, edit_message=False)


async def med_prompt_time(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "med.time.now_btn"), callback_data="med:time:now"),
                InlineKeyboardButton(t(lang, "med.time.custom_btn"), callback_data="med:time:custom"),
            ],
            nav_kb(flow=MED_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "med.time.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MED_FLOW)] = "time"
    _push_state(context, MED_FLOW, MED_TIME)
    _store.save(user, flow=MED_FLOW, step="time", draft=draft, now_utc=now_utc())
    return MED_TIME


async def med_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "time":
        return MED_TIME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    if cb.value == "now":
        draft["taken_at_utc"] = now_utc().isoformat()
        _store.save(user, flow=MED_FLOW, step="confirm", draft=draft, now_utc=now_utc())
        return await med_prompt_confirm(update, context, edit_message=True)
    if cb.value == "custom":
        _store.save(user, flow=MED_FLOW, step="time_custom", draft=draft, now_utc=now_utc())
        return await med_prompt_time_custom(update, context, edit_message=True)
    return MED_TIME


async def med_prompt_time_custom(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    text = t(lang, "med.time.custom_help")
    kb = nav_kb(flow=MED_FLOW, lang=lang, show_back=True, show_skip=False)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MED_FLOW)] = "time_custom"
    _push_state(context, MED_FLOW, MED_TIME_CUSTOM)
    _store.save(user, flow=MED_FLOW, step="time_custom", draft=draft, now_utc=now_utc())
    return MED_TIME_CUSTOM


async def med_time_custom_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    parsed = parse_user_time(update.message.text or "", user_tz=user.timezone, now_utc=now_utc())
    if not parsed:
        await update.message.reply_text(t(getattr(user, "language", "en"), "meal.time.parse_fail"))
        return MED_TIME_CUSTOM
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    draft["taken_at_utc"] = parsed.isoformat()
    _store.save(user, flow=MED_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return await med_prompt_confirm(update, context, edit_message=False)


async def med_prompt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MED_FLOW), {})
    taken_at = datetime.fromisoformat(draft.get("taken_at_utc")) if draft.get("taken_at_utc") else now_utc()
    dosage = draft.get("dosage") or t(lang, "common.none")
    text = (
        f"{t(lang, 'med.confirm.title')}\n"
        f"{t(lang, 'med.confirm.name')}: {draft.get('name')}\n"
        f"{t(lang, 'med.confirm.dosage')}: {dosage}\n"
        f"{t(lang, 'med.confirm.time')}: {fmt_dt_user(taken_at, user_tz=user.timezone)}\n\n"
        f"{t(lang, 'med.confirm.save_q')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "common.save"), callback_data="med:confirm:save"),
                InlineKeyboardButton(t(lang, "common.back"), callback_data="med:nav:back"),
                InlineKeyboardButton(t(lang, "common.cancel"), callback_data="med:nav:cancel"),
            ]
        ]
    )
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MED_FLOW)] = "confirm"
    _push_state(context, MED_FLOW, MED_CONFIRM)
    _store.save(user, flow=MED_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return MED_CONFIRM


async def med_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "confirm" or cb.value != "save":
        return MED_CONFIRM
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.get(_draft_key(MED_FLOW), {})
    create_medication(
        user,
        name=str(draft.get("name", "")),
        dosage=str(draft.get("dosage", "")),
        taken_at_utc=datetime.fromisoformat(draft.get("taken_at_utc")) if draft.get("taken_at_utc") else now_utc(),
    )
    _store.clear(user, flow=MED_FLOW)
    context.user_data.pop(_draft_key(MED_FLOW), None)
    context.user_data.pop(_hist_key(MED_FLOW), None)
    context.user_data.pop(_step_key(MED_FLOW), None)
    await q.edit_message_text(t(lang, "med.logged", disclaimer=t(lang, "disclaimer.text")))
    if q.message:
        await q.message.reply_text(t(lang, "common.what_next"), reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def med_nav_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MED_FLOW or cb.kind != "nav":
        return ConversationHandler.END
    if cb.value == "cancel":
        return await _cancel(update, context, flow=MED_FLOW)
    if cb.value == "skip":
        return await med_prompt_time(update, context, edit_message=True)
    if cb.value == "back":
        _pop_state(context, MED_FLOW)
        prev = _pop_state(context, MED_FLOW) or MED_NAME
        return await med_render_state(update, context, prev, edit_message=True)
    return ConversationHandler.END


# -----------------------
# Morning check flow
# -----------------------

MORNING_FLOW = "morning"
MORN_RESUME, MORN_SLEEP, MORN_STRESS, MORN_ACTIVITY, MORN_NOTES, MORN_CONFIRM = range(6)

async def morning_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel(update, context, flow=MORNING_FLOW)


async def morning_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    loaded = _store.load(user, flow=MORNING_FLOW, now_utc=now_utc())
    if loaded:
        context.user_data[_draft_key(MORNING_FLOW)] = loaded.draft
        context.user_data[_step_key(MORNING_FLOW)] = loaded.step
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(t(lang, "common.resume_draft"), callback_data="morning:resume:yes"),
                    InlineKeyboardButton(t(lang, "common.discard"), callback_data="morning:resume:no"),
                ]
            ]
        )
        if update.message:
            await update.message.reply_text(t(lang, "morning.unfinished_resume"), reply_markup=kb)
        return MORN_RESUME
    context.user_data[_draft_key(MORNING_FLOW)] = {}
    context.user_data[_hist_key(MORNING_FLOW)] = []
    return await morning_prompt_sleep(update, context, edit_message=False)


async def morning_resume_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "resume":
        return MORN_RESUME
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    if cb.value == "no":
        _store.clear(user, flow=MORNING_FLOW)
        context.user_data[_draft_key(MORNING_FLOW)] = {}
        context.user_data[_hist_key(MORNING_FLOW)] = []
        return await morning_prompt_sleep(update, context, edit_message=True)

    step = str(context.user_data.get(_step_key(MORNING_FLOW), "sleep"))
    mapping = {"sleep": MORN_SLEEP, "stress": MORN_STRESS, "activity": MORN_ACTIVITY, "notes": MORN_NOTES, "confirm": MORN_CONFIRM}
    return await morning_render_state(update, context, mapping.get(step, MORN_SLEEP), edit_message=True)


async def morning_render_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, *, edit_message: bool) -> int:
    if state == MORN_SLEEP:
        return await morning_prompt_sleep(update, context, edit_message=edit_message)
    if state == MORN_STRESS:
        return await morning_prompt_stress(update, context, edit_message=edit_message)
    if state == MORN_ACTIVITY:
        return await morning_prompt_activity(update, context, edit_message=edit_message)
    if state == MORN_NOTES:
        return await morning_prompt_notes(update, context, edit_message=edit_message)
    if state == MORN_CONFIRM:
        return await morning_prompt_confirm(update, context, edit_message=edit_message)
    return ConversationHandler.END


async def morning_prompt_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(sleep_position_label(lang, "left"), callback_data="morning:sleep:left"),
                InlineKeyboardButton(sleep_position_label(lang, "right"), callback_data="morning:sleep:right"),
                InlineKeyboardButton(sleep_position_label(lang, "back"), callback_data="morning:sleep:back"),
            ],
            [
                InlineKeyboardButton(sleep_position_label(lang, "stomach"), callback_data="morning:sleep:stomach"),
                InlineKeyboardButton(sleep_position_label(lang, "mixed"), callback_data="morning:sleep:mixed"),
                InlineKeyboardButton(sleep_position_label(lang, "unknown"), callback_data="morning:sleep:unknown"),
            ],
            nav_kb(flow=MORNING_FLOW, lang=lang, show_back=False, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "morning.sleep.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MORNING_FLOW)] = "sleep"
    _push_state(context, MORNING_FLOW, MORN_SLEEP)
    _store.save(user, flow=MORNING_FLOW, step="sleep", draft=draft, now_utc=now_utc())
    return MORN_SLEEP


async def morning_sleep_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "sleep":
        return MORN_SLEEP
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    draft["sleep_position"] = cb.value
    _store.save(user, flow=MORNING_FLOW, step="stress", draft=draft, now_utc=now_utc())
    return await morning_prompt_stress(update, context, edit_message=True)


async def morning_prompt_stress(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1", callback_data="morning:stress:1"),
                InlineKeyboardButton("2", callback_data="morning:stress:2"),
                InlineKeyboardButton("3", callback_data="morning:stress:3"),
                InlineKeyboardButton("4", callback_data="morning:stress:4"),
                InlineKeyboardButton("5", callback_data="morning:stress:5"),
            ],
            nav_kb(flow=MORNING_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "morning.stress.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MORNING_FLOW)] = "stress"
    _push_state(context, MORNING_FLOW, MORN_STRESS)
    _store.save(user, flow=MORNING_FLOW, step="stress", draft=draft, now_utc=now_utc())
    return MORN_STRESS


async def morning_stress_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "stress":
        return MORN_STRESS
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    draft["stress_level"] = int(cb.value)
    _store.save(user, flow=MORNING_FLOW, step="activity", draft=draft, now_utc=now_utc())
    return await morning_prompt_activity(update, context, edit_message=True)


async def morning_prompt_activity(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(activity_level_label(lang, "none"), callback_data="morning:activity:none"),
                InlineKeyboardButton(activity_level_label(lang, "light"), callback_data="morning:activity:light"),
            ],
            [
                InlineKeyboardButton(activity_level_label(lang, "moderate"), callback_data="morning:activity:moderate"),
                InlineKeyboardButton(activity_level_label(lang, "intense"), callback_data="morning:activity:intense"),
            ],
            [InlineKeyboardButton(activity_level_label(lang, "unknown"), callback_data="morning:activity:unknown")],
            nav_kb(flow=MORNING_FLOW, lang=lang, show_back=True, show_skip=False).inline_keyboard[0],
        ]
    )
    text = t(lang, "morning.activity.title")
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MORNING_FLOW)] = "activity"
    _push_state(context, MORNING_FLOW, MORN_ACTIVITY)
    _store.save(user, flow=MORNING_FLOW, step="activity", draft=draft, now_utc=now_utc())
    return MORN_ACTIVITY


async def morning_activity_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "activity":
        return MORN_ACTIVITY
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    draft["activity_level"] = cb.value
    _store.save(user, flow=MORNING_FLOW, step="notes", draft=draft, now_utc=now_utc())
    return await morning_prompt_notes(update, context, edit_message=True)


async def morning_prompt_notes(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    text = t(lang, "morning.notes.prompt")
    kb = nav_kb(flow=MORNING_FLOW, lang=lang, show_back=True, show_skip=True)
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    context.user_data[_step_key(MORNING_FLOW)] = "notes"
    _push_state(context, MORNING_FLOW, MORN_NOTES)
    _store.save(user, flow=MORNING_FLOW, step="notes", draft=draft, now_utc=now_utc())
    return MORN_NOTES


async def morning_notes_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    draft["activity_notes"] = (update.message.text or "").strip()
    _store.save(user, flow=MORNING_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return await morning_prompt_confirm(update, context, edit_message=False)


async def morning_prompt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_message: bool) -> int:
    if not update.effective_user:
        return ConversationHandler.END
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.setdefault(_draft_key(MORNING_FLOW), {})
    local_date = now_utc().astimezone(ZoneInfo(user.timezone)).date()
    notes = draft.get("activity_notes") or t(lang, "common.none")
    text = (
        f"{t(lang, 'morning.confirm.title')}\n"
        f"{t(lang, 'morning.confirm.date')}: {local_date.isoformat()}\n"
        f"{t(lang, 'morning.confirm.sleep')}: {sleep_position_label(lang, draft.get('sleep_position', 'unknown'))}\n"
        f"{t(lang, 'morning.confirm.stress')}: {draft.get('stress_level', 3)}/5\n"
        f"{t(lang, 'morning.confirm.activity')}: {activity_level_label(lang, draft.get('activity_level', 'unknown'))}\n"
        f"{t(lang, 'morning.confirm.notes')}: {notes}\n\n"
        f"{t(lang, 'morning.confirm.save_q')}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "common.save"), callback_data="morning:confirm:save"),
                InlineKeyboardButton(t(lang, "common.back"), callback_data="morning:nav:back"),
                InlineKeyboardButton(t(lang, "common.cancel"), callback_data="morning:nav:cancel"),
            ]
        ]
    )
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    context.user_data[_step_key(MORNING_FLOW)] = "confirm"
    _push_state(context, MORNING_FLOW, MORN_CONFIRM)
    _store.save(user, flow=MORNING_FLOW, step="confirm", draft=draft, now_utc=now_utc())
    return MORN_CONFIRM


async def morning_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not update.effective_user:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "confirm" or cb.value != "save":
        return MORN_CONFIRM
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", "en")
    draft = context.user_data.get(_draft_key(MORNING_FLOW), {})
    local_date = now_utc().astimezone(ZoneInfo(user.timezone)).date()
    create_morning_check(
        user,
        local_date=local_date,
        sleep_position=str(draft.get("sleep_position", "unknown")),
        stress_level=int(draft.get("stress_level", 3)),
        activity_level=str(draft.get("activity_level", "unknown")),
        activity_notes=draft.get("activity_notes"),
    )
    _store.clear(user, flow=MORNING_FLOW)
    context.user_data.pop(_draft_key(MORNING_FLOW), None)
    context.user_data.pop(_hist_key(MORNING_FLOW), None)
    context.user_data.pop(_step_key(MORNING_FLOW), None)
    await q.edit_message_text(t(lang, "morning.logged", disclaimer=t(lang, "disclaimer.text")))
    if q.message:
        await q.message.reply_text(t(lang, "common.what_next"), reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def morning_nav_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    cb = parse_cb(q.data)
    if not cb or cb.flow != MORNING_FLOW or cb.kind != "nav":
        return ConversationHandler.END
    if cb.value == "cancel":
        return await _cancel(update, context, flow=MORNING_FLOW)
    if cb.value == "skip":
        return await morning_prompt_confirm(update, context, edit_message=True)
    if cb.value == "back":
        _pop_state(context, MORNING_FLOW)
        prev = _pop_state(context, MORNING_FLOW) or MORN_SLEEP
        return await morning_render_state(update, context, prev, edit_message=True)
    return ConversationHandler.END


def meal_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("meal", meal_entry),
            MessageHandler(filters.Regex(r"^(Meal|➕\s*Meal)$"), meal_entry),
        ],
        states={
            MEAL_RESUME: [CallbackQueryHandler(meal_resume_cb, pattern=r"^meal:resume:")],
            MEAL_TIME: [
                CallbackQueryHandler(meal_time_cb, pattern=r"^meal:time:"),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_TIME_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, meal_custom_time_msg),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_INPUT: [
                MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, meal_input_msg),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_PORTION: [
                CallbackQueryHandler(meal_portion_cb, pattern=r"^meal:portion:"),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_FAT: [
                CallbackQueryHandler(meal_fat_cb, pattern=r"^meal:fat:"),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_POSTURE: [
                CallbackQueryHandler(meal_posture_cb, pattern=r"^meal:posture:"),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
            MEAL_CONFIRM: [
                CallbackQueryHandler(meal_confirm_cb, pattern=r"^meal:confirm:"),
                CallbackQueryHandler(meal_nav_cb, pattern=r"^meal:nav:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", meal_cancel)],
        name="meal_flow",
        persistent=False,
        per_message=False,
    )


def symptom_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("symptom", symptom_entry),
            MessageHandler(filters.Regex(r"^(Symptom|➕\s*Symptom)$"), symptom_entry),
        ],
        states={
            SYM_RESUME: [CallbackQueryHandler(symptom_resume_cb, pattern=r"^symptom:resume:")],
            SYM_TYPE: [
                CallbackQueryHandler(symptom_type_cb, pattern=r"^symptom:type:"),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_INTENSITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, symptom_intensity_msg),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_TIME: [
                CallbackQueryHandler(symptom_time_cb, pattern=r"^symptom:time:"),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_TIME_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, symptom_time_custom_msg),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_DURATION: [
                CallbackQueryHandler(symptom_duration_cb, pattern=r"^symptom:duration:"),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_DURATION_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, symptom_duration_custom_msg),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, symptom_notes_msg),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
            SYM_CONFIRM: [
                CallbackQueryHandler(symptom_confirm_cb, pattern=r"^symptom:confirm:"),
                CallbackQueryHandler(symptom_nav_cb, pattern=r"^symptom:nav:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", symptom_cancel)],
        name="symptom_flow",
        persistent=False,
        per_message=False,
    )


def medicine_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("med", med_entry),
            MessageHandler(filters.Regex(r"^(Medicine|➕\s*Medicine)$"), med_entry),
        ],
        states={
            MED_RESUME: [CallbackQueryHandler(med_resume_cb, pattern=r"^med:resume:")],
            MED_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, med_name_msg),
                CallbackQueryHandler(med_namepick_cb, pattern=r"^med:namepick:"),
                CallbackQueryHandler(med_nameother_cb, pattern=r"^med:nameother:"),
                CallbackQueryHandler(med_nav_cb, pattern=r"^med:nav:"),
            ],
            MED_DOSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, med_dosage_msg),
                CallbackQueryHandler(med_nav_cb, pattern=r"^med:nav:"),
            ],
            MED_TIME: [
                CallbackQueryHandler(med_time_cb, pattern=r"^med:time:"),
                CallbackQueryHandler(med_nav_cb, pattern=r"^med:nav:"),
            ],
            MED_TIME_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, med_time_custom_msg),
                CallbackQueryHandler(med_nav_cb, pattern=r"^med:nav:"),
            ],
            MED_CONFIRM: [
                CallbackQueryHandler(med_confirm_cb, pattern=r"^med:confirm:"),
                CallbackQueryHandler(med_nav_cb, pattern=r"^med:nav:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", med_cancel)],
        name="medicine_flow",
        persistent=False,
        per_message=False,
    )


def morning_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("morning", morning_entry),
            MessageHandler(filters.Regex(r"^(Morning check|🌅\s*Morning check)$"), morning_entry),
        ],
        states={
            MORN_RESUME: [CallbackQueryHandler(morning_resume_cb, pattern=r"^morning:resume:")],
            MORN_SLEEP: [CallbackQueryHandler(morning_sleep_cb, pattern=r"^morning:sleep:")],
            MORN_STRESS: [
                CallbackQueryHandler(morning_stress_cb, pattern=r"^morning:stress:"),
                CallbackQueryHandler(morning_nav_cb, pattern=r"^morning:nav:"),
            ],
            MORN_ACTIVITY: [
                CallbackQueryHandler(morning_activity_cb, pattern=r"^morning:activity:"),
                CallbackQueryHandler(morning_nav_cb, pattern=r"^morning:nav:"),
            ],
            MORN_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, morning_notes_msg),
                CallbackQueryHandler(morning_nav_cb, pattern=r"^morning:nav:"),
            ],
            MORN_CONFIRM: [
                CallbackQueryHandler(morning_confirm_cb, pattern=r"^morning:confirm:"),
                CallbackQueryHandler(morning_nav_cb, pattern=r"^morning:nav:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", morning_cancel)],
        name="morning_flow",
        persistent=False,
        per_message=False,
    )


def reports_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("report", _not_implemented),
            MessageHandler(filters.Regex(r"^(Reports|📊\s*Reports)$"), _not_implemented),
        ],
        states={},
        fallbacks=[CommandHandler("cancel", _not_implemented), CallbackQueryHandler(_not_implemented)],
        name="reports_flow",
        persistent=False,
        per_message=False,
    )


def settings_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("settings", _not_implemented),
            MessageHandler(filters.Regex(r"^(Settings|⚙️\s*Settings)$"), _not_implemented),
        ],
        states={},
        fallbacks=[CommandHandler("cancel", _not_implemented), CallbackQueryHandler(_not_implemented)],
        name="settings_flow",
        persistent=False,
        per_message=False,
    )


