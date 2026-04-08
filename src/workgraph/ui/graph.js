import { $, formatDuration, formatNodeLabel, state } from "./state.js";

function getNodeRuntime(instanceId) {
  return state.run?.nodes?.[instanceId] ?? null;
}

function getNodeProgress(node) {
  if (!node?.items?.length) return 0;
  return node.items.reduce((sum, item) => sum + (item.progress ?? 0), 0) / node.items.length;
}

export function computeGraphLayout(graph) {
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

export function renderGraph({ onSelectNode }) {
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

    const tooltip = document.createElementNS("http://www.w3.org/2000/svg", "title");
    tooltip.textContent = node.node_id;
    group.append(tooltip);

    const clipPath = document.createElementNS("http://www.w3.org/2000/svg", "clipPath");
    const clipId = `node-title-clip-${node.instance_id}`;
    clipPath.setAttribute("id", clipId);
    const clipRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    clipRect.setAttribute("x", String(x + 12));
    clipRect.setAttribute("y", String(y + 8));
    clipRect.setAttribute("width", "126");
    clipRect.setAttribute("height", "18");
    clipPath.append(clipRect);
    svg.append(clipPath);

    const label = formatNodeLabel(node.node_id);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
    title.setAttribute("x", String(x + 12));
    title.setAttribute("y", String(y + 23));
    title.setAttribute("font-size", "13");
    title.setAttribute("font-weight", "700");
    title.setAttribute("clip-path", `url(#${clipId})`);
    title.textContent = label.text;
    group.append(title);

    if (label.truncated) {
      const truncateBadge = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      truncateBadge.setAttribute("cx", String(x + 138));
      truncateBadge.setAttribute("cy", String(y + 18));
      truncateBadge.setAttribute("r", "4");
      truncateBadge.setAttribute("class", "graph-truncate-indicator");
      group.append(truncateBadge);
    }

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

    group.addEventListener("click", () => onSelectNode(node.instance_id));
    svg.append(group);
  }

  container.append(svg);
}
