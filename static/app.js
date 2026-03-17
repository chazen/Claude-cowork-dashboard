/* =========================================================
   Claude Cowork – Job Dashboard  |  app.js
   ========================================================= */

const REFRESH_INTERVAL_MS = 30_000;

let dashboardData = null;
let searchQuery = "";
let refreshTimer = null;

// ── Helpers ────────────────────────────────────────────────

function statusIcon(status) {
  return { success: "✓", warning: "⚠", failed: "✕", running: "↻" }[status] ?? "·";
}

function statusLabel(status) {
  return {
    success: "Succeeded",
    warning: "Succeeded with warnings",
    failed:  "Failed",
    running: "Running",
  }[status] ?? status;
}

function fmt(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr + "Z"); // treat stored times as UTC
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fromNow(isoStr) {
  if (!isoStr) return "";
  const ms = new Date(isoStr + "Z") - Date.now();
  const abs = Math.abs(ms);
  const mins  = Math.floor(abs / 60_000);
  const hours = Math.floor(abs / 3_600_000);
  const days  = Math.floor(abs / 86_400_000);

  const label = ms < 0 ? "ago" : "from now";
  if (days  > 0) return `${days}d ${label}`;
  if (hours > 0) return `${hours}h ${label}`;
  if (mins  > 0) return `${mins}m ${label}`;
  return "just now";
}

