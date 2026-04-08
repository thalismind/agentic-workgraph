import { renderGraph as renderGraphCanvas } from "./graph.js";
import { parseHashRoute, routeMatchesState, syncHashFromState } from "./router.js";
import {
  $,
  closeSocket,
  fetchJson,
  formatDuration,
  formatStarted,
  postJson,
  renderRunButton,
  setActiveButton,
  setTab,
  state,
  stopTraceRefresh,
} from "./state.js";

function formatRelativeTime(timestamp) {
  if (!timestamp) return "No messages yet";
  const deltaMs = Math.max(0, Date.now() - new Date(timestamp).getTime());
  if (deltaMs < 1000) return "Last event just now";
  const seconds = Math.floor(deltaMs / 1000);
  if (seconds < 60) return `Last event ${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `Last event ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `Last event ${hours}h ago`;
}

function renderWsStatus() {
  const indicator = $("ws-status-indicator");
  indicator.classList.toggle("connected", state.wsConnected);
  indicator.classList.toggle("disconnected", !state.wsConnected);
  $("ws-status-label").textContent = state.wsConnected ? "WS connected" : "WS disconnected";
  $("ws-status-last").textContent = formatRelativeTime(state.lastWsMessageAt);
}

function startWsStatusClock() {
  if (state.wsStatusTimer) clearInterval(state.wsStatusTimer);
  state.wsStatusTimer = setInterval(() => renderWsStatus(), 1000);
}

function closeLiveSocket() {
  closeSocket();
  state.wsConnected = false;
  renderWsStatus();
}

function renderLayoutControls() {
  const layout = $("main-layout");
  const focused = layout.classList.contains("detail-focus");
  $("focus-debugger-button").classList.toggle("hidden", focused);
  $("restore-layout-button").classList.toggle("hidden", !focused);
}

function setDetailFocus(enabled) {
  $("main-layout").classList.toggle("detail-focus", enabled);
  renderLayoutControls();
}

function renderWorkflows() {
  const workflowsList = $("workflows-list");
  const template = $("workflow-card-template");
  workflowsList.replaceChildren();
  $("workflow-count").textContent = String(state.workflows.length);

  for (const workflow of state.workflows) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.value = workflow.name;
    node.querySelector(".workflow-name").textContent = workflow.name;
    node.querySelector(".workflow-meta").textContent =
      `${workflow.run_count} runs · ${workflow.version_count} versions`;
    node.addEventListener("click", async () => {
      closeLiveSocket();
      stopTraceRefresh();
      state.selectedWorkflow = workflow.name;
      state.selectedVersion = null;
      state.selectedRunId = null;
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      setActiveButton(workflowsList, workflow.name);
      await loadWorkflowHistory();
    });
    if (state.selectedWorkflow === workflow.name) node.classList.add("active");
    workflowsList.append(node);
  }
}

function renderVersions(payload) {
  const versionsList = $("versions-list");
  const template = $("version-chip-template");
  versionsList.replaceChildren();

  for (const version of payload.versions) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.value = version.version;
    node.textContent = `${version.version}${version.is_current ? " current" : ""}`;
    node.classList.toggle("active", state.selectedVersion === version.version);
    node.addEventListener("click", async () => {
      state.selectedVersion = state.selectedVersion === version.version ? null : version.version;
      state.selectedRunId = null;
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      await loadWorkflowHistory();
    });
    versionsList.append(node);
  }
}

