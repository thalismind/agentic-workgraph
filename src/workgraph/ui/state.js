export const state = {
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
  wsConnected: false,
  lastWsMessageAt: null,
  wsStatusTimer: null,
  traceRefreshTimer: null,
  launchingRun: false,
  applyingHashRoute: false,
  collapsedSections: {
    finalArtifact: false,
    itemsList: false,
    traceList: false,
  },
  expandedDetailItems: new Set(),
};

export const $ = (id) => document.getElementById(id);

export async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${path}`);
  }
  return response.json();
}

export async function postJson(path) {
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${path}`);
  }
  return response.json();
}

export function formatDuration(durationMs) {
  if (durationMs == null) return "pending";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}

export function formatStarted(startedAt) {
  if (!startedAt) return "not started";
  return new Date(startedAt).toLocaleString();
}

export function formatNodeLabel(nodeId, maxLength = 20) {
  if (nodeId.length <= maxLength) return { text: nodeId, truncated: false };
  return { text: `${nodeId.slice(0, maxLength - 1)}…`, truncated: true };
}

export function setActiveButton(container, activeValue) {
  for (const button of container.querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.value === activeValue);
  }
}

export function closeSocket() {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
}

export function stopTraceRefresh() {
  if (state.traceRefreshTimer) {
    clearTimeout(state.traceRefreshTimer);
    state.traceRefreshTimer = null;
  }
}

export function setTab(tab) {
  state.selectedTab = tab;
  $("items-tab").classList.toggle("active", tab === "items");
  $("stream-tab").classList.toggle("active", tab === "stream");
  $("items-panel").classList.toggle("hidden", tab !== "items");
  $("stream-panel").classList.toggle("hidden", tab !== "stream");
}

export function renderRunButton() {
  const button = $("run-workflow-button");
  button.disabled = !state.selectedWorkflow || state.launchingRun;
  button.textContent = state.launchingRun ? "Starting..." : "Run Workflow";
}