function elapsedSince(isoStr) {
  if (!isoStr) return "";
  const secs = Math.floor((Date.now() - new Date(isoStr + "Z")) / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ── Fetch ──────────────────────────────────────────────────

async function fetchDashboard() {
  const res = await fetch("/api/dashboard");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Render stats ───────────────────────────────────────────

function renderStats(stats, serverTime) {
  document.getElementById("stat-total").textContent   = stats.total_jobs;
  document.getElementById("stat-running").textContent = stats.running;
  document.getElementById("stat-success").textContent = stats.success_today;
  document.getElementById("stat-failed").textContent  = stats.failed_today;

  const d = new Date(serverTime + "Z");
  document.getElementById("last-updated").textContent =
    "Updated " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Render live lane ───────────────────────────────────────

function renderLiveLane(jobs) {
  const lane = document.getElementById("live-lane");
  const count = document.getElementById("live-count");

  const running = [];
  for (const job of jobs) {
    for (const run of job.recent_runs) {
      if (run.status === "running") running.push({ job, run });
    }
  }

  count.textContent = running.length;

  if (running.length === 0) {
    lane.innerHTML = '<p class="empty-state">No jobs running right now.</p>';
    return;
  }

  lane.innerHTML = running.map(({ job, run }) => `
    <div class="lane-card is-running">
      <div class="lane-card-title">${esc(job.name)}</div>
      <div class="lane-card-meta">Started ${fmt(run.started_at)}</div>
      <div class="elapsed" data-started="${run.started_at}">
        ↻ ${elapsedSince(run.started_at)}
      </div>
    </div>
  `).join("");
}

// ── Render upcoming lane ───────────────────────────────────

function renderUpcomingLane(jobs) {
  const lane = document.getElementById("upcoming-lane");

  const upcoming = jobs
    .filter(j => j.enabled && j.next_run_at)
    .sort((a, b) => new Date(a.next_run_at) - new Date(b.next_run_at))
    .slice(0, 6);

  if (upcoming.length === 0) {
    lane.innerHTML = '<p class="empty-state">No upcoming scheduled runs found.</p>';
    return;
  }

  lane.innerHTML = upcoming.map(job => `
    <div class="lane-card">
      <div class="lane-card-title">${esc(job.name)}</div>
      <div class="lane-card-meta">
        <span class="next-run-time">${fmt(job.next_run_at)}</span>
        &nbsp;·&nbsp; ${fromNow(job.next_run_at)}
      </div>
      <div class="lane-card-meta" style="margin-top:4px; font-family:monospace;">
        ${esc(job.schedule)}
      </div>
    </div>
  `).join("");
}

// ── Render jobs grid ───────────────────────────────────────

function renderJobsGrid(jobs) {
  const grid = document.getElementById("jobs-grid");

  const filtered = searchQuery
    ? jobs.filter(j => j.name.toLowerCase().includes(searchQuery) || j.description.toLowerCase().includes(searchQuery))
    : jobs;

  if (filtered.length === 0) {
    grid.innerHTML = '<p class="empty-state">No jobs match your filter.</p>';
    return;
  }

  grid.innerHTML = filtered.map(job => {
    const pips = buildRunPips(job.recent_runs);
    const lastRun = job.recent_runs.find(r => r.status !== "running");
    const lastRunStr = lastRun ? fmt(lastRun.started_at) : "Never";
    const nextStr = job.next_run_at ? `${fmt(job.next_run_at)} (${fromNow(job.next_run_at)})` : "—";

    return `
      <div class="job-card" data-job-id="${job.id}">
        <div class="job-card-header">
          <div class="job-card-name">${esc(job.name)}</div>
          <div class="job-card-schedule">${esc(job.schedule)}</div>
        </div>
        <div class="job-card-desc">${esc(job.description)}</div>
        <div class="run-history">
          ${pips}
          <span class="run-history-label">last ${job.recent_runs.filter(r=>r.status!=="running").length}</span>
        </div>
        <div class="job-card-footer">
          <span>Last: ${lastRunStr}</span>
          <span>Next: <span class="next-run-time">${nextStr}</span></span>
          <span class="tag ${job.enabled ? "enabled" : "disabled"}">${job.enabled ? "enabled" : "disabled"}</span>
        </div>
      </div>
    `;
  }).join("");

  // Run pip click handlers → open modal
  grid.querySelectorAll(".run-pip[data-run]").forEach(pip => {
    pip.addEventListener("click", e => {
      e.stopPropagation();
      const runData = JSON.parse(pip.dataset.run);
      const jobId   = parseInt(pip.closest(".job-card").dataset.jobId);
      const job     = jobs.find(j => j.id === jobId);
      openRunModal(job, runData);
    });
  });
}

function buildRunPips(runs) {
  // Show up to 4 completed runs (skip running for pips)
  const completed = runs.filter(r => r.status !== "running");
  const pips = completed.slice(0, 4).map(run => {
    const safeRun = JSON.stringify(run).replace(/"/g, "&quot;");
    return `<div class="run-pip ${run.status}"
                 data-run="${safeRun}"
                 title="${statusLabel(run.status)} — ${fmt(run.started_at)}">
               ${statusIcon(run.status)}
             </div>`;
  });

  // Pad to 4
  while (pips.length < 4) {
    pips.push(`<div class="run-pip empty" title="No run data">·</div>`);
  }

  return pips.join("");
}

// ── Modal ──────────────────────────────────────────────────

function openRunModal(job, run) {
  document.getElementById("modal-title").textContent = job ? job.name : "Run Detail";

  const durationStr = run.duration_ms != null
    ? `${(run.duration_ms / 1000).toFixed(2)}s`
    : "—";

  let logHtml = "";
  if (run.output) {
    logHtml += `<div class="log-block">${esc(run.output)}</div>`;
  }
  if (run.error) {
    logHtml += `<div class="log-block error">${esc(run.error)}</div>`;
  }
  if (!run.output && !run.error) {
    logHtml = `<p class="muted" style="margin-top:12px">No output recorded.</p>`;
  }

  document.getElementById("modal-body").innerHTML = `
    <div class="detail-row">
      <span class="detail-label">Status</span>
      <span class="detail-value">
        <span class="dot ${run.status}"></span>&nbsp;${statusLabel(run.status)}
      </span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Started</span>
      <span class="detail-value">${fmt(run.started_at)}</span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Completed</span>
      <span class="detail-value">${fmt(run.completed_at)}</span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Duration</span>
      <span class="detail-value">${durationStr}</span>
    </div>
    ${logHtml}
  `;

  document.getElementById("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}

// ── Elapsed ticker ─────────────────────────────────────────

function tickElapsed() {
  document.querySelectorAll(".elapsed[data-started]").forEach(el => {
    el.textContent = "↻ " + elapsedSince(el.dataset.started);
  });
}

// ── Main render ────────────────────────────────────────────

function renderAll(data) {
  dashboardData = data;
  renderStats(data.stats, data.server_time);
  renderLiveLane(data.jobs);
  renderUpcomingLane(data.jobs);
  renderJobsGrid(data.jobs);
}

async function refresh() {
  try {
    const data = await fetchDashboard();
    renderAll(data);
  } catch (err) {
    console.error("Failed to fetch dashboard:", err);
    document.getElementById("last-updated").textContent = "Update failed – retrying…";
  }
}

// ── XSS helper ────────────────────────────────────────────

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Init ───────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Initial load
  refresh();

  // Auto-refresh
  refreshTimer = setInterval(refresh, REFRESH_INTERVAL_MS);

  // Elapsed ticker every second
  setInterval(tickElapsed, 1000);

  // Manual refresh
  document.getElementById("refresh-btn").addEventListener("click", refresh);

  // Search
  document.getElementById("search").addEventListener("input", e => {
    searchQuery = e.target.value.toLowerCase().trim();
    if (dashboardData) renderJobsGrid(dashboardData.jobs);
  });

  // Modal close
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });
});
