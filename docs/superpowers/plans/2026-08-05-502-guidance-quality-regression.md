# 502 多轮引导质量回归实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 502 六轮质量流程固化为评测预设，并通过通用意图、真值表诊断和上下文化工具语音修复已观察到的回复问题。

**架构：** 评测预设模块生成标准 `ConversationScenario`；诊断模块把图结构和真值表状态转换成稳定事实与可拆除错误边；Circuit Coach 根据意图选择动作计划、求助事实或诊断事实。工具执行仍由语义候选网关控制。

**技术栈：** Python 3.12、Pydantic、pytest、httpx、现有 circuit graph/simulator/planner。

---

### 任务 1：增加 502 多轮评测预设

**文件：**
- 创建：`src/tuco_ai_backend/evaluation_presets.py`
- 修改：`src/tuco_ai_backend/evaluation_cli.py`
- 创建：`tests/test_evaluation_presets.py`
- 修改：`tests/test_evaluation_cli.py`

- [ ] 编写失败测试，断言 `502-guidance-quality` 包含六轮、每轮 16 个槽位，并包含空板、错误直连和部分或门进度。
- [ ] 编写失败测试，断言 CLI 接受 `--conversation-preset 502-guidance-quality` 并写出多轮报告。
- [ ] 实现预设构造和 CLI 合并加载，重复场景名称返回参数错误。
- [ ] 运行 `pytest -q tests/test_evaluation_presets.py tests/test_evaluation_cli.py -k 'preset or conversation'`。

### 任务 2：增加通用真值表诊断

**文件：**
- 创建：`src/tuco_ai_backend/circuit_diagnostics.py`
- 创建：`tests/test_circuit_diagnostics.py`
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`

- [ ] 编写失败测试，使用 502 错误直连快照断言诊断返回与门到最终输出的可拆除边和儿童事实。
- [ ] 编写失败测试，断言正确输出不产生错误连线诊断，物理无效边仍有事实记录。
- [ ] 使用 `build_circuit_graph`、`simulate` 和关卡 `LevelLogicSpec` 实现诊断。
- [ ] 在明确“下一步”请求中，将诊断出的错误输出边优先转换成 `DisconnectPortsAction` 候选。
- [ ] 运行 `pytest -q tests/test_circuit_diagnostics.py tests/test_circuit_coach_v2.py -k 'diagnos or disconnect'`。

### 任务 3：扩展求助与检查意图

**文件：**
- 修改：`src/tuco_ai_backend/providers/openai_compatible.py`
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 修改：`tests/test_llm_service.py`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] 编写失败参数化测试，覆盖“给我一点提示”“我不会了”“接对了吗”“哪里有问题”“为什么不亮”。
- [ ] 实现 `提示求助` 和 `检查诊断`，检查意图优先于普通“为什么”原理意图。
- [ ] 求助模式注入规划候选事实但不开放工具；检查模式注入诊断事实并要求明确指出问题。
- [ ] 强化关卡目标与缺积木提示：包含核心判定条件和准确数量，禁止反问缺什么。
- [ ] 运行 `pytest -q tests/test_llm_service.py tests/test_circuit_coach_v2.py -k 'intent or hint or diagnos or missing'`。

### 任务 4：生成上下文化工具语音

**文件：**
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] 编写失败测试，断言连接与门到或门时语音同时包含“与门”和“或门”。
- [ ] 编写失败测试，断言摆放与门时语音说明“同时成立”，摆放或门时说明“汇总结果”。
- [ ] 让确定性语音读取快照中候选动作两端的积木类型；无法解析时保留现有安全兜底。
- [ ] 运行完整 `tests/test_circuit_coach_v2.py`。

### 任务 5：回归与真实模型复测

**文件：**
- 生成：`runtime/llm_evaluations/*.json`
- 生成：`runtime/llm_evaluations/*.md`

- [ ] 运行 `pytest -q tests/test_evaluation_presets.py tests/test_evaluation_cli.py tests/test_circuit_diagnostics.py tests/test_circuit_coach_v2.py tests/test_llm_service.py tests/test_evaluation_scenarios.py tests/test_evaluation_tracing.py`。
- [ ] 运行 `ruff check src tests` 与 `git diff --check`。
- [ ] 使用 `.env` 执行 `--conversation-preset 502-guidance-quality --concurrency 1`。
- [ ] 读取六轮 trace，对比首次结果，确认目标说明、求助、错误诊断和工具语音均改善。
