/* =========================================================
   Claude Cowork – Job Dashboard  |  app.js
   ========================================================= */

const REFRESH_INTERVAL_MS = 30_000;

let dashboardData = null;
let searchQuery   = "";
let refreshTimer  = null;

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
    unknown: "Unknown",
  }[status] ?? status;
}

function fmt(isoStr) {
  if (!isoStr) return "—";
  // Stored as naive UTC; append Z so Date parses correctly
  const s = isoStr.endsWith("Z") || isoStr.includes("+") ? isoStr : isoStr + "Z";
  const d = new Date(s);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fromNow(isoStr) {
  if (!isoStr) return "";
  const s = isoStr.endsWith("Z") || isoStr.includes("+") ? isoStr : isoStr + "Z";
  const ms  = new Date(s) - Date.now();
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
  const s = isoStr.endsWith("Z") || isoStr.includes("+") ? isoStr : isoStr + "Z";
  const secs = Math.floor((Date.now() - new Date(s)) / 1000);
  const m = Math.floor(secs / 60);
  const sec = secs % 60;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Cowork badge / notice ──────────────────────────────────

function renderCoworkStatus(available) {
  const badge  = document.getElementById("cowork-badge");
  const notice = document.getElementById("cowork-notice");

  badge.classList.remove("hidden", "connected", "disconnected");
  if (available) {
    badge.textContent = "⬡ Cowork connected";
    badge.classList.add("connected");
    notice.classList.add("hidden");
  } else {
    badge.textContent = "⬡ Cowork not detected";
    badge.classList.add("disconnected");
    notice.classList.remove("hidden");
  }
}

// ── Sync ───────────────────────────────────────────────────

async function triggerSync() {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "⟳ Syncing…";
  try {
    const res  = await fetch("/api/sync", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      btn.textContent = `⟳ Synced (${data.added + data.updated} jobs)`;
      await refresh();
    } else {
      btn.textContent = "⟳ Sync failed";
      renderCoworkStatus(false);
    }
  } catch (err) {
    btn.textContent = "⟳ Sync error";
    console.error("Sync error:", err);
  } finally {
    setTimeout(() => { btn.textContent = "⟳ Sync"; btn.disabled = false; }, 3000);
  }
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

  const d = new Date(serverTime.endsWith("Z") ? serverTime : serverTime + "Z");
  document.getElementById("last-updated").textContent =
    "Updated " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Render live lane ───────────────────────────────────────

function renderLiveLane(jobs) {
  const lane  = document.getElementById("live-lane");
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
      <div class="elapsed" data-started="${esc(run.started_at)}">
        ↻ ${elapsedSince(run.started_at)}
      </div>
    </div>
  `).join("");
}

// ── Render upcoming lane ───────────────────────────────────

function renderUpcomingLane(jobs) {
  const lane = document.getElementById("upcoming-lane");

  // Cowork jobs don't have next_run_at stored (Cowork manages the schedule internally),
  // so show all Cowork jobs sorted by most-recent last run (least recently run = up next)
  const coworkJobs = jobs.filter(j => j.source === "cowork");
  const manualUpcoming = jobs
    .filter(j => j.source !== "cowork" && j.enabled && j.next_run_at)
    .sort((a, b) => new Date(a.next_run_at) - new Date(b.next_run_at))
    .slice(0, 6);

  if (coworkJobs.length === 0 && manualUpcoming.length === 0) {
    lane.innerHTML = '<p class="empty-state">No upcoming scheduled runs found.</p>';
    return;
  }

  const renderCard = job => {
    const lastRun = job.recent_runs.find(r => r.status !== "running");
    const lastRunStr = lastRun ? fmt(lastRun.started_at) : "Never";
    const isCowork   = job.source === "cowork";
    return `
      <div class="lane-card">
        <div class="lane-card-title">${esc(job.name)}</div>
        <div class="lane-card-meta">
          ${isCowork
            ? `<span class="muted">Last ran: ${lastRunStr}</span>`
            : `<span class="next-run-time">${fmt(job.next_run_at)}</span> · ${fromNow(job.next_run_at)}`
          }
        </div>
        <div class="lane-card-meta" style="margin-top:4px; font-family:monospace; font-size:11px;">
          ${esc(job.schedule)}
        </div>
      </div>
    `;
  };

  const cards = [
    ...coworkJobs.map(renderCard),
    ...manualUpcoming.map(renderCard),
  ];
  lane.innerHTML = cards.join("");
}

// ── Render jobs grid ───────────────────────────────────────

function renderJobsGrid(jobs) {
  const grid = document.getElementById("jobs-grid");

  const filtered = searchQuery
    ? jobs.filter(j =>
        j.name.toLowerCase().includes(searchQuery) ||
        (j.description || "").toLowerCase().includes(searchQuery)
      )
    : jobs;

  if (filtered.length === 0) {
    grid.innerHTML = '<p class="empty-state">No jobs match your filter.</p>';
    return;
  }

  grid.innerHTML = filtered.map(job => {
    const pips       = buildRunPips(job.recent_runs);
    const lastRun    = job.recent_runs.find(r => r.status !== "running");
    const lastRunStr = lastRun ? fmt(lastRun.started_at) : "Never";
    const nextStr    = job.next_run_at
      ? `${fmt(job.next_run_at)} (${fromNow(job.next_run_at)})`
      : job.source === "cowork" ? "<span class='muted'>managed by Cowork</span>" : "—";

    const sourceTag  = job.source === "cowork"
      ? `<span class="tag cowork">cowork</span>`
      : `<span class="tag sample">sample</span>`;

    return `
      <div class="job-card" data-job-id="${job.id}">
        <div class="job-card-header">
          <div class="job-card-name">${esc(job.name)}</div>
          <div class="job-card-schedule">${esc(job.schedule)}</div>
        </div>
        <div class="job-card-desc">${esc(job.description)}</div>
        <div class="run-history">
          ${pips}
          <span class="run-history-label">last ${job.recent_runs.filter(r => r.status !== "running").length}</span>
        </div>
        <div class="job-card-footer">
          <span>Last: ${lastRunStr}</span>
          <span>Next: ${nextStr}</span>
          ${sourceTag}
        </div>
      </div>
    `;
  }).join("");

  // Run pip click → detail modal
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
  const completed = runs.filter(r => r.status !== "running");
  const pips = completed.slice(0, 4).map(run => {
    const safeRun = JSON.stringify(run).replace(/"/g, "&quot;");
    return `<div class="run-pip ${run.status}"
                 data-run="${safeRun}"
                 title="${statusLabel(run.status)} — ${fmt(run.started_at)}">
               ${statusIcon(run.status)}
             </div>`;
  });
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
  if (run.output) logHtml += `<div class="log-block">${esc(run.output)}</div>`;
  if (run.error)  logHtml += `<div class="log-block error">${esc(run.error)}</div>`;
  if (!run.output && !run.error) {
    logHtml = `<p class="muted" style="margin-top:12px">No output recorded.</p>`;
  }

  let sessionLink = "";
  if (run.cowork_session_id) {
    sessionLink = `
      <div class="detail-row">
        <span class="detail-label">Session ID</span>
        <span class="detail-value" style="font-family:monospace;font-size:12px">${esc(run.cowork_session_id)}</span>
      </div>`;
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
    ${sessionLink}
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
  renderCoworkStatus(data.cowork_available);
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

// ── Init ───────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  refresh();

  refreshTimer = setInterval(refresh, REFRESH_INTERVAL_MS);
  setInterval(tickElapsed, 1000);

  document.getElementById("refresh-btn").addEventListener("click", refresh);
  document.getElementById("sync-btn").addEventListener("click", triggerSync);

  document.getElementById("search").addEventListener("input", e => {
    searchQuery = e.target.value.toLowerCase().trim();
    if (dashboardData) renderJobsGrid(dashboardData.jobs);
  });

  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });
});
