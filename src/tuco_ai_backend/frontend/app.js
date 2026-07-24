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
    sendJson({ type: "circuit.snapshot", session_id: voiceSessionId, ...JSON.parse(elements.snapshot.value) });
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

elements.snapshot.value = JSON.stringify(sampleSnapshot, null, 2);
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
