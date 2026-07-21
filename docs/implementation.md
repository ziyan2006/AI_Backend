# AI 后端实施方案

## 1. 技术栈

- Python 3.11+
- FastAPI + WebSocket
- Pydantic 数据模型与严格工具参数校验
- HTTPX 调用 OpenAI 兼容接口及火山引擎 HTTP/WebSocket API
- Pytest 自动化测试
- 原生 HTML/CSS/JavaScript 测试台

## 2. 配置

环境变量统一使用 `TUCO_` 前缀：

```text
TUCO_LLM_BASE_URL
TUCO_LLM_MODEL
TUCO_LLM_API_KEY
TUCO_LLM_TIMEOUT_SECONDS
TUCO_VOLC_ASR_APP_ID
TUCO_VOLC_ASR_ACCESS_TOKEN
TUCO_VOLC_TTS_APP_ID
TUCO_VOLC_TTS_ACCESS_TOKEN
TUCO_VOLC_TTS_VOICE
```

首个提交只实现 LLM 配置。火山凭据在对应适配器开发时加入 `.env.example`。生产环境从密钥管理服务注入，测试页面写入的 Key 只保留在当前进程内存。

## 3. 阶段一：协议与模型测试台

### 交付

- `/api/health`：服务存活和能力状态。
- `/api/config`：查询/更新 Base URL、模型和临时 Key，响应永不包含 Key。
- `/api/tools`：查看当前工具 schema。
- `/api/test/decision`：用手工问题和电路 JSON 测试 GPT 决策。
- `/ws/device`：握手、会话、快照、PCM 字节计数和提交确认。
- 浏览器测试台：配置、请求、64 端口动画和 WebSocket 日志。
- Python 设备模拟器。

### 验收

- 模型不需要工具时返回自然语言。
- 模型需要提示时生成合法 `highlight_ports`。
- 非法端口、超长时长和未知模式被后端拒绝。
- API Key 不出现在配置响应、日志、Git diff 中。
- 模拟器可以完成握手、会话、快照、录音开始、二进制 PCM 和提交。

## 4. 阶段二：火山 ASR

### 实现

定义供应商无关接口：

```python
class AsrSession(Protocol):
    async def push_audio(self, pcm: bytes) -> None: ...
    async def commit(self) -> AsrResult: ...
    async def close(self) -> None: ...
```

每个设备录音轮次创建一个 ASR 会话。PCM 二进制帧进入有界队列，由单独协程发送到火山；WebSocket 接收循环不能被供应商网络请求阻塞。

### 测试

- 使用录制好的 16 kHz PCM fixture 模拟真实设备数据。
- Mock 火山协议的部分结果、最终结果、无语音、超时和断连。
- 验证 `input_audio.commit` 后只产生一次最终文本。
- 短音频无语音时返回 `asr.no_speech`，设备可以立即重试。

## 5. 阶段三：GPT 编排和工具闭环

### 输入

- 后端系统提示词。
- ASR 最终文本。
- 当前完整电路快照。
- 必要的短对话历史。
- `highlight_ports` 工具 schema。

### 执行

1. 用 `stream=false` 请求工具决策。
2. 若无工具，直接取得最终回答。
3. 若有工具，Pydantic 校验参数并检查拓扑版本。
4. 下发 `device.command`，等待对应 `call_id` 的结果。
5. 以 `role=tool` 将结果回传模型，获得最终教学回答。
6. 不在第一版开启并行工具调用。

### 测试

- 无工具、合法工具、非法 JSON、多个工具、设备拒绝和工具超时。
- 相同 `call_id` 的幂等行为。
- 模型选择端口必须来自当前快照。
- 拓扑变化时不执行旧命令。

## 6. 阶段四：火山 TTS

定义供应商无关流式接口：

```python
class TtsProvider(Protocol):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
```

服务向火山请求 16 kHz、单声道、S16LE PCM。首块有效 PCM 到达时先发送 `response.audio.start`，然后发送二进制块，结束时发送 `response.audio.done`。

### 防卡顿要求

- 不转码为 MP3，不让 ESP32 解码有损格式。
- 使用有界队列和持续发送协程。
- 供应商返回的小碎片在后端聚合为合理块再发送。
- 建议单个下行二进制帧 1024–4096 字节。
- TTS 生产中断时发送 `error` 并让设备调用 playback abort。
- 日志记录首包时间、总 PCM 字节、发送耗时和最大队列深度。

## 7. 阶段五：真实固件联调

用模拟器通过后，再进行嵌入式最小改造：

1. 新 WebSocket 客户端替换豆包端到端协议入口。
2. `board_snapshot_t` 增加 JSON v1 序列化。
3. 保持长按录音和现有 playback API。
4. 增加 `highlight_ports` 非阻塞执行器。
5. 开启全链路阶段耗时日志。

## 8. 测试数据

所有后端测试使用与固件结构一致的模拟数据：

- 16 槽位和 64 端口编号。
- 与门只连接一个输入的故障快照。
- 包含非法连接的快照。
- 录音时长对应的零值/正弦/真实语音 PCM fixture。
- 拓扑版本在思考阶段变化的竞态用例。

测试 fixture 不包含真实用户音频、设备密钥或云端凭据。

## 9. 部署建议

开发阶段运行单实例 Uvicorn。生产阶段：

- TLS 由反向代理终止。
- 设备使用独立认证令牌，不复用模型 API Key。
- 配置接口增加管理员鉴权或仅监听管理网。
- 会话状态放入单进程内存时保持单 worker；扩展到多 worker 前引入 Redis。
- 为 ASR、LLM、工具等待和 TTS 分别设置超时和指标。
- 通过结构化日志关联 `connection_id`、`session_id`、`call_id`。

## 10. 完成定义

正式后端完成需同时满足：

- 模拟器和真实设备均能完成 30 次连续语音交互。
- 端口工具调用闭环可追踪、可拒绝、可超时、可幂等。
- TTS 播放不比当前豆包版本更卡顿。
- ASR 无语音不会造成永久 thinking。
- 所有密钥均未进入源码、日志、前端响应或 Git 历史。
- 接入文档足以让未参与后端开发的嵌入式工程师独立联调。

