const elements = {
  health: document.querySelector(".service-status"),
  healthText: document.querySelector("#health-text"),
  baseUrl: document.querySelector("#base-url"),
  model: document.querySelector("#model"),
  apiKey: document.querySelector("#api-key"),
  timeout: document.querySelector("#timeout"),
  keyStatus: document.querySelector("#key-status"),
  saveConfig: document.querySelector("#save-config"),
  question: document.querySelector("#question"),
  snapshot: document.querySelector("#snapshot"),
  runDecision: document.querySelector("#run-decision"),
  decisionResult: document.querySelector("#decision-result"),
  decisionTime: document.querySelector("#decision-time"),
  portGrid: document.querySelector("#port-grid"),
  toolSummary: document.querySelector("#tool-summary"),
  connectWs: document.querySelector("#connect-ws"),
  runWsDemo: document.querySelector("#run-ws-demo"),
  protocolLog: document.querySelector("#protocol-log"),
  toast: document.querySelector("#toast"),
};

const sampleSnapshot = {
  schema_version: 1,
  topology_revision: 42,
  slots: [
    {
      slot: 0,
      component: "and_gate",
      ports: [
        { id: 8, role: "input_a" },
        { id: 9, role: "input_b" },
        { id: 10, role: "output" },
      ],
    },
    {
      slot: 1,
      component: "led",
      ports: [
        { id: 20, role: "input" },
        { id: 21, role: "ground" },
      ],
    },
  ],
  valid_links: [
    { from: 2, to: 8 },
    { from: 10, to: 20 },
  ],
  invalid_links: [],
  scan: { count: 135, stable_count: 4 },
};

let websocket = null;
let toastTimer = null;

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2600);
}

function setBusy(button, busy, busyLabel) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

function buildPortGrid() {
  const fragment = document.createDocumentFragment();
  for (let port = 0; port < 64; port += 1) {
    const cell = document.createElement("div");
    cell.className = "port";
    cell.dataset.port = String(port);
    cell.innerHTML = `<span>${String(port).padStart(2, "0")}</span>`;
    fragment.appendChild(cell);
  }
  elements.portGrid.replaceChildren(fragment);
}

function clearPorts() {
  document.querySelectorAll(".port.active").forEach((port) => {
    port.classList.remove("active", "pulse", "blink");
  });
}

function highlightPorts(toolCall) {
  clearPorts();
  if (!toolCall || toolCall.name !== "highlight_ports") {
    elements.toolSummary.textContent = "本次模型没有调用 highlight_ports。";
    return;
  }
  const args = toolCall.arguments;
  args.ports.forEach((port) => {
    document.querySelector(`.port[data-port="${port}"]`)?.classList.add("active", args.pattern);
  });
  elements.toolSummary.textContent = `端口 ${args.ports.join(", ")} · ${args.pattern} · ${args.duration_ms} ms\n${args.reason}`;
  setTimeout(clearPorts, args.duration_ms);
}

async function loadStatus() {
  try {
    const [healthResponse, configResponse] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/config"),
    ]);
    if (!healthResponse.ok || !configResponse.ok) throw new Error("服务响应异常");
    const health = await healthResponse.json();
    const config = await configResponse.json();
    elements.health.classList.add("online");
    elements.healthText.textContent = `服务在线 · v${health.version}`;
    elements.baseUrl.value = config.llm_base_url;
    elements.model.value = config.llm_model;
    elements.timeout.value = config.llm_timeout_seconds;
    elements.keyStatus.textContent = config.llm_api_key_configured
      ? "已配置 API Key。新输入只在当前服务进程内生效。"
      : "尚未配置 API Key。输入后只在当前服务进程内生效。";
  } catch (error) {
    elements.health.classList.add("offline");
    elements.healthText.textContent = "服务不可用";
    showToast(error.message);
  }
}

async function saveConfig() {
  const payload = {
    llm_base_url: elements.baseUrl.value.trim(),
    llm_model: elements.model.value.trim(),
    llm_timeout_seconds: Number(elements.timeout.value),
  };
  if (elements.apiKey.value) payload.llm_api_key = elements.apiKey.value;
  setBusy(elements.saveConfig, true, "应用中…");
  try {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "配置更新失败");
    elements.apiKey.value = "";
    elements.keyStatus.textContent = result.llm_api_key_configured
      ? "已配置 API Key，后端响应不会回显密钥。"
      : "尚未配置 API Key。";
    showToast("模型配置已更新");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.saveConfig, false);
  }
}

