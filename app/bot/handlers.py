from __future__ import annotations

import logging
import os
from datetime import datetime
import io
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.keyboards import main_menu_keyboard
from app.core.i18n import language_label, t
from app.services.users import ensure_user, get_user_language, set_user_language
from app.bot.handlers_flows import (
    meal_conversation,
    medicine_conversation,
    morning_conversation,
    reports_conversation,
    settings_conversation,
    symptom_conversation,
)
from app.services.reports import association_signals, last_7_days_summary
from app.services.exporting import export_csv_zip_bytes, export_json_bytes


def _lang(update: Update) -> str:
    if not update.effective_user:
        return "en"
    return get_user_language(update.effective_user.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = _lang(update)
    await update.message.reply_text(t(lang, "start.text"), reply_markup=main_menu_keyboard())


async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    current = _lang(update)
    usage = t(current, "lang.usage")
    if not context.args:
        await update.message.reply_text(
            t(current, "lang.current", lang=language_label(current, current), usage=usage),
            reply_markup=main_menu_keyboard(),
        )
        return

    raw = (context.args[0] or "").strip().lower()
    if raw not in ("en", "ru"):
        await update.message.reply_text(t(current, "lang.bad", usage=usage), reply_markup=main_menu_keyboard())
        return

    # Persist and respond in the new language.
    user = set_user_language(
        update.effective_user.id,
        lang=raw,
        default_timezone=context.bot_data["default_timezone"],
    )
    new_lang = getattr(user, "language", raw)
    await update.message.reply_text(
        t(new_lang, "lang.set_ok", lang=language_label(new_lang, new_lang)),
        reply_markup=main_menu_keyboard(),
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", _lang(update))
    now = datetime.now(tz=ZoneInfo("UTC"))
    summary = last_7_days_summary(user, now_utc=now, lang=lang)
    header, rows = association_signals(user, now_utc=now, window_hours=4, lang=lang)
    lines = [summary, "", header]
    if rows:
        for r in rows:
            p = f"{r.p:.0%}"
            avg = f"{r.avg_intensity:.1f}" if r.avg_intensity is not None else "-"
            lines.append(
                t(
                    lang,
                    "report.row_fmt",
                    label=r.label,
                    p=p,
                    with_symptom=r.meals_with_symptom,
                    total=r.meals_total,
                    avg=avg,
                )
            )
    else:
        lines.append(t(lang, "report.not_enough_data"))
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    lang = _lang(update)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "export.json_btn"), callback_data="export:json"),
                InlineKeyboardButton(t(lang, "export.csv_btn"), callback_data="export:csv"),
            ]
        ]
    )
    await update.message.reply_text(t(lang, "export.choose_format"), reply_markup=kb)


async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    await q.answer()
    fmt = (q.data or "").split(":", 1)[-1]
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    lang = getattr(user, "language", _lang(update))
    if fmt == "json":
        data = export_json_bytes(user)
        f = io.BytesIO(data)
        f.name = "reflux-export.json"
        await q.message.reply_document(document=f, caption=t(lang, "export.caption_json"))  # type: ignore[union-attr]
        return
    if fmt == "csv":
        data = export_csv_zip_bytes(user)
        f = io.BytesIO(data)
        f.name = "reflux-export-csv.zip"
        await q.message.reply_document(document=f, caption=t(lang, "export.caption_csv"))  # type: ignore[union-attr]
        return
    await q.message.reply_text(t(lang, "export.unknown_format"))  # type: ignore[union-attr]


def build_handlers(app: Application, *, default_timezone: str) -> None:
    app.bot_data["default_timezone"] = default_timezone
    # Optional LLM pipeline settings (meal parsing + taxonomy linking)
    app.bot_data["openai_api_key"] = (os.getenv("OPENAI_API_KEY", "").strip() or None)
    app.bot_data["openai_model_extract"] = (os.getenv("OPENAI_MODEL_EXTRACT", "gpt-4o-mini").strip() or "gpt-4o-mini")
    app.bot_data["openai_model_rerank"] = (os.getenv("OPENAI_MODEL_RERANK", "gpt-4o-mini").strip() or "gpt-4o-mini")
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lang", lang_command))
    app.add_handler(CommandHandler("report", report))
    # Backwards compatibility if a user still has old non-command keyboard buttons.
    app.add_handler(MessageHandler(filters.Regex(r"^(Reports|📊\s*Reports)$"), report))
    app.add_handler(CommandHandler("export", export_menu))
    app.add_handler(CallbackQueryHandler(export_callback, pattern=r"^export:"))

    # Slash commands as well as button texts.
    app.add_handler(meal_conversation())
    app.add_handler(symptom_conversation())
    app.add_handler(medicine_conversation())
    app.add_handler(morning_conversation())
    app.add_handler(reports_conversation())
    app.add_handler(settings_conversation())

    async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                t(_lang(update), "unknown.use_start"),
                reply_markup=main_menu_keyboard(),
            )

    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Keep it simple: log exception; avoid crashing the bot.
        logging.getLogger("reflux-bot").exception(
            "Unhandled error while processing update", exc_info=context.error
        )

    app.add_error_handler(on_error)