function renderRuns(payload) {
  const runsList = $("runs-list");
  const template = $("run-card-template");
  runsList.replaceChildren();
  $("history-title").textContent = payload.workflow;
  $("history-subtitle").textContent = payload.version
    ? `Showing runs for version ${payload.version}.`
    : `Showing all runs. Current version: ${payload.current_version}.`;

  if (payload.runs.length === 0) {
    runsList.innerHTML = `<div class="empty-state">No runs recorded for this selection.</div>`;
    return;
  }

  for (const run of payload.runs) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.value = run.run_id;
    node.querySelector(".run-id").textContent = run.run_id;
    const status = node.querySelector(".status-pill");
    status.textContent = run.status;
    status.className = `status-pill ${run.status}`;
    node.querySelector(".run-version").textContent = run.version;
    node.querySelector(".run-duration").textContent = formatDuration(run.duration_ms);
    node.querySelector(".run-started").textContent = formatStarted(run.started_at);
    node.querySelector(".run-errors").textContent = `${run.error_count} errors`;
    node.addEventListener("click", async () => {
      closeLiveSocket();
      stopTraceRefresh();
      state.selectedRunId = run.run_id;
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      setActiveButton(runsList, run.run_id);
      await loadRunDetail();
    });
    if (state.selectedRunId === run.run_id) node.classList.add("active");
    runsList.append(node);
  }
}

function renderDetailItems(containerId, items, emptyText, mapFn) {
  const container = $(containerId);
  const template = $("detail-item-template");
  container.replaceChildren();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">${emptyText}</div>`;
    return;
  }
  for (const item of items) {
    const node = template.content.firstElementChild.cloneNode(true);
    const mapped = mapFn(item);
    node.querySelector(".detail-item-title").textContent = mapped.title;
    node.querySelector(".detail-item-meta").textContent = mapped.meta;
    node.querySelector(".detail-item-body").textContent = mapped.body;
    if (mapped.active) node.classList.add("active");
    if (mapped.onClick) node.addEventListener("click", mapped.onClick);
    container.append(node);
  }
}

function getNodeRuntime(instanceId) {
  return state.run?.nodes?.[instanceId] ?? null;
}

function renderGraph() {
  return renderGraphCanvas({
    onSelectNode: async (nodeId) => {
      state.selectedNodeId = nodeId;
      state.selectedItemIndex = 0;
      renderGraph();
      await loadNodeInspector();
    },
  });
}

function renderNodeInspector() {
  const summary = $("node-summary");
  if (!state.selectedNodeId) {
    summary.textContent = "No node selected.";
    $("items-list").replaceChildren();
    $("stream-view").textContent = "No stream selected.";
    return;
  }

  const node = getNodeRuntime(state.selectedNodeId);
  const status = node?.status ?? "pending";
  const counters = node?.counters;
  summary.innerHTML = `
    <strong>${state.selectedNodeId}</strong>
    <br />
    <span class="muted">Status:</span> ${status}
    <br />
    <span class="muted">Items:</span> ${counters?.total ?? 0} total · ${counters?.completed ?? 0} done
    <br />
    <span class="muted">Duration:</span> ${formatDuration(node?.duration_ms ?? null)}
  `;

  renderDetailItems(
    "items-list",
    state.nodeItems,
    "No items recorded for this node.",
    (item) => ({
      title: `item ${item.index}`,
      meta: `${item.status} · ${formatDuration(item.duration_ms)}`,
      body: `${JSON.stringify(item.output)}\nprogress: ${Math.round((item.progress ?? 0) * 100)}%${item.progress_desc ? ` · ${item.progress_desc}` : ""}`,
      active: item.index === state.selectedItemIndex,
      onClick: async () => {
        state.selectedItemIndex = item.index;
        renderNodeInspector();
        await loadStream();
      },
    }),
  );

  $("stream-view").textContent = state.streamText || "No stream selected.";
}

function updateRunSummary() {
  if (!state.run) {
    $("detail-summary").textContent = "No run selected.";
    return;
  }
  const live = ["running", "pending"].includes(state.run.status)
    ? `<span class="status-pill running">live</span>`
    : "";
  $("detail-summary").innerHTML = `
    <div class="row row-space">
      <strong>Status: ${state.run.status}</strong>
      ${live}
    </div>
    <div class="summary-lines">
      <span><span class="muted">Started:</span> ${formatStarted(state.run.started_at)}</span>
      <span><span class="muted">Finished:</span> ${state.run.finished_at ? formatStarted(state.run.finished_at) : "pending"}</span>
    </div>
  `;
}

