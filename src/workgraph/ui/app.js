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
  if (durationMs == null) {
    return "pending";
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function formatStarted(startedAt) {
  if (!startedAt) {
    return "not started";
  }
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
      state.selectedWorkflow = workflow.name;
      state.selectedVersion = null;
      state.selectedRunId = null;
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      setActiveButton(workflowsList, workflow.name);
      await loadWorkflowHistory();
    });
    if (state.selectedWorkflow === workflow.name) {
      node.classList.add("active");
    }
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
    status.classList.add(run.status);
    node.querySelector(".run-version").textContent = run.version;
    node.querySelector(".run-duration").textContent = formatDuration(run.duration_ms);
    node.querySelector(".run-started").textContent = formatStarted(run.started_at);
    node.querySelector(".run-errors").textContent = `${run.error_count} errors`;
    node.addEventListener("click", async () => {
      closeSocket();
      state.selectedRunId = run.run_id;
      state.selectedNodeId = null;
      state.selectedItemIndex = 0;
      setActiveButton(runsList, run.run_id);
      await loadRunDetail();
    });
    if (state.selectedRunId === run.run_id) {
      node.classList.add("active");
    }
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
    container.append(node);
  }
}

function renderGraph() {
  const container = $("graph-canvas");
  container.replaceChildren();
  if (!state.graph) {
    container.textContent = "No workflow selected.";
    return;
  }

  $("graph-caption").textContent = `${state.graph.workflow} · ${state.graph.version}`;
  const width = Math.max(560, state.graph.nodes.length * 180);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} 210`);
  svg.setAttribute("class", "graph-svg");

  const positions = new Map();
  state.graph.nodes.forEach((node, index) => {
    positions.set(node.instance_id, { x: 40 + index * 170, y: 74 });
  });

  for (const edge of state.graph.edges) {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) continue;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(from.x + 120));
    line.setAttribute("y1", String(from.y + 30));
    line.setAttribute("x2", String(to.x));
    line.setAttribute("y2", String(to.y + 30));
    line.setAttribute("class", "graph-edge");
    svg.append(line);
  }

  for (const node of state.graph.nodes) {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const runNode = state.run?.nodes?.[node.instance_id];
    const status = runNode?.status ?? node.status ?? "pending";
    group.setAttribute("class", `graph-node ${status}${state.selectedNodeId === node.instance_id ? " active" : ""}`);
    group.dataset.value = node.instance_id;
    const { x, y } = positions.get(node.instance_id);

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", "120");
    rect.setAttribute("height", "64");
    rect.setAttribute("rx", "16");
    group.append(rect);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
    title.setAttribute("x", String(x + 12));
    title.setAttribute("y", String(y + 24));
    title.setAttribute("font-size", "13");
    title.setAttribute("font-weight", "700");
    title.textContent = node.node_id;
    group.append(title);

    const meta = document.createElementNS("http://www.w3.org/2000/svg", "text");
    meta.setAttribute("x", String(x + 12));
    meta.setAttribute("y", String(y + 44));
    meta.setAttribute("font-size", "11");
    const counters = runNode?.counters;
    meta.textContent = counters && counters.total > 1
      ? `✓ ${counters.completed} · ▸ ${counters.running} · ✗ ${counters.failed}`
      : status;
    group.append(meta);

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

  const node = state.run?.nodes?.[state.selectedNodeId];
  const status = node?.status ?? "pending";
  summary.innerHTML = `
    <strong>${state.selectedNodeId}</strong>
    <br />
    <span class="muted">Status:</span> ${status}
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
      body: `${JSON.stringify(item.output)}\nprogress: ${item.progress}`,
    }),
  );

  const itemsList = $("items-list");
  Array.from(itemsList.children).forEach((child, index) => {
    child.classList.toggle("active", index === state.selectedItemIndex);
    child.addEventListener("click", async () => {
      state.selectedItemIndex = index;
      renderNodeInspector();
      await loadStream();
    });
  });

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
  await loadStream();
}

function applyEvent(event) {
  if (!state.run || event.run_id !== state.selectedRunId) {
    return;
  }

  if (event.event === "run_status") {
    state.run.status = event.status;
  }

  if (event.node_id && state.run.nodes?.[event.node_id]) {
    const node = state.run.nodes[event.node_id];
    if (event.event === "node_status") {
      node.status = event.status;
    }
    if (event.event === "node_counters") {
      node.counters = event.counters;
    }
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

  renderGraph();
  renderNodeInspector();
}

function connectRunSocket() {
  closeSocket();
  if (!state.selectedRunId || !["running", "pending"].includes(state.run?.status ?? "")) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${window.location.host}/api/runs/${state.selectedRunId}/ws`);
  state.ws.onmessage = (message) => {
    const event = JSON.parse(message.data);
    applyEvent(event);
  };
  state.ws.onclose = () => {
    state.ws = null;
  };
}

async function loadRunDetail() {
  if (!state.selectedRunId) {
    $("detail-summary").textContent = "No run selected.";
    return;
  }

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
  if (!state.selectedNodeId && state.graph?.nodes?.length) {
    state.selectedNodeId = state.graph.nodes[0].instance_id;
  }

  $("detail-title").textContent = run.run_id;
  $("detail-subtitle").textContent = `${run.workflow} · ${run.version}`;
  $("detail-summary").innerHTML = `
    <strong>Status:</strong> ${run.status}
    <br />
    <strong>Started:</strong> ${formatStarted(run.started_at)}
    <br />
    <strong>Finished:</strong> ${run.finished_at ? formatStarted(run.finished_at) : "pending"}
  `;

  renderGraph();
  renderNodeInspector();

  renderDetailItems(
    "timeline-list",
    timeline,
    "No node timing data available.",
    (item) => ({
      title: item.node_id,
      meta: `${item.status} · ${formatDuration(item.duration_ms)}`,
      body: `${formatStarted(item.started_at)} → ${item.finished_at ? formatStarted(item.finished_at) : "pending"}`,
    }),
  );

  renderDetailItems(
    "errors-list",
    errors,
    "No errors recorded.",
    (item) => ({
      title: `${item.node_id}${item.item_index == null ? "" : ` [${item.item_index}]`}`,
      meta: item.error_type,
      body: item.message,
    }),
  );

  renderDetailItems(
    "trace-list",
    trace,
    "No trace spans recorded.",
    (item) => ({
      title: item.name,
      meta: item.status,
      body: JSON.stringify(item.attributes, null, 2),
    }),
  );

  await loadNodeInspector();
  connectRunSocket();
}

async function loadWorkflowHistory() {
  if (!state.selectedWorkflow) {
    return;
  }

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
    state.selectedRunId = null;
    state.selectedNodeId = state.graph?.nodes?.[0]?.instance_id ?? null;
    state.run = null;
    state.nodeItems = [];
    state.streamText = "";
    $("detail-summary").textContent = "No run selected.";
    $("timeline-list").replaceChildren();
    $("errors-list").replaceChildren();
    $("trace-list").replaceChildren();
    renderNodeInspector();
    renderGraph();
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

$("refresh-button").addEventListener("click", () => {
  refresh().catch((error) => {
    console.error(error);
  });
});

$("items-tab").addEventListener("click", () => setTab("items"));
$("stream-tab").addEventListener("click", () => setTab("stream"));
setTab("items");

refresh().catch((error) => {
  $("detail-summary").textContent = error.message;
  console.error(error);
});
