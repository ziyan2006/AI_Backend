# 多轮对话评测场景设计

## 1. 背景与目标

当前并发评测器已经能够对同一关卡连续发送多个 `--question`，并在轮次之间保留助手文字历史；但每一轮都会重新生成同一种测试快照，无法表达儿童根据上一轮提示摆放积木、修改接线或保留错误接线后继续提问的真实过程。同时，现有报告只保存规范化后的请求结果，不能完整还原后端实际发送给模型的提示、工具候选和原始响应。

本功能引入统一的多轮场景格式。手写场景和未来从实机 Session 日志导出的场景都转换为同一种 JSON，再由同一个执行器完成并发运行。测试器不对语言行为自动打分，只忠实保存完整诊断日志；后续由开发者或 Codex 读取日志，分析记忆、重复、事实 grounding、儿童友好度和引导连贯性。

## 2. 设计原则

1. 每一轮必须提供完整 `tuco_circuit_v2` 快照，测试器不得推测电路变化。
2. 同一场景共用一个 Session，轮次严格串行；不同场景可并发执行。
3. 评测链路与文字调试链路使用同一个 `CircuitCoachV2Client`，不复刻提示词或规划器。
4. 完整 JSON 报告保留所有诊断内容，Markdown 仅提供便于人工浏览的摘要。
5. 不自动生成语言质量分数，不使用第二个模型评审回复。
6. API Key、Authorization、Cookie 等敏感信息在落盘前统一脱敏。
7. 现有单轮和重复 `--question` 用法继续兼容。

## 3. 统一场景格式

场景文件使用 UTF-8 JSON，顶层结构如下：

```json
{
  "schema": "tuco_conversation_scenario_v1",
  "name": "502-逐步搭建局部进位",
  "description": "从摆好输入输出开始，经历摆门、接线和错误诊断。",
  "level_id": 502,
  "tags": ["majority", "memory", "diagnosis"],
  "turns": [
    {
      "user_text": "接下来应该怎么做？给我点提示",
      "snapshot": {
        "schema": "tuco_circuit_v2",
        "level": {"id": 502, "rule_version": 1},
        "unlocked_gates": [],
        "board": {"topology_revision": 1, "slots": [], "edges": []}
      },
      "note": "输入输出已摆好，尚未放置逻辑门。"
    }
  ]
}
```

字段约束：

- `schema` 固定为 `tuco_conversation_scenario_v1`。
- `name` 是报告中的稳定场景名，同一次运行中不得重复。
- `description` 和 `tags` 仅用于检索与人工说明。
- `level_id` 必须存在于后端关卡目录。
- `turns` 至少包含一轮。
- `user_text` 使用与设备文字链路相同的长度限制。
- `snapshot` 必须通过 `CircuitCoachV2Snapshot` 校验，且 `snapshot.level.id` 必须等于顶层 `level_id`。
- `note` 只写入报告，不发送给模型。
- 相邻轮次允许相同或跳跃的 `topology_revision`，但若电路结构发生变化而修订号未变化，加载阶段记录警告。

第一期不支持快照 patch、自动放置积木或自动执行工具调用。未来的实机日志转换器只负责生成上述标准 JSON。

## 4. 执行模型

每个场景生成稳定的运行时 Session：

```text
{run_id}-scenario-{scenario_index}-{slug}
```

执行规则：

1. 场景通过全局并发信号量获取一个执行槽位。
2. 场景内部依次处理 `turns`，不得并行。
3. 每轮使用该轮完整快照创建 `CircuitCoachDecisionRequest`。
4. 历史只包含之前成功产生的用户问题和非空 `assistant_text`。
5. 工具调用本身不会自动修改下一轮快照；下一轮状态完全以场景文件为准。
6. 某轮请求失败时记录完整异常，不向历史追加伪造回复，并继续执行后续轮次。
7. 每轮结束后保存一次内存中的追踪记录，最终统一生成 JSON 和 Markdown。

场景级并发而非轮次级并发可以保证 Session 历史顺序，同时继续利用多关卡评测器的吞吐能力。

## 5. 全链路追踪

为避免在评测器中复制私有提示构建逻辑，`CircuitCoachV2Client` 增加可选的只读追踪观察器。未注入观察器时生产行为不变。

观察器按 `trace_id` 收集以下阶段：

1. `decision_request`：规范化后的 `CircuitCoachDecisionRequest`。
2. `conversation_history`：进入本轮前实际传入客户端的历史。
3. `semantic_plan`：候选编号、候选动作、事实、搜索状态数、搜索耗时和降级原因。
4. `provider_request`：最终发送给兼容 OpenAI 接口的 JSON，包括 system/user messages、tools 和 tool choice。
5. `provider_response`：HTTP 状态码、响应头白名单和模型原始 JSON。
6. `normalized_decision`：最终返回设备的文字、工具调用和拓扑修订号。
7. `exception`：异常类型、消息和格式化堆栈。

