window.onerror = function(message, source, lineno, colno, error) {
  const div = document.createElement("div");
  div.style.position = "fixed";
  div.style.top = "0";
  div.style.left = "0";
  div.style.width = "100%";
  div.style.backgroundColor = "#ff5252";
  div.style.color = "white";
  div.style.padding = "20px";
  div.style.zIndex = "999999";
  div.style.fontSize = "16px";
  div.style.fontFamily = "monospace";
  div.style.whiteSpace = "pre-wrap";
  div.innerHTML = `<strong>JS Error:</strong> ${message}<br>at ${source}:${lineno}:${colno}<br>${error ? error.stack : ''}`;
  document.body.insertBefore(div, document.body.firstChild);
  return false;
};

const elements = {
  health: document.querySelector(".service-status"),
  healthText: document.querySelector("#health-text"),
  baseUrl: document.querySelector("#base-url"),
  model: document.querySelector("#model"),
  apiKey: document.querySelector("#api-key"),
  timeout: document.querySelector("#timeout"),
  volcApiKey: document.querySelector("#volc-api-key"),
  asrResource: document.querySelector("#asr-resource"),
  ttsResource: document.querySelector("#tts-resource"),
  ttsVoice: document.querySelector("#tts-voice"),
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
  holdToTalk: document.querySelector("#hold-to-talk"),
  voiceStatus: document.querySelector("#voice-status"),
  protocolLog: document.querySelector("#protocol-log"),
  toast: document.querySelector("#toast"),
  levelSelect: document.querySelector("#level-select"),
  levelTitleDisplay: document.querySelector("#level-title-display"),
  levelGoalDisplay: document.querySelector("#level-goal-display"),
  truthTableContainer: document.querySelector("#truth-table-container"),
  enterLevel: document.querySelector("#enter-level"),
  exitLevel: document.querySelector("#exit-level"),
  clearLogs: document.querySelector("#clear-logs"),
};
const sampleSnapshot = {
  schema_version: 1,
  topology_revision: 42,
  level: {
    level_id: 101,
    title: "启动飞船",
    short_goal: "总电门只需要把输入 A 直连到输出 Y 即可恢复主控台供电（直连导通）。",
    input_names: "总电门 A",
    output_names: "主控台供电 Y",
    input_count: 1,
    output_count: 1
  },
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

const LEVEL_DATA = {
  101: { level_id: 101, title: "启动飞船", short_goal: "总电门只需要把输入 A 直连到输出 Y 即可恢复主控台供电（直连导通）。", input_names: "总电门 A", output_names: "主控台供电 Y", input_count: 1, output_count: 1, truth_table: [{ in: [0], out: [0] }, { in: [1], out: [1] }] },
  102: { level_id: 102, title: "与非门", short_goal: "合成与非门（NAND）：只有两个输入同为 1 时才会切断输出。", input_names: "通道开关 A, B", output_names: "主电网输出 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [1] }, { in: [0, 1], out: [1] }, { in: [1, 0], out: [1] }, { in: [1, 1], out: [0] }] },
  103: { level_id: 103, title: "反向激光", short_goal: "合成非门（NOT）：输入 0 时输出 1 发射激光击碎陨石。", input_names: "发射按键 A", output_names: "激光炮输出 Y", input_count: 1, output_count: 1, truth_table: [{ in: [0], out: [1] }, { in: [1], out: [0] }] },
  201: { level_id: 201, title: "双重密码", short_goal: "合成与门（AND）：只有两人同时按下开锁指纹（输入同为 1）时大门才会打开。", input_names: "指纹开关 A, B", output_names: "驾驶舱大门 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [0] }, { in: [0, 1], out: [0] }, { in: [1, 0], out: [0] }, { in: [1, 1], out: [1] }] },
  202: { level_id: 202, title: "备用线路", short_goal: "合成或门（OR）：主管道或备用管道任意一条通电（任意输入为 1）即可供氧。", input_names: "主管道 A, 备用管道 B", output_names: "应急供氧 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [0] }, { in: [0, 1], out: [1] }, { in: [1, 0], out: [1] }, { in: [1, 1], out: [1] }] },
  203: { level_id: 203, title: "能量护罩", short_goal: "合成或非门（NOR）：只有两个方向都没有危险（输入同为 0）时，护罩才维持开启。", input_names: "左/右威胁传感器", output_names: "能量护罩 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [1] }, { in: [0, 1], out: [0] }, { in: [1, 0], out: [0] }, { in: [1, 1], out: [0] }] },
  301: { level_id: 301, title: "互斥钥匙", short_goal: "合成异或门（XOR）：只有两个输入电平互斥（一开一关，输入不同）时才开放密码钥匙。", input_names: "密码开关 A, B", output_names: "星门钥匙 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [0] }, { in: [0, 1], out: [1] }, { in: [1, 0], out: [1] }, { in: [1, 1], out: [0] }] },
  302: { level_id: 302, title: "雷达对接", short_goal: "合成同或门（XNOR）：两侧天线信号完全同步（同为 0 或同为 1）时才能完成对接。", input_names: "天线射频 A, B", output_names: "对接锁定 Y", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [1] }, { in: [0, 1], out: [0] }, { in: [1, 0], out: [0] }, { in: [1, 1], out: [1] }] },
  401: { level_id: 401, title: "求和电平", short_goal: "计算二进制加法个位求和（异或门逻辑）。", input_names: "加数 A, B", output_names: "个位求和 Sum", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [0] }, { in: [0, 1], out: [1] }, { in: [1, 0], out: [1] }, { in: [1, 1], out: [0] }] },
  402: { level_id: 402, title: "进位警报", short_goal: "检测能量溢出：只有当两个加数同为 1 时（1+1），才产生向高位的进位 1（与门逻辑）。", input_names: "加数 A, B", output_names: "高位进位 Carry", input_count: 2, output_count: 1, truth_table: [{ in: [0, 0], out: [0] }, { in: [0, 1], out: [0] }, { in: [1, 0], out: [0] }, { in: [1, 1], out: [1] }] },
  403: { level_id: 403, title: "半加引擎", short_goal: "组合个位求和（异或门）与高位进位（与门），实现完整的半加器电路。", input_names: "加数 A, B", output_names: "Sum, Carry", input_count: 2, output_count: 2, truth_table: [{ in: [0, 0], out: [0, 0] }, { in: [0, 1], out: [1, 0] }, { in: [1, 0], out: [1, 0] }, { in: [1, 1], out: [0, 1] }] },
  501: { level_id: 501, title: "三路求和", short_goal: "全加器个位求和：只有当输入 1 的个数为奇数个（1或3个）时个位输出 1。", input_names: "加数 A, B, C", output_names: "个位 Sum", input_count: 3, output_count: 1, truth_table: [{ in: [0, 0, 0], out: [0] }, { in: [0, 0, 1], out: [1] }, { in: [0, 1, 0], out: [1] }, { in: [0, 1, 1], out: [0] }, { in: [1, 0, 0], out: [1] }, { in: [1, 0, 1], out: [0] }, { in: [1, 1, 0], out: [0] }, { in: [1, 1, 1], out: [1] }] },
  502: { level_id: 502, title: "局部进位", short_goal: "全加器局部溢出检测：只要任意 2 个或 2 个以上输入同为 1，产生局部进位。", input_names: "加数 A, B, C", output_names: "局部进位", input_count: 3, output_count: 1, truth_table: [{ in: [0, 0, 0], out: [0] }, { in: [0, 0, 1], out: [0] }, { in: [0, 1, 0], out: [0] }, { in: [0, 1, 1], out: [1] }, { in: [1, 0, 0], out: [0] }, { in: [1, 0, 1], out: [1] }, { in: [1, 1, 0], out: [1] }, { in: [1, 1, 1], out: [1] }] },
  503: { level_id: 503, title: "进位汇聚", short_goal: "全加器进位合并（或门）：只要有任意一路局部溢出，最终进位输出 1。", input_names: "局部进位 A, B, C", output_names: "最终 Carry", input_count: 3, output_count: 1, truth_table: [{ in: [0, 0, 0], out: [0] }, { in: [0, 0, 1], out: [1] }, { in: [0, 1, 0], out: [1] }, { in: [0, 1, 1], out: [1] }, { in: [1, 0, 0], out: [1] }, { in: [1, 0, 1], out: [1] }, { in: [1, 1, 0], out: [1] }, { in: [1, 1, 1], out: [1] }] },
  504: { level_id: 504, title: "图灵主控", short_goal: "终极全加器引擎：组合三路求和与进位汇聚，实现三位二进制全加器。", input_names: "加数 A, B, 进位 C", output_names: "Sum, Carry", input_count: 3, output_count: 2, truth_table: [{ in: [0, 0, 0], out: [0, 0] }, { in: [0, 0, 1], out: [1, 0] }, { in: [0, 1, 0], out: [1, 0] }, { in: [0, 1, 1], out: [0, 1] }, { in: [1, 0, 0], out: [1, 0] }, { in: [1, 0, 1], out: [0, 1] }, { in: [1, 1, 0], out: [0, 1] }, { in: [1, 1, 1], out: [1, 1] }] },
  601: { level_id: 601, title: "信号分流", short_goal: "信号选择器：控制信号选择选通 A 频道或 B 频道。", input_names: "Select, A, B", output_names: "选通输出 Y", input_count: 3, output_count: 1, truth_table: [{ in: [0, 0, 0], out: [0] }, { in: [0, 1, 0], out: [1] }, { in: [1, 0, 0], out: [0] }, { in: [1, 0, 1], out: [1] }] },
  602: { level_id: 602, title: "指令翻译", short_goal: "二转四译码器：2 位二进制选择独热选通 4 个舱室之一。", input_names: "指令 A, B", output_names: "舱室 1, 2, 3, 4", input_count: 2, output_count: 4, truth_table: [{ in: [0, 0], out: [1, 0, 0, 0] }, { in: [0, 1], out: [0, 1, 0, 0] }, { in: [1, 0], out: [0, 0, 1, 0] }, { in: [1, 1], out: [0, 0, 0, 1] }] }
};

function renderTruthTable(truthTable) {
  if (!truthTable || !truthTable.length) return "";
  let html = `<table class="truth-table" style="border-collapse: collapse; width: 100%; margin-top: 6px; font-size: 12px; background: rgba(0,0,0,0.2); border-radius: 4px; overflow: hidden;">
    <thead>
      <tr style="background: rgba(255,255,255,0.08); text-align: left; color: #ccc;">
        <th style="padding: 5px 10px; border-bottom: 1px solid #444;">输入组合 (Inputs)</th>
        <th style="padding: 5px 10px; border-bottom: 1px solid #444;">预期输出 (Expected Output)</th>
      </tr>
    </thead>
    <tbody>`;
  truthTable.forEach((row) => {
    html += `<tr style="border-bottom: 1px solid rgba(255,255,255,0.03);">
      <td style="padding: 4px 10px; font-family: monospace; color: #64b5f6;">[ ${row.in.join(", ")} ]</td>
      <td style="padding: 4px 10px; font-family: monospace; color: #81c784;">[ ${row.out.join(", ")} ]</td>
    </tr>`;
  });
  html += `</tbody></table>`;
  return html;
}

function updateLevelInfo() {
  if (!elements.levelSelect) return;
  const levelId = parseInt(elements.levelSelect.value, 10);
  const data = LEVEL_DATA[levelId];
  if (!data) return;
  if (elements.levelTitleDisplay) {
    elements.levelTitleDisplay.innerHTML = `<strong>当前选定关卡：关卡 ${data.level_id} · ${data.title}</strong>`;
  }
  if (elements.levelGoalDisplay) {
    elements.levelGoalDisplay.textContent = `目标：${data.short_goal}`;
  }
  if (elements.truthTableContainer) {
    elements.truthTableContainer.innerHTML = `
      <div style="font-size: 12px; color: #aaa; margin-bottom: 4px; font-weight: bold;">目标真值表 (Truth Table):</div>
      ${renderTruthTable(data.truth_table)}
    `;
  }
  try {
    let snap = sampleSnapshot;
    if (elements.snapshot && elements.snapshot.value && elements.snapshot.value.trim()) {
      try {
        snap = JSON.parse(elements.snapshot.value);
      } catch (e) {}
    }
    snap.level = data;
    if (elements.snapshot) {
      elements.snapshot.value = JSON.stringify(snap, null, 2);
    }
  } catch (e) {}
}


let websocket = null;
let toastTimer = null;
let voiceSessionId = null;
let voiceReady = false;
let audioContext = null;
let mediaStream = null;
let mediaSource = null;
let audioProcessor = null;
let recording = false;
let voiceStarting = false;
let stopRequested = false;
let ttsChunks = [];
const messageWaiters = new Map();

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
    elements.asrResource.value = config.volc_asr_resource_id;
    elements.ttsResource.value = config.volc_tts_resource_id;
    elements.ttsVoice.value = config.volc_tts_voice_type;
    elements.keyStatus.textContent = config.llm_api_key_configured
      ? `GPT Key 已配置；火山 Key ${config.volc_api_key_configured ? "已配置" : "未配置"}。新输入只在当前服务进程内生效。`
      : `GPT Key 未配置；火山 Key ${config.volc_api_key_configured ? "已配置" : "未配置"}。`;
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
  if (elements.apiKey.value.trim()) payload.llm_api_key = elements.apiKey.value.trim();
  if (elements.volcApiKey.value.trim()) payload.volc_api_key = elements.volcApiKey.value.trim();
  payload.volc_asr_resource_id = elements.asrResource.value.trim();
  payload.volc_tts_resource_id = elements.ttsResource.value.trim();
  payload.volc_tts_voice_type = elements.ttsVoice.value.trim();
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
    elements.volcApiKey.value = "";
    elements.keyStatus.textContent = `GPT Key ${result.llm_api_key_configured ? "已配置" : "未配置"}；火山 Key ${result.volc_api_key_configured ? "已配置" : "未配置"}。后端不会回显密钥。`;
    if (websocket?.readyState === WebSocket.OPEN) {
      const providersReady = result.llm_api_key_configured && result.volc_api_key_configured;
      if (providersReady) {
        elements.voiceStatus.textContent = "配置已更新，正在重连语音服务……";
        websocket.addEventListener("close", connectWebSocket, { once: true });
      } else {
        elements.voiceStatus.textContent = "请先配置 GPT API Key 和火山新版 API Key。";
      }
      websocket.close();
    }
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
    if (circuit.level) {
      delete circuit.level.title;
      delete circuit.level.truth_table;
    }
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
  let html = "";
  
  // 清理初始的“正在连接…”文本节点或空节点，避免 append 混乱
  if (elements.protocolLog.childNodes.length === 1 && elements.protocolLog.firstChild.nodeType === 3) {
    elements.protocolLog.innerHTML = "";
  }
  
  if (payload && payload.type === "trace.log") {
    const levelColor = payload.level === "WARNING" || payload.level === "ERROR" ? "#ff5252" : "#9e9e9e";
    let moduleColor = "#fff";
    let messageColor = "#e0e0e0";
    
    switch (payload.module) {
      case "ASR":
        moduleColor = "#64b5f6";
        break;
      case "PRE-CHECK":
        moduleColor = "#ffb74d";
        break;
      case "LLM":
        moduleColor = "#4db6ac";
        break;
      case "FALLBACK":
        moduleColor = "#ff5252";
        messageColor = "#ff8a80";
        break;
      case "TTS":
        moduleColor = "#ba68c8";
        break;
    }
    
    // 移除消息体内冗余的模块前缀
    let cleanMessage = payload.message;
    if (cleanMessage.includes("] ")) {
      cleanMessage = cleanMessage.split("] ").slice(1).join("] ");
    }
    
    html = `<div style="margin: 2px 0; line-height: 1.4;"><span style="color: #616161;">[${stamp}]</span> <span style="color: #8d6e63; font-family: monospace;">[${payload.trace_id.slice(-6)}]</span> <span style="color: ${levelColor}; font-weight: bold;">[${payload.level}]</span> <span style="color: ${moduleColor}; font-weight: bold;">[${payload.module}]</span> <span style="color: ${messageColor};">${cleanMessage}</span></div>`;
  } else {
    const value = typeof payload === "string" ? payload : JSON.stringify(payload);
    let dirColor = "#e0e0e0";
    if (direction === "SEND") dirColor = "#81c784";
    if (direction === "RECV") dirColor = "#4fc3f7";
    if (direction === "OPEN" || direction === "CLOSE") dirColor = "#ffb74d";
    
    html = `<div style="margin: 2px 0;"><span style="color: #616161;">[${stamp}]</span> <span style="color: ${dirColor}; font-weight: bold; font-family: monospace; min-width: 45px; display: inline-block;">${direction}</span> <span style="color: #a0a0a0; word-break: break-all; font-family: monospace;">${value}</span></div>`;
  }

  const tempDiv = document.createElement("div");
  tempDiv.innerHTML = html;
  elements.protocolLog.appendChild(tempDiv.firstChild);
  
  while (elements.protocolLog.childNodes.length > 300) {
    elements.protocolLog.removeChild(elements.protocolLog.firstChild);
  }
  
  elements.protocolLog.scrollTop = elements.protocolLog.scrollHeight;
}

function connectWebSocket() {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.close();
    return;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  websocket = new WebSocket(`${scheme}://${location.host}/ws/device`);
  websocket.binaryType = "arraybuffer";
  voiceReady = false;
  voiceSessionId = `voice-${Date.now()}`;
  elements.protocolLog.textContent = "正在连接…";
  elements.connectWs.disabled = true;
  websocket.addEventListener("open", () => {
    elements.connectWs.disabled = false;
    elements.connectWs.textContent = "断开 WebSocket";
    elements.runWsDemo.disabled = false;
    logProtocol("OPEN", "连接成功");
    sendJson({ type: "device.hello", protocol_version: 2, device_id: "browser-voice-test" });
  });
  websocket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        logProtocol("RECV", event.data);
        return;
      }
      if (message.type === "trace.log") {
        logProtocol("RECV", message);
        return;
      }
      logProtocol("RECV", message);
      handleProtocolMessage(message);
    } else {
      const chunk = event.data;
      ttsChunks.push(chunk);
      streamPcmChunk(chunk);
      logProtocol("RECV", `<binary ${chunk.byteLength} bytes>`);
    }
  });
  websocket.addEventListener("close", () => {
    elements.connectWs.disabled = false;
    elements.connectWs.textContent = "连接 WebSocket";
    elements.runWsDemo.disabled = true;
    elements.holdToTalk.disabled = true;
    voiceReady = false;
    elements.voiceStatus.textContent = "语音连接已断开。";
    logProtocol("CLOSE", "连接已关闭");
  });
  websocket.addEventListener("error", () => showToast("WebSocket 连接失败"));
}

