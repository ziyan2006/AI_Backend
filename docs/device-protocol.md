# 设备 WebSocket 协议 v2

## 1. 传输

- 地址：`wss://<host>/ws/device`
- JSON 控制消息：WebSocket 文本帧，UTF-8。
- 音频数据：WebSocket 二进制帧。
- 每条 JSON 都包含 `type`；请求可包含 `request_id` 便于日志关联。
- 音频格式固定为 PCM S16LE、16000 Hz、单声道。

设备必须等待 `device.ready` 后才能开始会话。第一版一个连接只允许一个活动会话，不进行音频复用。

## 2. 设备到后端

### `device.hello`

```json
{
  "type": "device.hello",
  "protocol_version": 2,
  "device_id": "esp32s3box3b-001",
  "firmware_version": "0.3.0",
  "capabilities": {
    "audio": "pcm_s16le_16000_mono",
    "max_ports": 64,
    "tools": ["highlight_ports"]
  }
}
```

### `session.start`

```json
{
  "type": "session.start",
  "session_id": "01J...",
  "locale": "zh-CN"
}
```

### `circuit.snapshot`

字段名称应与固件 `board_snapshot_t` 一一映射，允许新增字段但不能改变已有字段语义。

```json
{
  "type": "circuit.snapshot",
  "schema_version": 1,
  "session_id": "01J...",
  "topology_revision": 42,
  "slots": [
    {
      "slot": 0,
      "component": "and_gate",
      "ports": [
        {"id": 8, "role": "input_a"},
        {"id": 9, "role": "input_b"},
        {"id": 10, "role": "output"}
      ]
    }
  ],
  "valid_links": [
    {"from": 2, "to": 8}
  ],
  "invalid_links": [],
  "scan": {
    "count": 135,
    "stable_count": 4
  }
}
```

### `input_audio.start`

```json
{
  "type": "input_audio.start",
  "session_id": "01J...",
  "format": "pcm_s16le",
  "sample_rate_hz": 16000,
  "channels": 1
}
```

收到后，设备连续发送二进制 PCM 帧。建议每帧 20–100 ms，避免极小帧造成调度开销，也避免大帧增加首包延迟。

### `input_audio.commit`

长按按键松开后发送：

```json
{
  "type": "input_audio.commit",
  "session_id": "01J..."
}
```

### `device.command.result`

```json
{
  "type": "device.command.result",
  "session_id": "01J...",
  "call_id": "call_abc123",
  "ok": true,
  "message": "ports highlighted"
}
```

### `heartbeat`

```json
{"type": "heartbeat", "timestamp_ms": 1784635200000}
```

## 3. 后端到设备

### `device.ready`

```json
{
  "type": "device.ready",
  "protocol_version": 2,
  "connection_id": "..."
}
```

### `session.ready`

```json
{"type": "session.ready", "session_id": "01J..."}
```

### `input_audio.committed`

```json
{
  "type": "input_audio.committed",
  "session_id": "01J...",
  "audio_bytes": 64000
}
```

### `asr.result`

```json
{
  "type": "asr.result",
  "session_id": "01J...",
  "text": "为什么这个灯不亮",
  "is_final": true
}
```

### `response.started`

```json
{"type": "response.started", "session_id": "01J..."}
```

### `device.command`

```json
{
  "type": "device.command",
  "session_id": "01J...",
  "call_id": "call_abc123",
  "name": "highlight_ports",
  "topology_revision": 42,
  "arguments": {
    "ports": [8, 21],
    "duration_ms": 3000,
    "pattern": "pulse",
    "reason": "与门缺少第二个输入连接"
  }
}
```

设备应校验拓扑版本和参数范围，立即安排非阻塞执行，并返回 `device.command.result`。灯效不能阻塞音频接收或播放任务。

### `response.audio.start`

```json
{
  "type": "response.audio.start",
  "session_id": "01J...",
  "format": "pcm_s16le",
  "sample_rate_hz": 16000,
  "channels": 1
}
```

此消息之后的二进制帧都属于 TTS PCM，直到 `response.audio.done`。

### `response.audio.done`

```json
{
  "type": "response.audio.done",
  "session_id": "01J...",
  "audio_bytes": 128000
}
```

### `error`

```json
{
  "type": "error",
  "session_id": "01J...",
  "code": "asr.no_speech",
  "message": "没有识别到有效语音",
  "retryable": true
}
```

建议错误码：

- `protocol.invalid_message`
- `protocol.invalid_state`
- `audio.invalid_format`
- `asr.no_speech`
- `asr.provider_error`
- `llm.provider_error`
- `tool.invalid_arguments`
- `tool.topology_changed`
- `tool.device_timeout`
- `tts.provider_error`

## 4. 时序要求

1. `circuit.snapshot` 必须先于本轮 `input_audio.commit`。
2. 二进制上行音频只允许出现在 `input_audio.start` 与 `input_audio.commit` 之间。
3. 二进制下行音频只允许出现在 `response.audio.start` 与 `response.audio.done` 之间。
4. 设备收到新的 `device.command` 时不得暂停或清空 TTS 播放缓冲区。
5. 后端若检测到重复 `call_id`，不得再次执行命令。
6. 连接重建后必须重新发送 `device.hello`、`session.start` 和完整电路快照。

## 5. 兼容性

- 协议主版本不兼容时，后端拒绝连接并返回 `protocol.unsupported_version`。
- JSON 中未知字段必须忽略，以支持向前扩展。
- 新增可选消息时不得改变现有消息的时序语义。
- 电路快照独立使用 `schema_version`，与 WebSocket 协议版本分开演进。
