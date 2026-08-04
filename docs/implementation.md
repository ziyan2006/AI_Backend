# AI 后端当前实现

## 1. 技术栈

- Python 3.11+
- FastAPI + WebSocket
- Pydantic 数据模型与严格工具参数校验
- HTTPX 调用 OpenAI 兼容 `/chat/completions`
- `websockets` 接入火山引擎 ASR/TTS V3 二进制协议
- Pytest、Ruff 和原生 HTML/CSS/JavaScript 测试台

## 2. 运行配置

环境变量统一使用 `TUCO_` 前缀：

```text
TUCO_LLM_BASE_URL
TUCO_LLM_MODEL
TUCO_LLM_API_KEY
TUCO_LLM_TIMEOUT_SECONDS
TUCO_VOLC_API_KEY
TUCO_VOLC_ASR_RESOURCE_ID
TUCO_VOLC_TTS_RESOURCE_ID
TUCO_VOLC_TTS_VOICE_TYPE
```

默认资源：

```text
ASR 1.0: volc.bigasr.sauc.duration
TTS: seed-tts-2.0
Voice: zh_female_vv_uranus_bigtts
```

`GET /api/config` 只返回 `*_configured` 布尔值，不返回任何 Key。浏览器页面提交的 Key 只保留在当前服务进程内存，服务重启后丢失。

## 3. 已实现处理流程

1. 设备完成 `device.hello`、`session.start` 和 `circuit.snapshot`。
2. 长按开始时发送 `input_audio.start`，随后上传 PCM S16LE 二进制帧。
3. 松开后发送 `input_audio.commit`。
4. 后端将本轮 PCM 发送给火山流式 ASR 1.0，并向设备返回 `asr.result`。
5. 后端把识别文本、系统提示词和完整电路快照发送给 GPT。
6. 若 GPT 调用 `highlight_ports`，后端发送 `device.command` 并等待 `device.command.result`。
7. 后端以 `role=tool` 回传执行结果，要求 GPT 生成最终文本。
8. 后端调用火山 TTS 2.0，发送 `response.audio.start`、PCM 二进制帧和 `response.audio.done`。

设备接收循环在管线执行期间保持运行，因此可以及时返回工具执行结果；所有 WebSocket 出站帧共用发送锁，避免 JSON 控制帧和音频帧并发写入同一连接。

## 4. 火山流式 ASR 1.0

端点：

```text
wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
```

该端点是文档推荐的双向流式优化版本；ASR 版本由资源 ID 决定。当前使用小时版资源 `volc.bigasr.sauc.duration`。

鉴权请求头：

```text
X-Api-Key
X-Api-Resource-Id
X-Api-Request-Id
```

输入格式固定为 PCM S16LE、16000 Hz、单声道。客户端使用官方二进制格式：首帧为 gzip JSON 的 Full Client Request，普通音频帧使用正序列号，最终音频帧使用负序列号。最终识别文本读取 `result.text`。

空文本映射为 `AsrNoSpeechError`，设备协议返回 `asr.no_speech`，避免界面永久停留在 thinking。

当前版本在后端完整缓存一轮设备 PCM，收到 `input_audio.commit` 后再快速分块上传 ASR。这一方式优先保证与现有固件兼容和实现稳定性；长录音的内存上限与实时上传可作为后续优化。

## 5. OpenAI 兼容 GPT 与工具闭环

首次请求使用：

```text
stream = false
tool_choice = auto
parallel_tool_calls = false
```

模型不需要工具时直接返回最终文本。模型调用 `highlight_ports` 时，后端校验端口、持续时间、动画模式和拓扑版本，然后等待设备回执。

设备返回结果后，后端重建 assistant tool call，并追加：

```json
{
  "role": "tool",
  "tool_call_id": "call_id",
  "content": "{...设备执行结果...}"
}
```

第二次请求使用 `tool_choice = none`，防止模型重复调用工具，并要求返回可用于 TTS 的最终中文回答。

中转站兼容性不能只根据模型名称判断，应分别验证工具 schema、`tool_calls`、`role=tool` 和第二次续写请求。

## 6. 火山 TTS 2.0

端点：

```text
wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream
```

当前实现遵循官方单向流 Demo：连接后发送一次 Full Client Request，不额外发送 `StartConnection` 或 `StartSession`。请求使用：

