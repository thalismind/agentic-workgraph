const state = {
  workflows: [],
  selectedWorkflow: null,
  selectedVersion: null,
  selectedRunId: null,
  selectedNodeId: null,
  selectedItemIndex: 0,
  selectedTab: "items",
  graph: null,
  run: null,
  timeline: [],
  errors: [],
  trace: [],
  nodeItems: [],
  streamText: "",
  streamingNodes: new Set(),
  ws: null,
  traceRefreshTimer: null,
  launchingRun: false,
};

const $ = (id) => document.getElementById(id);

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${path}`);
  }
  return response.json();
}

async function postJson(path) {
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${path}`);
  }
  return response.json();
}

function formatDuration(durationMs) {
  if (durationMs == null) return "pending";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function formatStarted(startedAt) {
  if (!startedAt) return "not started";
  return new Date(startedAt).toLocaleString();
}

function setActiveButton(container, activeValue) {
  for (const button of container.querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.value === activeValue);
  }
}

function closeSocket() {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
}

function stopTraceRefresh() {
  if (state.traceRefreshTimer) {
    clearTimeout(state.traceRefreshTimer);
    state.traceRefreshTimer = null;
  }
}

function setTab(tab) {
  state.selectedTab = tab;
  $("items-tab").classList.toggle("active", tab === "items");
  $("stream-tab").classList.toggle("active", tab === "stream");
  $("items-panel").classList.toggle("hidden", tab !== "items");
  $("stream-panel").classList.toggle("hidden", tab !== "stream");
}

