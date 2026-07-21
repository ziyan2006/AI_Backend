# TUCO AI 后端设计规格

日期：2026-07-21

## 决策

将豆包端到端语音会话拆分为火山 ASR、OpenAI 兼容 GPT 和火山 TTS。ESP32 继续负责音频 I/O、电路扫描和端口灯效，后端负责 AI 决策。

## 成功标准

- 保持长按说话、松开提交。
- 保持当前稳定的 PCM 播放实现。
- 每轮向后端提供结构化电路快照。
- 支持严格、可审计的 `highlight_ports` 工具调用。
- 可通过网页和模拟器在没有设备时验证协议与模型配置。
- ASR、LLM、TTS 可独立替换和测试。

## 非目标

- 不在本仓库修改 `E:\emb_agent`。
- 不重新设计 `board_snapshot_t`。
- 不在首个基础提交中完成火山 ASR/TTS 生产接入。
- 不将 API Key 持久化到网页或仓库。

## 参考

详细设计见 `docs/architecture.md`、`docs/device-protocol.md`、`docs/embedded-integration.md` 和 `docs/implementation.md`。

