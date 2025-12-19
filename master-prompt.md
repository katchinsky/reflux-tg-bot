# Reflux Tracking Bot

## 1) Product goal and scope

**Goal:** A Telegram bot that helps a user (initially single-user, later multi-user) log reflux-related data (meals, symptoms, meds, morning check-ins), then produces **useful reports**: trends, potential triggers, and correlations (with proper “this is suggestive, not medical certainty” framing).

**Non-goals (v1):**

* No diagnosis or medical advice beyond safe, generic “talk to a clinician” disclaimers.
* No complex nutrition database parsing.
* No wearable integrations.

---

## 2) Core user journeys

### A) Quick logging (fast, minimal friction)

* User taps **Log meal** → chooses time (“now / 1h ago / custom”) → enters text or photo → chooses portion (S/M/L) → optional fat level (Low/Med/High) → optional posture after (laying/sitting/walking/standing) → save.
* User taps **Log symptom** → chooses type (heartburn, regurgitation, burping, nausea, cough/hoarseness, chest discomfort, throat burn, bloating, other) → intensity 0–10 → start time (“now / custom”) → duration (ongoing / minutes) → save.
* User taps **Log medicine** → name + dosage + time; optionally make recurring (e.g., daily at 09:00) → save + schedule reminders.

### B) Morning check-in (structured daily prompt)

At a configurable time (default 09:00):

* sleep position (left/right/back/stomach/mixed/unknown)
* stress level 1–5
* physical activity (none/light/moderate/intense) OR quick tags (walk/gym/yoga/run/other)

### C) Reports (weekly + on-demand)

* “Last 7 days summary”
* “Possible triggers”
* “Correlations (exploratory)”
* “Medicine adherence”
* Export (CSV/JSON) for personal analysis

---

## 3) Bot UX specification (Telegram)

### Entry points

* `/start` → onboarding + timezone confirmation + privacy note.
* Persistent **Reply Keyboard** (recommended for speed):

  * ➕ Meal
  * ➕ Symptom
  * ➕ Medicine
  * 🌅 Morning check
  * 📊 Reports
  * ⚙️ Settings
* Also allow slash commands:

  * `/meal`, `/symptom`, `/med`, `/morning`, `/report`, `/export`, `/settings`

### Conversation patterns

Use **multi-step “wizard” flows** with inline buttons. Each step:

* shows current selections
* supports **Back** and **Skip**
* supports **Cancel**

Example: Meal flow steps

1. Time: [Now] [1h ago] [Custom]
2. Input: “Send text ingredients/drink or a photo” (accept either)
3. Portion: [S] [M] [L]
4. Fat: [Low] [Med] [High] [Skip]
5. Posture after: [Laying] [Sitting] [Walking] [Standing] [Skip]
6. Confirm card: summary + [Save] [Edit]

### “Smart defaults”

* Time defaults to now.
* If user sends a photo, store Telegram `file_id` + optional caption.
* If user logs a symptom with “ongoing”, duration stays null until they later close it:

  * “End last symptom” quick action in Symptom menu.

---

## 4) Data model (PostgreSQL recommended)

### Tables

**users**

* id (uuid)
* telegram_user_id (bigint, unique)
* timezone (text, default Europe/Belgrade)
* created_at

**meals**

* id (uuid)
* user_id (uuid, fk)
* occurred_at (timestamptz) — time meal/drink consumed
* notes_text (text) — ingredients/drink free text
* photo_file_id (text, nullable) — Telegram file_id
* portion_size (enum: small/medium/large)
* fat_level (enum: low/medium/high/unknown)
* posture_after (enum: laying/sitting/walking/standing/unknown)
* tags (text[] nullable) — optional, future
* created_at

**symptoms**

* id (uuid)
* user_id
* symptom_type (enum + “other”)
* intensity (smallint 0–10)
* started_at (timestamptz)
* duration_minutes (int nullable) — null means ongoing
* notes (text nullable)
* created_at

**medications**

* id (uuid)
* user_id
* name (text)
* dosage (text) — free-form: “20 mg”, “10ml”, etc.
* taken_at (timestamptz)
* is_scheduled (bool default false) — whether created from schedule
* created_at

**med_schedules**

