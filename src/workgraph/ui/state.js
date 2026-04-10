const FAVORITE_WORKFLOWS_STORAGE_KEY = "workgraph.favoriteWorkflows";

function loadFavoriteWorkflows() {
  try {
    const value = globalThis.localStorage?.getItem(FAVORITE_WORKFLOWS_STORAGE_KEY);
    if (!value) return new Set();
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? new Set(parsed.filter((entry) => typeof entry === "string")) : new Set();
  } catch {
    return new Set();
  }
}

export const state = {
  workflows: [],
  favoriteWorkflows: loadFavoriteWorkflows(),
  workflowFilter: "",
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
  workflowsRefreshTimer: null,
  launchingRun: false,
  launchMenuOpen: false,
  launchInputsDirty: false,
  upstreamOptions: [],
  upstreamOptionsLoading: false,
  upstreamOptionsError: "",
  launchSpec: null,
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

export async function postJson(path, body = undefined) {
  const response = await fetch(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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

export function isFavoriteWorkflow(name) {
  return state.favoriteWorkflows.has(name);
}

export function toggleFavoriteWorkflow(name) {
  if (state.favoriteWorkflows.has(name)) {
    state.favoriteWorkflows.delete(name);
  } else {
    state.favoriteWorkflows.add(name);
  }
  try {
    globalThis.localStorage?.setItem(
      FAVORITE_WORKFLOWS_STORAGE_KEY,
      JSON.stringify([...state.favoriteWorkflows].sort()),
    );
  } catch {
    // Ignore storage failures and keep the in-memory preference for this session.
  }
}

export function stopTraceRefresh() {
  if (state.traceRefreshTimer) {
    clearTimeout(state.traceRefreshTimer);
    state.traceRefreshTimer = null;
  }
}

export function stopWorkflowsRefresh() {
  if (state.workflowsRefreshTimer) {
    clearTimeout(state.workflowsRefreshTimer);
    state.workflowsRefreshTimer = null;
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
  const menuButton = $("run-workflow-menu-button");
  button.disabled = !state.selectedWorkflow || state.launchingRun;
  menuButton.disabled = !state.selectedWorkflow || state.launchingRun;
  button.textContent = state.launchingRun ? "Starting..." : "Run Workflow";
  menuButton.textContent = state.launchingRun ? "…" : "▾";
}
