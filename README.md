# TUCO AI Backend

数字电路学习玩具的独立 AI 后端。设备只负责采集/播放音频、上传电路状态并执行端口提示命令；ASR、提示词、GPT 工具调用和 TTS 由后端统一编排。

## 当前能力

- OpenAI 兼容 `/chat/completions` 模型接入。
- `highlight_ports` 工具调用及严格参数校验。
- 运行时修改模型地址、模型名称和临时 API Key。
- 设备 WebSocket 协议骨架，支持 JSON 控制帧和 PCM 二进制帧。
- 无构建步骤的浏览器测试台，可编辑电路快照并预览 64 端口闪烁。
- 基于当前 ESP32 固件交互方式的设备模拟器。

火山引擎流式 ASR/TTS 的正式接入属于下一阶段；接口边界和实施顺序已在文档中固定。

## 快速开始

```powershell
cd E:\tuco-ai-backend
Copy-Item .env.example .env
uv sync --extra dev
uv run uvicorn tuco_ai_backend.main:app --reload --host 0.0.0.0 --port 8000
```

打开 `http://127.0.0.1:8000` 使用测试台。API Key 可通过 `.env` 配置，也可在页面中临时写入当前进程内存；后端不会通过配置接口返回 Key，也不会持久化页面中输入的 Key。

## 验证

```powershell
uv run pytest
uv run ruff check .
uv run python simulator\device_simulator.py --url ws://127.0.0.1:8000/ws/device
```

## 文档

- `docs/architecture.md`：总体架构、边界和状态机。
- `docs/device-protocol.md`：设备 WebSocket v2 协议。
- `docs/embedded-integration.md`：嵌入式端最小改造说明。
- `docs/implementation.md`：分阶段实施与验收计划。

## 安全提示

- 不要把任何真实 API Key 写入仓库、日志或前端静态文件。
- 已在聊天或截图中暴露过的 Key 应立即轮换。
- 生产部署应为配置接口增加管理员鉴权，并将 Key 放入密钥管理服务。