追踪观察器不得改变请求、候选或决策。追踪失败只能写日志，不得导致正常模型请求失败。

## 6. 脱敏规则

写入磁盘前递归处理所有追踪对象：

- `Authorization`、`Proxy-Authorization`、`Cookie`、`Set-Cookie` 整体替换为 `[REDACTED]`。
- 名称包含 `api_key`、`apikey`、`token`、`secret`、`password` 的字段替换为 `[REDACTED]`，大小写不敏感。
- URL 中的查询参数按相同键名规则脱敏。
- 模型提示、用户文字、电路快照和工具参数不脱敏，因为它们是行为分析的必要输入。
- `.env` 内容不进入报告。

## 7. 报告结构

### 7.1 JSON 完整报告

每轮新增以下内容：

```json
{
  "turn_index": 1,
  "note": "输入输出已摆好",
  "user_text": "接下来应该怎么做？",
  "snapshot": {},
  "history_before_turn": [],
  "trace": {
    "semantic_plan": {},
    "provider_request": {},
    "provider_response": {},
    "normalized_decision": {}
  },
  "duration_ms": 1234,
  "error": null
}
```

报告同时保留运行 ID、模型、并发数、场景来源路径、开始结束时间及场景级错误。JSON 是后续行为分析的事实源。

### 7.2 Markdown 摘要

Markdown 按场景展示：

- 场景说明和标签。
- 每轮问题、人工备注、拓扑修订号。
- 相对上一轮新增、删除和改变的槽位及连线摘要。
- 模型回复和设备工具调用。
- 候选数量、搜索耗时、降级原因和请求耗时。
- 异常摘要。

完整提示词、原始响应和完整快照不重复嵌入 Markdown，避免报告无法阅读；Markdown 提供对应 JSON 路径和 `trace_id`。

## 8. CLI 设计

新增可重复参数：

```powershell
python -m tuco_ai_backend.evaluation_cli `
  --conversation-scenario tests/scenarios/502-progressive.json `
  --conversation-scenario tests/scenarios/403-wrong-wire.json `
  --concurrency 2 `
  --output-dir runtime/llm_evaluations
```

规则：

- 指定 `--conversation-scenario` 后自动使用 `circuit-v2`。
- 场景模式不能与 `--level`、`--levels`、`--question`、`--circuit-setup` 或 `--learning-activity` 混用。
- 可重复传入多个场景文件；执行顺序按命令行顺序，报告顺序稳定。
- 文件不存在、JSON 无效、关卡不匹配或场景名重复时，在发起任何模型请求前整体失败并返回退出码 2。
- 模型请求存在失败轮次时仍生成报告，并返回退出码 1。
- 所有轮次成功时返回退出码 0。

## 9. 实机日志转换边界

方案 C 的统一格式允许后续增加独立命令：

```powershell
python -m tuco_ai_backend.session_log_converter session.json --output scenario.json
```

转换器只提取 Session 中的用户输入和每轮请求时的完整快照，不复制旧模型回复作为新的历史。生成的场景必须通过同一加载器校验。转换器属于第二期，不阻塞第一期场景执行器。

## 10. 测试策略

1. 场景模型校验：空 turns、关卡不一致、重复名称和非法快照必须失败。
2. 多轮历史：第二轮收到第一轮真实问答，第三轮收到前两轮问答。
3. 快照演进：每轮客户端收到对应完整快照和修订号。
4. 并发边界：不同场景并发，同一场景最大活动轮次始终为一。
5. 失败延续：中间轮失败后仍执行下一轮，历史不包含失败轮助手内容。
6. 追踪完整性：请求、候选、原始响应和规范化结果均按同一 `trace_id` 归档。
7. 脱敏：嵌套 Header、字段和 URL 查询参数中的秘密不会落盘。
8. CLI 冲突：场景参数与旧评测参数混用时返回退出码 2。
9. 向后兼容：现有单关卡、重复问题和学习活动评测测试保持通过。

## 11. 分期实施

### 第一期：场景执行与基础报告

- 场景 Pydantic 模型和加载器。
- 场景级并发、轮次级串行执行。
- 每轮完整快照、历史和规范化决策记录。
- CLI `--conversation-scenario`。
- JSON 与 Markdown 场景报告。

### 第二期：模型原始链路追踪

- 可选追踪观察器。
- 语义候选、最终 provider payload 和原始 HTTP JSON 留存。
- 统一递归脱敏。

### 第三期：实机日志转换

- Session 日志到统一场景 JSON 的转换器。
- 来源追踪和转换警告。
- 使用真实 Session 建立回归场景库。

## 12. 非目标

- 不自动判断回复是否“好”或生成综合分数。
- 不使用评审模型。
- 不自动执行模型工具调用来修改快照。
- 不在评测器中实现第二套电路模拟器。
- 不改变固件协议、云端部署或设备烧录流程。