function renderRunButton() {
  const button = $("run-workflow-button");
  button.disabled = !state.selectedWorkflow || state.launchingRun;
  button.textContent = state.launchingRun ? "Starting..." : "Run Workflow";
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
      closeSocket();
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
      closeSocket();
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

function getNodeProgress(node) {
  if (!node?.items?.length) return 0;
  return node.items.reduce((sum, item) => sum + (item.progress ?? 0), 0) / node.items.length;
}

function computeGraphLayout(graph) {
  const levelMap = new Map();
  for (const node of graph.nodes) {
    const level = node.depends_on.length
      ? Math.max(...node.depends_on.map((dep) => levelMap.get(dep) ?? 0)) + 1
      : 0;
    levelMap.set(node.instance_id, level);
  }

  const lanes = new Map();
  for (const node of graph.nodes) {
    const level = levelMap.get(node.instance_id) ?? 0;
    const lane = lanes.get(level) ?? [];
    lane.push(node.instance_id);
    lanes.set(level, lane);
  }

  const positions = new Map();
  for (const [level, ids] of lanes.entries()) {
    ids.forEach((id, index) => {
      positions.set(id, { x: 48 + level * 220, y: 42 + index * 118 });
    });
  }

  const maxLevel = Math.max(0, ...lanes.keys());
  const maxLane = Math.max(1, ...Array.from(lanes.values()).map((value) => value.length));
  return {
    positions,
    width: 240 + maxLevel * 220,
    height: 120 + maxLane * 118,
  };
}

function renderGraph() {
  const container = $("graph-canvas");
  container.replaceChildren();
  if (!state.graph) {
    container.textContent = "No workflow selected.";
    $("graph-warnings").classList.add("hidden");
    return;
  }

  $("graph-caption").textContent = `${state.graph.workflow} · ${state.graph.version}`;
  const warningContainer = $("graph-warnings");
  warningContainer.replaceChildren();
  if (state.graph.warnings?.length) {
    warningContainer.classList.remove("hidden");
    for (const warning of state.graph.warnings) {
      const node = document.createElement("div");
      node.className = "warning-chip";
      node.textContent = warning;
      warningContainer.append(node);
    }
  } else {
    warningContainer.classList.add("hidden");
  }
  const layout = computeGraphLayout(state.graph);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
  svg.setAttribute("class", "graph-svg");

  for (const edge of state.graph.edges) {
    const from = layout.positions.get(edge.from);
    const to = layout.positions.get(edge.to);
    if (!from || !to) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const startX = from.x + 150;
    const startY = from.y + 38;
    const endX = to.x;
    const endY = to.y + 38;
    const controlX = (startX + endX) / 2;
    path.setAttribute("d", `M ${startX} ${startY} C ${controlX} ${startY}, ${controlX} ${endY}, ${endX} ${endY}`);
    path.setAttribute("class", "graph-edge");
    svg.append(path);
  }

  for (const node of state.graph.nodes) {
    if (!node.loop_iterations || node.loop_iterations < 2) continue;
    const { x, y } = layout.positions.get(node.instance_id);
    const loopPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    loopPath.setAttribute(
      "d",
      `M ${x + 34} ${y + 8} C ${x - 28} ${y - 34}, ${x + 178} ${y - 34}, ${x + 116} ${y + 8}`,
    );
    loopPath.setAttribute("class", "graph-loop-edge");
    svg.append(loopPath);

    const loopLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
    loopLabel.setAttribute("x", String(x + 56));
    loopLabel.setAttribute("y", String(y - 14));
    loopLabel.setAttribute("class", "graph-loop-label");
    loopLabel.textContent = `↺ ${node.loop_iterations}x`;
    svg.append(loopLabel);
  }

  for (const node of state.graph.nodes) {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const runtime = getNodeRuntime(node.instance_id);
    const status = runtime?.status ?? node.status ?? "pending";
    const progress = getNodeProgress(runtime);
    const counters = runtime?.counters;
    const { x, y } = layout.positions.get(node.instance_id);
    const streaming = state.streamingNodes.has(node.instance_id);
    group.setAttribute(
      "class",
      `graph-node ${status}${streaming ? " streaming" : ""}${state.selectedNodeId === node.instance_id ? " active" : ""}`,
    );
    group.dataset.value = node.instance_id;

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", "150");
    rect.setAttribute("height", "76");
    rect.setAttribute("rx", "18");
    group.append(rect);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
    title.setAttribute("x", String(x + 12));
    title.setAttribute("y", String(y + 23));
    title.setAttribute("font-size", "13");
    title.setAttribute("font-weight", "700");
    title.textContent = node.node_id;
    group.append(title);

    if (streaming) {
      const typing = document.createElementNS("http://www.w3.org/2000/svg", "text");
      typing.setAttribute("x", String(x + 112));
      typing.setAttribute("y", String(y + 23));
      typing.setAttribute("font-size", "14");
      typing.setAttribute("class", "typing-indicator");
      typing.textContent = "...";
      group.append(typing);
    }

    const meta = document.createElementNS("http://www.w3.org/2000/svg", "text");
    meta.setAttribute("x", String(x + 12));
    meta.setAttribute("y", String(y + 42));
    meta.setAttribute("font-size", "11");
    meta.textContent = counters && counters.total > 1
      ? `✓ ${counters.completed} · ▸ ${counters.running} · ✗ ${counters.failed}`
      : (node.loop_iterations && node.loop_iterations > 1 ? `loop ${node.loop_iterations}x · ${status}` : status);
    group.append(meta);

    const badge = document.createElementNS("http://www.w3.org/2000/svg", "text");
    badge.setAttribute("x", String(x + 12));
    badge.setAttribute("y", String(y + 60));
    badge.setAttribute("font-size", "10");
    badge.textContent = runtime?.duration_ms != null ? formatDuration(runtime.duration_ms) : "waiting";
    group.append(badge);

    const progressBg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    progressBg.setAttribute("x", String(x + 92));
    progressBg.setAttribute("y", String(y + 55));
    progressBg.setAttribute("width", "46");
    progressBg.setAttribute("height", "8");
    progressBg.setAttribute("rx", "4");
    progressBg.setAttribute("class", "graph-progress-bg");
    group.append(progressBg);

    const progressBar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    progressBar.setAttribute("x", String(x + 92));
    progressBar.setAttribute("y", String(y + 55));
    progressBar.setAttribute("width", String(Math.max(3, 46 * progress)));
    progressBar.setAttribute("height", "8");
    progressBar.setAttribute("rx", "4");
    progressBar.setAttribute("class", "graph-progress-bar");
    group.append(progressBar);

    group.addEventListener("click", async () => {
      state.selectedNodeId = node.instance_id;
      state.selectedItemIndex = 0;
      renderGraph();
      await loadNodeInspector();
    });

    svg.append(group);
  }

  container.append(svg);
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
  closeSocket();
  if (!state.selectedRunId || !["running", "pending"].includes(state.run?.status ?? "")) return;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${window.location.host}/api/runs/${state.selectedRunId}/ws`);
  state.ws.onmessage = (message) => applyEvent(JSON.parse(message.data));
  state.ws.onclose = () => {
    state.ws = null;
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
}

async function loadWorkflowHistory() {
  if (!state.selectedWorkflow) return;

  const [versionsPayload, runsPayload, graphPayload] = await Promise.all([
    fetchJson(`/api/workflows/${state.selectedWorkflow}/versions`),
    fetchJson(
      `/api/workflows/${state.selectedWorkflow}/runs${
        state.selectedVersion ? `?version=${encodeURIComponent(state.selectedVersion)}` : ""
      }`,
    ),
    fetchJson(`/api/workflows/${state.selectedWorkflow}/graph`),
  ]);

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
    closeSocket();
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
}

async function refresh() {
  state.workflows = await fetchJson("/api/workflows");
  if (!state.selectedWorkflow && state.workflows.length > 0) {
    state.selectedWorkflow = state.workflows[0].name;
  }
  renderWorkflows();
  renderRunButton();
  await loadWorkflowHistory();
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
$("items-tab").addEventListener("click", () => setTab("items"));
$("stream-tab").addEventListener("click", () => setTab("stream"));
setTab("items");
renderRunButton();

refresh().catch((error) => {
  $("detail-summary").textContent = error.message;
  console.error(error);
});
