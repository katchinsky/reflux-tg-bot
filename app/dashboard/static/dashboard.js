/* global Chart */

function fmtDate(d) {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text || "";
}

async function fetchJson(path) {
  const resp = await fetch(path, { credentials: "include" });
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText} ${t}`.trim());
  }
  return await resp.json();
}

function ensureTable(el, headers) {
  el.innerHTML = "";
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  }
  thead.appendChild(tr);
  el.appendChild(thead);
  const tbody = document.createElement("tbody");
  el.appendChild(tbody);
  return tbody;
}

let catChart = null;
let symDailyChart = null;
let symTypeChart = null;
let symHistChart = null;

function destroyChart(ch) {
  try {
    if (ch) ch.destroy();
  } catch (_) {}
}

async function refresh(from, to) {
  setStatus("Loading…");
  const qp = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;

  const [cats, syms, cors] = await Promise.all([
    fetchJson(`/api/dashboard/product-categories?${qp}`),
    fetchJson(`/api/dashboard/symptoms?${qp}`),
    fetchJson(`/api/dashboard/correlations?${qp}`),
  ]);

  // Categories
  const catLabels = (cats.categories || []).map((c) => c.name);
  const catCounts = (cats.categories || []).map((c) => c.meal_count);
  destroyChart(catChart);
  catChart = new Chart(document.getElementById("catChart"), {
    type: "bar",
    data: { labels: catLabels, datasets: [{ label: "Meals", data: catCounts }] },
    options: { responsive: true, indexAxis: "y" },
  });

  const catTable = document.getElementById("catTable");
  const catBody = ensureTable(catTable, ["Category", "Meals", "Share %", "Symptom in 4h %"]);
  for (const c of cats.categories || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${c.name}</td><td>${c.meal_count}</td><td>${c.share_pct.toFixed(
      1
    )}</td><td>${(c.symptom_window_rate_pct ?? 0).toFixed(1)}</td>`;
    catBody.appendChild(tr);
  }

  // Symptoms daily
  const dailyLabels = (syms.daily || []).map((d) => d.date);
  const dailyCounts = (syms.daily || []).map((d) => d.count);
  destroyChart(symDailyChart);
  symDailyChart = new Chart(document.getElementById("symDailyChart"), {
    type: "line",
    data: { labels: dailyLabels, datasets: [{ label: "Symptoms per day", data: dailyCounts }] },
    options: { responsive: true },
  });

  // Symptoms by type
  const typeLabels = (syms.by_type || []).map((x) => x.type);
  const typeCounts = (syms.by_type || []).map((x) => x.count);
  destroyChart(symTypeChart);
  symTypeChart = new Chart(document.getElementById("symTypeChart"), {
    type: "bar",
    data: { labels: typeLabels, datasets: [{ label: "Count", data: typeCounts }] },
    options: { responsive: true },
  });

  // Intensity histogram
  const histLabels = (syms.intensity_histogram || []).map((x) => x.bucket);
  const histCounts = (syms.intensity_histogram || []).map((x) => x.count);
  destroyChart(symHistChart);
  symHistChart = new Chart(document.getElementById("symHistChart"), {
    type: "bar",
    data: { labels: histLabels, datasets: [{ label: "Count", data: histCounts }] },
    options: { responsive: true },
  });

  // Correlations
  const corrTable = document.getElementById("corrTable");
  const corrBody = ensureTable(corrTable, ["Feature", "Meals (n)", "Symptom after meal %", "Baseline %", "Delta (pp)"]);
  for (const f of cors.features || []) {
    const tr = document.createElement("tr");
    const baseline = cors.baseline_rate_pct ?? 0;
    tr.innerHTML = `<td>${f.label}</td><td>${f.support_meals}</td><td>${f.symptom_rate_pct.toFixed(
      1
    )}</td><td>${baseline.toFixed(1)}</td><td>${f.delta_pct_points.toFixed(1)}</td>`;
    corrBody.appendChild(tr);
  }

  setStatus(`Loaded ${from} → ${to}`);
}

function lastNDays(n) {
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (n - 1));
  return { from: fmtDate(from), to: fmtDate(to) };
}

window.addEventListener("DOMContentLoaded", () => {
  const fromEl = document.getElementById("from");
  const toEl = document.getElementById("to");
  const applyEl = document.getElementById("apply");
  const last7El = document.getElementById("last7");
  const last30El = document.getElementById("last30");

  const init = lastNDays(7);
  fromEl.value = init.from;
  toEl.value = init.to;

  const run = () => refresh(fromEl.value, toEl.value).catch((e) => setStatus(`Error: ${e.message}`));
  applyEl.addEventListener("click", (e) => {
    e.preventDefault();
    run();
  });
  last7El.addEventListener("click", (e) => {
    e.preventDefault();
    const r = lastNDays(7);
    fromEl.value = r.from;
    toEl.value = r.to;
    run();
  });
  last30El.addEventListener("click", (e) => {
    e.preventDefault();
    const r = lastNDays(30);
    fromEl.value = r.from;
    toEl.value = r.to;
    run();
  });

  run();
});


