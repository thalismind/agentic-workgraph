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
  ws: null,
  detailRefreshTimer: null,
  detailRefreshPending: false,
};

const $ = (id) => document.getElementById(id);

async function fetchJson(path) {
  const response = await fetch(path);
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

function stopDetailRefresh() {
  if (state.detailRefreshTimer) {
    clearTimeout(state.detailRefreshTimer);
    state.detailRefreshTimer = null;
  }
}

function setTab(tab) {
  state.selectedTab = tab;
  $("items-tab").classList.toggle("active", tab === "items");
  $("stream-tab").classList.toggle("active", tab === "stream");
  $("items-panel").classList.toggle("hidden", tab !== "items");
  $("stream-panel").classList.toggle("hidden", tab !== "stream");
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
      stopDetailRefresh();
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
      stopDetailRefresh();
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
    return;
  }

  $("graph-caption").textContent = `${state.graph.workflow} · ${state.graph.version}`;
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
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const runtime = getNodeRuntime(node.instance_id);
    const status = runtime?.status ?? node.status ?? "pending";
    const progress = getNodeProgress(runtime);
    const counters = runtime?.counters;
    const { x, y } = layout.positions.get(node.instance_id);
    group.setAttribute("class", `graph-node ${status}${state.selectedNodeId === node.instance_id ? " active" : ""}`);
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

    const meta = document.createElementNS("http://www.w3.org/2000/svg", "text");
    meta.setAttribute("x", String(x + 12));
    meta.setAttribute("y", String(y + 42));
    meta.setAttribute("font-size", "11");
    meta.textContent = counters && counters.total > 1
      ? `✓ ${counters.completed} · ▸ ${counters.running} · ✗ ${counters.failed}`
      : status;
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

async function scheduleDetailRefresh(immediate = false) {
  if (!state.selectedRunId) return;
  if (state.detailRefreshPending) return;
  state.detailRefreshPending = true;
  if (!immediate) {
    await new Promise((resolve) => {
      state.detailRefreshTimer = setTimeout(resolve, 450);
    });
  }
  state.detailRefreshTimer = null;
  state.detailRefreshPending = false;
  await refreshRunDetailData();
  renderDetailPanels();
  if (state.selectedNodeId) {
    await loadNodeInspector();
  }
}

function applyEvent(event) {
  if (!state.run || event.run_id !== state.selectedRunId) return;

  if (event.event === "run_status") {
    state.run.status = event.status;
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

  renderDetailPanels();

  if (["node_status", "node_error", "run_status", "node_output"].includes(event.event)) {
    scheduleDetailRefresh();
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
  if (!state.selectedNodeId && state.graph?.nodes?.length) {
    state.selectedNodeId = state.graph.nodes[0].instance_id;
  }

  $("detail-title").textContent = state.run.run_id;
  $("detail-subtitle").textContent = `${state.run.workflow} · ${state.run.version}`;
  $("detail-summary").innerHTML = `
    <strong>Status:</strong> ${state.run.status}
    <br />
    <strong>Started:</strong> ${formatStarted(state.run.started_at)}
    <br />
    <strong>Finished:</strong> ${state.run.finished_at ? formatStarted(state.run.finished_at) : "pending"}
  `;

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
    stopDetailRefresh();
    state.selectedRunId = null;
    state.selectedNodeId = state.graph?.nodes?.[0]?.instance_id ?? null;
    state.run = null;
    state.timeline = [];
    state.errors = [];
    state.trace = [];
    state.nodeItems = [];
    state.streamText = "";
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
  await loadWorkflowHistory();
}

$("refresh-button").addEventListener("click", () => refresh().catch(console.error));
$("items-tab").addEventListener("click", () => setTab("items"));
$("stream-tab").addEventListener("click", () => setTab("stream"));
setTab("items");

refresh().catch((error) => {
  $("detail-summary").textContent = error.message;
  console.error(error);
});
