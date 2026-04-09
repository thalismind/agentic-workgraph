import { $, formatDuration, formatNodeLabel, state } from "./state.js";

const WORKGRAPH_NODE_TYPE = "workgraph/runtime";

let liteGraphRegistered = false;
let liteGraphGraph = null;
let liteGraphCanvas = null;
let resizeObserver = null;
const nodePositionsByGraph = new Map();

function liteGraphApi() {
  const { LiteGraph, LGraph, LGraphCanvas } = window;
  if (!LiteGraph || !LGraph || !LGraphCanvas) return null;
  return { LiteGraph, LGraph, LGraphCanvas };
}

function getNodeRuntime(instanceId) {
  return state.run?.nodes?.[instanceId] ?? null;
}

function getNodeProgress(node) {
  if (node?.items?.length) {
    return node.items.reduce((sum, item) => sum + (item.progress ?? 0), 0) / node.items.length;
  }
  const counters = node?.counters;
  if (!counters?.total) return 0;
  return Math.max(0, Math.min(1, counters.completed / counters.total));
}

function graphKey() {
  if (!state.graph) return null;
  return `${state.graph.workflow}:${state.graph.version}`;
}

function saveCurrentPositions() {
  if (!liteGraphGraph) return;
  const key = graphKey();
  if (!key) return;
  const positions = new Map();
  for (const node of liteGraphGraph._nodes || []) {
    const instanceId = node.properties?.instanceId;
    if (!instanceId || !Array.isArray(node.pos)) continue;
    positions.set(instanceId, [node.pos[0], node.pos[1]]);
  }
  nodePositionsByGraph.set(key, positions);
}

function statusTheme(status, streaming) {
  if (status === "failed") {
    return {
      color: "#7d2f39",
      bgcolor: "#f9eaed",
      boxcolor: "#9d4951",
      text: "#3d1111",
      muted: "#7a3131",
      titleText: "#f0e6d3",
    };
  }
  if (status === "completed") {
    return {
      color: "#083f34",
      bgcolor: "#e2f1ec",
      boxcolor: "#0a4a3c",
      text: "#112520",
      muted: "#42635a",
      titleText: "#f0e6d3",
    };
  }
  if (status === "running" || streaming) {
    return {
      color: "#9b6c11",
      bgcolor: "#fff5da",
      boxcolor: "#d4a537",
      text: "#402d11",
      muted: "#7d6337",
      titleText: "#1a1a1a",
    };
  }
  return {
    color: "#31213f",
    bgcolor: "#efebf5",
    boxcolor: "#6a5a7d",
    text: "#1d2130",
    muted: "#645d72",
    titleText: "#f0e6d3",
  };
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
      positions.set(id, { x: 48 + level * 280, y: 52 + index * 168 });
    });
  }

  const maxLevel = Math.max(0, ...lanes.keys());
  const maxLane = Math.max(1, ...Array.from(lanes.values()).map((value) => value.length));
  return {
    positions,
    width: 320 + maxLevel * 280,
    height: 196 + maxLane * 168,
  };
}

