import { state } from "./state.js";

export function parseHashRoute() {
  const rawHash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : window.location.hash;
  const params = new URLSearchParams(rawHash);
  return {
    workflow: params.get("workflow"),
    version: params.get("version"),
    run: params.get("run"),
  };
}

export function routeMatchesState(route) {
  return (
    (route.workflow || null) === (state.selectedWorkflow || null) &&
    (route.version || null) === (state.selectedVersion || null) &&
    (route.run || null) === (state.selectedRunId || null)
  );
}

function buildHashRoute() {
  const params = new URLSearchParams();
  if (state.selectedWorkflow) params.set("workflow", state.selectedWorkflow);
  if (state.selectedVersion) params.set("version", state.selectedVersion);
  if (state.selectedRunId) params.set("run", state.selectedRunId);
  const route = params.toString();
  return route ? `#${route}` : "";
}

export function syncHashFromState(replace = false) {
  if (state.applyingHashRoute) return;
  const nextHash = buildHashRoute();
  if (window.location.hash === nextHash) return;
  if (replace) {
    const route = `${window.location.pathname}${window.location.search}${nextHash}`;
    window.history.replaceState(null, "", route);
    return;
  }
  window.location.hash = nextHash;
}
