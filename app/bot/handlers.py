from __future__ import annotations

import logging
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
from app.bot.text import START_TEXT
from app.services.users import ensure_user
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    await update.message.reply_text(START_TEXT, reply_markup=main_menu_keyboard())


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    now = datetime.now(tz=ZoneInfo("UTC"))
    summary = last_7_days_summary(user, now_utc=now)
    header, rows = association_signals(user, now_utc=now, window_hours=4)
    lines = [summary, "", header]
    if rows:
        for r in rows:
            p = f"{r.p:.0%}"
            avg = f"{r.avg_intensity:.1f}" if r.avg_intensity is not None else "-"
            lines.append(f"- {r.label}: {p} ({r.meals_with_symptom}/{r.meals_total}), avg intensity {avg}")
    else:
        lines.append("- Not enough data yet (need a few meals logged).")
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Export JSON", callback_data="export:json"),
                InlineKeyboardButton("Export CSV (zip)", callback_data="export:csv"),
            ]
        ]
    )
    await update.message.reply_text("Choose export format:", reply_markup=kb)


async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    await q.answer()
    fmt = (q.data or "").split(":", 1)[-1]
    user = ensure_user(update.effective_user.id, default_timezone=context.bot_data["default_timezone"])
    if fmt == "json":
        data = export_json_bytes(user)
        f = io.BytesIO(data)
        f.name = "reflux-export.json"
        await q.message.reply_document(document=f, caption="Your export (JSON).")  # type: ignore[union-attr]
        return
    if fmt == "csv":
        data = export_csv_zip_bytes(user)
        f = io.BytesIO(data)
        f.name = "reflux-export-csv.zip"
        await q.message.reply_document(document=f, caption="Your export (CSV zip).")  # type: ignore[union-attr]
        return
    await q.message.reply_text("Unknown export format.")  # type: ignore[union-attr]


def build_handlers(app: Application, *, default_timezone: str) -> None:
    app.bot_data["default_timezone"] = default_timezone
    app.add_handler(CommandHandler("start", start))
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
            await update.message.reply_text("Use /start to see the menu.", reply_markup=main_menu_keyboard())

    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Keep it simple: log exception; avoid crashing the bot.
        logging.getLogger("reflux-bot").exception(
            "Unhandled error while processing update", exc_info=context.error
        )

    app.add_error_handler(on_error)


