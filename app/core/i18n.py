from __future__ import annotations

from typing import Literal

Lang = Literal["en", "ru"]


def _norm_lang(v: str | None) -> Lang:
    vv = (v or "").strip().lower()
    if vv == "ru":
        return "ru"
    return "en"


STRINGS: dict[Lang, dict[str, str]] = {
    "en": {
        # Common / nav
        "common.back": "Back",
        "common.skip": "Skip",
        "common.cancel": "Cancel",
        "common.save": "Save",
        "common.discard": "Discard",
        "common.resume_draft": "Resume draft",
        "common.cancelled": "Cancelled.",
        "common.menu": "Menu:",
        "common.what_next": "What next?",
        "common.not_implemented": "Not implemented yet.",
        "common.none": "(none)",
        "common.unknown": "Unknown",
        # Start / disclaimers
        "start.text": (
            "Reflux Tracking Bot\n\n"
            "Use the buttons below to log meals, symptoms, medicines, or a morning check-in"
        ),
        "disclaimer.text": "Note: This bot provides tracking and exploratory signals only.",
        # Language command
        "lang.usage": "Usage: /lang en or /lang ru",
        "lang.current": "Current language: {lang}\n{usage}",
        "lang.set_ok": "Language set to {lang}.",
        "lang.bad": "Unknown language. {usage}",
        # Main handlers
        "unknown.use_start": "Use /start to see the menu.",
        # Export
        "export.choose_format": "Choose export format:",
        "export.json_btn": "Export JSON",
        "export.csv_btn": "Export CSV (zip)",
        "export.caption_json": "Your export (JSON).",
        "export.caption_csv": "Your export (CSV zip).",
        "export.unknown_format": "Unknown export format.",
        # Report (handler-level)
        "report.not_enough_data": "Not enough data yet (need a few meals logged).",
        "report.row_fmt": "- {label}: {p} ({with_symptom}/{total}), avg intensity {avg}",
        # Meal flow
        "meal.unfinished_resume": "You have an unfinished meal draft. Resume?",
        "meal.time.title": "Meal time:",
        "meal.time.now": "Now",
        "meal.time.one_hour_ago": "1h ago",
        "meal.time.custom": "Custom",
        "meal.time.custom_help": "Send time as `HH:MM` (today) or `yesterday HH:MM`.",
        "meal.time.parse_fail": "Couldn’t parse that time. Try `13:10` or `yesterday 21:30`.",
        "meal.input.help": "Send meal notes as text, or send a photo (with optional caption).",
        "meal.portion.title": "Portion size:",
        "meal.fat.title": "Fat level (optional):",
        "meal.posture.title": "Posture after (optional):",
        "meal.confirm.title": "**Meal draft**",
        "meal.confirm.time": "Time",
        "meal.confirm.portion": "Portion",
        "meal.confirm.fat": "Fat",
        "meal.confirm.posture": "Posture",
        "meal.confirm.notes": "Notes",
        "meal.confirm.save_q": "Save?",
        "meal.logged": "Meal logged.\n\n{disclaimer}",
        # Symptom flow
        "symptom.unfinished_resume": "You have an unfinished symptom draft. Resume?",
        "symptom.type.title": "Symptom type:",
        "symptom.intensity.help": "Intensity (0–10). Send a number.",
        "symptom.intensity.bad": "Please send a number 0–10.",
        "symptom.intensity.range": "Intensity must be 0–10.",
        "symptom.time.title": "Start time:",
        "symptom.time.custom_help": "Send start time as `HH:MM` (today) or `yesterday HH:MM`.",
        "symptom.duration.title": "Duration:",
        "symptom.duration.ongoing": "Ongoing",
        "symptom.duration.custom_btn": "Custom minutes",
        "symptom.duration.custom_help": "Send duration in minutes (number), or type `ongoing`.",
        "symptom.duration.bad": "Please send a number of minutes, or `ongoing`.",
        "symptom.duration.range": "That duration seems off. Send minutes (1–1440).",
        "symptom.notes.help": "Optional notes (send text), or tap Skip.",
        "symptom.confirm.title": "**Symptom draft**",
        "symptom.confirm.type": "Type",
        "symptom.confirm.intensity": "Intensity",
        "symptom.confirm.started": "Started",
        "symptom.confirm.duration": "Duration",
        "symptom.confirm.notes": "Notes",
        "symptom.confirm.save_q": "Save?",
        "symptom.logged": "Symptom logged.\n\n{disclaimer}",
        # Medicine flow
        "med.unfinished_resume": "You have an unfinished medicine draft. Resume?",
        "med.name.prompt": "Medicine name (e.g., Omeprazole):",
        "med.name.bad": "Please send a medicine name.",
        "med.name.other_btn": "Other",
        "med.dosage.prompt": "Dosage (optional). Send text like `20 mg`, or tap Skip.",
        "med.time.title": "When did you take it?",
        "med.time.now_btn": "Taken now",
        "med.time.custom_btn": "Custom",
        "med.time.custom_help": "Send time as `HH:MM` (today) or `yesterday HH:MM`.",
        "med.confirm.title": "**Medicine draft**",
        "med.confirm.name": "Name",
        "med.confirm.dosage": "Dosage",
        "med.confirm.time": "Time",
        "med.confirm.save_q": "Save?",
        "med.logged": "Medicine logged.\n\n{disclaimer}",
        # Morning check
        "morning.unfinished_resume": "You have an unfinished morning-check draft. Resume?",
        "morning.sleep.title": "Sleep position:",
        "morning.stress.title": "Stress level (1–5):",
        "morning.activity.title": "Physical activity level:",
        "morning.notes.prompt": "Optional activity notes (e.g., walk/gym). Send text, or tap Skip.",
        "morning.confirm.title": "**Morning check draft**",
        "morning.confirm.date": "Date",
        "morning.confirm.sleep": "Sleep",
        "morning.confirm.stress": "Stress",
        "morning.confirm.activity": "Activity",
        "morning.confirm.notes": "Notes",
        "morning.confirm.save_q": "Save?",
        "morning.logged": "Morning check saved.\n\n{disclaimer}",
        # Reports service
        "reports.last_7_days.title": "Last 7 days",
        "reports.last_7_days.symptoms": "- Symptoms: {count} (avg intensity {avg:.1f})",
        "reports.last_7_days.most_common": "- Most common: {common}",
        "reports.last_7_days.meals": "- Meals logged: {count}",
        "reports.last_7_days.stress_avg": "- Morning stress avg (recent): {avg:.1f}/5",
        "reports.signals.header": (
            "Possible signals (within {window_hours}h after meals)\n"
            "Baseline: {baseline:.0%} ({with_any}/{total})\n"
            "These are suggestive signals only, not medical certainty."
        ),
        "reports.signals.feature.portion": "portion",
        "reports.signals.feature.fat": "fat",
        "reports.signals.feature.posture": "posture",
        "reports.signals.label": "{feature}={value}",
    },
    "ru": {
        # Common / nav
        "common.back": "Назад",
        "common.skip": "Пропустить",
        "common.cancel": "Отмена",
        "common.save": "Сохранить",
        "common.discard": "Удалить",
        "common.resume_draft": "Продолжить",
        "common.cancelled": "Отменено!",
        "common.menu": "Меню:",
        "common.what_next": "Что дальше?",
        "common.not_implemented": "Пока не реализовано.",
        "common.none": "(нет)",
        "common.unknown": "Неизвестно",
        # Start / disclaimers
        "start.text": (
            # "Reflux Tracking Bot\n\n"
            "Привет!\nЭто журнал для записи еды, симптомов рефлюкса и приема лекарств. "
            "Когда-нибудь тут будут симпатичные графики.\n\n"
            "Посвящается Варваре 💚"
        ),
        "disclaimer.text": "Дисклеймер: это журнал наблюдений, не медицинская рекомендация.",
        # Language command
        "lang.usage": "Чтобы переключить язык, воспользуйтесь командой: /lang en или /lang ru",
        "lang.current": "Текущий язык: {lang}\n{usage}",
        "lang.set_ok": "Теперь ботик будет говорить с вами на одном языке: {lang}! 💚",
        "lang.bad": "На таком языке мы говорить не умеем! Попробуйте /lang en или /lang ru.",
        # Main handlers
        "unknown.use_start": "Нажмите /start, чтобы открыть меню",
        # Export
        "export.choose_format": "В каком формате экспортируем данные?",
        "export.json_btn": "Экспорт JSON",
        "export.csv_btn": "Экспорт CSV (zip)",
        "export.caption_json": "Ваши данные (JSON)",
        "export.caption_csv": "Ваши данные (CSV zip)",
        "export.unknown_format": "Неизвестный формат экспорта!",
        # Report (handler-level)
        "report.not_enough_data": "Данных пока недостаточно. Продолжаем наблюдение...",
        "report.row_fmt": "* {label}, {p} случаев ({with_symptom}/{total})",
        # Meal flow
        "meal.unfinished_resume": "Мы нашли незавершённый черновик! Продолжаем?",
        "meal.time.title": "Когда вы ели?",
        "meal.time.now": "Сейчас",
        "meal.time.one_hour_ago": "1 час назад",
        "meal.time.custom": "Другое",
        "meal.time.custom_help": "Отправьте время как `HH:MM` (сегодня) или `yesterday HH:MM`.",
        "meal.time.parse_fail": "Не удалось распознать время. Попробуйте `13:10` или `yesterday 21:30`.",
        "meal.input.help": "Отправьте описание еды текстом или фото (можно с подписью).",
        "meal.portion.title": "Какой размер порции?",
        "meal.fat.title": "Какой уровень жирности?",
        "meal.posture.title": "А какая поза после еды?",
        "meal.confirm.title": "Запись еды",
        "meal.confirm.time": "Время",
        "meal.confirm.portion": "Размер порции",
        "meal.confirm.fat": "Жирность",
        "meal.confirm.posture": "Поза после",
        "meal.confirm.notes": "Заметки",
        "meal.confirm.save_q": "Сохранить?",
        "meal.logged": "Запись сохранена.\n\n{disclaimer}",
        # Symptom flow
        
        "symptom.unfinished_resume": "Мы нашли незавершённый черновик симптома. Продолжаем?",
        "symptom.type.title": "Какой симптом?",
        "symptom.intensity.help": "Какая интенсивность? Отправьте число от 0 до 10.",
        "symptom.intensity.bad": "Пожалуйста, отправьте целое число от 0 до 10.",
        "symptom.intensity.range": "Интенсивность должна быть целым числом от 0 до 10.",
        "symptom.time.title": "Когда начался симптом?",
        "symptom.time.custom_help": "Отправьте время начала как `HH:MM` (сегодня) или `yesterday HH:MM`.",
        "symptom.duration.title": "А сколько продлился симптом?",
        "symptom.duration.ongoing": "Продолжается сейчас",
        "symptom.duration.custom_btn": "Другое",
        "symptom.duration.custom_help": "Отправьте длительность в минутах (число) или напишите `ongoing`.",
        "symptom.duration.bad": "Пожалуйста, отправьте число минут или `ongoing`.",
        "symptom.duration.range": "Длительность выглядит странно. Отправьте минуты (1–1440).",
        "symptom.notes.help": "Заметки (опционально). Отправьте текст или нажмите «Пропустить».",
        "symptom.confirm.title": "**Запись симптома**",
        "symptom.confirm.type": "Тип",
        "symptom.confirm.intensity": "Интенсивность",
        "symptom.confirm.started": "Начало",
        "symptom.confirm.duration": "Длительность",
        "symptom.confirm.notes": "Заметки",
        "symptom.confirm.save_q": "Сохранить?",
        "symptom.logged": "Симптом сохранён.\n\n{disclaimer}",
        # Medicine flow
        "med.unfinished_resume": "У вас есть незавершённый черновик лекарства. Продолжить?",
        "med.name.prompt": "Название лекарства (например, Omeprazole):",
        "med.name.bad": "Пожалуйста, отправьте название лекарства.",
        "med.name.other_btn": "Другое",
        "med.dosage.prompt": "Дозировка (опционально). Отправьте текст вроде `20 mg` или нажмите «Пропустить».",
        "med.time.title": "Когда вы приняли лекарство?",
        "med.time.now_btn": "Только что",
        "med.time.custom_btn": "Другое",
        "med.time.custom_help": "Отправьте время как `HH:MM` (сегодня) или `yesterday HH:MM`.",
        "med.confirm.title": "Запись лекарства",
        "med.confirm.name": "Название",
        "med.confirm.dosage": "Дозировка",
        "med.confirm.time": "Время",
        "med.confirm.save_q": "Сохранить?",
        "med.logged": "Лекарство сохранено.\n\n{disclaimer}",
        # Morning check
        "morning.unfinished_resume": "У вас есть незавершённый черновик утренней проверки. Продолжить?",
        "morning.sleep.title": "Как вы спали?",
        "morning.stress.title": "Какой уровень стресса был вчера?",
        "morning.activity.title": "Какой уровень физической активности был вчера?",
        "morning.notes.prompt": "Дополнительные заметки (опционально)",
        "morning.confirm.title": "Запись утренней проверки",
        "morning.confirm.date": "Дата",
        "morning.confirm.sleep": "Сон",
        "morning.confirm.stress": "Стресс вчера",
        "morning.confirm.activity": "Активность вчера",
        "morning.confirm.notes": "Заметки",
        "morning.confirm.save_q": "Сохранить?",
        "morning.logged": "Утренняя проверка сохранена.\n\n{disclaimer}",
        # Reports service
        "reports.last_7_days.title": "В последнюю неделю вы внесли...\nvvvvvvvvvvvvvvvvvvvvv\n",
        "reports.last_7_days.symptoms": "* Симптомы: {count}\n* Средняя интенсивность симптомов: {avg:.1f}",
        "reports.last_7_days.most_common": "* Самые частые симптомы: {common}",
        "reports.last_7_days.meals": "* Приёмы пищи: {count}",
        "reports.last_7_days.stress_avg": "* Средний уровень стресса: {avg:.1f}/5",
        "reports.signals.header": (
            "Обстоятельства, в которых чаще всего возникают симптомы...\nvvvvvvvvvvvvvvvvvvvvv\n\n"
            "* Базовая вероятность возникновения симптома после еды: {baseline:.0%} ({with_any}/{total})"
        ),
        "reports.signals.feature.portion": "Размер порции:",
        "reports.signals.feature.fat": "Уровень жирности:",
        "reports.signals.feature.posture": "Поза после еды:",
        "reports.signals.label": "{feature} {value}",
    },
}


