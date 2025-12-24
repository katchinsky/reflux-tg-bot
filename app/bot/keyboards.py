from __future__ import annotations

from telegram import ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["/meal", "/symptom"],
            ["/med", "/morning"],
            ["/report", "/export", "/dashboard"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