function handleProtocolMessage(message) {
  const waiter = messageWaiters.get(message.type);
  if (waiter) {
    messageWaiters.delete(message.type);
    waiter.resolve(message);
  }
  if (message.type === "device.ready") {
    sendJson({ type: "session.start", session_id: voiceSessionId, locale: "zh-CN" });
  } else if (message.type === "session.ready") {
    const snap = JSON.parse(elements.snapshot.value);
    if (snap.level) {
      delete snap.level.title;
      delete snap.level.truth_table;
    }
    sendJson({ type: "circuit.snapshot", session_id: voiceSessionId, ...snap });
  } else if (message.type === "circuit.snapshot.accepted") {
    voiceReady = true;
    elements.holdToTalk.disabled = false;
    elements.voiceStatus.textContent = "语音测试已就绪，请按住按钮说话。";
  } else if (message.type === "asr.result") {
    elements.voiceStatus.textContent = `识别结果：${message.text}`;
  } else if (message.type === "device.command") {
    highlightPorts({ name: message.name, arguments: message.arguments });
    sendJson({
      type: "device.command.result",
      session_id: message.session_id,
      call_id: message.call_id,
      ok: true,
      message: "browser simulator executed command",
    });
  } else if (message.type === "response.audio.start") {
    ttsChunks = [];
    nextPlayTime = 0;
    elements.voiceStatus.textContent = message.text || "正在接收语音回答…";
  } else if (message.type === "response.audio.done") {
    elements.voiceStatus.textContent = "回答播放中。";
    const delayMs = Math.max(0, (nextPlayTime - (audioContext?.currentTime || 0)) * 1000);
    setTimeout(() => {
      if (websocket && websocket.readyState === WebSocket.OPEN) {
        sendJson({ type: "response.audio.played", session_id: message.session_id || voiceSessionId });
      }
    }, delayMs);
  } else if (message.type === "response.done") {
    elements.voiceStatus.textContent = "本轮对话完成。";
  } else if (message.type === "error") {
    const errorMessage = message.code === "voice.not_configured"
      ? "请先配置 GPT API Key 和火山新版 API Key，然后重新连接 WebSocket。"
      : message.message;
    elements.voiceStatus.textContent = `错误：${errorMessage}`;
    showToast(errorMessage);
  }
}