function ensureLiteGraphRegistration() {
  const api = liteGraphApi();
  if (!api || liteGraphRegistered) return api;
  const { LiteGraph } = api;
  LiteGraph.NODE_TITLE_COLOR = "#f0e6d3";
  LiteGraph.NODE_SELECTED_TITLE_COLOR = "#f0e6d3";
  LiteGraph.NODE_TEXT_COLOR = "#16211f";
  LiteGraph.NODE_SUBTEXT_COLOR = "#566460";
  LiteGraph.NODE_BOX_OUTLINE_COLOR = "#00e5b0";
  LiteGraph.LINK_COLOR = "#3a2b4c";
  LiteGraph.EVENT_LINK_COLOR = "#d4a537";
  LiteGraph.CONNECTING_LINK_COLOR = "#00e5b0";

  function WorkgraphRuntimeNode() {
    this.size = [220, 118];
    this.properties = {
      instanceId: "",
      nodeId: "",
      fullTitle: "",
      metaLine: "",
      durationLine: "",
      progress: 0,
      loopIterations: null,
      streaming: false,
      selected: false,
      theme: statusTheme("pending", false),
    };
  }

  WorkgraphRuntimeNode.title = "Workgraph";
  WorkgraphRuntimeNode.prototype.onDrawForeground = function onDrawForeground(ctx) {
    const properties = this.properties || {};
    const theme = properties.theme || statusTheme("pending", false);
    const width = this.size[0];
    const progress = Math.max(0, Math.min(1, properties.progress ?? 0));

    ctx.save();
    ctx.font = "700 12px system-ui, sans-serif";
    ctx.fillStyle = theme.text;
    ctx.fillText(properties.metaLine || "", 14, 42, width - 28);

    ctx.font = "12px system-ui, sans-serif";
    ctx.fillStyle = theme.muted;
    ctx.fillText(properties.durationLine || "", 14, 60, width - 28);

    if (properties.loopIterations && properties.loopIterations > 1) {
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.fillStyle = "#8b6722";
      ctx.fillText(`↺ ${properties.loopIterations}x`, 14, 78, 48);
      ctx.beginPath();
      ctx.strokeStyle = "#d4a537";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.moveTo(42, 72);
      ctx.bezierCurveTo(4, 42, width - 4, 42, width - 42, 72);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const barX = 118;
    const barY = 70;
    const barWidth = width - barX - 16;
    ctx.fillStyle = "rgba(64, 78, 97, 0.12)";
    ctx.beginPath();
    ctx.roundRect(barX, barY, barWidth, 9, 4.5);
    ctx.fill();
    ctx.fillStyle = theme.boxcolor;
    ctx.beginPath();
    ctx.roundRect(barX, barY, Math.max(6, barWidth * progress), 9, 4.5);
    ctx.fill();

    if (properties.streaming) {
      ctx.font = "700 14px system-ui, sans-serif";
      ctx.fillStyle = "#1a1a1a";
      ctx.fillText("...", width - 34, 22, 20);
    }
    ctx.restore();
  };

  LiteGraph.registerNodeType(WORKGRAPH_NODE_TYPE, WorkgraphRuntimeNode);
  liteGraphRegistered = true;
  return api;
}

function resizeCanvas() {
  if (!liteGraphCanvas) return;
  const container = $("graph-canvas");
  const canvas = $("graph-canvas-surface");
  const width = Math.max(520, Math.floor(container.clientWidth || 520));
  const height = Math.max(280, Math.floor(container.clientHeight || 280));
  canvas.style.height = `${height}px`;
  liteGraphCanvas.resize(width, height);
}

function ensureCanvas(onSelectNode) {
  const api = ensureLiteGraphRegistration();
  if (!api) return null;
  const { LGraph, LGraphCanvas } = api;

  if (!liteGraphGraph) {
    liteGraphGraph = new LGraph();
  }
  if (!liteGraphCanvas) {
    const canvas = $("graph-canvas-surface");
    liteGraphCanvas = new LGraphCanvas(canvas, liteGraphGraph, { skip_rendering: false, autoresize: false });
    liteGraphCanvas.background_image = null;
    liteGraphCanvas.clear_background_color = "#dfe8ea";
    liteGraphCanvas.render_shadows = false;
    liteGraphCanvas.show_info = false;
    liteGraphCanvas.allow_dragcanvas = true;
    liteGraphCanvas.allow_dragnodes = true;
    liteGraphCanvas.allow_searchbox = false;
    liteGraphCanvas.allow_reconnect_links = false;
    liteGraphCanvas.multi_select = false;
    liteGraphCanvas.default_link_color = "#3a2b4c";
    liteGraphCanvas.default_connection_color = {
      input_off: "#95a09d",
      input_on: "#00e5b0",
      output_off: "#95a09d",
      output_on: "#00e5b0",
    };
    liteGraphCanvas.default_connection_color_byType = {
      "": "#3a2b4c",
      "-1": "#d4a537",
    };
    liteGraphCanvas.default_connection_color_byTypeOff = {
      "": "#9e96ab",
      "-1": "#e0b85e",
    };
    liteGraphCanvas.onNodeSelected = (node) => {
      const instanceId = node?.properties?.instanceId;
      if (instanceId && instanceId !== state.selectedNodeId) {
        onSelectNode(instanceId);
      }
    };
    canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  } else {
    liteGraphCanvas.onNodeSelected = (node) => {
      const instanceId = node?.properties?.instanceId;
      if (instanceId && instanceId !== state.selectedNodeId) {
        onSelectNode(instanceId);
      }
    };
  }

  if (!resizeObserver && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => resizeCanvas());
    resizeObserver.observe($("graph-canvas"));
  }

  resizeCanvas();
  return api;
}

function setGraphVisibility(showGraph) {
  $("graph-canvas-surface").classList.toggle("hidden", !showGraph);
  $("graph-canvas-empty").classList.toggle("hidden", showGraph);
}

