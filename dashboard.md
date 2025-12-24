## Task: Web Dashboard for Reflux Tracker (Code-based Login from Telegram Bot)

### Goal

Implement a small web dashboard service that lets a user see their reflux tracking data (meals/product categories, symptoms, and correlations).
Access is via a **short login code** issued inside the Telegram bot.

---

## 1. User flow

1. In Telegram, user sends `/dashboard` or taps a “📊 Dashboard” button.
2. Bot replies with:

   * A **dashboard URL** (e.g. `https://reflux-dashboard.example.com`)
   * A **short login code** (e.g. `X7F9KQ`, 6–8 chars), valid for a limited time (e.g. 10–15 minutes).
3. User opens the URL in a browser, sees a **“Enter your code”** screen.
4. User enters the code → dashboard backend verifies it, links it to the correct user, and:

   * creates a session (cookie/JWT)
   * redirects to the **main dashboard page**.
5. Once logged in, the user can:

   * Change date range (e.g. Last 7 days / 30 days / custom).
   * View:

     * Most common product categories
     * Symptom statistics
     * Correlation signals between meals/product categories and symptoms.

---

## 2. Authentication & security requirements

### 2.1 Code generation (Telegram side)

* Endpoint (example): `POST /api/dashboard/create-code`

  * Input: `telegram_user_id`
  * Output:

    * `code` (short alphanumeric, case-insensitive)
    * `expires_at`
* The bot will call this endpoint, then send the resulting code to the user in chat.

### 2.2 Code storage & validation (dashboard backend)

* New table `login_codes` (or equivalent):

  * `id` (uuid)
  * `user_id` (uuid, FK to users)
  * `code` (string, unique, indexed)
  * `expires_at` (timestamptz)
  * `used_at` (timestamptz, nullable)
  * `created_at` (timestamptz)

* Constraints:

  * Code is valid **only if**:

    * now ≤ `expires_at`
    * `used_at` is null
  * On successful login:

    * set `used_at = now()`
    * create authenticated session (JWT or secure cookie).

* Rate limiting:

  * Optionally limit login attempts per IP or per code to avoid brute-force.

* Session:

  * Lifetime: e.g. 7 days of inactivity.
  * Logout endpoint to clear cookie.

### 2.3 Security notes

* All dashboard endpoints must be behind authentication.
* Use HTTPS only.
* No health/medical data in URL query params once authenticated (only in response body).

---

## 3. Dashboard UI & functionality

### 3.1 General layout

Single-page dashboard with:

* Top bar:

  * Date range selector: [Last 7 days] [Last 30 days] [Custom…]
  * (Optional) Symptom type filter dropdown.
* Main content, 3 blocks:

  1. **Product categories**
  2. **Symptom statistics**
  3. **Correlations / possible triggers**

All charts/tables update when date range or filters change.

---

### 3.2 Product categories section

**Goal:** Show which product categories appear most often in meals associated with the selected period, with simple frequency statistics.

Assumption: backend already derives or will derive **product categories** from meal text (e.g. “coffee”, “tomato-based”, “dairy”, “spicy”, etc.).

**UI:**

* Title: “Most common product categories”
* Components:

  * Horizontal bar chart:

    * X: count of meals containing category
    * Y: category names
  * Table underneath:

    * Columns: `Category`, `Meals count`, `Share of all meals (%)`, optionally `Avg symptom probability within 4h (%)`.

**Data contract (example API response):**

`GET /api/dashboard/product-categories?from=...&to=...`

```json
{
  "from": "2025-12-01",
  "to": "2025-12-08",
  "total_meals": 42,
  "categories": [
    {
      "name": "Coffee",
      "meal_count": 12,
      "share_pct": 28.6,
      "symptom_window_rate_pct": 66.7
    },
    {
      "name": "Tomato-based",
      "meal_count": 9,
      "share_pct": 21.4,
      "symptom_window_rate_pct": 55.6
    }
  ]
}
```

---

### 3.3 Symptom statistics section

**Goal:** Visualize symptom burden over time and distribution by type/intensity.

**UI:**

* Subsection A: **Timeline**

  * Line chart:

    * X: date
    * Y: number of symptoms per day
    * Optional second line: average intensity per day.
* Subsection B: **By type**

  * Pie or bar chart:

    * categories: symptom types (heartburn, regurgitation, etc.)
    * metric: count or total duration.
