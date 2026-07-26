# 嵌入式端接入说明

## 0. 新固件 `circuit_coach_v2` 后端助教模式

新固件工作区为 `E:\emb_agent_new\main`。设备设置页应提供两种助教方式：

```text
固件直连助教：ESP-Claw / DeepSeek
AI 后端助教：本项目 FastAPI
```

AI 后端助教不复用下文的音频 WebSocket 协议。新固件继续在本地完成 ASR、TTS、
电路扫描与 LED 高亮；ASR 完成后使用本机配置的后端地址请求：

```text
POST /api/device/circuit-coach/decision
```

比赛版本不要求设备 Token。请求体固定为：

```json
{
  "session_id": "device-level-session",
  "user_text": "接下来应该怎么做？",
  "circuit_snapshot": {
    "schema": "tuco_circuit_v2",
    "level": {"id": 101, "goal": "...", "inputs": "...", "outputs": "..."},
    "unlocked_gates": ["INPUT", "OUTPUT"],
    "gate_templates": [["INPUT", ["up", "output"]]],
    "board": {"topology_revision": 0, "slots": [], "edges": []}
  }
}
```

响应体与旧测试台无关，设备只处理两类结果之一：

```json
{"assistant_text": "自然中文回复", "tool_call": null, "topology_revision": 0}
```

```json
{
  "assistant_text": null,
  "tool_call": {
    "name": "highlight_ports",
    "arguments": {"output_port": 0, "input_port": 4}
  },
  "topology_revision": 0
}
```

或：

```json
{
  "assistant_text": null,
  "tool_call": {
    "name": "highlight_empty_slot",
    "arguments": {"slot": 6, "gate": "NAND"}
  },
  "topology_revision": 0
}
```

固件必须继续校验端口/槽位、积木解锁状态与拓扑版本，随后自行播放既有的固定高亮提示。

> 当前新固件的 `highlight_empty_slot` 会在任意未连接输出端和输入端同时存在时拒绝空槽提示。
> 对需要先放置逻辑门的关卡，已摆好输入/输出积木时仍会出现这种端口对；在接入前应放宽该固件限制，
> 由模型的 `unlocked_gates` 和固件的槽位/积木校验共同决定是否可高亮空槽。

## 1. 改造原则

本次改造允许修改嵌入式通信层，但不重写已经稳定的音频播放逻辑。设备从“直接连接豆包端到端语音模型”改为“连接自建 AI 后端”。

保持现有交互：

```text
按住按钮 -> 开始录音和上传
松开按钮 -> 停止录音并提交本轮语音
```

不要改成按一下开始、再按一下停止。

## 2. 必须保持不变的播放接口

当前固件位于 `E:\emb_agent\main`，稳定播放入口如下：

- `audio_self_test_voice_playback_begin()`
- `audio_self_test_voice_playback_push()`
- `audio_self_test_voice_playback_finish()`
- `audio_self_test_voice_playback_abort()`

后端下发 `response.audio.start` 时调用 `begin`；每个二进制下行 PCM 帧调用 `push`；收到 `response.audio.done` 时调用 `finish`；连接断开或 TTS 错误时调用 `abort`。

音频格式必须继续使用：

```text
PCM S16LE / 16000 Hz / mono
```

设备现有播放模块包含固定写块、预缓冲和断流恢复机制。通信任务只负责持续喂入数据，不能绕过该 API 直接写扬声器，也不能因为解析工具命令而阻塞音频接收。

## 3. 电路快照改造

固件已经通过 `board_snapshot_t` 定义完整状态，无需设计第二套电路模型。需要新增一个纯序列化层，将同一份快照转换为 `circuit.snapshot` JSON：

```text
board_snapshot_t
  -> board_snapshot_to_json_v1()
  -> WebSocket 文本帧 circuit.snapshot
```

序列化必须包含 16 个槽位、64 个端口角色、合法/非法连接、拓扑版本和扫描计数。原先在 `volcengine_voice.c` 中把快照转换成中文系统提示词的逻辑不再用于 AI 请求，提示词由后端维护。

推荐在以下时机发送完整快照：

1. `session.start` 后。
2. 用户按住按钮开始本轮录音前，若拓扑版本有变化。
3. 会话中扫描器确认稳定拓扑发生变化时。

第一版不需要做增量 diff，完整快照体积可控，兼容和调试更简单。

## 4. 通信任务职责

建议新增独立的后端 WebSocket 客户端模块，不把新协议继续堆入豆包协议解析代码。

### 上行

- 连接建立后发送 `device.hello`。
- 长按触发时确保会话和快照已就绪，发送 `input_audio.start`。
- 从现有采集 ring buffer 读取 PCM，直接发送 WebSocket 二进制帧。
- 松开按键后停止采集并发送 `input_audio.commit`。
- 执行端口提示命令后发送 `device.command.result`。

### 下行

- 文本帧进入控制消息解析器。
- 二进制帧只有在 `response.audio.start` 后才进入播放 `push`。
- `device.command` 放入单独队列，由灯效任务执行。
- `error` 更新屏幕状态并按 `retryable` 决定是否允许立即重试。

## 5. 屏幕状态映射

建议保留当前中文状态风格，并按协议事件映射：

| 协议事件 | 屏幕状态 |
|---|---|
| 按键按下且已开始采集 | 正在听你说…… |
| `input_audio.committed` | 正在思考…… |
| `asr.result` | 可选显示识别文本 |
| `response.audio.start` | 提示来了！ |
| `response.audio.done` | 恢复待机 |
| `asr.no_speech` | 没有听清，请再说一次 |
| 其他 `error` | 显示简短错误并恢复可重试状态 |

“一直 thinking”必须由后端超时和设备本地兜底共同避免。设备在提交音频后启动响应超时；超时后主动结束本轮并提示重试，但不破坏 WebSocket 长连接。

## 6. `highlight_ports` 执行

设备收到命令后依次校验：

1. `name` 为 `highlight_ports`。
2. `topology_revision` 等于当前稳定快照版本。
3. 端口号位于 `0..63`，数量不超过 4。
4. `duration_ms` 位于 `500..10000`。
5. `pattern` 为 `blink` 或 `pulse`。
6. `call_id` 未执行过。

灯效必须非阻塞运行，到期后恢复原显示状态。重复 `call_id` 返回第一次结果，不重复启动 10 秒计时。

## 7. 推荐改造顺序

1. 保留现有音频采集/播放模块，新增协议客户端。
2. 先接通 `device.hello`、会话和心跳，不发送音频。
3. 接通上行 PCM，并用后端模拟响应验证字节数。
4. 接通下行测试 PCM，确认仍然连续播放。
5. 增加电路 JSON 序列化和快照版本校验。
6. 增加端口提示命令队列。
7. 最后切换到真实 ASR/GPT/TTS 管线。

## 8. 联调验收

- 连续进行 30 次长按对话，无卡死、无永久 thinking。
- 录音 1 秒、5 秒和 15 秒时，上行字节数符合采样率。
- TTS 首包延迟、播放 underrun 次数和总字节数可从日志查看。
- 拔网重连后能重新握手并发送完整快照。
- 电路在模型思考期间变化时，旧拓扑命令被设备拒绝。
- 工具灯效执行期间，TTS 播放无明显断续。

