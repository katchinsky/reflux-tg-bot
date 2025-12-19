## Reflux Tracking Bot (Telegram + Python + SQLite)

Implements the flows described in `master-prompt.md`: meal/symptom/medicine/morning check logging + basic reporting.

### Prereqs

- Python 3.11+
- A Telegram bot token from `@BotFather`

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install .
cp env.example .env
```

Edit `.env` and set `BOT_TOKEN`.

### Run (polling)

```bash
python -m app.main
```

The bot persists data to `reflux.db` in the project root (configurable).

### Notes

- This bot provides **tracking and exploratory signals only**. It does not provide medical advice.
- Webhook/FastAPI deployment and reminder scheduling are intentionally deferred in v1.


