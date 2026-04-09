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
  stopWorkflowsRefresh,
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

const ARTIFACT_HIGHLIGHT_KEYS = [
  "identified_as",
  "stdout",
  "response",
  "result",
  "output",
  "content",
  "summary",
  "llm_review",
];

const ARTIFACT_CONTEXT_KEYS = [
  "prompt",
  "input",
  "inputs",
  "command",
  "provider",
  "model",
  "system_prompt",
  "system_prompt_path",
  "tools",
];

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function truncateArtifactText(value, limit = 900) {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n...`;
}

function formatArtifactValue(value, limit = 900) {
  if (typeof value === "string") {
    return truncateArtifactText(value, limit);
  }
  return truncateArtifactText(JSON.stringify(value, null, 2), limit);
}

function buildArtifactPreview(artifact) {
  if (!isPlainObject(artifact)) {
    return {
      highlights: [],
      preview: formatArtifactValue(artifact, 1800),
    };
  }

  const highlights = [];
  const previewObject = {};

  for (const key of ARTIFACT_HIGHLIGHT_KEYS) {
    if (artifact[key] === undefined || artifact[key] === null || artifact[key] === "") continue;
    highlights.push({
      label: key.replaceAll("_", " "),
      value: formatArtifactValue(artifact[key], key === "stdout" ? 1200 : 500),
    });
    previewObject[key] = artifact[key];
  }

  const remainingKeys = Object.keys(artifact).filter(
    (key) => !(key in previewObject) && !ARTIFACT_CONTEXT_KEYS.includes(key),
  );
  for (const key of remainingKeys) {
    previewObject[key] = artifact[key];
  }

  const hiddenKeys = Object.keys(artifact).filter((key) => ARTIFACT_CONTEXT_KEYS.includes(key));
  if (hiddenKeys.length) {
    previewObject._hidden_context_fields = hiddenKeys;
  }

  return {
    highlights,
    preview: JSON.stringify(previewObject, null, 2),
  };
}

function jsonPrimitiveClass(value) {
  if (value === null) return "json-null";
  if (typeof value === "string") return "json-string";
  if (typeof value === "number") return "json-number";
  if (typeof value === "boolean") return "json-boolean";
  return "";
}

function formatJsonPrimitive(value) {
  if (typeof value === "string") return JSON.stringify(value);
  if (value === null) return "null";
  return String(value);
}

function createJsonKeyNode(key) {
  const fragment = document.createDocumentFragment();
  if (key != null) {
    const keyNode = document.createElement("span");
    keyNode.className = "json-key";
    keyNode.textContent = `${JSON.stringify(String(key))}: `;
    fragment.append(keyNode);
  }
  return fragment;
}

function createJsonTree(value, key = null) {
  if (value !== null && typeof value === "object") {
    const isArray = Array.isArray(value);
    const entries = isArray ? value.map((item, index) => [index, item]) : Object.entries(value);
    const details = document.createElement("details");
    details.className = "json-branch";
    details.open = true;

    const summary = document.createElement("summary");
    summary.className = "json-branch-summary";
    summary.append(createJsonKeyNode(key));

    const opener = document.createElement("span");
    opener.className = "json-punctuation";
    opener.textContent = isArray ? "[" : "{";
    summary.append(opener);

    const meta = document.createElement("span");
    meta.className = "json-summary";
    if (entries.length === 0) {
      meta.textContent = isArray ? "]" : "}";
    } else {
      meta.textContent = ` ${entries.length} item${entries.length === 1 ? "" : "s"} `;
      const closer = document.createElement("span");
      closer.className = "json-punctuation";
      closer.textContent = isArray ? "]" : "}";
      summary.append(meta, closer);
    }
    if (entries.length === 0) {
      summary.append(meta);
    }

    details.append(summary);
    if (entries.length > 0) {
      const children = document.createElement("div");
      children.className = "json-children";
      for (const [childKey, childValue] of entries) {
        children.append(createJsonTree(childValue, childKey));
      }
      details.append(children);
    }
    return details;
  }

  const leaf = document.createElement("div");
  leaf.className = "json-leaf";
  leaf.append(createJsonKeyNode(key));
  const valueNode = document.createElement("span");
  valueNode.className = jsonPrimitiveClass(value);
  valueNode.textContent = formatJsonPrimitive(value);
  leaf.append(valueNode);
  return leaf;
}

function renderJsonValue(container, value, { compact = false } = {}) {
  container.replaceChildren();
  const root = document.createElement("div");
  root.className = `json-viewer${compact ? " compact" : ""}`;
  root.append(createJsonTree(value));
  container.append(root);
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

function parseJsonField(fieldId, fallbackLabel) {
  const raw = $(fieldId).value.trim();
  if (!raw) return fallbackLabel === "args" ? [] : {};
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`Invalid ${fallbackLabel} JSON: ${error.message}`);
  }
}

function setLaunchMenuError(message = "") {
  const node = $("run-workflow-menu-error");
  node.textContent = message;
  node.classList.toggle("hidden", !message);
}

function workflowGraphName(workflowName) {
  return workflowName?.replace(/^thalis-/, "") ?? "";
}

function normalizeNextInputValue(key, value) {
  if (key === "selector" && typeof value === "string") {
    return [value];
  }
  return value;
}

function applyNextInputsToLaunch(nextInputs = {}) {
  const kwargs = parseJsonField("run-workflow-kwargs", "kwargs");
  if (kwargs === null || Array.isArray(kwargs) || typeof kwargs !== "object") {
    throw new Error("kwargs JSON must parse to an object");
  }
  const allowed = new Set((state.launchSpec?.params ?? []).map((param) => param.name));
  const merged = { ...kwargs };
  for (const [key, value] of Object.entries(nextInputs)) {
    if (key === "downstream_graph" || !allowed.has(key)) continue;
    merged[key] = normalizeNextInputValue(key, value);
  }
  $("run-workflow-kwargs").value = serializeLaunchJson(merged, {});
  state.launchInputsDirty = true;
}

function renderUpstreamLaunchHelper() {
  const wrapper = $("upstream-launch-helper");
  const select = $("upstream-result-select");
  const hint = $("upstream-result-hint");
  const currentGraph = workflowGraphName(state.selectedWorkflow);
  const options = state.upstreamOptions.filter((option) => {
    const downstreamGraph = option.nextInputs?.downstream_graph;
    return !downstreamGraph || downstreamGraph === currentGraph;
  });
  const supported = Boolean(state.selectedWorkflow);
  wrapper.classList.toggle("hidden", !supported);
  select.replaceChildren();

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = state.upstreamOptionsLoading
    ? "Loading recent upstream artifacts..."
    : "Choose a recent upstream artifact";
  select.append(placeholder);

  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.runId;
    element.textContent = option.label;
    select.append(element);
  }

  if (state.upstreamOptionsError) {
    hint.textContent = state.upstreamOptionsError;
  } else if (!options.length) {
    hint.textContent = "No compatible upstream artifacts found for this workflow.";
  } else {
    hint.textContent = "Selecting an upstream artifact will merge its next_inputs into kwargs.";
  }
}

function serializeLaunchJson(value, fallback) {
  return JSON.stringify(value ?? fallback, null, 2);
}

function setLaunchInputs(args = [], kwargs = {}, { force = false } = {}) {
  if (state.launchInputsDirty && !force) return;
  $("run-workflow-args").value = serializeLaunchJson(args, []);
  $("run-workflow-kwargs").value = serializeLaunchJson(kwargs, {});
  state.launchInputsDirty = false;
}

function syncLaunchInputsFromRun({ force = false } = {}) {
  if (!state.run) {
    setLaunchInputs([], {}, { force });
    return;
  }
  setLaunchInputs(state.run.workflow_args ?? [], state.run.workflow_kwargs ?? {}, { force });
}

function renderLaunchMenu() {
  const menu = $("run-workflow-menu");
  const button = $("run-workflow-menu-button");
  menu.classList.toggle("hidden", !state.launchMenuOpen);
  button.setAttribute("aria-expanded", String(state.launchMenuOpen));
  renderUpstreamLaunchHelper();
}

function setLaunchMenuOpen(open) {
  state.launchMenuOpen = open;
  if (!open) setLaunchMenuError("");
  renderLaunchMenu();
  if (open) {
    refreshUpstreamOptions().catch((error) => {
      state.upstreamOptionsError = error.message;
      renderUpstreamLaunchHelper();
      console.error(error);
    });
  }
}

async function refreshUpstreamOptions() {
  if (!state.selectedWorkflow) {
    state.upstreamOptions = [];
    state.upstreamOptionsError = "";
    renderUpstreamLaunchHelper();
    return;
  }
  state.upstreamOptionsLoading = true;
  state.upstreamOptionsError = "";
  renderUpstreamLaunchHelper();
  try {
    const workflowSummaries = await fetchJson("/api/workflows");
    const runsByWorkflow = await Promise.all(
      workflowSummaries.map(async (workflow) => {
        const payload = await fetchJson(`/api/workflows/${workflow.name}/runs`);
        return payload.runs
          .filter((run) => run.status === "completed")
          .slice(0, 3)
          .map((run) => ({ workflow: workflow.name, run }));
      }),
    );
    const recentRuns = runsByWorkflow.flat().slice(0, 12);
    const artifacts = await Promise.all(
      recentRuns.map(async ({ workflow, run }) => {
        const payload = await fetchJson(`/api/runs/${run.run_id}/artifact`);
        const artifact = payload.artifact;
        const manifest = payload.manifest;
        if (!artifact || !manifest?.next_inputs) return null;
        return {
          runId: run.run_id,
          workflow,
          runName: artifact.run_name ?? run.run_id,
          summary: artifact.summary ?? "",
          nextInputs: manifest.next_inputs,
          label: `${workflow} · ${artifact.run_name ?? run.run_id}`,
        };
      }),
    );
    state.upstreamOptions = artifacts.filter(Boolean);
  } finally {
    state.upstreamOptionsLoading = false;
    renderUpstreamLaunchHelper();
  }
}

function setSectionCollapsed(key, collapsed) {
  state.collapsedSections[key] = collapsed;
}

function toggleSection(key) {
  setSectionCollapsed(key, !state.collapsedSections[key]);
  renderCollapsedSections();
}

function renderCollapsedSections() {
  const sections = [
    {
      key: "finalArtifact",
      buttonId: "toggle-final-artifact",
      contentId: "final-artifact",
      label: "artifact",
    },
    {
      key: "itemsList",
      buttonId: "toggle-items-list",
      contentId: "items-list",
      label: "items",
    },
    {
      key: "traceList",
      buttonId: "toggle-trace-list",
      contentId: "trace-list",
      label: "traces",
    },
  ];
  for (const section of sections) {
    const collapsed = Boolean(state.collapsedSections[section.key]);
    const button = $(section.buttonId);
    const content = $(section.contentId);
    if (!button || !content) continue;
    button.textContent = collapsed
      ? (section.collapsedLabel ?? `Show ${section.label}`)
      : (section.expandedLabel ?? "Collapse");
    button.setAttribute("aria-expanded", String(!collapsed));
    content.classList.toggle("hidden", collapsed);
    content.classList.toggle("collapsible-content", true);
  }
}

function setDetailFocus(enabled) {
  $("main-layout").classList.toggle("detail-focus", enabled);
  renderLayoutControls();
}

function renderWorkflows() {
  const workflowsList = $("workflows-list");
  const template = $("workflow-card-template");
  workflowsList.replaceChildren();
  const filter = state.workflowFilter.trim().toLowerCase();
  const visibleWorkflows = state.workflows.filter((workflow) =>
    !filter || workflow.name.toLowerCase().includes(filter),
  );
  $("workflow-count").textContent = String(visibleWorkflows.length);

  if (visibleWorkflows.length === 0) {
    workflowsList.innerHTML = `<div class="empty-state">No workflows match this search.</div>`;
    return;
  }

  for (const workflow of visibleWorkflows) {
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
    const itemKey = mapped.key ?? `${containerId}:${mapped.title}:${mapped.meta}`;
    node.querySelector(".detail-item-title").textContent = mapped.title;
    node.querySelector(".detail-item-meta").textContent = mapped.meta;
    const body = node.querySelector(".detail-item-body");
    body.replaceChildren();
    body.classList.toggle("detail-item-body-code", Boolean(mapped.bodyCode));
    if (mapped.jsonValue !== undefined) {
      renderJsonValue(body, mapped.jsonValue, { compact: true });
    } else {
      body.textContent = mapped.body;
    }
    const head = node.querySelector(".detail-item-head");
    const expanded = state.expandedDetailItems.has(itemKey);
    node.classList.toggle("collapsed", !expanded);
    head.setAttribute("aria-expanded", String(expanded));
    if (mapped.active) node.classList.add("active");
    head.addEventListener("click", async () => {
      if (state.expandedDetailItems.has(itemKey)) {
        state.expandedDetailItems.delete(itemKey);
      } else {
        state.expandedDetailItems.add(itemKey);
      }
      if (mapped.onClick) {
        await mapped.onClick();
      } else {
        renderDetailPanels();
      }
    });
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
      key: `node-item:${state.selectedNodeId}:${item.index}`,
      title: `item ${item.index}`,
      meta: `${item.status} · ${formatDuration(item.duration_ms)}`,
      jsonValue: {
        output: item.output,
        progress: item.progress ?? 0,
        progress_desc: item.progress_desc ?? null,
      },
      bodyCode: true,
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
    $("final-artifact").textContent = "No terminal artifact recorded.";
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

  const artifactPanel = $("final-artifact");
  if (!state.run.final_output?.length) {
    artifactPanel.textContent = "No terminal artifact recorded.";
    renderCollapsedSections();
    return;
  }
  const artifact = state.run.final_output[0];
  artifactPanel.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = "Final Artifact";
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.textContent = `Terminal node: ${state.run.final_node_id ?? "unknown"}`;
  const viewer = document.createElement("div");
  renderJsonValue(viewer, artifact);
  artifactPanel.append(heading, meta, viewer);
  renderCollapsedSections();
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
      key: `timeline:${item.node_id}`,
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
      key: `error:${item.node_id}:${item.item_index ?? "na"}:${item.timestamp ?? item.message}`,
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
      key: `trace:${item.name}:${item.start_time ?? item.end_time ?? item.status}`,
      title: item.name,
      meta: item.status,
      jsonValue: item.attributes ?? {},
      bodyCode: true,
    }),
  );
  renderCollapsedSections();
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

async function reconcileSelectedRun(runId) {
  if (!runId || state.selectedRunId !== runId) return;
  await refreshRunDetailData();
  $("detail-title").textContent = state.run.run_id;
  $("detail-subtitle").textContent = `${state.run.workflow} · ${state.run.version}`;
  updateRunSummary();
  renderDetailPanels();
  await loadNodeInspector();

  if (!state.selectedWorkflow) return;
  const runsPayload = await fetchJson(
    `/api/workflows/${state.selectedWorkflow}/runs${
      state.selectedVersion ? `?version=${encodeURIComponent(state.selectedVersion)}` : ""
    }`,
  );
  renderRuns(runsPayload);
  setActiveButton($("runs-list"), state.selectedRunId);
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
  const subscribedRunId = state.selectedRunId;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${window.location.host}/api/runs/${subscribedRunId}/ws`);
  state.ws.onopen = () => {
    state.wsConnected = true;
    renderWsStatus();
  };
  state.ws.onmessage = (message) => applyEvent(JSON.parse(message.data));
  state.ws.onclose = () => {
    state.ws = null;
    state.wsConnected = false;
    renderWsStatus();
    reconcileSelectedRun(subscribedRunId).catch(console.error);
  };
}