```json
{
  "req_params": {
    "speaker": "zh_female_vv_uranus_bigtts",
    "audio_params": {
      "format": "pcm",
      "sample_rate": 16000,
      "enable_timestamp": false
    },
    "text": "最终回答"
  }
}
```

`AudioOnlyServer` 的 payload 直接作为设备 PCM 下发；收到 `FullServerResponse` 的 `SessionFinished` 后结束。火山原生返回 PCM S16LE、16000 Hz、单声道，后端不转码为 MP3，也不改变现有 ESP32 播放 API。

## 7. API 与测试台

- `/api/health`：服务与能力状态。
- `/api/config`：运行时配置，响应脱敏。
- `/api/tools`：当前工具 schema。
- `/api/test/decision`：用手工问题和电路快照测试 GPT。
- `/ws/device`：设备 WebSocket v2。
- `/`：浏览器测试台。

测试台支持：

- 编辑 LLM、火山资源和临时 Key。
- 发送电路决策测试。
- 自动完成设备握手、会话和快照。
- 浏览器长按录音、16 kHz 重采样和 PCM 上传。
- 自动确认 `device.command` 并显示端口动画。
- 缓冲并播放后端返回的 PCM。
- 使用 100 ms 静音 PCM 验证设备协议，不依赖麦克风权限。

### 概念活动态 AI 助教

半加器、三路求和、全加器等关卡在正式组装前可以进入 0/1 概念练习。活动页只有儿童主动按住右键时才会发起语音请求；不会主动请求模型、不会启动电路判题，也不会下发端口高亮。

活动请求仍沿用原关卡的 `session_id` 与 `circuit_snapshot`，并额外携带可选的 `learning_activity`。其中包含活动类型、回合、槽位含义、当前与目标 0/1、十进制读数，以及是否答对或完成。后端检测到该字段后会切换到活动态提示词：忽略空电路的输入/输出积木提醒，不向模型提供 `highlight_ports` 或 `highlight_empty_slot`。

儿童未明确索要答案时，模型只推进一个观察点；明确要求答案时才给完整的 0/1 位置。即使模型意外返回工具调用，后端和固件都会拦截，不会驱动硬件端口高亮。

固件从活动页进入正式游玩时继续使用同一关卡会话，因此活动中的对话历史可延续到后续电路指导；离开关卡流程时才关闭会话。

并发评测器支持 `--learning-activity unsolved|near-solved|solved`，支持 401、403、501、504 关卡，并要求使用 `--protocol circuit-v2`。其中 501 使用 `three_input_parity`：`current_decimal` 表示当前有几个输入为 1，`target_decimal` 表示目标个位结果。可组合多个 `--question` 覆盖提示、原因、索要答案和闲聊。

## 8. 自动化验证

```powershell
uv run pytest
uv run ruff check .
node --check src\tuco_ai_backend\frontend\app.js
```

测试覆盖协议编解码、ASR 响应解析、配置密钥脱敏、GPT 工具结果续写、语音管线和设备 WebSocket 完整工具闭环。

2026 年 7 月 21 日已使用真实火山资源验证 TTS → PCM → ASR 云端往返。真实凭据不写入仓库、测试 fixture 或日志。

## 9. 已知限制与后续工作

- ASR 目前在 `input_audio.commit` 后开始云端上传，不是录音期间实时发送。
- 每个设备连接只维护一个活动会话和一个响应任务；新提交会取消仍在运行的旧响应。
- 工具等待超时为 15 秒，当前仅支持单个、串行工具调用。
- 浏览器测试台使用已弃用但仍广泛可用的 `ScriptProcessorNode`；正式产品不依赖该前端。
- 配置接口和设备连接尚未增加生产级鉴权。
- 生产部署应限制单轮 PCM 大小、增加结构化阶段耗时日志，并根据并发模型决定是否引入 Redis。

## 10. 联调完成标准

- 连续进行 30 次长按对话，无卡死、无永久 thinking。
- 录音 1 秒、5 秒和 15 秒时，上行字节数符合 16 kHz 单声道 S16LE。
- TTS 通过现有 playback begin/push/finish API 连续播放，无明显 underrun。
- 拔网重连后重新握手并发送完整快照。
- 电路在模型思考期间变化时，设备拒绝旧拓扑命令。
- 工具灯效执行期间，音频接收任务不被阻塞。
- 所有密钥均未进入源码、日志、前端响应或 Git 历史。
