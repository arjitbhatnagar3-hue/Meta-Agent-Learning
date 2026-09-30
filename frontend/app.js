(() => {
  "use strict";

  const API = "/api/v1";
  const SAVED_PROMPTS_KEY = "meta-agentx.saved-prompts.v1";
  const ACTIVE_STATUSES = new Set(["planning", "executing", "aggregating"]);
  const TERMINAL_STATUSES = new Set(["complete", "failed", "cancelled"]);
  const STATUS_LABELS = {
    queued: "Queued",
    planning: "Planning",
    executing: "In progress",
    aggregating: "Finalizing",
    complete: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  const EVENT_TITLES = {
    queued: "Added to the queue",
    claimed: "Worker picked up the task",
    planning: "Plan created",
    executing: "Specialist work",
    aggregating: "Final report prepared",
    complete: "Workflow completed",
    failed: "Workflow failed",
    cancelled: "Task cancelled",
    retried: "Requeued for a fresh attempt",
    lease_recovered: "Worker lease recovered",
    stage: "Progress update",
  };
  const AGENT_TONES = ["violet", "blue", "green", "peach", "cyan", "rose"];
  const AGENT_MARKS = {
    "Manager Agent": "M",
    "Document Agent": "D",
    "Financial Agent": "$",
    "CSV/Data Agent": "CSV",
    "Invoice Agent": "I",
    "Anomaly Agent": "!",
    "Research Agent": "R",
    "Reviewer Agent": "✓",
    "Synthesis Agent": "S",
  };
  const ICONS = {
    queued: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="6.5"/><path d="M10 6.5V10l2.4 1.6"/></svg>',
    active: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3.3v3.1M10 13.6v3.1M3.3 10h3.1M13.6 10h3.1"/><circle cx="10" cy="10" r="4.7"/></svg>',
    complete: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4.5 10.2 3.4 3.3 7.6-7.2"/></svg>',
    failed: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8"/><circle cx="10" cy="10" r="6.5"/></svg>',
    cancelled: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6.3 6.3 13.7 13.7M13.7 6.3l-7.4 7.4"/><circle cx="10" cy="10" r="6.5"/></svg>',
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    tasks: [],
    agents: [],
    templates: [],
    integrations: [],
    savedPrompts: [],
    stats: null,
    health: null,
    businessContext: null,
    businessContextLoaded: false,
    businessContextDirty: false,
    businessContextUploading: false,
    currentView: "overview",
    statusFilter: "all",
    query: "",
    selectedTaskId: null,
    selectedTask: null,
    selectedEvents: [],
    selectedEventsSignature: "",
    selectedDetailSignature: "",
    selectedPriority: "medium",
    selectedTemplateId: null,
    socket: null,
    socketReconnectTimer: null,
    selectedTaskPoller: null,
    taskPoller: null,
    statsPoller: null,
    healthPoller: null,
    toastTimerIds: new Set(),
    previousFocus: null,
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function trimText(value, length = 100) {
    const text = String(value ?? "").trim();
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  }

  function formatDate(value, options = {}) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      ...(options.withTime ? { hour: "numeric", minute: "2-digit" } : {}),
    }).format(date);
  }

  function relativeTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 45) return "Just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}d ago`;
    return formatDate(value);
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || "Unknown";
  }

  function statusIcon(status) {
    if (status === "complete") return ICONS.complete;
    if (status === "failed") return ICONS.failed;
    if (status === "cancelled") return ICONS.cancelled;
    if (ACTIVE_STATUSES.has(status)) return ICONS.active;
    return ICONS.queued;
  }

  async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type") && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    let response;
    try {
      response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    } catch (error) {
      throw new Error("Could not reach the Meta AgentX API. Check that the server is running.");
    }

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json().catch(() => ({}))
      : await response.text().catch(() => "");
    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail : null;
      throw new Error(detail || `Request failed (${response.status}).`);
    }
    return payload;
  }

  function currentTasks() {
    const query = state.query.trim().toLocaleLowerCase();
    return state.tasks.filter((task) => {
      const matchesQuery = !query || `${task.goal} ${task.id} ${task.priority} ${task.status}`.toLocaleLowerCase().includes(query);
      let matchesStatus = true;
      if (state.statusFilter === "active") {
        matchesStatus = task.status === "queued" || ACTIVE_STATUSES.has(task.status);
      } else if (state.statusFilter === "complete") {
        matchesStatus = task.status === "complete";
      } else if (state.statusFilter === "failed") {
        matchesStatus = task.status === "failed" || task.status === "cancelled";
      }
      return matchesQuery && matchesStatus;
    });
  }

  function taskRow(task) {
    const id = escapeHtml(task.id);
    const goal = escapeHtml(task.goal);
    const status = escapeHtml(task.status);
    const priority = escapeHtml(task.priority || "medium");
    const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
    const plannedCount = task.plan?.subtasks?.length;
    const taskDate = formatDate(task.created_at);
    const agentLabel = plannedCount ? `${plannedCount} specialists` : "Agent workflow";
    const progressMarkup = task.status === "queued" || ACTIVE_STATUSES.has(task.status)
      ? `<span class="run-progress" aria-label="${progress}% complete"><span style="width:${progress}%"></span></span>`
      : "";
    return `
      <button class="task-row" type="button" data-task-id="${id}" aria-label="Open task: ${goal}">
        <span class="run-main">
          <span class="run-status-icon status-${status}">${statusIcon(task.status)}</span>
          <span class="run-copy">
            <span class="run-goal">${goal}</span>
            <span class="run-meta"><span class="run-id">${escapeHtml(task.id.slice(0, 8))}</span><span class="run-meta-dot"></span><span>${escapeHtml(agentLabel)}</span><span class="run-meta-dot"></span><span>${escapeHtml(taskDate)}</span></span>
            ${progressMarkup}
          </span>
        </span>
        <span><span class="priority-badge priority-${priority}">${priority}</span></span>
        <span><span class="status-badge status-${status}">${escapeHtml(statusLabel(task.status))}</span></span>
        <span class="run-updated">${escapeHtml(relativeTime(task.updated_at))}</span>
      </button>`;
  }

  function emptyState(title, description, action = true) {
    return `<div class="empty-state">
      <span class="empty-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5h10v15H7z"/><path d="M10 8h4M10 12h4M10 16h2"/><path d="m4 7 .5.5L5.5 6"/></svg></span>
      <strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p>
      ${action ? '<button class="button button-primary" type="button" data-open-task><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>Create your first task</button>' : ""}
    </div>`;
  }

  function renderTasks() {
    const filtered = currentTasks();
    const recentTarget = $("#recentTasks");
    const allTarget = $("#allTasks");
    const runCount = filtered.length;
    const taskCount = state.tasks.length;

    $("#navTaskCount").textContent = String(taskCount);
    $("#filterAllCount").textContent = String(taskCount);
    $("#runResultCount").textContent = `${runCount} ${runCount === 1 ? "run" : "runs"}`;
    $("#listFootnote").textContent = taskCount >= 100
      ? "Showing the latest 100 runs. Use search to narrow the list."
      : `Showing ${taskCount} ${taskCount === 1 ? "run" : "runs"}.`;

    if (recentTarget) {
      if (!state.tasks.length) {
        recentTarget.innerHTML = emptyState(
          "Your first great question starts here.",
          "Give your agent team a goal and get a clear, evidence-backed answer.",
        );
      } else {
        recentTarget.innerHTML = filtered.slice(0, 5).map(taskRow).join("")
          || '<div class="loading-state">No recent runs match this search.</div>';
      }
    }

    if (allTarget) {
      if (!state.tasks.length) {
        allTarget.innerHTML = emptyState(
          "No runs yet — let’s change that.",
          "Create a task to see planning, agent progress, and the final report all in one place.",
        );
      } else if (!filtered.length) {
        allTarget.innerHTML = emptyState(
          "Nothing matches those filters.",
          "Try a different status or clear your search to see more runs.",
          false,
        );
      } else {
        allTarget.innerHTML = filtered.map(taskRow).join("");
      }
    }
  }

  function renderStats(stats) {
    state.stats = stats;
    $("#metricTotal").textContent = Number(stats.total || 0).toLocaleString();
    $("#metricActive").textContent = (Number(stats.queued || 0) + Number(stats.active || 0)).toLocaleString();
    $("#metricCompleted").textContent = Number(stats.completed || 0).toLocaleString();
    $("#metricSuccess").textContent = Number(stats.completed || 0) + Number(stats.failed || 0) === 0
      ? "—"
      : Number(stats.success_rate || 0).toFixed(stats.success_rate % 1 ? 1 : 0);
  }

  async function loadTasks({ quiet = true } = {}) {
    try {
      const tasks = await apiRequest(`${API}/tasks?limit=100`);
      state.tasks = Array.isArray(tasks) ? tasks : [];
      renderTasks();
      if (state.selectedTaskId && state.socket?.readyState !== WebSocket.OPEN) {
        const updated = state.tasks.find((task) => task.id === state.selectedTaskId);
        if (updated) updateSelectedTask(updated);
      }
    } catch (error) {
      if (!quiet) showToast("Could not load runs", error.message, "error");
      const recent = $("#recentTasks");
      const all = $("#allTasks");
      if (recent && !state.tasks.length) recent.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
      if (all && !state.tasks.length) all.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadStats() {
    try {
      const stats = await apiRequest(`${API}/stats`);
      renderStats(stats);
    } catch (error) {
      console.warn("Could not load workspace stats:", error.message);
    }
  }

  function renderStorageStatus(health) {
    const databaseKind = health.database_kind || "local_sqlite";
    const supabaseDatabase = databaseKind === "supabase_postgres";
    $("#databaseStorageName").textContent = supabaseDatabase ? "Supabase Postgres" : "Local SQLite database";
    $("#databaseStorageDescription").textContent = supabaseDatabase
      ? "Task history, saved analyses, profiles, and task-time snapshots use the configured cloud database."
      : "Task history and analyses are stored on the API host; this disk may not survive some redeploys.";
    $("#databaseStorageBadge").textContent = supabaseDatabase ? "CLOUD" : "LOCAL";
    $("#databaseStorageBadge").className = `integration-status ${supabaseDatabase ? "is-connected" : "is-local"}`;

    const supabaseFiles = String(health.file_storage || "").startsWith("supabase_storage:");
    $("#fileStorageName").textContent = supabaseFiles ? "Private Supabase Storage" : "Local database file storage";
    $("#fileStorageDescription").textContent = supabaseFiles
      ? `Original uploads are configured for the private bucket (${String(health.file_storage).split(":").slice(1).join(":")}); an upload verifies access.`
      : "Original files are stored with the local database; extracted text is also kept for task context.";
    $("#fileStorageBadge").textContent = supabaseFiles ? "CONFIGURED" : "LOCAL";
    $("#fileStorageBadge").className = `integration-status ${supabaseFiles ? "is-configured" : "is-local"}`;

    const uploadStatus = $("#businessUploadStatus");
    if (uploadStatus && !state.businessContextUploading) {
      uploadStatus.textContent = supabaseFiles
        ? "New originals will use your configured private Supabase bucket."
        : "Files are stored on the app server's local database disk.";
    }
    if (health.storage_configuration_warning) {
      $("#fileStorageDescription").textContent = health.storage_configuration_warning;
      $("#fileStorageBadge").textContent = "CHECK SETUP";
      $("#fileStorageBadge").className = "integration-status is-warning";
    }
  }

  async function loadHealth() {
    const pill = $("#connectionStatus");
    try {
      const health = await apiRequest("/health");
      state.health = health;
      pill.classList.remove("offline");
      $(".connection-text", pill).textContent = "API online";
      $("#sidebarMode").textContent = health.llm_mode === "fake" ? "Fake model · demo output" : "Hugging Face · configured";
      $(".sidebar-meta:not(.worker-meta)").classList.remove("offline");
      const workerCount = Number(health.worker?.online_workers || 0);
      const workerStatus = $("#workerStatus");
      workerStatus.classList.toggle("offline", workerCount === 0);
      $("#workerMode").textContent = workerCount
        ? `Worker online · ${workerCount}`
        : "Worker offline · queue will wait";
      workerStatus.title = workerCount
        ? `${workerCount} worker process${workerCount === 1 ? "" : "es"} reporting healthy heartbeats.`
        : "Start python -m backend.worker to process queued tasks.";
      pill.title = `${health.llm_provider} · ${health.model}`;
      document.body.dataset.llmMode = health.llm_mode;
      renderStorageStatus(health);
    } catch (error) {
      pill.classList.add("offline");
      $(".connection-text", pill).textContent = "API offline";
      $("#sidebarMode").textContent = "API connection needed";
      $(".sidebar-meta:not(.worker-meta)").classList.add("offline");
      $("#workerStatus").classList.add("offline");
      $("#workerMode").textContent = "Worker status unavailable";
      pill.title = error.message;
    }
  }

  function agentTone(agent, index) {
    const category = String(agent.category || "").toLocaleLowerCase();
    if (category.includes("revenue")) return "violet";
    if (category.includes("finance")) return "blue";
    if (category.includes("customer")) return "green";
    if (category.includes("product")) return "peach";
    if (category.includes("synthesis")) return "cyan";
    if (category.includes("decision")) return "rose";
    return AGENT_TONES[index % AGENT_TONES.length];
  }

  function agentMark(agent) {
    return AGENT_MARKS[agent.name] || String(agent.name || "A").slice(0, 1).toUpperCase();
  }

  function renderAgents() {
    const agents = state.agents;
    $("#agentCount").textContent = String(agents.length || 0);
    const miniTarget = $("#miniAgentList");
    const cardTarget = $("#agentGrid");
    if (!agents.length) {
      miniTarget.innerHTML = '<div class="agent-loading">Agent catalog is unavailable.</div>';
      cardTarget.innerHTML = '<div class="loading-state">Could not load the agent catalog.</div>';
      return;
    }

    miniTarget.innerHTML = agents.slice(0, 4).map((agent, index) => `
      <div class="mini-agent-row">
        <span class="mini-agent-avatar agent-tone-${agentTone(agent, index)}">${escapeHtml(agentMark(agent))}</span>
        <span class="mini-agent-copy"><strong>${escapeHtml(agent.name)}</strong><small>${escapeHtml(agent.category)}</small></span>
        <span class="agent-ready" title="Available"></span>
      </div>`).join("");

    cardTarget.innerHTML = agents.map((agent, index) => {
      const tools = Array.isArray(agent.tools) ? agent.tools : [];
      const chips = tools.length
        ? tools.map((tool) => `<span class="tool-chip">${escapeHtml(tool)}</span>`).join("")
        : '<span class="tool-chip no-tools-chip">Supplied documents & task context</span>';
      return `<article class="agent-card">
        <div class="agent-card-top"><span class="agent-card-icon agent-tone-${agentTone(agent, index)}">${escapeHtml(agentMark(agent))}</span><span class="agent-category">${escapeHtml(agent.category)}</span></div>
        <h2>${escapeHtml(agent.name)}</h2><p>${escapeHtml(agent.description)}</p>
        ${agent.responsibility ? `<div class="agent-responsibility"><strong>Owns</strong><span>${escapeHtml(agent.responsibility)}</span></div>` : ""}
        <div class="tool-chips">${chips}</div>
      </article>`;
    }).join("");
  }

  function renderTemplates() {
    const quick = $("#quickTemplates");
    const modal = $("#modalTemplates");
    if (!state.templates.length) {
      quick.innerHTML = "";
      modal.innerHTML = '<span class="field-hint">No playbooks available right now.</span>';
      return;
    }
    quick.innerHTML = state.templates.slice(0, 2).map((template) => `
      <button class="quick-template" type="button" data-template-id="${escapeHtml(template.id)}">${escapeHtml(template.title)}</button>`).join("");
    modal.innerHTML = state.templates.map((template, index) => `
      <button class="modal-template" type="button" data-template-id="${escapeHtml(template.id)}">
        <span class="modal-template-icon" aria-hidden="true">${["◌", "↗", "⌘", "✳"][index % 4]}</span>
        <span class="modal-template-copy"><strong>${escapeHtml(template.title)}</strong><small>${escapeHtml(template.description)}</small></span>
      </button>`).join("");
  }

  function loadSavedPrompts() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(SAVED_PROMPTS_KEY) || "[]");
      state.savedPrompts = Array.isArray(parsed)
        ? parsed.filter((item) => item && typeof item.goal === "string" && item.goal.trim().length >= 10).slice(0, 40)
        : [];
    } catch (error) {
      state.savedPrompts = [];
      console.warn("Saved prompts could not be read from this browser:", error.message);
    }
    renderSavedPromptList();
  }

  function persistSavedPrompts() {
    try {
      window.localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(state.savedPrompts.slice(0, 40)));
      return true;
    } catch (error) {
      showToast("Could not save prompt", "Browser storage may be full or disabled.", "error");
      return false;
    }
  }

  function renderSavedPromptList() {
    const select = $("#savedPromptList");
    if (!select) return;
    const selected = select.value;
    select.innerHTML = state.savedPrompts.length
      ? state.savedPrompts.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`).join("")
      : '<option value="">No saved prompts yet</option>';
    if (state.savedPrompts.some((item) => item.id === selected)) select.value = selected;
    const hasSelection = Boolean(select.value);
    $("#loadPromptButton").disabled = !hasSelection;
    $("#deletePromptButton").disabled = !hasSelection;
  }

  function saveCurrentPrompt() {
    const goal = $("#taskGoal").value.trim();
    if (goal.length < 10) {
      $("#savedPromptStatus").textContent = "Write a prompt of at least 10 characters before saving it.";
      $("#savedPromptStatus").classList.add("is-error");
      return;
    }
    const titleInput = $("#savedPromptTitle");
    const title = titleInput.value.trim() || trimText(goal.split(/\n|[.!?]/)[0], 58) || "Saved analysis prompt";
    const matching = state.savedPrompts.find((item) => item.goal === goal);
    const prompt = {
      id: matching?.id || (window.crypto?.randomUUID ? window.crypto.randomUUID() : `prompt-${Date.now()}`),
      title,
      goal,
      priority: state.selectedPriority,
      saved_at: new Date().toISOString(),
    };
    state.savedPrompts = [prompt, ...state.savedPrompts.filter((item) => item.id !== prompt.id)].slice(0, 40);
    if (!persistSavedPrompts()) return;
    loadSavedPrompts();
    $("#savedPromptList").value = prompt.id;
    titleInput.value = title;
    $("#savedPromptStatus").textContent = "Saved in this browser only. It won’t follow you to another device or browser.";
    $("#savedPromptStatus").classList.remove("is-error");
    showToast("Prompt saved", "You can reuse it from this browser’s prompt list.");
  }

  function applySavedPrompt() {
    const prompt = state.savedPrompts.find((item) => item.id === $("#savedPromptList").value);
    if (!prompt) return;
    $("#taskGoal").value = prompt.goal;
    state.selectedPriority = ["low", "medium", "high", "critical"].includes(prompt.priority) ? prompt.priority : "medium";
    state.selectedTemplateId = null;
    $("#savedPromptTitle").value = prompt.title || "";
    $$(".modal-template").forEach((button) => button.classList.remove("selected"));
    updatePrioritySelection();
    updateGoalCount();
    $("#savedPromptStatus").textContent = `Loaded “${prompt.title}”. Edit it freely before creating a task.`;
    $("#savedPromptStatus").classList.remove("is-error");
  }

  function deleteSavedPrompt() {
    const id = $("#savedPromptList").value;
    if (!id) return;
    const selected = state.savedPrompts.find((item) => item.id === id);
    state.savedPrompts = state.savedPrompts.filter((item) => item.id !== id);
    if (!persistSavedPrompts()) return;
    loadSavedPrompts();
    $("#savedPromptTitle").value = "";
    $("#savedPromptStatus").textContent = selected ? `Removed “${selected.title}” from this browser.` : "Prompt removed.";
  }

  async function loadAgents() {
    try {
      const agents = await apiRequest(`${API}/agents`);
      state.agents = Array.isArray(agents) ? agents : [];
      renderAgents();
    } catch (error) {
      console.warn("Could not load agent catalog:", error.message);
      renderAgents();
    }
  }

  const INTEGRATION_STATUS_LABELS = {
    ready: "Ready",
    configured: "Configured",
    not_configured: "Not connected",
    coming_soon: "Coming soon",
  };

  function renderIntegrations() {
    const target = $("#integrationGrid");
    if (!target) return;
    const integrations = state.integrations || [];
    $("#integrationCount").textContent = `${integrations.length} options`;
    target.innerHTML = integrations.map((item) => {
      const status = item.status || "not_configured";
      const envVars = (item.environment_variables || []).map((name) => `<code>${escapeHtml(name)}</code>`).join("");
      const action = status === "configured"
        ? `<button class="button button-secondary integration-test-button" type="button" data-integration-test="${escapeHtml(item.id)}">Test read-only connection</button>`
        : status === "not_configured"
          ? `<span class="integration-setup-hint">Set on both API and worker:</span><div class="integration-env-list">${envVars}</div>`
          : status === "coming_soon"
            ? '<span class="integration-setup-hint">This provider needs a future adapter; it is not connected by selecting the card.</span>'
            : '<span class="integration-setup-hint">Upload this source from Business context.</span>';
      return `<article class="panel integration-card ${status === "coming_soon" ? "is-coming-soon" : ""}">
        <div class="integration-card-top"><div><span class="integration-category">${escapeHtml(item.category)}</span><h3>${escapeHtml(item.name)}</h3></div><span class="integration-status ${status === "configured" ? "is-configured" : status === "ready" ? "is-connected" : status === "coming_soon" ? "is-upcoming" : "is-local"}">${escapeHtml(INTEGRATION_STATUS_LABELS[status] || status)}</span></div>
        <p>${escapeHtml(item.description)}</p>
        <div class="integration-method">${escapeHtml(item.connection_method)}</div>
        <div class="integration-card-footer">${action}<span class="read-only-label">Read-only</span></div>
      </article>`;
    }).join("") || '<div class="detail-empty-plan">No connection options are available.</div>';
  }

  async function loadIntegrations() {
    try {
      const items = await apiRequest(`${API}/integrations`);
      state.integrations = Array.isArray(items) ? items : [];
      renderIntegrations();
    } catch (error) {
      const target = $("#integrationGrid");
      if (target) target.innerHTML = `<div class="integration-load-error">${escapeHtml(error.message)}</div>`;
    }
  }

  async function testIntegration(integrationId, button) {
    if (button) {
      button.disabled = true;
      button.textContent = "Testing…";
    }
    try {
      const result = await apiRequest(`${API}/integrations/${encodeURIComponent(integrationId)}/test`, { method: "POST" });
      showToast("Connection works", result.message || "Read-only test succeeded.");
    } catch (error) {
      showToast("Connection test failed", error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Test read-only connection";
      }
    }
  }

  async function loadTemplates() {
    try {
      const templates = await apiRequest(`${API}/templates`);
      state.templates = Array.isArray(templates) ? templates : [];
      renderTemplates();
    } catch (error) {
      console.warn("Could not load task templates:", error.message);
      renderTemplates();
    }
  }

  function formatFileSize(bytes) {
    const size = Math.max(0, Number(bytes) || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function setBusinessContextSaveState(message, tone = "") {
    const target = $("#businessContextSaveState");
    target.textContent = message;
    target.classList.remove("is-dirty", "is-saved");
    if (tone) target.classList.add(tone);
  }

  function updateBusinessContextCount() {
    const field = $("#businessProfileText");
    const count = $("#businessContextCount");
    if (!field || !count) return;
    count.textContent = `${field.value.length.toLocaleString()} / 20,000`;
  }

  function renderBusinessDocuments(documents) {
    const target = $("#businessDocumentList");
    if (!target) return;
    if (!documents?.length) {
      target.innerHTML = '<div class="context-empty-documents">No business documents yet.</div>';
      return;
    }
    target.innerHTML = documents.map((document) => {
      const id = escapeHtml(document.id);
      const filename = escapeHtml(document.filename);
      const type = escapeHtml(String(document.extension || "file").replace(/^\./, ""));
      const truncated = document.was_truncated ? " · Text shortened to fit" : "";
      return `<article class="business-document-row">
        <span class="context-file-kind">${type}</span>
        <span class="context-document-main"><strong title="${filename}">${filename}</strong><small>${formatFileSize(document.size_bytes)} · ${Number(document.extracted_characters || 0).toLocaleString()} characters${truncated}</small></span>
        <a class="context-document-download" href="${API}/business-context/documents/${encodeURIComponent(document.id)}/download" download="${filename}" aria-label="Download ${filename}" title="Download">↓</a>
        <button class="context-document-remove" type="button" data-delete-business-document="${id}" aria-label="Remove ${filename}" title="Remove">×</button>
      </article>`;
    }).join("");
  }

  function renderBusinessContext(data, { preserveProfile = false } = {}) {
    state.businessContext = data;
    state.businessContextLoaded = true;
    if (!preserveProfile && !state.businessContextDirty) {
      $("#businessName").value = data.business_name || "";
      $("#businessProfileText").value = data.profile_text || "";
      updateBusinessContextCount();
      setBusinessContextSaveState(data.updated_at ? "Profile saved" : "Not saved yet", data.updated_at ? "is-saved" : "");
    }
    renderBusinessDocuments(data.documents || []);
  }

  async function loadBusinessContext({ preserveProfile = false } = {}) {
    try {
      const data = await apiRequest(`${API}/business-context`);
      renderBusinessContext(data, { preserveProfile });
      $("#businessContextError").hidden = true;
      return data;
    } catch (error) {
      const errorTarget = $("#businessContextError");
      errorTarget.textContent = `Could not load business context: ${error.message}`;
      errorTarget.hidden = false;
      return null;
    }
  }

  async function saveBusinessContext(event) {
    event.preventDefault();
    const button = $("#saveBusinessContextButton");
    const profile = {
      business_name: $("#businessName").value.trim(),
      profile_text: $("#businessProfileText").value.trim(),
    };
    button.disabled = true;
    button.innerHTML = '<span class="spinner spinner-small"></span><span>Saving…</span>';
    $("#businessContextError").hidden = true;
    setBusinessContextSaveState("Saving profile…");
    try {
      const saved = await apiRequest(`${API}/business-context`, {
        method: "PUT",
        body: JSON.stringify(profile),
      });
      state.businessContextDirty = false;
      renderBusinessContext(saved);
      setBusinessContextSaveState("Saved just now", "is-saved");
      showToast("Business profile saved", "New tasks will use this profile and your uploaded references.");
    } catch (error) {
      $("#businessContextError").textContent = error.message;
      $("#businessContextError").hidden = false;
      setBusinessContextSaveState("Could not save · try again");
    } finally {
      button.disabled = false;
      button.innerHTML = '<span>Save profile</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10.5 4 4 8-9"/></svg>';
    }
  }

  async function uploadBusinessDocuments(event) {
    const input = event.currentTarget;
    const files = [...(input.files || [])];
    input.value = "";
    if (!files.length || state.businessContextUploading) return;

    state.businessContextUploading = true;
    input.disabled = true;
    const status = $("#businessUploadStatus");
    status.classList.remove("is-error");
    const failures = [];
    let uploaded = 0;
    for (const [index, file] of files.entries()) {
      status.textContent = `Adding ${file.name} (${index + 1} of ${files.length})…`;
      if (file.size > 5 * 1024 * 1024) {
        failures.push(`${file.name}: files must be 5 MB or smaller.`);
        continue;
      }
      const formData = new FormData();
      formData.append("file", file, file.name);
      try {
        await apiRequest(`${API}/business-context/documents`, {
          method: "POST",
          body: formData,
        });
        uploaded += 1;
      } catch (error) {
        failures.push(`${file.name}: ${error.message}`);
      }
    }
    state.businessContextUploading = false;
    input.disabled = false;
    await loadBusinessContext({ preserveProfile: true });
    if (failures.length) {
      status.textContent = failures.join(" ");
      status.classList.add("is-error");
      showToast(uploaded ? "Some files were added" : "Upload could not be completed", failures[0], "error");
    } else {
      status.textContent = `${uploaded} ${uploaded === 1 ? "file" : "files"} added. New tasks will include their extracted text.`;
      showToast("Reference files added", `${uploaded} ${uploaded === 1 ? "document is" : "documents are"} ready for future tasks.`);
    }
  }

  async function removeBusinessDocument(documentId) {
    const document = state.businessContext?.documents?.find((item) => item.id === documentId);
    const filename = document?.filename || "this document";
    if (!window.confirm(`Remove ${filename} from future task context?`)) return;
    try {
      await apiRequest(`${API}/business-context/documents/${encodeURIComponent(documentId)}`, {
        method: "DELETE",
      });
      await loadBusinessContext({ preserveProfile: true });
      showToast("Reference removed", "New tasks will no longer include this file.");
    } catch (error) {
      showToast("Could not remove file", error.message, "error");
    }
  }

  function showView(view) {
    const names = { overview: "Overview", tasks: "Task runs", agents: "Agent library", businessContext: "Business context", connections: "Data connections" };
    if (!names[view]) return;
    state.currentView = view;
    $$(".view").forEach((section) => { section.hidden = section.id !== `${view}View`; });
    $$(".nav-link[data-view]").forEach((link) => link.classList.toggle("active", link.dataset.view === view));
    $("#breadcrumbCurrent").textContent = names[view];
    $("#sidebar").classList.remove("mobile-open");
    $("#mobileScrim").hidden = true;
    if (view === "tasks") $("#globalSearch").focus({ preventScroll: true });
    if (view === "businessContext" && !state.businessContextLoaded) loadBusinessContext();
    if (view === "connections") {
      loadIntegrations();
      loadHealth();
    }
  }

  function openTaskModal(template = null) {
    if (!$("#detailBackdrop").hidden) closeTaskDetail();
    state.previousFocus = document.activeElement;
    state.selectedTemplateId = null;
    state.selectedPriority = "medium";
    $("#taskGoal").value = "";
    $("#savedPromptTitle").value = "";
    $("#useConversationMemory").checked = true;
    $("#formError").hidden = true;
    updateGoalCount();
    updatePrioritySelection();
    renderTemplates();
    renderSavedPromptList();
    $("#taskModal").hidden = false;
    document.body.classList.add("modal-open");
    if (template) applyTemplate(template);
    window.setTimeout(() => $("#taskGoal").focus(), 30);
  }

  function closeTaskModal() {
    if ($("#taskModal").hidden) return;
    $("#taskModal").hidden = true;
    if (!$("#detailBackdrop").hidden) return;
    document.body.classList.remove("modal-open");
    if (state.previousFocus instanceof HTMLElement) state.previousFocus.focus({ preventScroll: true });
  }

  function updateGoalCount() {
    const length = $("#taskGoal").value.length;
    const count = $("#goalCount");
    count.textContent = `${length.toLocaleString()} / 20,000`;
    count.classList.toggle("needs-more", length > 0 && length < 10);
    $("#submitTaskButton").disabled = $("#taskGoal").value.trim().length < 10;
  }

  function updatePrioritySelection() {
    $$(".priority-option").forEach((button) => {
      const selected = button.dataset.priority === state.selectedPriority;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-checked", String(selected));
    });
  }

  function applyTemplate(template) {
    $("#taskGoal").value = template.goal;
    state.selectedTemplateId = template.id;
    state.selectedPriority = template.priority || "medium";
    $$(".modal-template").forEach((button) => button.classList.toggle("selected", button.dataset.templateId === template.id));
    updatePrioritySelection();
    updateGoalCount();
  }

  function chooseTemplate(id) {
    const template = state.templates.find((item) => item.id === id);
    if (!template) return;
    if ($("#taskModal").hidden) openTaskModal(template);
    else applyTemplate(template);
  }

  async function submitTask(event) {
    event.preventDefault();
    const goal = $("#taskGoal").value.trim();
    if (goal.length < 10) {
      $("#formError").textContent = "Please add a little more detail (at least 10 characters).";
      $("#formError").hidden = false;
      return;
    }

    const button = $("#submitTaskButton");
    button.disabled = true;
    button.innerHTML = '<span class="spinner spinner-small"></span><span>Creating task…</span>';
    $("#formError").hidden = true;
    try {
      const accepted = await apiRequest(`${API}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          goal,
          priority: state.selectedPriority,
          use_memory: $("#useConversationMemory").checked,
        }),
      });
      closeTaskModal();
      $("#taskForm").reset();
      state.selectedTemplateId = null;
      showToast("Task added to the queue", "Your agent team is getting ready to work.");
      await Promise.all([loadTasks(), loadStats()]);
      if (accepted?.task_id) await openTask(accepted.task_id);
    } catch (error) {
      $("#formError").textContent = error.message;
      $("#formError").hidden = false;
    } finally {
      button.disabled = false;
      button.innerHTML = '<span>Create task</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M10 5l5 5-5 5" /></svg>';
      updateGoalCount();
    }
  }

  function closeSocket() {
    if (state.socketReconnectTimer) {
      window.clearTimeout(state.socketReconnectTimer);
      state.socketReconnectTimer = null;
    }
    if (state.socket) {
      const oldSocket = state.socket;
      state.socket = null;
      oldSocket.onclose = null;
      oldSocket.close();
    }
    if (state.selectedTaskPoller) {
      window.clearInterval(state.selectedTaskPoller);
      state.selectedTaskPoller = null;
    }
  }

  function openDetailShell() {
    $("#detailBackdrop").hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeTaskDetail() {
    if ($("#detailBackdrop").hidden) return;
    closeSocket();
    state.selectedTaskId = null;
    state.selectedTask = null;
    state.selectedEvents = [];
    state.selectedEventsSignature = "";
    state.selectedDetailSignature = "";
    $("#detailBackdrop").hidden = true;
    if ($("#taskModal").hidden) document.body.classList.remove("modal-open");
  }

  function planWaves(task) {
    const subtasks = task.plan?.subtasks;
    if (!Array.isArray(subtasks) || !subtasks.length) return [];
    const resultWaves = task.result?.execution_waves;
    if (Array.isArray(resultWaves) && resultWaves.length) return resultWaves;

    const remaining = new Map(subtasks.map((item) => [item.id, new Set(item.dependencies || [])]));
    const completed = new Set();
    const waves = [];
    while (remaining.size) {
      const ready = [...remaining.entries()]
        .filter(([, dependencies]) => [...dependencies].every((dependency) => completed.has(dependency)))
        .map(([id]) => id).sort();
      if (!ready.length) return [subtasks.map((item) => item.id)];
      waves.push(ready);
      ready.forEach((id) => { completed.add(id); remaining.delete(id); });
    }
    return waves;
  }

  function renderDag(task) {
    const subtasks = task.plan?.subtasks;
    if (!Array.isArray(subtasks) || !subtasks.length) return "";
    const byId = new Map(subtasks.map((item) => [item.id, item]));
    const results = task.result?.agent_results || {};
    const waves = planWaves(task);
    const stageMatch = String(task.current_stage || "").match(/wave\s+(\d+)/i);
    const currentWave = stageMatch ? Number(stageMatch[1]) - 1 : -1;
    const columns = waves.map((wave, waveIndex) => `
      <section class="dag-lane" aria-label="DAG wave ${waveIndex + 1}">
        <div class="dag-lane-heading"><span>Wave ${waveIndex + 1}</span><small>${wave.length} ${wave.length === 1 ? "task" : "tasks"}</small></div>
        ${wave.map((id) => {
          const item = byId.get(id);
          if (!item) return "";
          const result = results[id];
          const isActive = !result && ACTIVE_STATUSES.has(task.status) && waveIndex === currentWave;
          const status = result ? (result.success ? "success" : "failure") : isActive ? "active" : "pending";
          const confidence = Number.isFinite(Number(result?.confidence)) ? `${Math.round(Number(result.confidence) * 100)}%` : "";
          const depends = (item.dependencies || []).map((dependency) => byId.get(dependency)?.agent_name || dependency).join(", ");
          return `<article class="dag-node dag-node-${status}" data-dag-node="${escapeHtml(id)}" data-dependencies="${escapeHtml(JSON.stringify(item.dependencies || []))}">
            <div class="dag-node-top"><span class="dag-node-status-dot"></span><span class="dag-node-wave">${escapeHtml(status === "success" ? "Done" : status === "failure" ? "Needs review" : status === "active" ? "Running" : "Queued")}</span>${confidence ? `<span class="dag-node-confidence">${confidence}</span>` : ""}</div>
            <strong>${escapeHtml(item.agent_name)}</strong><p>${escapeHtml(item.goal)}</p>
            ${depends ? `<small>Depends on ${escapeHtml(depends)}</small>` : '<small>Independent investigation</small>'}
          </article>`;
        }).join("")}
      </section>`).join("");
    return `<div class="dag-scroll" data-dag-board><div class="dag-canvas" style="--dag-columns:${waves.length}"><svg class="dag-links" aria-hidden="true"><defs><marker id="dag-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="currentColor"></path></marker></defs></svg><div class="dag-columns">${columns}</div></div></div>`;
  }

  function drawDagEdges() {
    $$('[data-dag-board]').forEach((board) => {
      const canvas = $(".dag-canvas", board);
      const svg = $(".dag-links", board);
      if (!canvas || !svg) return;
      const bounds = canvas.getBoundingClientRect();
      const width = Math.max(canvas.scrollWidth, bounds.width);
      const height = Math.max(canvas.scrollHeight, bounds.height);
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.querySelectorAll("path.dag-edge").forEach((path) => path.remove());
      const nodes = new Map($$('[data-dag-node]', canvas).map((node) => [node.dataset.dagNode, node]));
      nodes.forEach((target, targetId) => {
        let dependencies = [];
        try { dependencies = JSON.parse(target.dataset.dependencies || "[]"); } catch (error) { dependencies = []; }
        dependencies.forEach((sourceId) => {
          const source = nodes.get(sourceId);
          if (!source) return;
          const from = source.getBoundingClientRect();
          const to = target.getBoundingClientRect();
          const startX = from.right - bounds.left;
          const startY = from.top + from.height / 2 - bounds.top;
          const endX = to.left - bounds.left;
          const endY = to.top + to.height / 2 - bounds.top;
          const bend = Math.max(22, (endX - startX) * 0.42);
          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          path.classList.add("dag-edge");
          if (target.classList.contains("dag-node-failure") || source.classList.contains("dag-node-failure")) path.classList.add("dag-edge-failure");
          path.setAttribute("d", `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`);
          path.setAttribute("marker-end", "url(#dag-arrow)");
          svg.append(path);
        });
      });
    });
  }

  function renderEvidence(evidence) {
    if (!Array.isArray(evidence) || !evidence.length) {
      return '<p class="evidence-empty">No source excerpts were attached to this result. Treat its claims as unverified.</p>';
    }
    return `<div class="evidence-list">${evidence.slice(0, 20).map((item) => `
      <article class="evidence-item"><div class="evidence-source"><span>${escapeHtml(item.kind || "source")}</span><strong>${escapeHtml(item.source || "Unknown source")}</strong>${item.location ? `<small>${escapeHtml(item.location)}</small>` : ""}</div><blockquote>${escapeHtml(item.excerpt || "")}</blockquote></article>`).join("")}</div>`;
  }

  function renderAgentWarnings(warnings) {
    if (!Array.isArray(warnings) || !warnings.length) return "";
    return `<div class="agent-warnings"><strong>Source/tool warning</strong><ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul></div>`;
  }

  function renderReview(review) {
    if (!review) return '<div class="review-empty">No structured reviewer checklist was returned.</div>';
    const checks = [
      ["Used supplied documents", review.used_supplied_documents],
      ["Supported by source data", review.supported_by_data],
      ["Calculations consistent", review.calculations_consistent],
    ];
    const checkRows = checks.map(([label, value]) => {
      const stateName = value === true ? "yes" : value === false ? "no" : "unknown";
      const display = value === true ? "Yes" : value === false ? "No" : "Not verified";
      return `<div class="review-check review-${stateName}"><span class="review-check-icon">${value === true ? "✓" : value === false ? "!" : "—"}</span><span>${escapeHtml(label)}</span><strong>${display}</strong></div>`;
    }).join("");
    const risk = String(review.hallucination_risk || "medium").toLowerCase();
    const missing = Array.isArray(review.missing_information) && review.missing_information.length
      ? `<div class="review-missing"><strong>Information still missing</strong><ul>${review.missing_information.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`
      : "";
    return `<div class="review-card"><div class="review-card-top"><strong>Reviewer checklist</strong><span class="review-risk risk-${escapeHtml(risk)}">${escapeHtml(risk)} hallucination risk</span></div><div class="review-checks">${checkRows}</div>${review.notes ? `<p class="review-notes">${escapeHtml(review.notes)}</p>` : ""}${missing}<p class="review-caveat">Automated review can miss errors. Verify high-impact calculations against the original files.</p></div>`;
  }

  function renderAgentMessages(messages) {
    if (!Array.isArray(messages) || !messages.length) return "";
    return `<details class="agent-message-log"><summary>${messages.length} agent messages · Delegations and handoffs</summary><div class="agent-message-list">${messages.map((message) => `
      <article class="agent-message"><div class="agent-message-route"><strong>${escapeHtml(message.from_agent || "Agent")}</strong><span>→</span><strong>${escapeHtml(message.to_agent || "Team")}</strong><small>${escapeHtml(message.message_type || "handoff")}</small></div><p>${escapeHtml(message.content || "")}</p>${Array.isArray(message.evidence) && message.evidence.length ? `<small>${message.evidence.length} evidence reference${message.evidence.length === 1 ? "" : "s"} attached</small>` : ""}</article>`).join("")}</div></details>`;
  }

  function renderTrace(trace) {
    if (!Array.isArray(trace) || !trace.length) return "";
    return `<details class="trace-details"><summary>${trace.length} ${trace.length === 1 ? "tool call" : "tool calls"} · View evidence</summary>
      ${trace.map((item) => `<div class="trace-item"><div class="trace-tool"><strong>${escapeHtml(item.tool || "Tool")}</strong><span>Iteration ${escapeHtml(item.iteration ?? "—")}</span></div><pre>${escapeHtml(JSON.stringify({ arguments: item.arguments, observation: item.observation }, null, 2))}</pre></div>`).join("")}
    </details>`;
  }

  function renderPlan(task) {
    const subtasks = task.plan?.subtasks;
    if (!Array.isArray(subtasks) || !subtasks.length) {
      return '<div class="detail-card detail-empty-plan">The task tree will appear after the Manager Agent creates a plan.</div>';
    }
    const byId = new Map(subtasks.map((item) => [item.id, item]));
    const results = task.result?.agent_results || {};
    const waves = planWaves(task);
    const stageMatch = String(task.current_stage || "").match(/wave\s+(\d+)/i);
    const currentWave = stageMatch ? Number(stageMatch[1]) - 1 : -1;

    const waveMarkup = waves.map((wave, index) => `
      <section class="plan-wave">
        <div class="wave-label"><span>Work wave ${index + 1}</span><small>${wave.length} ${wave.length === 1 ? "agent" : "agents"}${wave.length > 1 ? " · parallel" : ""}</small></div>
        ${wave.map((id) => {
          const item = byId.get(id);
          if (!item) return "";
          const result = results[id];
          const isActive = !result && ACTIVE_STATUSES.has(task.status) && index === currentWave;
          const indicator = result ? (result.success ? "success" : "failure") : isActive ? "active" : "";
          const dependencies = Array.isArray(item.dependencies) && item.dependencies.length
            ? `<p class="plan-task-goal">After: ${item.dependencies.map((dep) => escapeHtml(byId.get(dep)?.agent_name || dep)).join(", ")}</p>`
            : "";
          const confidence = result && Number.isFinite(Number(result.confidence))
            ? `<span class="agent-result-confidence">${Math.round(Number(result.confidence) * 100)}% confidence</span>`
            : "";
          const answer = result
            ? `<div class="agent-result ${result.success ? "" : "agent-result-error"}">${escapeHtml(result.answer || result.error || "No response recorded.")}</div>${confidence}${renderAgentWarnings(result.warnings)}${result.evidence?.length ? renderEvidence(result.evidence) : ""}${result.review ? renderReview(result.review) : ""}${renderTrace(result.trace)}`
            : "";
          return `<article class="plan-task"><div class="plan-task-top"><span class="plan-task-indicator ${indicator}"></span><span class="plan-task-name">${escapeHtml(item.id.replaceAll("_", " "))}</span><span class="plan-task-agent">${escapeHtml(item.agent_name)}</span></div><p class="plan-task-goal">${escapeHtml(item.goal)}</p>${dependencies}${answer}</article>`;
        }).join("")}
      </section>`).join("");
    return `${renderDag(task)}<div class="plan-waves">${waveMarkup}</div>`;
  }

  function renderTaskEvents(events) {
    if (!Array.isArray(events) || !events.length) {
      return '<div class="detail-card detail-empty-plan">Activity will appear here as the task moves through the workflow.</div>';
    }
    return `<div class="detail-card detail-events">${events.map((event) => {
      const title = EVENT_TITLES[event.event_type] || "Workflow update";
      const worker = event.detail?.worker_id
        ? `<span class="event-worker">Worker ${escapeHtml(event.detail.worker_id.slice(0, 16))}</span>`
        : "";
      const percent = event.progress !== null && event.progress !== undefined
        ? `<span class="event-progress">${Number(event.progress)}%</span>`
        : "";
      const status = event.status ? statusLabel(event.status) : "";
      const meta = [status, worker].filter(Boolean).join(" · ");
      return `<article class="timeline-event"><span class="timeline-marker event-${escapeHtml(event.event_type)}"></span><div class="timeline-copy"><div class="timeline-event-title">${escapeHtml(title)}${percent}</div><p>${escapeHtml(event.stage)}</p>${meta ? `<small>${escapeHtml(meta)}</small>` : ""}</div><time>${escapeHtml(formatDate(event.created_at, { withTime: true }))}</time></article>`;
    }).join("")}</div>`;
  }

  function progressStepIndex(task) {
    const status = task.status;
    if (status === "queued") return 0;
    if (status === "planning") return 1;
    if (status === "executing") return 2;
    if (status === "aggregating") return 3;
    if (status === "complete") return 4;
    return Math.max(0, Math.min(3, Math.floor((Number(task.progress) || 0) / 26)));
  }

  function renderProgressSteps(task) {
    const labels = ["Submitted", "Planning", "Agent work", "Final review"];
    const current = progressStepIndex(task);
    const failedOrCancelled = task.status === "failed" || task.status === "cancelled";
    return labels.map((label, index) => {
      const done = task.status === "complete" || index < current;
      const active = !failedOrCancelled && task.status !== "complete" && index === current;
      const mark = done ? "✓" : String(index + 1);
      return `<div class="progress-step ${done ? "done" : ""} ${active ? "current" : ""}"><span class="progress-step-mark">${mark}</span><span>${label}</span></div>`;
    }).join("");
  }

  function detailMarkup(task) {
    const status = escapeHtml(task.status);
    const priority = escapeHtml(task.priority || "medium");
    const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
    const result = task.result || {};
    const finalReport = result.final_report;
    const confidence = Number(result.confidence);
    const hasConfidence = Number.isFinite(confidence);
    const canCancel = task.status === "queued" || ACTIVE_STATUSES.has(task.status);
    const canRetry = task.status === "failed" || task.status === "cancelled";
    const partialIssues = [
      ...(result.failed_agents || []).map((item) => `${item.agent_name || item.task_id || "Agent"}: ${item.error || "assignment failed"}`),
      ...(result.agent_warnings || []).flatMap((item) => (item.warnings || []).map((warning) => `${item.agent_name || "Agent"}: ${warning}`)),
    ];
    const partialWarning = result.partial_failure
      ? `<div class="partial-warning"><strong>Some agent work or provider data was unavailable.</strong><span>${partialIssues.map(escapeHtml).join(" · ") || "See the task tree for details."}</span></div>`
      : "";
    const reportMarkup = finalReport
      ? `<section class="detail-section"><div class="detail-section-heading"><h3>Final report</h3><span>${result.partial_failure ? "Partial · see gaps" : result.success === false ? "Review recommended" : "Reviewer checked"}</span></div>${partialWarning}<div class="detail-card detail-report-card"><div class="report-label">Synthesis Agent</div><p class="detail-report-text">${escapeHtml(finalReport)}</p>${hasConfidence ? `<div class="confidence-meter"><div><span>Overall confidence</span><strong>${Math.round(confidence * 100)}%</strong></div><span class="confidence-track"><i style="width:${Math.max(0, Math.min(100, confidence * 100))}%"></i></span></div>` : ""}<div class="report-actions"><button class="button button-secondary" type="button" data-detail-action="copy"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="6" width="9" height="11" rx="1.5"/><path d="M12.5 6V4.5A1.5 1.5 0 0 0 11 3H5A1.5 1.5 0 0 0 3.5 4.5v8A1.5 1.5 0 0 0 5 14h2"/></svg>Copy</button><button class="button button-secondary" type="button" data-detail-action="download"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3.5v9M6.5 9 10 12.5 13.5 9M4 14.5v2h12v-2"/></svg>Download</button></div></div>${result.review ? `<div class="review-section"><div class="detail-section-heading"><h3>Quality review</h3><span>Reviewer Agent</span></div>${renderReview(result.review)}</div>` : ""}<div class="report-evidence"><div class="detail-section-heading"><h3>Evidence & source excerpts</h3><span>${Array.isArray(result.evidence) ? result.evidence.length : 0} references</span></div>${renderEvidence(result.evidence)}</div></section>`
      : "";
    const messageMarkup = result.agent_messages?.length
      ? `<section class="detail-section"><div class="detail-section-heading"><h3>Agent communication</h3><span>${result.agent_messages.length} handoffs</span></div>${renderAgentMessages(result.agent_messages)}</section>`
      : "";
    const errorMarkup = task.error
      ? `<section class="detail-section"><div class="detail-section-heading"><h3>What needs attention</h3></div><div class="detail-error">${escapeHtml(task.error)}</div></section>`
      : "";
    const agentCount = task.plan?.subtasks?.length || result.plan?.subtasks?.length || 0;
    const stage = task.current_stage || statusLabel(task.status);
    const footerNote = task.worker_id
      ? `Worker ${escapeHtml(task.worker_id.slice(0, 18))}`
      : `Attempt ${Number(task.attempts) || 0}`;
    const eventCount = state.selectedEvents.length;

    return `<div class="detail-header">
        <div class="detail-header-left"><span class="run-status-icon status-${status}">${statusIcon(task.status)}</span><span class="detail-header-label"><strong>Task details</strong><small>${escapeHtml(task.id)}</small></span></div>
        <div class="detail-header-actions"><span class="status-badge status-${status}">${escapeHtml(statusLabel(task.status))}</span><button class="icon-button detail-close" type="button" data-close-detail aria-label="Close task details"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>
      </div>
      <div class="detail-scroll" id="detailScroll">
        <p class="detail-kicker">YOUR TASK</p><h2 class="detail-title" id="detailTitle">${escapeHtml(task.goal)}</h2>
        <div class="detail-metadata"><span>Created ${escapeHtml(formatDate(task.created_at, { withTime: true }))}</span><i class="detail-meta-divider"></i><span>Updated ${escapeHtml(relativeTime(task.updated_at))}</span><i class="detail-meta-divider"></i><span>${agentCount || "—"} ${agentCount === 1 ? "specialist" : "specialists"}</span></div>
        <div class="detail-badges"><span class="priority-badge priority-${priority}">${priority} priority</span>${task.plan?.subtasks?.length ? `<span class="status-badge">${task.plan.subtasks.length} planned steps</span>` : ""}</div>
        <section class="detail-progress-card ${ACTIVE_STATUSES.has(task.status) ? "is-running" : ""}"><div class="detail-progress-heading"><div><strong>Workflow progress</strong><div class="detail-stage">${escapeHtml(stage)}</div></div><span>${progress}%</span></div><div class="detail-progress-track"><span style="width:${progress}%"></span></div><div class="progress-steps">${renderProgressSteps(task)}</div></section>
        <section class="detail-section"><div class="detail-section-heading"><h3>Activity timeline</h3><span>${eventCount} ${eventCount === 1 ? "event" : "events"}</span></div><div id="taskEvents">${renderTaskEvents(state.selectedEvents)}</div></section>
        ${reportMarkup}
        ${errorMarkup}
        <section class="detail-section"><div class="detail-section-heading"><h3>Task DAG & responsibility tree</h3><span>${agentCount ? `${agentCount} delegated steps` : "Waiting for plan"}</span></div>${renderPlan(task)}</section>
        ${messageMarkup}
      </div>
      <footer class="detail-footer"><span class="detail-footer-note">${footerNote}</span><div class="detail-footer-actions">${canRetry ? '<button class="button button-secondary" type="button" data-detail-action="retry">Retry task</button>' : ""}${canCancel ? '<button class="button button-danger" type="button" data-detail-action="cancel">Cancel task</button>' : ""}</div></footer>`;
  }

  function updateSelectedTask(task) {
    if (task.id !== state.selectedTaskId) return;
    state.selectedTask = task;
    const signature = JSON.stringify([
      task.status,
      task.progress,
      task.current_stage,
      task.plan,
      task.result,
      task.error,
      task.attempts,
      task.worker_id,
    ]);
    if (signature === state.selectedDetailSignature) return;
    state.selectedDetailSignature = signature;
    const panel = $(".detail-drawer");
    const scroll = $("#detailScroll", panel)?.scrollTop || 0;
    panel.innerHTML = detailMarkup(task);
    const scroller = $("#detailScroll", panel);
    if (scroller) scroller.scrollTop = scroll;
    window.requestAnimationFrame(drawDagEdges);
  }

  function upsertTask(task) {
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) state.tasks[index] = task;
    else state.tasks.unshift(task);
    state.tasks.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    state.tasks = state.tasks.slice(0, 100);
    renderTasks();
    updateSelectedTask(task);
  }

  async function loadTaskEvents(taskId) {
    try {
      const events = await apiRequest(`${API}/tasks/${encodeURIComponent(taskId)}/events`);
      if (state.selectedTaskId !== taskId) return;
      const nextEvents = Array.isArray(events) ? events : [];
      const signature = JSON.stringify(nextEvents.map((item) => [
        item.id, item.event_type, item.status, item.progress, item.stage, item.created_at,
      ]));
      if (signature === state.selectedEventsSignature) return;
      state.selectedEvents = nextEvents;
      state.selectedEventsSignature = signature;
      const target = $("#taskEvents");
      const section = target?.closest(".detail-section");
      if (target) target.innerHTML = renderTaskEvents(state.selectedEvents);
      const count = section ? $(".detail-section-heading span", section) : null;
      if (count) {
        const length = state.selectedEvents.length;
        count.textContent = `${length} ${length === 1 ? "event" : "events"}`;
      }
    } catch (error) {
      console.warn("Could not load task activity:", error.message);
    }
  }

  function connectTaskSocket(taskId) {
    closeSocket();
    if (!("WebSocket" in window)) {
      startSelectedTaskPolling(taskId);
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/tasks/${encodeURIComponent(taskId)}`;
    let socket;
    try {
      socket = new WebSocket(url);
    } catch (error) {
      startSelectedTaskPolling(taskId);
      return;
    }
    state.socket = socket;
    socket.onmessage = (event) => {
      try {
        const task = JSON.parse(event.data);
        if (task.id) upsertTask(task);
        if (task.error) showToast("Could not follow task", task.error, "error");
      } catch (error) {
        console.warn("Invalid task update:", error);
      }
    };
    socket.onerror = () => {
      if (state.socket === socket) startSelectedTaskPolling(taskId);
    };
    socket.onclose = () => {
      if (state.socket !== socket || state.selectedTaskId !== taskId) return;
      state.socket = null;
      if (!TERMINAL_STATUSES.has(state.selectedTask?.status)) {
        startSelectedTaskPolling(taskId);
        state.socketReconnectTimer = window.setTimeout(() => {
          if (state.selectedTaskId === taskId) connectTaskSocket(taskId);
        }, 3500);
      }
    };
  }

  function startSelectedTaskPolling(taskId) {
    if (state.selectedTaskPoller) return;
    state.selectedTaskPoller = window.setInterval(async () => {
      if (state.selectedTaskId !== taskId) return;
      try {
        const task = await apiRequest(`${API}/tasks/${encodeURIComponent(taskId)}`);
        upsertTask(task);
        await loadTaskEvents(taskId);
        if (TERMINAL_STATUSES.has(task.status)) {
          window.clearInterval(state.selectedTaskPoller);
          state.selectedTaskPoller = null;
        }
      } catch (error) {
        console.warn("Task refresh failed:", error.message);
      }
    }, 2500);
  }

  async function openTask(taskId) {
    state.previousFocus = document.activeElement;
    state.selectedTaskId = taskId;
    state.selectedTask = null;
    state.selectedEvents = [];
    state.selectedEventsSignature = "";
    state.selectedDetailSignature = "";
    $(".detail-drawer").innerHTML = '<div class="detail-loading"><span class="spinner"></span><span>Loading task details…</span></div>';
    openDetailShell();
    try {
      const task = await apiRequest(`${API}/tasks/${encodeURIComponent(taskId)}`);
      upsertTask(task);
      await loadTaskEvents(taskId);
      connectTaskSocket(taskId);
      if (!TERMINAL_STATUSES.has(task.status)) startSelectedTaskPolling(taskId);
    } catch (error) {
      $(".detail-drawer").innerHTML = `<div class="detail-header"><div class="detail-header-label"><strong>Task unavailable</strong><small>${escapeHtml(error.message)}</small></div><button class="icon-button detail-close" type="button" data-close-detail aria-label="Close"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>`;
    }
  }

  function showToast(title, message, kind = "success") {
    const toast = document.createElement("div");
    toast.className = `toast ${kind === "error" ? "toast-error" : ""}`;
    toast.innerHTML = `<span class="toast-icon">${kind === "error" ? "!" : "✓"}</span><span><strong>${escapeHtml(title)}</strong>${escapeHtml(message)}</span>`;
    $("#toastRegion").append(toast);
    const timer = window.setTimeout(() => {
      toast.remove();
      state.toastTimerIds.delete(timer);
    }, 4300);
    state.toastTimerIds.add(timer);
  }

  async function cancelTask() {
    if (!state.selectedTaskId) return;
    const id = state.selectedTaskId;
    try {
      const result = await apiRequest(`${API}/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (result.cancelled) showToast("Task cancelled", "The worker will stop at its next safe checkpoint.");
      else showToast("Task already finished", `Current status: ${statusLabel(result.status)}.`);
      const task = await apiRequest(`${API}/tasks/${encodeURIComponent(id)}`);
      upsertTask(task);
      await Promise.all([loadTaskEvents(id), loadStats()]);
    } catch (error) {
      showToast("Could not cancel task", error.message, "error");
    }
  }

  async function retryTask() {
    if (!state.selectedTaskId) return;
    const id = state.selectedTaskId;
    try {
      const task = await apiRequest(`${API}/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" });
      upsertTask(task);
      showToast("Task requeued", "A fresh worker attempt is on its way.");
      await Promise.all([loadTaskEvents(id), loadStats()]);
      connectTaskSocket(id);
      startSelectedTaskPolling(id);
    } catch (error) {
      showToast("Could not retry task", error.message, "error");
    }
  }

  async function copyReport() {
    const text = state.selectedTask?.result?.final_report;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast("Report copied", "The final report is on your clipboard.");
    } catch (error) {
      const field = document.createElement("textarea");
      field.value = text;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.append(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      showToast(copied ? "Report copied" : "Copy unavailable", copied ? "The report is on your clipboard." : "Select the report text and copy it manually.", copied ? "success" : "error");
    }
  }

  function downloadReport() {
    const task = state.selectedTask;
    const text = task?.result?.final_report;
    if (!text) return;
    const evidence = (task.result?.evidence || []).map((item, index) => `${index + 1}. ${item.source || "Source"}${item.location ? ` (${item.location})` : ""}: ${item.excerpt || ""}`).join("\n");
    const review = task.result?.review;
    const reviewText = review
      ? `\n\nREVIEW CHECKLIST\nDocuments used: ${review.used_supplied_documents ? "Yes" : "No"}\nSupported by data: ${review.supported_by_data ? "Yes" : "No"}\nCalculations consistent: ${review.calculations_consistent === null ? "Not verified" : review.calculations_consistent ? "Yes" : "No"}\nHallucination risk: ${review.hallucination_risk}\nMissing information: ${(review.missing_information || []).join("; ") || "None reported"}`
      : "";
    const evidenceText = evidence ? `\n\nEVIDENCE\n${evidence}` : "\n\nEVIDENCE\nNo source excerpts were attached.";
    const warnings = (task.result?.agent_warnings || []).flatMap((item) => (item.warnings || []).map((warning) => `${item.agent_name || "Agent"}: ${warning}`));
    const warningText = warnings.length ? `\n\nSOURCE / TOOL WARNINGS\n${warnings.map((warning) => `- ${warning}`).join("\n")}` : "";
    const content = `${task.goal}\n\n${text}${reviewText}${evidenceText}${warningText}\n\nConfidence: ${Math.round((Number(task.result.confidence) || 0) * 100)}%\nTask ID: ${task.id}\nGenerated by Meta AgentX\n`;
    downloadBlob(content, `${safeFileName(task.goal, "agentx-report")}.txt`, "text/plain;charset=utf-8");
    showToast("Report downloaded", "Your report is saved as a text file.");
  }

  function safeFileName(value, fallback) {
    const name = String(value || fallback).toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 55);
    return name || fallback;
  }

  function downloadBlob(content, filename, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportRuns() {
    const tasks = currentTasks();
    if (!tasks.length) {
      showToast("Nothing to export", "There are no runs matching the current filters.", "error");
      return;
    }
    const columns = ["id", "goal", "priority", "status", "progress", "attempts", "created_at", "updated_at"];
    const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = [columns.join(","), ...tasks.map((task) => columns.map((column) => csvCell(task[column])).join(","))];
    downloadBlob(`\ufeff${rows.join("\r\n")}`, "meta-agentx-task-runs.csv", "text/csv;charset=utf-8");
    showToast("Runs exported", `${tasks.length} ${tasks.length === 1 ? "run" : "runs"} saved as CSV.`);
  }

  function toggleShortcuts(force) {
    const popover = $("#shortcutPopover");
    const shouldOpen = typeof force === "boolean" ? force : popover.hidden;
    popover.hidden = !shouldOpen;
  }

  function toggleMobileMenu(force) {
    const sidebar = $("#sidebar");
    const show = typeof force === "boolean" ? force : !sidebar.classList.contains("mobile-open");
    sidebar.classList.toggle("mobile-open", show);
    $("#mobileScrim").hidden = !show;
  }

  function bindEvents() {
    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const openTaskButton = target.closest("[data-open-task]");
      if (openTaskButton) {
        openTaskModal();
        return;
      }
      const closeModal = target.closest("[data-close-task-modal]");
      if (closeModal) {
        closeTaskModal();
        return;
      }
      const integrationTestButton = target.closest("[data-integration-test]");
      if (integrationTestButton) {
        testIntegration(integrationTestButton.dataset.integrationTest, integrationTestButton);
        return;
      }
      const deleteBusinessDocumentButton = target.closest("[data-delete-business-document]");
      if (deleteBusinessDocumentButton) {
        removeBusinessDocument(deleteBusinessDocumentButton.dataset.deleteBusinessDocument);
        return;
      }
      const viewButton = target.closest("[data-view]");
      if (viewButton) {
        if (viewButton instanceof HTMLAnchorElement) event.preventDefault();
        showView(viewButton.dataset.view);
        return;
      }
      const taskButton = target.closest("[data-task-id]");
      if (taskButton) {
        openTask(taskButton.dataset.taskId);
        return;
      }
      const templateButton = target.closest("[data-template-id]");
      if (templateButton) {
        chooseTemplate(templateButton.dataset.templateId);
        return;
      }
      const priorityButton = target.closest("[data-priority]");
      if (priorityButton) {
        state.selectedPriority = priorityButton.dataset.priority;
        updatePrioritySelection();
        return;
      }
      const filterButton = target.closest("[data-status-filter]");
      if (filterButton) {
        state.statusFilter = filterButton.dataset.statusFilter;
        $$(".filter-pill").forEach((item) => item.classList.toggle("active", item === filterButton));
        renderTasks();
        return;
      }
      const detailAction = target.closest("[data-detail-action]");
      if (detailAction) {
        const action = detailAction.dataset.detailAction;
        if (action === "cancel") cancelTask();
        if (action === "retry") retryTask();
        if (action === "copy") copyReport();
        if (action === "download") downloadReport();
        return;
      }
      if (target.closest("[data-close-detail]")) {
        closeTaskDetail();
        return;
      }
      if (target.id === "detailBackdrop") {
        closeTaskDetail();
        return;
      }
      if (target.id === "taskModal") {
        closeTaskModal();
        return;
      }
      if (target.closest("#shortcutsButton")) {
        toggleShortcuts();
        return;
      }
      if (target.closest("#closeShortcuts")) {
        toggleShortcuts(false);
        return;
      }
      if (target.closest("#mobileMenuButton")) {
        toggleMobileMenu();
        return;
      }
      if (target.id === "mobileScrim") {
        toggleMobileMenu(false);
        return;
      }
      if (!target.closest("#shortcutPopover")) toggleShortcuts(false);
      if (!target.closest("#sidebar") && window.innerWidth <= 760) toggleMobileMenu(false);
    });

    $("#taskGoal").addEventListener("input", () => {
      if (state.selectedTemplateId) {
        state.selectedTemplateId = null;
        $$(".modal-template").forEach((button) => button.classList.remove("selected"));
      }
      updateGoalCount();
      $("#formError").hidden = true;
    });
    $("#taskForm").addEventListener("submit", submitTask);
    $("#savePromptButton").addEventListener("click", saveCurrentPrompt);
    $("#loadPromptButton").addEventListener("click", applySavedPrompt);
    $("#deletePromptButton").addEventListener("click", deleteSavedPrompt);
    $("#savedPromptList").addEventListener("change", renderSavedPromptList);
    $("#businessContextForm").addEventListener("submit", saveBusinessContext);
    const markBusinessContextDirty = () => {
      state.businessContextDirty = true;
      setBusinessContextSaveState("Unsaved changes", "is-dirty");
      $("#businessContextError").hidden = true;
    };
    $("#businessName").addEventListener("input", markBusinessContextDirty);
    $("#businessProfileText").addEventListener("input", () => {
      updateBusinessContextCount();
      markBusinessContextDirty();
    });
    $("#businessFileInput").addEventListener("change", uploadBusinessDocuments);
    $("#globalSearch").addEventListener("input", (event) => {
      state.query = event.target.value;
      renderTasks();
    });
    $("#exportRuns").addEventListener("click", exportRuns);
    $("#mobileScrim").addEventListener("click", () => toggleMobileMenu(false));
    window.addEventListener("resize", () => window.requestAnimationFrame(drawDagEdges), { passive: true });
    $("#detailBackdrop").addEventListener("click", (event) => {
      if (event.target === $("#detailBackdrop")) closeTaskDetail();
    });
    $("#taskModal").addEventListener("click", (event) => {
      if (event.target === $("#taskModal")) closeTaskModal();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (!$("#shortcutPopover").hidden) toggleShortcuts(false);
        if (!$("#taskModal").hidden) closeTaskModal();
        else if (!$("#detailBackdrop").hidden) closeTaskDetail();
        else toggleMobileMenu(false);
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const isTyping = target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (isTyping) return;
      if (event.key.toLocaleLowerCase() === "n") {
        event.preventDefault();
        openTaskModal();
      } else if (event.key === "/") {
        event.preventDefault();
        $("#globalSearch").focus();
      }
    });
  }

  async function init() {
    bindEvents();
    loadSavedPrompts();
    renderTemplates();
    renderTasks();
    updateGoalCount();
    await Promise.allSettled([loadHealth(), loadAgents(), loadTemplates(), loadTasks({ quiet: false }), loadStats()]);
    state.taskPoller = window.setInterval(() => loadTasks(), 7000);
    state.statsPoller = window.setInterval(loadStats, 12000);
    state.healthPoller = window.setInterval(loadHealth, 8000);
  }

  document.addEventListener("DOMContentLoaded", init, { once: true });
})();