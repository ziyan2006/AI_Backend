# TUCO AI 后端

TUCO AI 后端为数字电路学习玩具提供电路助教能力：接收电路快照和用户问题，结合关卡规则与对话历史调用 OpenAI 兼容模型，返回可直接朗读的中文提示，并在需要时返回经过安全规划的高亮动作。

后端不直接驱动红外、I2C、灯光或扬声器；硬件扫描、拓扑变化检测、动作最终校验和执行由 ESP32 固件负责。

## 与当前固件的关系

当前归档固件采用 HTTP 接入，固件本地完成火山 ASR/TTS 和播放：

```text
ESP32 本机 ASR -> POST /api/device/circuit-coach/decision
-> 后端分析快照/关卡并调用 LLM
-> assistant_text + 可选 tool_call
-> 固件校验并执行高亮，固件本机 TTS 播放文字
```

固件快照模式为 `tuco_circuit_v2`：16 个积木槽位、每槽 4 个端口，共 64 个端口。`board.slots`、`board.edges`、`board.topology_revision` 是动作规划依据。固件的 `CONFIG_TUCO_REMOTE_ASSISTANT_URL` 应指向本服务的 `/api/device/circuit-coach/decision`。

后端同时提供 `/ws/device` WebSocket v2（设备握手、PCM 上传、后端 ASR/LLM/TTS、工具回传），用于协议联调、浏览器测试台和模拟器；当前固件的 HTTP 流程不会自动使用它。

## 用户可用能力

- 按关卡目标、当前电路和已解锁积木提供分步骤中文指导。
- 诊断缺失积木、逻辑门未接满、非法/无效连接和可继续动作。
- 返回 `highlight_ports`（连接/断开端口）或 `highlight_empty_slot`（提示放置积木）动作。
- 内置 17 个关卡：`101`、`102`、`103`、`201`、`202`、`203`、`301`、`302`、`401`、`402`、`403`、`501`、`502`、`503`、`504`、`601`、`602`。
- 支持二进制、半加器、全加器、三输入奇偶和三输入进位练习。
- 提供浏览器电路模拟器、文字决策测试、语音协议测试、Session 日志和 LLM 评测。

## 安装与启动

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；音频抓包需要 `ffmpeg`。

```bash
cp .env.example .env
uv sync --extra dev
uv run uvicorn tuco_ai_backend.main:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://127.0.0.1:8000/`（测试台）、`http://127.0.0.1:8000/docs`（OpenAPI 文档）或 `GET /api/health`（健康检查）。设备应把 `CONFIG_TUCO_REMOTE_ASSISTANT_URL` 配为设备可访问的后端地址。

## 配置

复制 `.env.example` 为 `.env` 后填写实际值。不要提交 `.env` 或把密钥写入日志、网页、固件和文档。

| 变量 | 作用 |
| --- | --- |
| `TUCO_LLM_BASE_URL` | OpenAI 兼容服务根地址。 |
| `TUCO_LLM_MODEL` | 模型名称。 |
| `TUCO_LLM_API_KEY` | LLM 密钥。 |
| `TUCO_LLM_TIMEOUT_SECONDS` | LLM 超时，默认 45 秒。 |
| `TUCO_VOLC_API_KEY` | WebSocket 语音管线的火山密钥。当前固件本机语音不经此后端转发。 |
| `TUCO_VOLC_ASR_RESOURCE_ID` | ASR 资源，默认 `volc.bigasr.sauc.duration`。 |
| `TUCO_VOLC_TTS_RESOURCE_ID` | TTS 资源，默认 `seed-tts-2.0`。 |
| `TUCO_VOLC_TTS_VOICE_TYPE` | TTS 音色，默认 `zh_female_vv_uranus_bigtts`。 |
| `TUCO_DEVICE_TOKEN` | WebSocket `device.hello` 令牌；HTTP 接口当前不读取。 |
| `TUCO_ADMIN_TOKEN` | 配置和音频抓包接口的管理员令牌。 |
| `TUCO_MAX_AUDIO_BYTES` | WebSocket 单轮 PCM 限制，默认 512 KiB。 |
| `TUCO_PIPELINE_TIMEOUT_SECONDS` | WebSocket 管线超时，默认 120 秒。 |
| `TUCO_AUDIO_CAPTURE_ENABLED` | 是否抓包，默认 `false`。 |
| `TUCO_AUDIO_CAPTURE_DIR` | 抓包目录，默认 `runtime/audio_captures`。 |
| `TUCO_AUDIO_CAPTURE_RETENTION_DAYS` / `TUCO_AUDIO_CAPTURE_MAX_FILES` | 抓包保留天数/数量，默认 7 天/100 个。 |

网页或 `PUT /api/config` 可更新模型和火山配置，更新会写回 `.env`；接口不会返回密钥。配置 `TUCO_ADMIN_TOKEN` 后，`/api/config` 和 `/api/debug/audio-captures` 需要 `X-Tuco-Admin-Token` 请求头。

## HTTP 电路助教接口

请求：`POST /api/device/circuit-coach/decision`。