def t(locale: str | None, key: str, **kwargs) -> str:
    ll = _norm_lang(locale)
    template = STRINGS.get(ll, {}).get(key) or STRINGS["en"].get(key) or key
    try:
        return template.format(**kwargs)
    except Exception:
        # If formatting fails, return raw template to avoid crashing the bot.
        return template


def language_label(lang: str | None, code: str) -> str:
    ll = _norm_lang(lang)
    if code == "ru":
        return "Русский" if ll == "ru" else "Russian"
    return "English" if ll == "en" else "Английский"


def portion_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    if vv == "small":
        return "Small" if ll == "en" else "Маленькая"
    if vv == "large":
        return "Large" if ll == "en" else "Большая"
    # medium as default
    return "Medium" if ll == "en" else "Средняя"


def fat_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    if vv == "low":
        return "Low" if ll == "en" else "Низкая"
    if vv == "medium":
        return "Medium" if ll == "en" else "Средняя"
    if vv == "high":
        return "High" if ll == "en" else "Высокая"
    return "Unknown" if ll == "en" else "Неизвестно"


def posture_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    if vv == "laying":
        return "Laying" if ll == "en" else "Лёжа"
    if vv == "sitting":
        return "Sitting" if ll == "en" else "Сидя"
    if vv == "walking":
        return "Walking" if ll == "en" else "Ходьба"
    if vv == "standing":
        return "Standing" if ll == "en" else "Стоя"
    return "Unknown" if ll == "en" else "Неизвестно"


