const state = {
  jobs: [],
  selectedId: null,
  refreshTimer: null,
  backoffMs: 60000,
  typing: false,
  statusLabels: new Map(
    Array.from(document.querySelector("#statusFilter").options).map((option) => [
      option.value,
      option.textContent,
    ]),
  ),
};

const els = {
  healthText: document.querySelector("#healthText"),
  healthCard: document.querySelector("#healthCard"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  refreshBtn: document.querySelector("#refreshBtn"),
  updatedAt: document.querySelector("#updatedAt"),
  jobCount: document.querySelector("#jobCount"),
  jobsList: document.querySelector("#jobsList"),
  jobDetail: document.querySelector("#jobDetail"),
  message: document.querySelector("#message"),
  jobForm: document.querySelector("#jobForm"),
  createResult: document.querySelector("#createResult"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    cache: "no-store",
    ...options,
  });
  const body = await response.json().catch(() => ({
    ok: false,
    error: { message: "Runtime returned a malformed response." },
  }));
  if (!response.ok || !body.ok) {
    const message = body?.error?.message || `Request failed (${response.status})`;
    const code = body?.error?.code || "request_failed";
    throw new Error(`${code}: ${message}`);
  }
  return body.data;
}

function showMessage(text, type = "info") {
  els.message.textContent = text;
  els.message.className = `message ${type}`;
}

function clearMessage() {
  els.message.className = "message hidden";
  els.message.textContent = "";
}

function chip(value) {
  const label = escapeHtml(value || "unset");
  const cls = escapeHtml(String(value || "").toLowerCase());
  return `<span class="chip ${cls}">${label}</span>`;
}

