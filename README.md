# TUCO AI Backend

数字电路学习玩具的独立 AI 后端。设备只负责采集/播放音频、上传电路状态并执行端口提示命令；ASR、提示词、GPT 工具调用和 TTS 由后端统一编排。

## 当前能力

- OpenAI 兼容 `/chat/completions` 模型接入。
- `highlight_ports` 工具调用及严格参数校验。
- 火山引擎 ASR 2.0，将设备 PCM 识别为文本。
- 火山引擎 TTS 2.0，直接返回 PCM S16LE、16000 Hz、单声道音频。
- GPT 工具调用结果回传模型后生成最终教学回答。
- 完整设备 WebSocket v2 管线，支持音频上传、端口命令确认和语音流下发。
- 运行时修改模型、火山资源和临时 API Key，配置响应始终脱敏。
- 无构建步骤的浏览器测试台，支持长按录音、PCM 播放和 64 端口动画。
- 基于当前 ESP32 固件交互方式的设备模拟器。

完整处理链路：

```text
ESP32 PCM
→ 火山 ASR 2.0
→ OpenAI 兼容 GPT + function calling
→ device.command / device.command.result
→ 火山 TTS 2.0
→ PCM S16LE 16 kHz mono
```

## 快速开始

```powershell
cd E:\tuco-ai-backend
Copy-Item .env.example .env
uv sync --extra dev
uv run uvicorn tuco_ai_backend.main:app --reload --host 0.0.0.0 --port 8000
```

打开 `http://127.0.0.1:8000` 使用测试台。API Key 可通过 `.env` 配置，也可在页面中临时写入当前进程内存；后端不会通过配置接口返回 Key，也不会持久化页面中输入的 Key。

至少需要配置：

```text
TUCO_LLM_API_KEY
TUCO_VOLC_API_KEY
```

默认火山资源为 `volc.seedasr.sauc.duration`、`seed-tts-2.0`，默认音色为 `zh_female_vv_uranus_bigtts`。

## 验证

```powershell
uv run pytest
uv run ruff check .
node --check src\tuco_ai_backend\frontend\app.js
uv run python simulator\device_simulator.py --url ws://127.0.0.1:8000/ws/device
```

## 文档

- `docs/architecture.md`：总体架构、边界和状态机。
- `docs/device-protocol.md`：设备 WebSocket v2 协议。
- `docs/embedded-integration.md`：嵌入式端最小改造说明。
- `docs/implementation.md`：当前实现、供应商协议和已知限制。

## 安全提示

- 不要把任何真实 API Key 写入仓库、日志或前端静态文件。
- 已在聊天或截图中暴露过的 Key 应立即轮换。
- 生产部署应为配置接口增加管理员鉴权，并将 Key 放入密钥管理服务。
