(function (globalScope) {
  "use strict";

  const BRICKS = [
    { gate: 0, component: "input", displayName: "输入积木", symbol: "IN" },
    { gate: 1, component: "output", displayName: "输出积木", symbol: "OUT" },
    { gate: 2, component: "not_gate", displayName: "非门积木", symbol: "NOT" },
    { gate: 3, component: "and_gate", displayName: "与门积木", symbol: "AND" },
    { gate: 4, component: "or_gate", displayName: "或门积木", symbol: "OR" },
    { gate: 5, component: "nand_gate", displayName: "与非门积木", symbol: "NAND" },
    { gate: 6, component: "nor_gate", displayName: "或非门积木", symbol: "NOR" },
    { gate: 7, component: "xor_gate", displayName: "异或门积木", symbol: "XOR" },
    { gate: 8, component: "xnor_gate", displayName: "同或门积木", symbol: "XNOR" },
  ];

  const SLOT_LAYOUT = [
    { slot: 6, board: 4, cascadeSlot: 7 },
    { slot: 4, board: 3, cascadeSlot: 5 },
    { slot: 2, board: 2, cascadeSlot: 3 },
    { slot: 0, board: 1, cascadeSlot: 1 },
    { slot: 7, board: 4, cascadeSlot: 8 },
    { slot: 5, board: 3, cascadeSlot: 6 },
    { slot: 3, board: 2, cascadeSlot: 4 },
    { slot: 1, board: 1, cascadeSlot: 2 },
    { slot: 8, board: 5, cascadeSlot: 9 },
    { slot: 10, board: 6, cascadeSlot: 11 },
    { slot: 12, board: 7, cascadeSlot: 13 },
    { slot: 14, board: 8, cascadeSlot: 15 },
    { slot: 9, board: 5, cascadeSlot: 10 },
    { slot: 11, board: 6, cascadeSlot: 12 },
    { slot: 13, board: 7, cascadeSlot: 14 },
    { slot: 15, board: 8, cascadeSlot: 16 },
  ];

  const UPPER_PORTS = [
    { side: "top", label: "P3", localPort: 3 },
    { side: "right", label: "P0", localPort: 0 },
    { side: "bottom", label: "P1", localPort: 1 },
    { side: "left", label: "P2", localPort: 2 },
  ];

  const LOWER_PORTS = [
    { side: "top", label: "P5", localPort: 1 },
    { side: "right", label: "P6", localPort: 2 },
    { side: "bottom", label: "P7", localPort: 3 },
    { side: "left", label: "P4", localPort: 0 },
  ];

  const BRICK_BY_COMPONENT = new Map(BRICKS.map((brick) => [brick.component, brick]));
  const NULL_GATE = 9;

  function assertSlot(slot) {
    if (!Number.isInteger(slot) || slot < 0 || slot >= 16) {
      throw new RangeError("slot must be an integer between 0 and 15");
    }
  }

  function assertLocalPort(localPort) {
    if (!Number.isInteger(localPort) || localPort < 0 || localPort >= 4) {
      throw new RangeError("localPort must be an integer between 0 and 3");
    }
  }

  function getGlobalPort(slot, localPort) {
    assertSlot(slot);
    assertLocalPort(localPort);
    return slot * 4 + localPort;
  }

  function getSlotDisplayPorts(slot) {
    assertSlot(slot);
    return (slot % 2 === 0 ? UPPER_PORTS : LOWER_PORTS).map((port) => ({ ...port }));
  }

  function getVisualSide(slot, localPort) {
    assertLocalPort(localPort);
    const port = getSlotDisplayPorts(slot).find((item) => item.localPort === localPort);
    return port ? port.side : "top";
  }

  function normalizeLevel(level) {
    if (!level || !Number.isInteger(level.level_id)) return null;
    const normalized = {
      level_id: level.level_id,
      short_goal: level.short_goal || "",
      input_names: level.input_names || "",
      output_names: level.output_names || "",
      input_count: Number.isInteger(level.input_count) ? level.input_count : 0,
      output_count: Number.isInteger(level.output_count) ? level.output_count : 0,
    };
    return normalized;
  }

  function createState(level) {
    return {
      revision: 0,
      slots: Array(16).fill(null),
      links: [],
      level: normalizeLevel(level),
      subscribers: new Set(),
      nextLinkId: 1,
    };
  }

  function endpointEquals(first, second) {
    return first.slot === second.slot && first.localPort === second.localPort;
  }

  function cloneEndpoint(endpoint) {
    return { slot: endpoint.slot, localPort: endpoint.localPort };
  }

  function getBrickForSlot(state, slot) {
    assertSlot(slot);
    return BRICK_BY_COMPONENT.get(state.slots[slot]) || null;
  }

  function getEndpointRole(state, endpoint) {
    assertSlot(endpoint.slot);
    assertLocalPort(endpoint.localPort);
    const brick = getBrickForSlot(state, endpoint.slot);
    if (!brick) return "unused";
    const side = getVisualSide(endpoint.slot, endpoint.localPort);
    if (brick.component === "input") return "output";
    if (brick.component === "output") return side === "left" ? "input" : "unused";
    if (brick.component === "not_gate") {
      if (side === "left") return "input";
      if (side === "right") return "output";
      return "unused";
    }
    if (side === "right") return "output";
    return side === "top" || side === "bottom" ? "input" : "unused";
  }

  function hasLinkAtEndpoint(state, endpoint) {
    return state.links.some((link) => endpointEquals(link.first, endpoint) || endpointEquals(link.second, endpoint));
  }

  function classifyLink(state, first, second) {
    assertSlot(first.slot);
    assertLocalPort(first.localPort);
    assertSlot(second.slot);
    assertLocalPort(second.localPort);
    if (hasLinkAtEndpoint(state, first) || hasLinkAtEndpoint(state, second)) {
      return { kind: "multiple", error: 4 };
    }
    const firstRole = getEndpointRole(state, first);
    const secondRole = getEndpointRole(state, second);
    if (firstRole === "unused" || secondRole === "unused") return { kind: "unused" };
    if (firstRole === "output" && secondRole === "input") {
      return { kind: "valid", from: cloneEndpoint(first), to: cloneEndpoint(second) };
    }
    if (firstRole === "input" && secondRole === "output") {
      return { kind: "valid", from: cloneEndpoint(second), to: cloneEndpoint(first) };
    }
    return { kind: "direction", error: 3 };
  }

  function notify(state) {
    const snapshot = buildSnapshot(state, state.level);
    state.subscribers.forEach((subscriber) => subscriber(snapshot));
  }

  function bump(state) {
    state.revision += 1;
    notify(state);
  }

  function placeBrick(state, slot, component) {
    assertSlot(slot);
    if (!BRICK_BY_COMPONENT.has(component)) throw new Error(`unknown component: ${component}`);
    state.slots[slot] = component;
    state.links = state.links.filter((link) => link.first.slot !== slot && link.second.slot !== slot);
    bump(state);
  }

  function removeBrick(state, slot) {
    assertSlot(slot);
    if (!state.slots[slot]) return;
    state.slots[slot] = null;
    state.links = state.links.filter((link) => link.first.slot !== slot && link.second.slot !== slot);
    bump(state);
  }

  function clearState(state) {
    if (!state.slots.some(Boolean) && state.links.length === 0) return;
    state.slots = Array(16).fill(null);
    state.links = [];
    bump(state);
  }

  function addLink(state, first, second) {
    const classification = classifyLink(state, first, second);
    const link = {
      id: state.nextLinkId,
      first: cloneEndpoint(first),
      second: cloneEndpoint(second),
      kind: classification.kind,
      error: classification.error,
      from: classification.from,
      to: classification.to,
    };
    state.nextLinkId += 1;
    state.links.push(link);
    bump(state);
    return link;
  }

  function removeLink(state, linkId) {
    const originalLength = state.links.length;
    state.links = state.links.filter((link) => link.id !== linkId);
    if (state.links.length !== originalLength) bump(state);
  }

  function subscribe(state, listener) {
    state.subscribers.add(listener);
    return () => state.subscribers.delete(listener);
  }

  function setLevel(state, level) {
    state.level = normalizeLevel(level);
    notify(state);
  }

  function buildSnapshot(state, level) {
    const normalizedLevel = normalizeLevel(level === undefined ? state.level : level);
    return {
      schema_version: 2,
      topology_revision: state.revision,
      ...(normalizedLevel ? { level: normalizedLevel } : {}),
      slots: state.slots.map((component, slot) => {
        const brick = BRICK_BY_COMPONENT.get(component);
        return brick
          ? {
              slot,
              gate: brick.gate,
              component: brick.component,
              display_name: brick.displayName,
              present: true,
            }
          : {
              slot,
              present: false,
            };
      }),
      valid_links: state.links
        .filter((link) => link.kind === "valid")
        .map((link) => ({ from_slot: link.from.slot, to_slot: link.to.slot })),
      invalid_links: state.links
        .filter((link) => link.kind === "direction" || link.kind === "multiple")
        .map((link) => ({ error: link.error })),
      scan: { ir_scans: 0, i2c_scans: 0 },
    };
  }

  function mount(container, options = {}) {
    if (!container) throw new Error("a simulator container is required");
    const state = createState(options.level);
    let selectedEndpoint = null;
    let highlightedPorts = new Set();
    let highlightedPattern = "pulse";
    let resizeObserver = null;

    function emitSnapshot(snapshot) {
      if (typeof options.onChange === "function") options.onChange(snapshot);
    }

    function render() {
      container.replaceChildren();
      const shell = document.createElement("div");
      shell.className = "simulator-shell";
      const palette = document.createElement("div");
      palette.className = "brick-palette";
      const paletteTitle = document.createElement("p");
      paletteTitle.className = "palette-title";
      paletteTitle.textContent = "虚拟积木";
      palette.appendChild(paletteTitle);

      BRICKS.forEach((brick) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "brick-palette-item";
        item.draggable = true;
        item.dataset.component = brick.component;
        item.innerHTML = `<strong>${brick.symbol}</strong><span>${brick.displayName}</span>`;
        item.addEventListener("dragstart", (event) => {
          event.dataTransfer.effectAllowed = "copy";
          event.dataTransfer.setData("application/x-tuco-brick", brick.component);
        });
        palette.appendChild(item);
      });

      const board = document.createElement("div");
      board.className = "simulator-board";
      const wires = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      wires.classList.add("simulator-wires");
      wires.setAttribute("aria-label", "电路连线");
      board.appendChild(wires);

      SLOT_LAYOUT.forEach((mapping) => {
        const brick = getBrickForSlot(state, mapping.slot);
        const slot = document.createElement("article");
        slot.className = "simulator-slot";
        slot.dataset.slot = String(mapping.slot);
        slot.addEventListener("dragover", (event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
          slot.classList.add("drag-over");
        });
        slot.addEventListener("dragleave", () => slot.classList.remove("drag-over"));
        slot.addEventListener("drop", (event) => {
          event.preventDefault();
          slot.classList.remove("drag-over");
          const component = event.dataTransfer.getData("application/x-tuco-brick");
          if (component) placeBrick(state, mapping.slot, component);
        });

        const label = document.createElement("span");
        label.className = "simulator-slot-label";
        label.textContent = `底板 ${mapping.board} · 槽位 ${mapping.cascadeSlot}`;
        slot.appendChild(label);

        const body = document.createElement("div");
        body.className = "simulator-brick";
        body.innerHTML = brick
          ? `<strong>${brick.symbol}</strong><span>${brick.displayName}</span>`
          : "<span>拖入积木</span>";
        slot.appendChild(body);

        if (brick) {
          const removeButton = document.createElement("button");
          removeButton.type = "button";
          removeButton.className = "simulator-remove-brick";
          removeButton.textContent = "×";
          removeButton.setAttribute("aria-label", `移除${brick.displayName}`);
          removeButton.addEventListener("click", () => removeBrick(state, mapping.slot));
          slot.appendChild(removeButton);
        }

        getSlotDisplayPorts(mapping.slot).forEach((port) => {
          const role = getEndpointRole(state, { slot: mapping.slot, localPort: port.localPort });
          const globalPort = getGlobalPort(mapping.slot, port.localPort);
          const button = document.createElement("button");
          button.type = "button";
          button.className = `simulator-port port-${port.side} port-role-${role}`;
          button.dataset.slot = String(mapping.slot);
          button.dataset.localPort = String(port.localPort);
          button.dataset.globalPort = String(globalPort);
          button.disabled = !brick;
          button.setAttribute("aria-label", `底板 ${mapping.board}，槽位 ${mapping.cascadeSlot}，${port.label}`);
          button.innerHTML = `<i></i><span>${port.label}</span>`;
          if (selectedEndpoint && endpointEquals(selectedEndpoint, { slot: mapping.slot, localPort: port.localPort })) {
            button.classList.add("port-selected");
          }
          if (highlightedPorts.has(globalPort)) {
            button.classList.add("port-highlighted", `port-highlight-${highlightedPattern}`);
          }
          button.addEventListener("click", () => {
            const endpoint = { slot: mapping.slot, localPort: port.localPort };
            if (!selectedEndpoint) {
              selectedEndpoint = endpoint;
              render();
              return;
            }
            if (endpointEquals(selectedEndpoint, endpoint)) {
              selectedEndpoint = null;
              render();
              return;
            }
            addLink(state, selectedEndpoint, endpoint);
            selectedEndpoint = null;
          });
          slot.appendChild(button);
        });
        board.appendChild(slot);
      });

      shell.append(palette, board);
      container.appendChild(shell);
      requestAnimationFrame(drawWires);
    }

    function drawWires() {
      const board = container.querySelector(".simulator-board");
      const wires = container.querySelector(".simulator-wires");
      if (!board || !wires) return;
      const boardRect = board.getBoundingClientRect();
      wires.setAttribute("viewBox", `0 0 ${boardRect.width} ${boardRect.height}`);
      wires.setAttribute("width", String(boardRect.width));
      wires.setAttribute("height", String(boardRect.height));
      wires.replaceChildren();
      state.links.forEach((link) => {
        const first = getPortCenter(board, boardRect, link.first);
        const second = getPortCenter(board, boardRect, link.second);
        if (!first || !second) return;
        const middleX = (first.x + second.x) / 2;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M ${first.x} ${first.y} C ${middleX} ${first.y}, ${middleX} ${second.y}, ${second.x} ${second.y}`);
        path.classList.add("simulator-wire", `wire-${link.kind}`);
        path.dataset.linkId = String(link.id);
        path.addEventListener("click", () => removeLink(state, link.id));
        wires.appendChild(path);
      });
    }

    function getPortCenter(board, boardRect, endpoint) {
      const selector = `.simulator-port[data-slot="${endpoint.slot}"][data-local-port="${endpoint.localPort}"]`;
      const port = board.querySelector(selector);
      if (!port) return null;
      const rect = port.getBoundingClientRect();
      return {
        x: rect.left - boardRect.left + rect.width / 2,
        y: rect.top - boardRect.top + rect.height / 2,
      };
    }

    const unsubscribe = subscribe(state, (snapshot) => {
      render();
      emitSnapshot(snapshot);
    });
    render();
    emitSnapshot(buildSnapshot(state, state.level));
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(() => requestAnimationFrame(drawWires));
      resizeObserver.observe(container);
    }
    globalScope.addEventListener?.("resize", drawWires);

    return {
      state,
      getSnapshot: () => buildSnapshot(state, state.level),
      setLevel: (level) => setLevel(state, level),
      highlightPorts(ports, pattern = "pulse") {
        highlightedPorts = new Set(Array.isArray(ports) ? ports : []);
        highlightedPattern = pattern === "blink" ? "blink" : "pulse";
        render();
      },
      clearHighlights() {
        highlightedPorts = new Set();
        render();
      },
      clear() {
        selectedEndpoint = null;
        clearState(state);
      },
      destroy() {
        unsubscribe();
        resizeObserver?.disconnect();
        globalScope.removeEventListener?.("resize", drawWires);
        container.replaceChildren();
      },
    };
  }

  const api = {
    BRICKS,
    SLOT_LAYOUT,
    addLink,
    buildSnapshot,
    classifyLink,
    clearState,
    createState,
    getEndpointRole,
    getGlobalPort,
    getSlotDisplayPorts,
    mount,
    placeBrick,
    removeBrick,
    removeLink,
    setLevel,
    subscribe,
  };

  globalScope.TucoCircuitSimulator = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