async function loadStream() {
  if (!state.selectedRunId || !state.selectedNodeId) {
    state.streamText = "";
    renderNodeInspector();
    return;
  }
  const payload = await fetchJson(
    `/api/runs/${state.selectedRunId}/nodes/${state.selectedNodeId}/items/${state.selectedItemIndex}/stream`,
  );
  state.streamText = payload
    .filter((entry) => !entry._truncated)
    .map((entry) => entry.token)
    .join("");
  renderNodeInspector();
}

async function loadNodeInspector() {
  if (!state.selectedRunId || !state.selectedNodeId) {
    renderNodeInspector();
    return;
  }
  state.nodeItems = await fetchJson(`/api/runs/${state.selectedRunId}/nodes/${state.selectedNodeId}/items`);
  if (!state.nodeItems.some((item) => item.index === state.selectedItemIndex)) {
    state.selectedItemIndex = state.nodeItems[0]?.index ?? 0;
  }
  await loadStream();
}

function renderDetailPanels() {
  renderGraph();
  renderNodeInspector();

  renderDetailItems(
    "timeline-list",
    state.timeline,
    "No node timing data available.",
    (item) => ({
      title: item.node_id,
      meta: `${item.status} · ${formatDuration(item.duration_ms)}`,
      body: `${formatStarted(item.started_at)} → ${item.finished_at ? formatStarted(item.finished_at) : "pending"}`,
      active: item.node_id === state.selectedNodeId,
      onClick: async () => {
        state.selectedNodeId = item.node_id;
        state.selectedItemIndex = 0;
        renderDetailPanels();
        await loadNodeInspector();
      },
    }),
  );

  renderDetailItems(
    "errors-list",
    state.errors,
    "No errors recorded.",
    (item) => ({
      title: `${item.node_id}${item.item_index == null ? "" : ` [${item.item_index}]`}`,
      meta: item.error_type,
      body: item.message,
    }),
  );

  renderDetailItems(
    "trace-list",
    state.trace,
    "No trace spans recorded.",
    (item) => ({
      title: item.name,
      meta: item.status,
      body: JSON.stringify(item.attributes, null, 2),
    }),
  );
}

async function refreshRunDetailData() {
  if (!state.selectedRunId) return;
  const [run, timeline, errors, trace] = await Promise.all([
    fetchJson(`/api/runs/${state.selectedRunId}`),
    fetchJson(`/api/runs/${state.selectedRunId}/timeline`),
    fetchJson(`/api/runs/${state.selectedRunId}/errors`),
    fetchJson(`/api/runs/${state.selectedRunId}/trace`),
  ]);
  state.run = run;
  state.timeline = timeline;
  state.errors = errors;
  state.trace = trace;
}

async function scheduleTraceRefresh(delayMs = 350) {
  if (!state.selectedRunId) return;
  stopTraceRefresh();
  state.traceRefreshTimer = setTimeout(async () => {
    state.traceRefreshTimer = null;
    state.trace = await fetchJson(`/api/runs/${state.selectedRunId}/trace`);
    renderDetailPanels();
  }, delayMs);
}

function ensureTimelineNode(nodeId) {
  let entry = state.timeline.find((item) => item.node_id === nodeId);
  if (!entry) {
    entry = {
      node_id: nodeId,
      status: "pending",
      started_at: null,
      finished_at: null,
      duration_ms: null,
    };
    state.timeline.push(entry);
  }
  return entry;
}