```json
{
  "session_id": "fw-201-3",
  "user_text": "下一步怎么接？",
  "direct_hint_requested": true,
  "circuit_snapshot": {
    "schema": "tuco_circuit_v2",
    "level": {
      "id": 201, "rule_version": 1, "goal": "完成当前关卡",
      "inputs": "A、B", "outputs": "结果", "input_count": 2, "output_count": 1
    },
    "unlocked_gates": ["INPUT", "OUTPUT", "AND"],
    "gate_templates": [],
    "board": {
      "topology_revision": 12, "link_count": 1,
      "invalid_link_count": 0, "ignored_link_count": 0,
      "link_overflow": false, "slots": [], "edges": []
    }
  }
}
```

示例中的 `slots` 和 `edges` 仅为占位，设备必须发送完整快照。后端已显式兼容当前固件发送的 `direct_hint_requested` 字段；省略时默认为 `false`。该字段目前作为请求上下文保留，助教仍会结合 `user_text` 和电路状态判断具体提示策略。

响应：

```json
{
  "assistant_text": "先把输入积木的输出端接到与门的一个输入端。",
  "tool_call": {
    "call_id": "rev12-action-1",
    "name": "highlight_ports",
    "arguments": {"output_port": 3, "input_port": 9, "intent": "connect"}
  },
  "topology_revision": 12
}
```

后端只把当前快照中验证过的候选映射为工具调用；固件仍必须检查端口角色、槽位状态和 `topology_revision`，拓扑变化后丢弃过期动作。

## WebSocket v2、测试台与模拟器

控制帧是 UTF-8 JSON，音频是 PCM S16LE、16 kHz、单声道二进制帧。典型顺序：

```text
device.hello -> device.ready -> session.start -> session.ready
-> circuit.snapshot -> input_audio.start -> PCM
-> input_audio.commit -> asr.result / device.command / response.audio.*
```

完整字段和时序见 [docs/device-protocol.md](docs/device-protocol.md)。模拟器：

```bash
uv run python simulator/device_simulator.py --url ws://127.0.0.1:8000/ws/device
```

WebSocket 语音管线需同时配置 `TUCO_LLM_API_KEY` 与 `TUCO_VOLC_API_KEY`；HTTP 固件流程只要求后端 LLM 配置。

## 验证、评测与调试

```bash
uv run pytest
uv run ruff check .
node --check src/tuco_ai_backend/frontend/app.js
uv run python scripts/run_concurrent_level_llm_eval.py --list-levels
uv run python scripts/run_concurrent_level_llm_eval.py --level 401 --level 403 --concurrency 4
```

评测报告写入 `runtime/llm_evaluations`。设置 `TUCO_AUDIO_CAPTURE_ENABLED=true` 可保存 WebSocket 输入/输出音频，使用管理员令牌访问 `GET /api/debug/audio-captures`；音频可能含隐私，调试后应清理。

## API 概览

- `GET /api/health`：健康检查。
- `GET/PUT /api/config`：脱敏配置读写。
- `GET /api/tools`：文字决策工具定义。
- `POST /api/device/circuit-coach/decision`：当前固件使用的电路助教接口。
- `POST /api/test/decision`：文字决策测试。
- `POST /api/test/sessions/start`、`GET /api/test/sessions`、`GET /api/test/sessions/{session_id}`、`POST /api/test/sessions/end`：测试 Session。
- `GET /api/debug/audio-captures`、`GET /api/debug/audio-captures/{name}`：音频抓包。
- `WS /ws/device`：WebSocket v2；`GET /docs`：交互式 API 文档。

## 目录说明

```text
src/tuco_ai_backend/
  main.py                     FastAPI 路由
  models.py                   请求/响应/快照模型
  providers/                  LLM、火山 ASR/TTS 适配器
  circuit_graph.py            电路图构建
  circuit_simulator.py        逻辑仿真
  circuit_diagnostics.py      电路诊断
  circuit_planner.py          安全动作规划
  semantic_action_gateway.py  候选动作映射
  level_logic.py + data/      关卡规则
  device_ws.py               WebSocket 状态机
  voice_pipeline.py           ASR -> LLM -> TTS
  session_store.py            Session/Trace 记录
  evaluation*.py              评测功能
  frontend/                   浏览器测试台和模拟器
simulator/                    Python 设备模拟器
scripts/                      评测辅助脚本
docs/                         协议、架构和嵌入式文档
tests/                        测试
runtime/                      本地运行产物，不应提交敏感数据
```

## 安全边界

- 不提交密钥、令牌和带语音的运行日志。
- `TUCO_ADMIN_TOKEN` 只保护配置和音频抓包；HTTP 决策、文字测试和 Session 查询没有同等内置鉴权。公网部署必须增加反向代理、网络访问控制或额外认证。
- 工具动作不是硬件执行确认，固件必须按快照和拓扑版本再次校验。
- 修改槽位、端口角色或关卡规则时，同时更新固件序列化、后端模型、测试和文档。

更多细节见 [docs/architecture.md](docs/architecture.md)、[docs/embedded-integration.md](docs/embedded-integration.md) 和 [docs/implementation.md](docs/implementation.md)。