function waitForMessage(type, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      messageWaiters.delete(type);
      reject(new Error(`等待 ${type} 超时`));
    }, timeoutMs);
    messageWaiters.set(type, {
      resolve: (message) => {
        clearTimeout(timer);
        resolve(message);
      },
    });
  });
}

async function ensureMicrophone() {
  if (mediaStream) return;
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  audioContext = audioContext || new AudioContext();
}

function downsample(input, inputRate, outputRate) {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let index = 0; index < output.length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let source = start; source < end; source += 1) sum += input[source];
    output[index] = sum / Math.max(1, end - start);
  }
  return output;
}

function floatToPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = value < 0 ? value * 32768 : value * 32767;
  }
  return pcm.buffer;
}

async function beginVoiceRecording() {
  if (!voiceReady || recording || voiceStarting) return;
  voiceStarting = true;
  stopRequested = false;
  try {
    await ensureMicrophone();
    const started = waitForMessage("input_audio.started");
    sendJson({
      type: "input_audio.start",
      session_id: voiceSessionId,
      format: "pcm_s16le",
      sample_rate_hz: 16000,
      channels: 1,
    });
    await started;
    mediaSource = audioContext.createMediaStreamSource(mediaStream);
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    audioProcessor.onaudioprocess = (event) => {
      if (!recording || websocket.readyState !== WebSocket.OPEN) return;
      const samples = event.inputBuffer.getChannelData(0);
      websocket.send(floatToPcm16(downsample(samples, audioContext.sampleRate, 16000)));
    };
    mediaSource.connect(audioProcessor);
    audioProcessor.connect(audioContext.destination);
    recording = true;
    elements.holdToTalk.classList.add("recording");
    elements.holdToTalk.textContent = "正在录音，松开结束";
    elements.voiceStatus.textContent = "正在听你说……";
    if (stopRequested) endVoiceRecording();
  } catch (error) {
    showToast(error.message);
  } finally {
    voiceStarting = false;
  }
}