async function runDecision() {
  let circuit;
  try {
    circuit = JSON.parse(elements.snapshot.value);
  } catch {
    showToast("电路快照不是有效 JSON");
    return;
  }
  setBusy(elements.runDecision, true, "模型思考中…");
  elements.decisionResult.textContent = "请求已发送…";
  const started = performance.now();
  try {
    const response = await fetch("/api/test/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: elements.question.value.trim(), circuit }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "模型请求失败");
    elements.decisionResult.textContent = JSON.stringify(result, null, 2);
    elements.decisionTime.textContent = `${Math.round(performance.now() - started)} ms`;
    highlightPorts(result.tool_call);
  } catch (error) {
    elements.decisionResult.textContent = `ERROR: ${error.message}`;
    elements.decisionTime.textContent = `${Math.round(performance.now() - started)} ms`;
    showToast(error.message);
  } finally {
    setBusy(elements.runDecision, false);
  }
}

function logProtocol(direction, payload) {
  const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const value = typeof payload === "string" ? payload : JSON.stringify(payload);
  elements.protocolLog.textContent += `\n[${stamp}] ${direction} ${value}`;
  elements.protocolLog.scrollTop = elements.protocolLog.scrollHeight;
}

function connectWebSocket() {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.close();
    return;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  websocket = new WebSocket(`${scheme}://${location.host}/ws/device`);
  elements.protocolLog.textContent = "正在连接…";
  elements.connectWs.disabled = true;
  websocket.addEventListener("open", () => {
    elements.connectWs.disabled = false;
    elements.connectWs.textContent = "断开 WebSocket";
    elements.runWsDemo.disabled = false;
    logProtocol("OPEN", "连接成功");
  });
  websocket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      try { logProtocol("RECV", JSON.parse(event.data)); }
      catch { logProtocol("RECV", event.data); }
    } else {
      logProtocol("RECV", `<binary ${event.data.size} bytes>`);
    }
  });
  websocket.addEventListener("close", () => {
    elements.connectWs.disabled = false;
    elements.connectWs.textContent = "连接 WebSocket";
    elements.runWsDemo.disabled = true;
    logProtocol("CLOSE", "连接已关闭");
  });
  websocket.addEventListener("error", () => showToast("WebSocket 连接失败"));
}

function sendJson(payload) {
  websocket.send(JSON.stringify(payload));
  logProtocol("SEND", payload);
}

async function runWebSocketDemo() {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
  const sessionId = `browser-${Date.now()}`;
  const steps = [
    () => sendJson({ type: "device.hello", protocol_version: 2, device_id: "browser-simulator" }),
    () => sendJson({ type: "session.start", session_id: sessionId, locale: "zh-CN" }),
    () => sendJson({ type: "circuit.snapshot", session_id: sessionId, ...JSON.parse(elements.snapshot.value) }),
    () => sendJson({ type: "input_audio.start", session_id: sessionId, format: "pcm_s16le", sample_rate_hz: 16000, channels: 1 }),
    () => {
      const pcm = new ArrayBuffer(3200);
      websocket.send(pcm);
      logProtocol("SEND", "<binary 3200 bytes / 100 ms silence>");
    },
    () => sendJson({ type: "input_audio.commit", session_id: sessionId }),
  ];
  setBusy(elements.runWsDemo, true, "模拟中…");
  try {
    for (const step of steps) {
      step();
      await new Promise((resolve) => setTimeout(resolve, 140));
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.runWsDemo, false);
    elements.runWsDemo.disabled = websocket.readyState !== WebSocket.OPEN;
  }
}

elements.snapshot.value = JSON.stringify(sampleSnapshot, null, 2);
buildPortGrid();
loadStatus();
elements.saveConfig.addEventListener("click", saveConfig);
elements.runDecision.addEventListener("click", runDecision);
elements.connectWs.addEventListener("click", connectWebSocket);
elements.runWsDemo.addEventListener("click", runWebSocketDemo);