* Subsection C (optional): **Intensity distribution**

  * Histogram or stacked bar:

    * intensity buckets (0–3, 4–6, 7–10)
    * counts.

**Data contract:**

`GET /api/dashboard/symptoms?from=...&to=...&symptom_type=optional`

```json
{
  "from": "2025-12-01",
  "to": "2025-12-08",
  "daily": [
    { "date": "2025-12-01", "count": 3, "avg_intensity": 5.7 },
    { "date": "2025-12-02", "count": 1, "avg_intensity": 4.0 }
  ],
  "by_type": [
    { "type": "heartburn", "count": 7, "avg_intensity": 6.1 },
    { "type": "regurgitation", "count": 3, "avg_intensity": 4.7 }
  ],
  "intensity_histogram": [
    { "bucket": "0-3", "count": 2 },
    { "bucket": "4-6", "count": 5 },
    { "bucket": "7-10", "count": 3 }
  ]
}
```

---

### 3.4 Correlations / possible triggers section

**Goal:** Show simple, understandable associations between meals/features and symptoms (exploratory, not medical truth).

**Underlying logic (backend):**

* Define a **symptom window** after each meal, e.g. 0–4 hours.
* For each feature:

  * product category
  * portion size
  * fat level
  * posture after meal
* Compute:

  * `support`: number of meals with this feature in the selected date range.
  * `symptom_window_rate`: percentage of those meals followed by ≥1 symptom in window.
  * `baseline_rate`: percentage of all meals followed by symptoms in window.
  * `lift` or `delta = symptom_window_rate - baseline_rate`.

**UI:**

* Section title: “Possible triggers (exploratory)”
* Optional info note: “These are statistical associations only and not a diagnosis.”
* Table:

  * Columns:

    * `Feature` (e.g. “Category: Coffee”, “Fat: High”, “Posture: Laying”)
    * `Meals with feature (n)`
    * `Symptom after meal (%)`
    * `Baseline (%)`
    * `Delta (pp)` (or `Lift`)

Optionally sort by `Delta` descending and hide rows with low support (`n < 4`).

**Data contract:**

`GET /api/dashboard/correlations?from=...&to=...`

```json
{
  "from": "2025-12-01",
  "to": "2025-12-08",
  "baseline_rate_pct": 35.0,
  "symptom_window_hours": 4,
  "features": [
    {
      "feature_key": "category:coffee",
      "label": "Category: Coffee",
      "support_meals": 12,
      "symptom_rate_pct": 66.7,
      "delta_pct_points": 31.7
    },
    {
      "feature_key": "fat:high",
      "label": "Fat: High",
      "support_meals": 9,
      "symptom_rate_pct": 77.8,
      "delta_pct_points": 42.8
    }
  ]
}
```

---

## 4. Technical requirements

### 4.1 Service boundary

* Dashboard can be:

  * a separate web app that talks to the same database as the bot backend, or
  * a separate frontend (SPA) calling the existing bot backend’s new `/api/dashboard/*` endpoints.
* Expected stack (flexible, but pick one and document it):

  * Backend: existing stack (e.g. FastAPI, Django, Node, etc.)
  * Frontend: simple React/Vue/Svelte or server-rendered pages.

### 4.2 Endpoints summary

Backend must provide at least:

* `POST /api/dashboard/create-code`

  * Input: `telegram_user_id`
  * Output: `code`, `expires_at`
* `POST /auth/code-login`

  * Input: `code`
  * Output: success/failure + session token/cookie.
* `GET /api/dashboard/product-categories`
* `GET /api/dashboard/symptoms`
* `GET /api/dashboard/correlations`
* (optional) `GET /api/dashboard/overview` for quick summary metrics.

All `/api/dashboard/*` require an authenticated session.

---

## 5. Acceptance criteria

* User can:

  * Request dashboard code in Telegram.
  * Open dashboard URL in browser, enter code, and successfully see their own data.
* No cross-user leaks: each code maps to exactly one user.
* Dashboard displays for selected date range:

  * **Product categories**: bar chart + table with counts and shares.
  * **Symptom stats**: timeline + by-type distribution (and optionally intensity histogram).
  * **Correlations**: table of top features with support and symptom rates vs baseline.
* All filters (date range, symptom type) correctly update all sections.
* All requests are served over HTTPS and require authenticated session after login.