function updateTimelineFromEvent(event) {
  if (!event.node_id) return;
  const entry = ensureTimelineNode(event.node_id);
  if (event.event === "node_status") {
    entry.status = event.status;
    if (event.status === "running" && !entry.started_at) {
      entry.started_at = event.timestamp;
    }
    if (["completed", "failed"].includes(event.status)) {
      entry.finished_at = event.timestamp;
      if (entry.started_at) {
        entry.duration_ms = Math.max(
          0,
          new Date(entry.finished_at).getTime() - new Date(entry.started_at).getTime(),
        );
      }
    }
  }
}

function updateErrorsFromEvent(event) {
  if (event.event !== "node_error") return;
  state.errors = [
    ...state.errors,
    {
      node_id: event.node_id,
      item_index: event.item_index ?? null,
      error_type: event.error_type ?? "error",
      message: event.message ?? "Unknown error",
      timestamp: event.timestamp,
    },
  ];
}

function applyEvent(event) {
  if (!state.run || event.run_id !== state.selectedRunId) return;

  if (event.event === "run_status") {
    state.run.status = event.status;
    if (["completed", "failed"].includes(event.status)) {
      state.run.finished_at = event.timestamp;
    }
  }

  if (event.node_id && state.run.nodes?.[event.node_id]) {
    const node = state.run.nodes[event.node_id];
    if (event.event === "node_status") node.status = event.status;
    if (event.event === "node_counters") node.counters = event.counters;
  }

  if (event.event === "node_progress" && event.node_id === state.selectedNodeId && event.item_index != null) {
    const item = state.nodeItems.find((entry) => entry.index === event.item_index);
    if (item) {
      item.progress = event.progress;
      item.progress_desc = event.desc;
    }
  }

  if (
    event.event === "node_stream" &&
    event.node_id === state.selectedNodeId &&
    event.item_index === state.selectedItemIndex
  ) {
    state.streamText += event.token ?? event.chunk ?? "";
  }
  if (event.event === "node_stream") {
    state.streamingNodes.add(event.node_id);
  }
  if (event.event === "node_stream_end") {
    state.streamingNodes.delete(event.node_id);
  }

  updateTimelineFromEvent(event);
  updateErrorsFromEvent(event);
  state.lastWsMessageAt = event.timestamp ?? new Date().toISOString();
  renderWsStatus();
  updateRunSummary();
  renderDetailPanels();

  if (
    (event.event === "node_status" && ["completed", "failed"].includes(event.status)) ||
    (event.event === "run_status" && ["completed", "failed"].includes(event.status))
  ) {
    scheduleTraceRefresh();
  }
}