function buildNodeProperties(node) {
  const runtime = getNodeRuntime(node.instance_id);
  const status = runtime?.status ?? node.status ?? "pending";
  const streaming = state.streamingNodes.has(node.instance_id);
  const counters = runtime?.counters;
  const progress = getNodeProgress(runtime);
  const theme = statusTheme(status, streaming);

  const metaLine = counters && counters.total > 1
    ? `✓ ${counters.completed} · ▸ ${counters.running} · ✗ ${counters.failed}`
    : status;

  return {
    instanceId: node.instance_id,
    nodeId: node.node_id,
    fullTitle: node.node_id,
    metaLine,
    durationLine: runtime?.duration_ms != null ? formatDuration(runtime.duration_ms) : "waiting",
    progress,
    loopIterations: node.loop_iterations ?? null,
    streaming,
    selected: state.selectedNodeId === node.instance_id,
    theme,
  };
}

export function renderGraph({ onSelectNode }) {
  const container = $("graph-canvas");
  const empty = $("graph-canvas-empty");
  const warningContainer = $("graph-warnings");

  warningContainer.replaceChildren();
  if (!state.graph) {
    empty.textContent = "No workflow selected.";
    $("graph-caption").textContent = "Select a workflow or run to render the graph.";
    warningContainer.classList.add("hidden");
    setGraphVisibility(false);
    if (liteGraphGraph) {
      liteGraphGraph.clear();
    }
    return;
  }

  const api = ensureCanvas(onSelectNode);
  if (!api) {
    empty.textContent = "LiteGraph failed to load.";
    setGraphVisibility(false);
    return;
  }
  const { LiteGraph } = api;

  $("graph-caption").textContent = `${state.graph.workflow} · ${state.graph.version}`;
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

  saveCurrentPositions();
  const key = graphKey();
  const savedPositions = nodePositionsByGraph.get(key) ?? new Map();
  const layout = computeGraphLayout(state.graph);
  const previousScale = liteGraphCanvas?.ds?.scale ?? 0.8;
  const previousOffset = liteGraphCanvas?.ds?.offset ? [...liteGraphCanvas.ds.offset] : [24, 24];
  const nodeMap = new Map();

  liteGraphGraph.clear();

  for (const nodeData of state.graph.nodes) {
    const graphNode = LiteGraph.createNode(WORKGRAPH_NODE_TYPE);
    if (!graphNode) continue;
    const defaults = layout.positions.get(nodeData.instance_id) ?? { x: 32, y: 32 };
    const stored = savedPositions.get(nodeData.instance_id);
    graphNode.pos = stored ? [...stored] : [defaults.x, defaults.y];
    graphNode.title = formatNodeLabel(nodeData.node_id, 28).text;
    graphNode.properties = buildNodeProperties(nodeData);
    graphNode.color = graphNode.properties.theme.color;
    graphNode.bgcolor = graphNode.properties.theme.bgcolor;
    graphNode.boxcolor = graphNode.properties.theme.boxcolor;
    graphNode.title_text_color = graphNode.properties.theme.titleText;
    graphNode.textcolor = graphNode.properties.theme.text;
    graphNode.addOutput("", "");
    for (const _dependency of nodeData.depends_on) {
      graphNode.addInput("", "");
    }
    if (nodeData.loop_iterations && nodeData.loop_iterations > 1) {
      graphNode.addInput("loop", "");
    }
    graphNode.setSize([220, Math.max(graphNode.size[1], 118)]);
    liteGraphGraph.add(graphNode);
    nodeMap.set(nodeData.instance_id, graphNode);
  }

  const sourceMap = new Map(state.graph.nodes.map((node) => [node.instance_id, node]));
  for (const edge of state.graph.edges) {
    const from = nodeMap.get(edge.from);
    const to = nodeMap.get(edge.to);
    const targetInfo = sourceMap.get(edge.to);
    if (!from || !to || !targetInfo) continue;
    const targetSlot = Math.max(0, targetInfo.depends_on.indexOf(edge.from));
    from.connect(0, to, targetSlot);
  }

  for (const nodeData of state.graph.nodes) {
    if (!nodeData.loop_iterations || nodeData.loop_iterations < 2) continue;
    const graphNode = nodeMap.get(nodeData.instance_id);
    if (!graphNode) continue;
    const loopSlot = Math.max(0, graphNode.inputs.length - 1);
    graphNode.connect(0, graphNode, loopSlot);
  }

  setGraphVisibility(true);
  resizeCanvas();
  const hasStoredPositions = savedPositions.size > 0;
  liteGraphCanvas.ds.scale = hasStoredPositions ? previousScale : 1;
  liteGraphCanvas.ds.offset = hasStoredPositions ? previousOffset : [12, 18];
  liteGraphCanvas.setDirty(true, true);
  container.classList.remove("empty-state");
}