* id (uuid)
* user_id
* name (text)
* dosage (text)
* rrule (text) — iCal RRULE string, e.g. `FREQ=DAILY;BYHOUR=9;BYMINUTE=0`
* start_at (timestamptz)
* is_active (bool)
* last_fired_at (timestamptz nullable)
* created_at

**morning_checks**

* id (uuid)
* user_id
* date (date) — local date in user timezone
* sleep_position (enum: left/right/back/stomach/mixed/unknown)
* stress_level (smallint 1–5)
* activity_level (enum: none/light/moderate/intense/unknown)
* activity_notes (text nullable)
* created_at

**events_audit** (optional but very useful)

* id
* user_id
* event_type (text) — “meal_created”, “symptom_updated”, etc.
* payload (jsonb)
* created_at

### Indexes (important for reporting)

* meals(user_id, occurred_at desc)
* symptoms(user_id, started_at desc)
* medications(user_id, taken_at desc)
* morning_checks(user_id, date desc)

---

## 5) Backend architecture

### Stack suggestion (fast to implement, reliable)

* **Python + FastAPI** (webhook endpoint + admin endpoints)
* **PostgreSQL**
* **Redis** (optional but recommended) for:

  * conversation state (wizard progress)
  * job queue locks
* **Job runner**: Celery/RQ/Arq or a simple cron + worker loop for schedules
* Telegram Bot API via a mature library:

  * `python-telegram-bot` or `aiogram`

### High-level components

1. **Bot Handler**

   * Receives updates (webhook)
   * Routes messages/callbacks
   * Manages conversation state
2. **Service Layer**

   * Validates and persists data
   * Provides report queries
3. **Scheduler**

   * Reads `med_schedules` + morning check schedule
   * Sends reminders
4. **Reporting Engine**

   * Aggregations (daily counts, averages)
   * Correlation analysis (see section 7)

### State management (wizard flows)

Store a per-user state object:

```json
{
  "flow": "meal",
  "step": "portion",
  "draft": {
    "occurred_at": "...",
    "notes_text": "...",
    "photo_file_id": "...",
    "portion_size": "medium",
    "fat_level": "unknown",
    "posture_after": "unknown"
  }
}
```

Persist in Redis with TTL (e.g., 24h). Fallback to DB if you want resilience.

---

## 6) Validation rules and edge cases

### Input validation

* Intensity must be 0–10; reject otherwise with a re-prompt.
* Stress level 1–5.
* If user chooses custom time:

  * Accept “HH:MM” (assume today, user timezone)
  * Accept “yesterday 21:30”
  * Accept Telegram-native date picker if using WebApp later (not required in v1)

### Symptom duration logic

* If user selects “ongoing”, duration_minutes = null.
* Provide action: “Stop current symptom” → sets duration_minutes = now - started_at.

### Multi-user support (v1 ready)

Even if you build for one person, use `telegram_user_id` as identity so it scales.

---

## 7) Reports and correlation spec

### A) Basic stats (v1)

* Symptoms per day, average intensity
* Meal count per day
* Morning stress/activity trends
* Medication adherence: taken vs scheduled count

**Queries** (examples)

* Daily symptoms: `date_trunc('day', started_at at time zone user_tz)`
* Average intensity per symptom_type last 7/30 days

### B) Trigger correlations (exploratory, explainable)

This should be framed as “signals”:

**Approach v1 (simple & robust): time-window association**

* Define association windows:

  * Meal → symptoms within **0–4h** after meal start (configurable)
  * Posture “laying” after meal weights risk higher
* Compute, for each feature:

  * portion_size (S/M/L)
  * fat_level (Low/Med/High)
  * posture_after
* Metrics:

  * `P(symptom within window | feature=value)`
  * average intensity within window
  * compare against baseline `P(symptom within window | any meal)`
* Output top “most associated” features with counts.

**Example output**

* “Large portions: 9 meals logged, symptoms within 4h after 6 (67%), baseline 35%”
* “High fat: 5 meals, symptoms within 4h after 4 (80%)”
* “Laying after meals: 7 times, symptoms after 5 (71%)”

### C) Ingredient-level signals (v1.5)

Ingredients are free text. Don’t over-promise.

* Extract keywords via:

  * simple tokenization + stopword removal
  * optional user-defined aliases (“tomato” includes “pizza”, etc.)
* Compute the same window association per token appearing in meal notes.
* Only show tokens meeting minimum support (e.g., ≥4 meals).