function connectRunSocket() {
  closeLiveSocket();
  if (!state.selectedRunId || !["running", "pending"].includes(state.run?.status ?? "")) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${window.location.host}/api/runs/${state.selectedRunId}/ws`);
  state.ws.onopen = () => {
    state.wsConnected = true;
    renderWsStatus();
  };
  state.ws.onmessage = (message) => applyEvent(JSON.parse(message.data));
  state.ws.onclose = () => {
    state.ws = null;
    state.wsConnected = false;
    renderWsStatus();
  };
}

async function loadRunDetail() {
  if (!state.selectedRunId) {
    $("detail-summary").textContent = "No run selected.";
    return;
  }

  await refreshRunDetailData();
  state.streamingNodes.clear();
  if (!state.selectedNodeId && state.graph?.nodes?.length) {
    state.selectedNodeId = state.graph.nodes[0].instance_id;
  }

  $("detail-title").textContent = state.run.run_id;
  $("detail-subtitle").textContent = `${state.run.workflow} · ${state.run.version}`;
  updateRunSummary();

  renderDetailPanels();
  await loadNodeInspector();
  connectRunSocket();
  syncHashFromState(state.applyingHashRoute);
}

async function loadWorkflowHistory() {
  if (!state.selectedWorkflow) return;

  const [versionsPayload, graphPayload] = await Promise.all([
    fetchJson(`/api/workflows/${state.selectedWorkflow}/versions`),
    fetchJson(`/api/workflows/${state.selectedWorkflow}/graph`),
  ]);
  const knownVersions = new Set(versionsPayload.versions.map((version) => version.version));
  if (state.selectedVersion && !knownVersions.has(state.selectedVersion)) {
    state.selectedVersion = null;
  }
  const runsPayload = await fetchJson(
    `/api/workflows/${state.selectedWorkflow}/runs${
      state.selectedVersion ? `?version=${encodeURIComponent(state.selectedVersion)}` : ""
    }`,
  );

  state.graph = graphPayload;
  renderRunButton();
  renderVersions(versionsPayload);
  renderRuns(runsPayload);
  renderGraph();

  if (runsPayload.runs.length > 0) {
    if (!runsPayload.runs.some((run) => run.run_id === state.selectedRunId)) {
      state.selectedRunId = runsPayload.runs[0].run_id;
    }
    setActiveButton($("runs-list"), state.selectedRunId);
    await loadRunDetail();
  } else {
    closeLiveSocket();
    stopTraceRefresh();
    state.selectedRunId = null;
    state.selectedNodeId = state.graph?.nodes?.[0]?.instance_id ?? null;
    state.run = null;
    state.timeline = [];
    state.errors = [];
    state.trace = [];
    state.nodeItems = [];
    state.streamText = "";
    state.streamingNodes.clear();
    $("detail-summary").textContent = "No run selected.";
    renderDetailPanels();
  }

  syncHashFromState(state.applyingHashRoute);
}

async function applyHashRoute() {
  if (state.workflows.length === 0) {
    renderWorkflows();
    renderRunButton();
    return;
  }

  const route = parseHashRoute();
  if (state.selectedWorkflow && routeMatchesState(route)) return;
  const workflowNames = new Set(state.workflows.map((workflow) => workflow.name));
  const nextWorkflow = route.workflow && workflowNames.has(route.workflow)
    ? route.workflow
    : state.workflows[0].name;
  const workflowChanged = state.selectedWorkflow !== nextWorkflow;
  const runChanged = state.selectedRunId !== route.run;

  state.applyingHashRoute = true;
  try {
    closeLiveSocket();
    stopTraceRefresh();
    state.selectedWorkflow = nextWorkflow;
    state.selectedVersion = route.version || null;
    state.selectedRunId = route.run || null;
    if (workflowChanged || runChanged) {
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      state.nodeItems = [];
      state.streamText = "";
      state.streamingNodes.clear();
    }

    renderWorkflows();
    renderRunButton();
    await loadWorkflowHistory();
  } finally {
    state.applyingHashRoute = false;
  }
}

async function refresh() {
  state.workflows = await fetchJson("/api/workflows");
  await applyHashRoute();
}

async function launchWorkflowRun() {
  if (!state.selectedWorkflow || state.launchingRun) return;
  state.launchingRun = true;
  renderRunButton();
  try {
    const payload = await postJson(`/api/workflows/${state.selectedWorkflow}/runs`);
    state.selectedVersion = null;
    state.selectedRunId = payload.run_id;
    state.selectedNodeId = null;
    state.selectedItemIndex = 0;
    await refresh();
  } finally {
    state.launchingRun = false;
    renderRunButton();
  }
}

$("refresh-button").addEventListener("click", () => refresh().catch(console.error));
$("run-workflow-button").addEventListener("click", () => launchWorkflowRun().catch(console.error));
$("focus-debugger-button").addEventListener("click", () => setDetailFocus(true));
$("restore-layout-button").addEventListener("click", () => setDetailFocus(false));
$("items-tab").addEventListener("click", () => setTab("items"));
$("stream-tab").addEventListener("click", () => setTab("stream"));
window.addEventListener("hashchange", () => {
  applyHashRoute().catch(console.error);
});
setTab("items");
renderRunButton();
renderLayoutControls();
renderWsStatus();
startWsStatusClock();

refresh().catch((error) => {
  $("detail-summary").textContent = error.message;
  console.error(error);
});
