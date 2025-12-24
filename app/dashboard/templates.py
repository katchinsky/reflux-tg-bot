from __future__ import annotations

from html import escape


def _page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{escape(title)}</title>
    <style>
      :root {{ color-scheme: light dark; }}
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; }}
      header {{ padding: 14px 18px; border-bottom: 1px solid rgba(127,127,127,0.3); }}
      main {{ padding: 18px; max-width: 980px; margin: 0 auto; }}
      .card {{ border: 1px solid rgba(127,127,127,0.35); border-radius: 10px; padding: 14px; margin: 14px 0; }}
      .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
      input[type="text"], input[type="date"] {{ padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(127,127,127,0.5); min-width: 160px; }}
      select {{ padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(127,127,127,0.5); background: transparent; }}
      button {{ padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(127,127,127,0.5); background: transparent; cursor: pointer; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ padding: 8px 6px; border-bottom: 1px solid rgba(127,127,127,0.25); text-align: left; }}
      .muted {{ opacity: 0.75; }}
      .err {{ color: #b00020; }}
      canvas {{ max-width: 100%; }}
    </style>
  </head>
  <body>
    {body_html}
  </body>
</html>"""


def render_login(*, error: str | None = None, configured: bool = True) -> str:
    msg = ""
    if not configured:
        msg = '<p class="err">Dashboard is not configured. Set DASHBOARD_SESSION_SECRET.</p>'
    elif error:
        msg = f'<p class="err">{escape(error)}</p>'
    body = f"""
    <header><strong>Reflux dashboard</strong></header>
    <main>
      <div class="card">
        <h2>Enter your code</h2>
        <p class="muted">Get a short login code from Telegram via <code>/dashboard</code>.</p>
        {msg}
        <form method="POST" action="/auth/code-login">
          <div class="row">
            <input name="code" type="text" inputmode="latin" autocomplete="one-time-code" placeholder="X7F9KQ" maxlength="10" />
            <button type="submit">Log in</button>
          </div>
        </form>
      </div>
    </main>
    """
    return _page("Dashboard login", body)


def render_dashboard() -> str:
    body = """
    <header>
      <div class="row" style="justify-content: space-between;">
        <strong>Reflux dashboard</strong>
        <form method="POST" action="/auth/logout"><button type="submit">Logout</button></form>
      </div>
    </header>
    <main>
      <div class="card">
        <div class="row">
          <button id="last7">Last 7 days</button>
          <button id="last30">Last 30 days</button>
          <span class="muted">Custom:</span>
          <input id="from" type="date" />
          <input id="to" type="date" />
          <span class="muted">Category level:</span>
          <select id="catLevel">
            <option value="lowest">Lowest</option>
            <option value="0">0</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
            <option value="5">5</option>
          </select>
          <button id="apply">Apply</button>
        </div>
        <p id="status" class="muted"></p>
      </div>

      <div class="card">
        <h3>Most common product categories</h3>
        <canvas id="catChart" height="220"></canvas>
        <table id="catTable"></table>
      </div>

      <div class="card">
        <h3>Symptom statistics</h3>
        <div class="row">
          <span class="muted">Timeline buckets:</span>
          <select id="symBucket">
            <option value="24">Daily</option>
            <option value="3">3 hours</option>
          </select>
        </div>
        <canvas id="symDailyChart" height="120"></canvas>
        <div class="row">
          <div style="flex:1; min-width: 280px;">
            <h4>By type</h4>
            <canvas id="symTypeChart" height="120"></canvas>
          </div>
          <div style="flex:1; min-width: 280px;">
            <h4>Intensity distribution</h4>
            <canvas id="symHistChart" height="120"></canvas>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Medications</h3>
        <table id="medsTable"></table>
      </div>

      <div class="card">
        <h3>Possible triggers (exploratory)</h3>
        <p class="muted">These are statistical associations only and not a diagnosis.</p>
        <table id="corrTable"></table>
      </div>

      <div class="card">
        <h3>Timeline (meals & symptoms)</h3>
        <p class="muted">A combined chronological list in your timezone.</p>
        <table id="timelineTable"></table>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
      <script src="/static/dashboard.js"></script>
    </main>
    """
    return _page("Reflux dashboard", body)