function filteredJobs() {
  const query = els.searchInput.value.trim().toLowerCase();
  const status = els.statusFilter.value;
  return state.jobs.filter((job) => {
    if (status && job.status !== status) return false;
    if (!query) return true;
    const haystack = [
      job.ref,
      job.title,
      job.service,
      job.owner,
      job.status,
      job.next_action,
      job.campaign_id,
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function labelForStatus(status) {
  return String(status || "")
    .split("_")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function updateStatusFilterOptions() {
  const selected = els.statusFilter.value;
  const statuses = new Set([""]);
  state.statusLabels.forEach((_, value) => statuses.add(value));
  state.jobs.forEach((job) => {
    if (job.status) statuses.add(job.status);
  });
  els.statusFilter.innerHTML = Array.from(statuses).map((status) => {
    const label = state.statusLabels.get(status) || labelForStatus(status) || "All";
    return `<option value="${escapeHtml(status)}">${escapeHtml(label)}</option>`;
  }).join("");
  if (statuses.has(selected)) {
    els.statusFilter.value = selected;
  }
}

function renderJobs() {
  updateStatusFilterOptions();
  const jobs = filteredJobs();
  els.jobCount.textContent = jobs.length;
  if (!jobs.length) {
    els.jobsList.innerHTML = `<div class="detail-empty">No matching jobs. Try clearing search or changing the status filter.</div>`;
    return;
  }
  els.jobsList.innerHTML = jobs.map((job) => `
    <button class="job-row ${Number(job.id) === Number(state.selectedId) ? "selected" : ""}" data-job-id="${escapeHtml(job.id)}" type="button">
      <div class="job-topline">
        <span class="ref">${escapeHtml(job.ref || `job.${job.id}`)}</span>
        ${chip(job.status)}
        ${chip(job.priority)}
        ${chip(job.risk_level)}
      </div>
      <div class="job-title">${escapeHtml(job.title)}</div>
      <div class="job-meta">
        <span>${escapeHtml(job.service || "service unset")}</span>
        <span>${escapeHtml(job.owner || "owner unset")}</span>
      </div>
      <div class="job-next">${escapeHtml(job.next_action || "No next action recorded.")}</div>
    </button>
  `).join("");
}

function renderDetail(data) {
  const job = data.job || data;
  const events = data.events || [];
  const audits = data.audits || [];
  els.jobDetail.className = "";
  els.jobDetail.innerHTML = `
    <div class="job-topline">
      <span class="ref">${escapeHtml(job.ref || `job.${job.id}`)}</span>
      ${chip(job.status)}
      ${chip(job.priority)}
      ${chip(job.risk_level)}
    </div>
    <h2>${escapeHtml(job.title)}</h2>
    <div class="detail-meta">
      <span>${escapeHtml(job.service || "service unset")}</span>
      <span>${escapeHtml(job.owner || "owner unset")}</span>
      <span>${escapeHtml(job.campaign_id || "no campaign")}</span>
    </div>
    <div class="detail-block">
      <h3>Next action</h3>
      <p>${escapeHtml(job.next_action || "No next action recorded.")}</p>
    </div>
    <div class="detail-block">
      <h3>Payload</h3>
      <pre>${escapeHtml(JSON.stringify(job.payload || {}, null, 2))}</pre>
    </div>
    <div class="detail-block">
      <h3>Recent events</h3>
      ${events.length ? events.slice(0, 6).map((event) => `
        <p><strong>${escapeHtml(event.event_type)}</strong> ${escapeHtml(event.title || "")}<br>
        <span class="job-next">${escapeHtml(event.details || "")}</span></p>
      `).join("") : "<p>No events returned.</p>"}
    </div>
    <div class="detail-block">
      <h3>Audits</h3>
      ${audits.length ? audits.slice(0, 4).map((audit) => `
        <p><strong>${escapeHtml(audit.result)}</strong> by ${escapeHtml(audit.auditor)}<br>
        <span class="job-next">${escapeHtml(audit.evidence || audit.required_fix || "")}</span></p>
      `).join("") : "<p>No audits returned.</p>"}
    </div>
  `;
}

async function loadHealth() {
  const data = await requestJson("/api/health");
  els.healthText.textContent = `${data.status || "ok"} ${data.schema_version ? `schema ${data.schema_version}` : ""}`.trim();
  els.healthCard.querySelector(".status-dot").className = "status-dot";
}

async function loadJobs() {
  const data = await requestJson("/api/jobs?limit=80");
  state.jobs = data.jobs || [];
  renderJobs();
  if (state.selectedId && state.jobs.some((job) => Number(job.id) === Number(state.selectedId))) {
    await selectJob(state.selectedId, { keepMessage: true });
  }
  els.updatedAt.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

async function refreshAll() {
  if (state.typing) return;
  clearMessage();
  try {
    await loadHealth();
    await loadJobs();
    state.backoffMs = 60000;
  } catch (error) {
    els.healthText.textContent = "Needs attention";
    els.healthCard.querySelector(".status-dot").className = "status-dot bad";
    showMessage(error.message, "error");
    state.backoffMs = Math.min(state.backoffMs * 2, 300000);
  } finally {
    scheduleRefresh();
  }
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refreshAll, state.backoffMs);
}

async function selectJob(id, options = {}) {
  state.selectedId = Number(id);
  renderJobs();
  try {
    const data = await requestJson(`/api/jobs/${encodeURIComponent(id)}`);
    renderDetail(data);
    if (!options.keepMessage) clearMessage();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

els.jobsList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-job-id]");
  if (row) selectJob(row.dataset.jobId);
});

els.searchInput.addEventListener("input", renderJobs);
els.statusFilter.addEventListener("change", renderJobs);
els.refreshBtn.addEventListener("click", refreshAll);

els.jobForm.addEventListener("focusin", () => { state.typing = true; });
els.jobForm.addEventListener("focusout", () => {
  setTimeout(() => {
    state.typing = Boolean(els.jobForm.matches(":focus-within"));
    if (!state.typing) scheduleRefresh();
  }, 100);
});

els.jobForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = els.jobForm.querySelector("button[type='submit']");
  submit.disabled = true;
  els.createResult.className = "create-result";
  els.createResult.textContent = "Creating queued job...";
  const form = Object.fromEntries(new FormData(els.jobForm).entries());
  try {
    const data = await requestJson("/api/jobs", {
      method: "POST",
      body: JSON.stringify(form),
    });
    const job = data.job || data;
    els.createResult.className = "create-result success";
    els.createResult.textContent = `Created ${job.ref || `job.${job.id}`}`;
    els.jobForm.reset();
    state.typing = false;
    await refreshAll();
    if (job.id) await selectJob(job.id);
  } catch (error) {
    els.createResult.className = "create-result error";
    els.createResult.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

refreshAll();