function endVoiceRecording() {
  if (voiceStarting && !recording) {
    stopRequested = true;
    return;
  }
  if (!recording) return;
  recording = false;
  audioProcessor?.disconnect();
  mediaSource?.disconnect();
  audioProcessor = null;
  mediaSource = null;
  elements.holdToTalk.classList.remove("recording");
  elements.holdToTalk.textContent = "按住说话";
  elements.voiceStatus.textContent = "正在思考……";
  lastCommitTime = performance.now();
  sendJson({ type: "input_audio.commit", session_id: voiceSessionId });
}

async function streamPcmChunk(arrayBuffer) {
  if (!arrayBuffer || !arrayBuffer.byteLength) return;
  audioContext = audioContext || new AudioContext();
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }
  const pcm = new Int16Array(arrayBuffer, 0, Math.floor(arrayBuffer.byteLength / 2));
  const buffer = audioContext.createBuffer(1, pcm.length, 16000);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  const now = audioContext.currentTime;
  if (nextPlayTime < now) nextPlayTime = now;
  source.start(nextPlayTime);
  nextPlayTime += buffer.duration;
}

async function playPcmChunks(chunks) {
  if (!chunks.length) return;
}


function sendJson(payload) {
  websocket.send(JSON.stringify(payload));
  logProtocol("SEND", payload);
}