def symptom_type_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    mapping_en = {
        "heartburn": "Heartburn",
        "regurgitation": "Regurgitation",
        "nausea": "Nausea",
        "reflux": "Reflux",
        "cough_hoarseness": "Cough/Hoarseness",
        "chest_discomfort": "Chest discomfort",
        "throat_burn": "Throat burn",
        "bloating": "Bloating",
        "stomach_pain": "Stomach pain",
        "other": "Other",
    }
    mapping_ru = {
        "reflux": "Рефлюкс",
        "heartburn": "Изжога",
        "regurgitation": "Отрыжка",
        "nausea": "Тошнота",
        "cough_hoarseness": "Кашель / осиплость",
        "chest_discomfort": "Дискомфорт в груди",
        "throat_burn": "Жжение в горле",
        "bloating": "Вздутие",
        "stomach_pain": "Боль в животе",
        "other": "Другое",
    }
    if ll == "ru":
        return mapping_ru.get(vv) or mapping_ru["other"]
    return mapping_en.get(vv) or mapping_en["other"]


def sleep_position_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    mapping_en = {
        "left": "Left",
        "right": "Right",
        "back": "Back",
        "stomach": "Stomach",
        "mixed": "Mixed",
        "unknown": "Unknown",
    }
    mapping_ru = {
        "left": "На левом боку",
        "right": "На правом боку",
        "back": "На спине",
        "stomach": "На животе",
        "mixed": "Смешанно",
        "unknown": "Неизвестно",
    }
    if ll == "ru":
        return mapping_ru.get(vv) or mapping_ru["unknown"]
    return mapping_en.get(vv) or mapping_en["unknown"]


def activity_level_label(lang: str | None, v: str | None) -> str:
    ll = _norm_lang(lang)
    vv = (v or "").strip().lower()
    mapping_en = {
        "none": "None",
        "light": "Light",
        "moderate": "Moderate",
        "intense": "Intense",
        "unknown": "Unknown",
    }
    mapping_ru = {
        "none": "Нет",
        "light": "Лёгкая",
        "moderate": "Умеренная",
        "intense": "Интенсивная",
        "unknown": "Неизвестно",
    }
    if ll == "ru":
        return mapping_ru.get(vv) or mapping_ru["unknown"]
    return mapping_en.get(vv) or mapping_en["unknown"]


