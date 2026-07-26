# TUCO AI Backend

数字电路学习玩具的独立 AI 后端。设备只负责采集/播放音频、上传电路状态并执行端口提示命令；ASR、提示词、GPT 工具调用和 TTS 由后端统一编排。

## 当前能力

- OpenAI 兼容 `/chat/completions` 模型接入。
- `highlight_ports` 工具调用及严格参数校验。
- 火山引擎流式语音识别 ASR 1.0，将设备 PCM 识别为文本。
- 火山引擎 TTS 2.0，直接返回 PCM S16LE、16000 Hz、单声道音频。
- GPT 工具调用结果回传模型后生成最终教学回答。
- 完整设备 WebSocket v2 管线，支持音频上传、端口命令确认和语音流下发。
- 运行时修改模型、火山资源和临时 API Key，配置响应始终脱敏。
- 无构建步骤的浏览器测试台，支持长按录音、PCM 播放和 64 端口动画。
- 基于当前 ESP32 固件交互方式的设备模拟器。

完整处理链路：

```text
ESP32 PCM
→ 火山流式 ASR 1.0
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
TUCO_DEVICE_TOKEN
TUCO_ADMIN_TOKEN
```

生产环境必须配置独立的设备令牌和管理员令牌。设备在 `device.hello`
中发送 `device_token`；管理员 API 使用 `X-Tuco-Admin-Token` 请求头。
单轮上传 PCM 默认限制为 512 KiB，且整条语音管线默认 120 秒超时。

默认火山资源为 `volc.bigasr.sauc.duration`、`seed-tts-2.0`，默认音色为 `zh_female_vv_uranus_bigtts`。

## 并发关卡 LLM 评测

无需启动 FastAPI 服务，可直接复用后端的文字决策链路，并发评测全部 17 个关卡。脚本会从
`.env` 读取现有 `TUCO_LLM_*` 配置，不经过 ASR、TTS 或全局测试 Session。

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py --concurrency 4
```

同一关卡内的问题按顺序执行并保留独立短期历史，不同关卡之间并发且不会混用上下文：

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py `
  --question "这关要做什么？" `
  --question "接下来怎么做？"
```

评测时可选择电路初始状态。默认 `empty` 表示尚未摆放积木；`placed-io` 会按照关卡要求放好
输入、输出积木，并使用固件相同的端口方向快照。后者未指定问题时，默认提问为“接下来应该怎么做？给我点提示”。

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py `
  --level 401 --level 403 `
  --circuit-setup placed-io
```

先查看可评测关卡：

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py --list-levels
```

只评测部分关卡。可以使用可重复的 `--level` 参数，也兼容逗号分隔的 `--levels`：

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py --level 301 --level 302
```

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py `
  --levels 301,302,403 `
  --env-file .env `
  --output-dir runtime\llm_evaluations
```

也可在指定关卡时使用其他配置文件、输出目录：

```powershell
uv run python scripts\run_concurrent_level_llm_eval.py `
  --level 301 --level 302 `
  --env-file .env `
  --output-dir runtime\llm_evaluations
```

每次运行都会生成同名的 JSON 原始报告和 Markdown 汇总，默认写入
`runtime/llm_evaluations`。单个关卡请求失败不会取消其他关卡；报告仍会落盘，但进程返回非零退出码。

## 语音抓包

调试语音识别时可在 `.env` 中开启 `TUCO_AUDIO_CAPTURE_ENABLED=true`。服务会将每轮设备上行
语音保存为 `input` MP3，并将 TTS 下行语音保存为 `output` MP3；两者均为 16 kHz 单声道。
文件默认保存到 `runtime/audio_captures`，最多保留 100 个且 7 天后自动删除。生产环境需要安装
`ffmpeg`。管理员可通过 `GET /api/debug/audio-captures` 列出文件，并通过
`GET /api/debug/audio-captures/{name}` 下载试听；两个接口都要求 `X-Tuco-Admin-Token`。

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