async function runWebSocketDemo() {
  if (!websocket || websocket.readyState !== WebSocket.OPEN || !voiceReady) return;
  setBusy(elements.runWsDemo, true, "模拟中…");
  try {
    const started = waitForMessage("input_audio.started");
    sendJson({
      type: "input_audio.start",
      session_id: voiceSessionId,
      format: "pcm_s16le",
      sample_rate_hz: 16000,
      channels: 1,
    });
    await started;
    const pcm = new ArrayBuffer(3200);
    websocket.send(pcm);
    logProtocol("SEND", "<binary 3200 bytes / 100 ms silence>");
    const committed = waitForMessage("input_audio.committed");
    sendJson({ type: "input_audio.commit", session_id: voiceSessionId });
    await committed;
    elements.voiceStatus.textContent = "模拟 PCM 已提交。";
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(elements.runWsDemo, false);
    elements.runWsDemo.disabled = websocket.readyState !== WebSocket.OPEN;
  }
}
buildPortGrid();
loadStatus();
elements.saveConfig.addEventListener("click", saveConfig);
elements.runDecision.addEventListener("click", runDecision);
elements.connectWs.addEventListener("click", connectWebSocket);
elements.runWsDemo.addEventListener("click", runWebSocketDemo);
elements.holdToTalk.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  elements.holdToTalk.setPointerCapture(event.pointerId);
  beginVoiceRecording();
});
elements.holdToTalk.addEventListener("pointerup", endVoiceRecording);
elements.holdToTalk.addEventListener("pointercancel", endVoiceRecording);
if (elements.levelSelect) {
  elements.levelSelect.addEventListener("change", updateLevelInfo);
  updateLevelInfo();
}
if (elements.enterLevel) {
  elements.enterLevel.addEventListener("click", () => {
    updateLevelInfo();
    connectWebSocket();
    showToast(`已进入关卡 ${elements.levelSelect.value}，开启对话！`);
  });
}
if (elements.exitLevel) {
  elements.exitLevel.addEventListener("click", () => {
    if (websocket) websocket.close();
    elements.holdToTalk.disabled = true;
    elements.voiceStatus.textContent = "已退出关卡。请选择关卡并点击【进入关卡】。";
    showToast("已退出关卡并清空对话记忆。");
  });
}
if (elements.clearLogs) {
  elements.clearLogs.addEventListener("click", () => {
    elements.protocolLog.innerHTML = "";
    showToast("日志面板已清空");
  });
}
