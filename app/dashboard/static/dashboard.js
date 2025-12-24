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
let medsChart = null;

function destroyChart(ch) {
  try {
    if (ch) ch.destroy();
  } catch (_) {}
}

async function refresh(from, to) {
  setStatus("Loading…");
  const catLevelEl = document.getElementById("catLevel");
  const catLevel = catLevelEl ? catLevelEl.value : "lowest";
  const symBucketEl = document.getElementById("symBucket");
  const symBucket = symBucketEl ? symBucketEl.value : "24";
  const qp = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&category_level=${encodeURIComponent(
    catLevel
  )}`;

  const [cats, syms, meds, cors, tl] = await Promise.all([
    fetchJson(`/api/dashboard/product-categories?${qp}`),
    fetchJson(`/api/dashboard/symptoms?${qp}&bucket_hours=${encodeURIComponent(symBucket)}`),
    fetchJson(`/api/dashboard/medications?${qp}`),
    fetchJson(`/api/dashboard/correlations?${qp}`),
    fetchJson(`/api/dashboard/timeline?${qp}`),
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
  const catBody = ensureTable(catTable, ["Category", "Parents", "Level", "Meals", "Share %", "Symptom in 4h %"]);
  for (const c of cats.categories || []) {
    const tr = document.createElement("tr");
    const parents = (c.parents || []).map((p) => p.name).join(" > ");
    tr.innerHTML = `<td>${c.name}</td><td>${parents}</td><td>${c.level ?? ""}</td><td>${
      c.meal_count
    }</td><td>${c.share_pct.toFixed(
      1
    )}</td><td>${(c.symptom_window_rate_pct ?? 0).toFixed(1)}</td>`;
    catBody.appendChild(tr);
  }

  // Symptoms daily
  const dailyLabels = (syms.daily || []).map((d) => d.date);
  const dailyCounts = (syms.daily || []).map((d) => d.count);
  const dailyAvgIntensity = (syms.daily || []).map((d) => d.avg_intensity ?? 0);
  destroyChart(symDailyChart);
  symDailyChart = new Chart(document.getElementById("symDailyChart"), {
    type: "line",
    data: {
      labels: dailyLabels,
      datasets: [
        {
          label: "Symptoms per bucket",
          data: dailyCounts,
          yAxisID: "yCount",
        },
        {
          label: "Avg intensity",
          data: dailyAvgIntensity,
          yAxisID: "yIntensity",
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        yCount: { type: "linear", position: "left", beginAtZero: true },
        yIntensity: { type: "linear", position: "right", beginAtZero: true, min: 0, max: 10, grid: { drawOnChartArea: false } },
      },
    },
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

  // Medications
  const medLabels = (meds.by_name || []).slice(0, 12).map((m) => m.name);
  const medCounts = (meds.by_name || []).slice(0, 12).map((m) => m.count);
  destroyChart(medsChart);
  medsChart = new Chart(document.getElementById("medsChart"), {
    type: "bar",
    data: { labels: medLabels, datasets: [{ label: "Taken (count)", data: medCounts }] },
    options: { responsive: true },
  });
  const medsTable = document.getElementById("medsTable");
  const medsBody = ensureTable(medsTable, ["Medication", "Count", "Share %", "Last taken"]);
  for (const m of meds.by_name || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${m.name}</td><td>${m.count}</td><td>${(m.share_pct ?? 0).toFixed(
      1
    )}</td><td>${m.last_taken_at ?? ""}</td>`;
    medsBody.appendChild(tr);
  }

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

  // Timeline
  const tlTable = document.getElementById("timelineTable");
  const tlBody = ensureTable(tlTable, ["When", "Kind", "Details"]);
  for (const e of tl.events || []) {
    const tr = document.createElement("tr");
    let details = "";
    if (e.kind === "meal") {
      const bits = [];
      if (e.notes) bits.push(e.notes);
      const meta = [];
      if (e.portion) meta.push(`portion=${e.portion}`);
      if (e.fat) meta.push(`fat=${e.fat}`);
      if (e.posture) meta.push(`posture=${e.posture}`);
      if (meta.length) bits.push(meta.join(", "));
      details = bits.join(" · ");
    } else if (e.kind === "symptom") {
      details = `${e.type ?? "Symptom"} (intensity ${e.intensity ?? 0})`;
      if (e.duration_minutes != null) details += `, ${e.duration_minutes} min`;
    } else if (e.kind === "medication") {
      details = `${e.name ?? "Medication"}` + (e.dosage ? ` (${e.dosage})` : "");
    } else {
      details = "";
    }
    // Format the date and time for display
    function formatDateTime(dtStr) {
      if (!dtStr) return "";
      const d = new Date(dtStr);
      if (isNaN(d)) return dtStr; // fallback if invalid
      // YYYY-MM-DD HH:MM (local)
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      const h = String(d.getHours()).padStart(2, "0");
      const min = String(d.getMinutes()).padStart(2, "0");
      return `${y}-${m}-${day} ${h}:${min}`;
    }
    tr.innerHTML = `<td>${formatDateTime(e.at)}</td><td>${e.kind ?? ""}</td><td>${details}</td>`;
    tlBody.appendChild(tr);
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
  const catLevelEl = document.getElementById("catLevel");
  const symBucketEl = document.getElementById("symBucket");

  const init = lastNDays(7);
  fromEl.value = init.from;
  toEl.value = init.to;

  const run = () => refresh(fromEl.value, toEl.value).catch((e) => setStatus(`Error: ${e.message}`));
  applyEl.addEventListener("click", (e) => {
    e.preventDefault();
    run();
  });
  if (catLevelEl) {
    catLevelEl.addEventListener("change", () => run());
  }
  if (symBucketEl) {
    symBucketEl.addEventListener("change", () => run());
  }
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