### D) Statistical correlation (v2)

When you have enough data:

* Logistic regression or Bayesian model for symptom occurrence in a window
* Include confounders: stress level, activity, sleep position
* Still output explainable summaries, not just coefficients.

---

## 8) Reminders and recurring medicine

### User controls

Settings:

* Morning check reminder time (on/off + time)
* Medication reminders (per schedule on/off)
* Report cadence (weekly summary on/off)

### RRULE format

Store schedules as RRULE + start time in timezone.
Example: daily 09:00

* start_at: `2025-12-19T09:00:00+01:00`
* rrule: `FREQ=DAILY`

### Scheduler algorithm (safe and simple)

* Every minute (or 5 minutes) worker:

  * For active schedules, compute next occurrence after `last_fired_at` (or start_at)
  * If next_occurrence <= now and not fired yet → send reminder
  * Update `last_fired_at`
* Use a DB transaction + advisory lock to avoid double-sends.

Reminder message

* “Time for: Omeprazole — 20 mg”
* Buttons: [Taken now] [Snooze 15m] [Skip]

---

## 9) Privacy, security, and reliability

### Privacy (must-have)

* Clearly state: data is personal health info; store securely; user can delete/export anytime.
* Provide:

  * `/export` → JSON/CSV
  * `/delete_my_data` → hard delete user rows (with confirmation step)

### Security

* Store bot token in secret manager / env vars.
* HTTPS webhook.
* DB backups.
* If storing photos: prefer Telegram `file_id` only (no download) to minimize sensitive storage. If you later need the binary, download and store encrypted.

### Reliability

* Idempotency: Telegram can resend updates; store processed update_ids for ~24h in Redis.
* Graceful failure: if report query fails, return a friendly message and log.

---

## 10) Implementation plan (phased)

### Phase 0 (1 day): skeleton

* Bot webhook + basic keyboard
* Postgres schema + migrations
* User registration on `/start`

### Phase 1 (2–4 days): logging MVP

* Meal wizard (text/photo + portion + fat + posture + time)
* Symptom wizard (type + intensity + start + duration/ongoing)
* Medicine log (one-off)
* List last 10 entries for each type (“History” screen inside each menu)

### Phase 2 (2–3 days): reminders

* Morning check reminder + form
* Med schedules + reminders + “Taken now” button
* Settings page

### Phase 3 (3–6 days): reports v1

* 7/30-day summaries
* Window-based association for portion/fat/posture
* Basic charts as text (Telegram-friendly), e.g. sparkline-style counts

### Phase 4 (optional): export + ingredient tokenization

* CSV/JSON export
* Keyword associations with minimum-support threshold

---

## 11) Telegram message templates (copy-ready)

### Meal saved

**Meal logged ✅**
Time: 13:10
Portion: Medium
Fat: High
Posture after: Sitting
Notes: “pizza + cola”

### Symptom saved

**Symptom logged ✅**
Type: Heartburn
Intensity: 7/10
Started: 14:05
Duration: Ongoing
Button: [End symptom]

### Report snippet

**Last 7 days**

* Symptoms: 12 (avg intensity 5.3)
* Most common: Heartburn (7), Regurgitation (3)
* Possible signals (within 4h after meals):

  * High fat: 80% (4/5) vs baseline 35%
  * Large portions: 67% (6/9)
  * Laying after meals: 71% (5/7)

---

## 12) Concrete engineering details (API + structure)

### Suggested repository layout

* `app/bot/handlers.py` (routing, flows)
* `app/bot/keyboards.py` (inline/reply keyboards)
* `app/core/state.py` (Redis state store)
* `app/db/models.py` (SQLAlchemy)
* `app/db/migrations/` (Alembic)
* `app/services/logging.py` (create meal/symptom/med/morning)
* `app/services/reports.py`
* `app/scheduler/worker.py`
* `app/main.py` (FastAPI webhook)

### Webhook endpoint

* `POST /telegram/webhook`
* Verify secret path or header token to prevent random calls.

---

## 13) Open decisions (but you can still start now)

These don’t block implementation; pick defaults:

* Default symptom types list (you can adjust in Settings later).
* Association window (recommend default **4 hours**, configurable 2–6h).
* Fat level: self-reported Low/Med/High is enough for v1.