async function loadRunDetail() {
  if (!state.selectedRunId) {
    $("detail-summary").textContent = "No run selected.";
    setLaunchInputs([], {}, { force: true });
    return;
  }

  await refreshRunDetailData();
  state.streamingNodes.clear();
  if (!state.selectedNodeId && state.graph?.nodes?.length) {
    state.selectedNodeId = state.graph.nodes[0].instance_id;
  }

  $("detail-title").textContent = state.run.run_id;
  $("detail-subtitle").textContent = `${state.run.workflow} · ${state.run.version}`;
  syncLaunchInputsFromRun();
  updateRunSummary();

  renderDetailPanels();
  await loadNodeInspector();
  connectRunSocket();
  syncHashFromState(state.applyingHashRoute);
}

async function loadWorkflowHistory() {
  if (!state.selectedWorkflow) return;

  const [versionsPayload, graphPayload, launchSpecPayload] = await Promise.all([
    fetchJson(`/api/workflows/${state.selectedWorkflow}/versions`),
    fetchJson(`/api/workflows/${state.selectedWorkflow}/graph`),
    fetchJson(`/api/workflows/${state.selectedWorkflow}/launch-spec`),
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
  state.launchSpec = launchSpecPayload;
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
    setLaunchInputs([], {}, { force: true });
    $("detail-summary").textContent = "No run selected.";
    renderDetailPanels();
  }

  syncHashFromState(state.applyingHashRoute);
}

async function waitForRunInWorkflowHistory(workflowName, runId, attempts = 12, delayMs = 250) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const runsPayload = await fetchJson(`/api/workflows/${workflowName}/runs`);
    if (runsPayload.runs.some((run) => run.run_id === runId)) {
      return true;
    }
    if (attempt < attempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  return false;
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

function workflowSummarySignature(workflow) {
  return JSON.stringify({
    name: workflow.name,
    current_version: workflow.current_version,
    version_count: workflow.version_count,
    run_count: workflow.run_count,
    latest_run_id: workflow.latest_run?.run_id ?? null,
    latest_run_status: workflow.latest_run?.status ?? null,
    latest_run_version: workflow.latest_run?.version ?? null,
  });
}

function workflowsChanged(nextWorkflows) {
  if (nextWorkflows.length !== state.workflows.length) return true;
  for (let index = 0; index < nextWorkflows.length; index += 1) {
    if (workflowSummarySignature(nextWorkflows[index]) !== workflowSummarySignature(state.workflows[index])) {
      return true;
    }
  }
  return false;
}

async function refreshWorkflowsInBackground() {
  const nextWorkflows = await fetchJson("/api/workflows");
  const changed = workflowsChanged(nextWorkflows);
  if (!changed) return;

  const selectedWorkflowChanged =
    !!state.selectedWorkflow &&
    workflowSummarySignature(nextWorkflows.find((workflow) => workflow.name === state.selectedWorkflow) ?? {}) !==
      workflowSummarySignature(state.workflows.find((workflow) => workflow.name === state.selectedWorkflow) ?? {});

  state.workflows = nextWorkflows;
  renderWorkflows();
  renderRunButton();

  if (selectedWorkflowChanged && !state.applyingHashRoute) {
    await loadWorkflowHistory();
  }
}

function scheduleWorkflowsRefresh(delayMs = 5000) {
  stopWorkflowsRefresh();
  state.workflowsRefreshTimer = setTimeout(async () => {
    state.workflowsRefreshTimer = null;
    try {
      await refreshWorkflowsInBackground();
    } catch (error) {
      console.error(error);
    } finally {
      scheduleWorkflowsRefresh(delayMs);
    }
  }, delayMs);
}

async function launchWorkflowRun() {
  if (state.launchMenuOpen) {
    return launchWorkflowRunFromMenu();
  }
  return launchWorkflowRunWithPayload();
}

async function launchWorkflowRunWithPayload(payload = { args: [], kwargs: {} }) {
  if (!state.selectedWorkflow || state.launchingRun) return;
  state.launchingRun = true;
  renderRunButton();
  try {
    setLaunchInputs(payload.args ?? [], payload.kwargs ?? {}, { force: true });
    const response = await postJson(`/api/workflows/${state.selectedWorkflow}/runs`, payload);
    const launchedWorkflow = state.selectedWorkflow;
    const launchedRunId = response.run_id;
    state.selectedVersion = null;
    state.selectedRunId = launchedRunId;
    state.selectedNodeId = null;
    state.selectedItemIndex = 0;
    setLaunchMenuOpen(false);
    await waitForRunInWorkflowHistory(launchedWorkflow, launchedRunId);
    await refresh();
  } finally {
    state.launchingRun = false;
    renderRunButton();
  }
}

async function launchWorkflowRunFromMenu() {
  const args = parseJsonField("run-workflow-args", "args");
  const kwargs = parseJsonField("run-workflow-kwargs", "kwargs");
  if (!Array.isArray(args)) {
    throw new Error("args JSON must parse to an array");
  }
  if (kwargs === null || Array.isArray(kwargs) || typeof kwargs !== "object") {
    throw new Error("kwargs JSON must parse to an object");
  }
  await launchWorkflowRunWithPayload({ args, kwargs });
}

$("refresh-button").addEventListener("click", () => refresh().catch(console.error));
$("run-workflow-button").addEventListener("click", () => {
  setLaunchMenuError("");
  launchWorkflowRun().catch((error) => {
    setLaunchMenuError(error.message);
    console.error(error);
  });
});
$("run-workflow-args").addEventListener("input", () => {
  state.launchInputsDirty = true;
});
$("run-workflow-kwargs").addEventListener("input", () => {
  state.launchInputsDirty = true;
});
$("workflow-search").addEventListener("input", (event) => {
  state.workflowFilter = event.target.value ?? "";
  renderWorkflows();
});
$("upstream-result-select").addEventListener("change", async (event) => {
  const runId = event.target.value;
  if (!runId) return;
  const option = state.upstreamOptions.find((item) => item.runId === runId);
  if (!option) return;
  try {
    applyNextInputsToLaunch(option.nextInputs);
    setLaunchMenuError("");
    $("upstream-result-hint").textContent = `Applied next_inputs from ${option.workflow} · ${option.runName}.`;
  } catch (error) {
    setLaunchMenuError(error.message);
    console.error(error);
  }
});
$("run-workflow-menu-button").addEventListener("click", () => {
  setLaunchMenuOpen(!state.launchMenuOpen);
});
$("focus-debugger-button").addEventListener("click", () => setDetailFocus(true));
$("restore-layout-button").addEventListener("click", () => setDetailFocus(false));
$("toggle-final-artifact").addEventListener("click", () => toggleSection("finalArtifact"));
$("toggle-items-list").addEventListener("click", () => toggleSection("itemsList"));
$("toggle-trace-list").addEventListener("click", () => toggleSection("traceList"));
$("items-tab").addEventListener("click", () => setTab("items"));
$("stream-tab").addEventListener("click", () => setTab("stream"));
window.addEventListener("hashchange", () => {
  applyHashRoute().catch(console.error);
});
setTab("items");
renderRunButton();
renderLayoutControls();
renderWsStatus();
renderCollapsedSections();
renderLaunchMenu();
startWsStatusClock();
scheduleWorkflowsRefresh();

refresh().catch((error) => {
  $("detail-summary").textContent = error.message;
  console.error(error);
});
