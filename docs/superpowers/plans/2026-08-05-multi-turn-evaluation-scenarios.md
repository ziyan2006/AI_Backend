# 多轮对话评测场景实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让后端评测器并发执行包含逐轮完整电路快照的多轮场景，并保存可供人工分析的完整模型请求、原始响应和规范化决策。

**架构：** 标准场景 JSON 由独立 Pydantic 加载器校验；场景之间并发、场景内部串行。`CircuitCoachV2Client` 通过可选只读追踪观察器暴露语义规划和 Provider 交换，评测器将追踪与轮次结果合并后输出完整 JSON 和简洁 Markdown。实机日志转换器只负责生成同一标准场景格式。

**技术栈：** Python 3.11、Pydantic v2、asyncio、httpx、pytest、ruff、OpenAI-compatible Chat Completions。

---

## 文件职责

### 新文件

- `src/tuco_ai_backend/evaluation_scenarios.py`：场景模型、加载校验、快照差异摘要和场景结果类型。
- `src/tuco_ai_backend/evaluation_tracing.py`：追踪事件协议、内存收集器、递归脱敏。
- `src/tuco_ai_backend/session_log_converter.py`：实机 Session 日志转换为标准场景 JSON。
- `tests/test_evaluation_scenarios.py`：模型校验、场景执行、并发和报告测试。
- `tests/test_evaluation_tracing.py`：追踪收集与脱敏测试。
- `tests/test_session_log_converter.py`：Session 转换测试。
- `tests/scenarios/502-progressive.json`：502 逐步搭建示例。
- `tests/scenarios/502-wrong-wire.json`：502 错误接线诊断示例。

### 修改文件

- `src/tuco_ai_backend/evaluation.py`：场景执行器和场景报告写入入口。
- `src/tuco_ai_backend/evaluation_cli.py`：`--conversation-scenario` 模式和参数冲突校验。
- `src/tuco_ai_backend/providers/circuit_coach_v2.py`：可选追踪观察器埋点。
- `tests/test_evaluation.py`：多轮历史和旧评测兼容回归。
- `tests/test_evaluation_cli.py`：场景 CLI 集成与退出码。
- `tests/test_circuit_coach_v2.py`：Provider 追踪事件测试。
- `docs/implementation.md`、`PROJECT_HANDOFF.md`：使用方法和当前状态。

---

### 任务 1：建立标准场景模型与加载器

**文件：**
- 创建：`src/tuco_ai_backend/evaluation_scenarios.py`
- 创建：`tests/test_evaluation_scenarios.py`

- [ ] **步骤 1：编写场景校验失败测试**

```python
def test_scenario_requires_matching_level_and_non_empty_turns(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({
        "schema": "tuco_conversation_scenario_v1",
        "name": "invalid",
        "level_id": 502,
        "turns": [{
            "user_text": "下一步呢",
            "snapshot": build_snapshot(level_id=403, revision=1),
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot level 403 does not match scenario level 502"):
        load_conversation_scenarios([path])
```

- [ ] **步骤 2：运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py
```

预期：收集阶段失败，因为 `evaluation_scenarios` 尚不存在。

- [ ] **步骤 3：实现模型与加载器**

```python
class ConversationScenarioTurn(BaseModel):
    user_text: str = Field(min_length=1, max_length=2000)
    snapshot: CircuitCoachV2Snapshot
    note: str | None = Field(default=None, max_length=500)


class ConversationScenario(BaseModel):
    schema_name: Literal["tuco_conversation_scenario_v1"] = Field(alias="schema")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    level_id: int = Field(ge=1, le=9999)
    tags: list[str] = Field(default_factory=list)
    turns: list[ConversationScenarioTurn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_turn_levels(self) -> Self:
        for turn in self.turns:
            if turn.snapshot.level.id != self.level_id:
                raise ValueError(
                    f"snapshot level {turn.snapshot.level.id} does not match "
                    f"scenario level {self.level_id}"
                )
        return self
```

`load_conversation_scenarios(paths)` 必须在返回前检查文件存在、JSON 对象格式、重复场景名和关卡目录存在性。加载结果同时保存 `source_path` 和修订号警告。

- [ ] **步骤 4：增加结构变化未提升修订号警告测试**

比较相邻轮次的 `slots` 和 `edges`；结构变化但 `topology_revision` 相同只记录警告，不拒绝场景。

- [ ] **步骤 5：运行测试和静态检查**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py
.\.venv\Scripts\python.exe -m ruff check src tests
```

- [ ] **步骤 6：提交**

```powershell
git add -- src/tuco_ai_backend/evaluation_scenarios.py tests/test_evaluation_scenarios.py
git commit -m "feat: 定义多轮评测场景格式"
```

---

### 任务 2：实现场景级并发与轮次级串行执行

**文件：**
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`src/tuco_ai_backend/evaluation_scenarios.py`
- 修改：`tests/test_evaluation_scenarios.py`

- [ ] **步骤 1：编写多轮历史与快照演进测试**

```python
@pytest.mark.asyncio
async def test_scenario_turns_share_history_but_use_each_full_snapshot() -> None:
    client = RecordingScenarioClient()
    scenario = scenario_with_revisions(10, 11, 12)

    report = await run_conversation_scenarios(
        client,
        scenarios=[scenario],
        concurrency=1,
        model="fake",
        run_id="multi-turn",
    )

    assert [item.revision for item in client.calls] == [10, 11, 12]
    assert client.calls[0].history == []
    assert client.calls[1].history == [
        {"role": "user", "content": scenario.turns[0].user_text},
        {"role": "assistant", "content": "reply-1"},
    ]
    assert report.scenarios[0].turns[2].history_before_turn == client.calls[2].history
```

- [ ] **步骤 2：编写并发边界与失败延续测试**

两个场景应同时活动；同一场景的最大活动轮次数必须为 1。第二轮抛异常后第三轮仍执行，且第三轮历史不包含失败轮的助手消息。

- [ ] **步骤 3：运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py -k "history or concurrency or failure"
```

- [ ] **步骤 4：实现结果类型与执行器**

```python
@dataclass
class ScenarioTurnEvaluationResult:
    turn_index: int
    trace_id: str
    user_text: str
    note: str | None
    snapshot: dict[str, Any]
    history_before_turn: list[dict[str, str]]
    duration_ms: int
    assistant_text: str | None = None
    tool_call: dict[str, Any] | None = None
    topology_revision: int | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    error_stack: str | None = None
```

`run_conversation_scenarios` 为每个场景生成一个协程并使用全局 `asyncio.Semaphore`。场景协程内部直接遍历 turns，不再获取子信号量。

- [ ] **步骤 5：验证**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py tests/test_evaluation.py
.\.venv\Scripts\python.exe -m ruff check src tests
```

- [ ] **步骤 6：提交**

```powershell
git add -- src/tuco_ai_backend/evaluation.py src/tuco_ai_backend/evaluation_scenarios.py `
  tests/test_evaluation_scenarios.py tests/test_evaluation.py
git commit -m "feat: 并发执行多轮电路场景"
```

---

### 任务 3：输出完整 JSON 和可读 Markdown

**文件：**
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`src/tuco_ai_backend/evaluation_scenarios.py`
- 修改：`tests/test_evaluation_scenarios.py`

- [ ] **步骤 1：编写报告结构测试**

```python
def test_scenario_report_keeps_full_snapshot_history_and_trace(tmp_path: Path) -> None:
    json_path, markdown_path = write_conversation_evaluation_report(report, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    turn = payload["scenarios"][0]["turns"][0]
    assert turn["snapshot"]["schema"] == "tuco_circuit_v2"
    assert turn["history_before_turn"] == []
    assert turn["trace"]["provider_request"]["messages"]
    assert "完整快照不在 Markdown 展开" not in markdown_path.read_text(encoding="utf-8")
```

- [ ] **步骤 2：编写快照差异摘要测试**

`summarize_snapshot_change(previous, current)` 必须分别返回新增/移除/改变槽位和新增/移除连线；比较连线时忽略端口顺序。

- [ ] **步骤 3：实现报告函数**

新增：

```python
def render_conversation_markdown(report: ConversationEvaluationReport) -> str: ...

def write_conversation_evaluation_report(
    report: ConversationEvaluationReport,
    output_dir: Path,
) -> tuple[Path, Path]: ...
```

JSON 使用 `ensure_ascii=False` 和两空格缩进。Markdown 每轮只展示修订号、变化摘要、问题、回复、工具、候选数量、搜索耗时、降级原因和错误摘要。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/evaluation.py src/tuco_ai_backend/evaluation_scenarios.py `
  tests/test_evaluation_scenarios.py
git commit -m "feat: 生成多轮场景评测报告"
```

---

### 任务 4：接入场景 CLI 模式

**文件：**
- 修改：`src/tuco_ai_backend/evaluation_cli.py`
- 修改：`tests/test_evaluation_cli.py`

- [ ] **步骤 1：编写参数解析和冲突测试**

```python
def test_parse_args_accepts_repeated_conversation_scenarios() -> None:
    args = parse_args([
        "--conversation-scenario", "a.json",
        "--conversation-scenario", "b.json",
    ])
    assert args.conversation_scenarios == [Path("a.json"), Path("b.json")]


@pytest.mark.parametrize("conflict", ["--level", "--question", "--circuit-setup"])
def test_scenario_mode_rejects_legacy_selection_flags(conflict: str) -> None: ...
```

冲突检查必须区分用户是否显式传入选项，不能因为旧参数存在默认值而错误拒绝场景模式。

- [ ] **步骤 2：编写 CLI 集成测试**

使用两个临时场景和 Fake Client，断言：协议自动为 v2、场景顺序稳定、报告写入、客户端关闭；加载错误退出 2，请求失败但报告存在时退出 1。

- [ ] **步骤 3：实现 CLI 分支**

```python
parser.add_argument(
    "--conversation-scenario",
    dest="conversation_scenarios",
    type=Path,
    action="append",
    help="运行标准多轮场景 JSON，可重复传入。",
)
```

`run_cli` 在读取模型配置前完成所有场景加载与冲突校验。场景模式创建 `CircuitCoachV2Client`，调用 `run_conversation_scenarios` 和专用报告写入函数。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_cli.py tests/test_evaluation_scenarios.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/evaluation_cli.py tests/test_evaluation_cli.py
git commit -m "feat: 增加多轮场景评测命令"
```

---

### 任务 5：建立追踪观察器与递归脱敏

**文件：**
- 创建：`src/tuco_ai_backend/evaluation_tracing.py`
- 创建：`tests/test_evaluation_tracing.py`

- [ ] **步骤 1：编写事件归档测试**

```python
def test_trace_collector_groups_events_by_trace_id() -> None:
    collector = EvaluationTraceCollector()
    collector.record("trace-1", "provider_request", {"model": "fake"})
    collector.record("trace-1", "provider_response", {"choices": []})

    assert collector.take("trace-1") == {
        "provider_request": {"model": "fake"},
        "provider_response": {"choices": []},
    }
    assert collector.take("trace-1") == {}
```

- [ ] **步骤 2：编写嵌套脱敏测试**

```python
def test_redact_sensitive_data_handles_headers_fields_and_url_queries() -> None:
    redacted = redact_sensitive_data({
        "headers": {"Authorization": "Bearer secret", "X-Trace": "ok"},
        "api_key": "secret",
        "url": "https://example.test/v1?token=secret&mode=test",
    })
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["X-Trace"] == "ok"
    assert redacted["api_key"] == "[REDACTED]"
    assert "secret" not in redacted["url"]
```

- [ ] **步骤 3：实现追踪协议**

```python
TraceEventName = Literal[
    "decision_request",
    "conversation_history",
    "semantic_plan",
    "provider_request",
    "provider_response",
    "normalized_decision",
    "exception",
]


class DecisionTraceSink(Protocol):
    def record(self, trace_id: str, event: TraceEventName, payload: Any) -> None: ...
```

收集器在加锁后深拷贝并脱敏 payload。重复事件使用最后一次值；`take` 原子弹出整轮追踪。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_tracing.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/evaluation_tracing.py tests/test_evaluation_tracing.py
git commit -m "feat: 增加评测链路追踪与脱敏"
```

---

### 任务 6：在 Circuit Coach Provider 中记录完整链路

**文件：**
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] **步骤 1：编写 Provider 事件测试**

使用 `httpx.MockTransport` 和 `EvaluationTraceCollector`，调用 `decide(..., trace_id="trace-provider")` 后断言事件包含：请求模型、完整 messages、候选编号、HTTP 状态、原始 choices 和规范化工具调用。

- [ ] **步骤 2：编写追踪失败不影响决策测试**

注入一个 `record` 总是抛异常的 sink；`decide` 仍必须返回原决策，且只写 warning 日志。

- [ ] **步骤 3：实现可选观察器**

```python
class CircuitCoachV2Client(OpenAICompatibleClient):
    def __init__(
        self,
        config: RuntimeConfigStore,
        http_client: httpx.AsyncClient | None = None,
        trace_sink: DecisionTraceSink | None = None,
    ) -> None:
        super().__init__(config, http_client=http_client)
        self._trace_sink = trace_sink
```

新增 `_trace(trace_id, event, payload)`，仅在 `trace_id` 和 sink 同时存在时执行，并捕获 sink 的全部异常。

`decide` 依次记录：规范化请求和历史、`CircuitPlan` 序列化结果、最终 payload、HTTP 状态与响应头白名单、解码后的原始 JSON、最终 `DecisionResponse`。异常路径记录 `type/message/stack` 后重新抛出原异常。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_coach_v2.py tests/test_evaluation_tracing.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/providers/circuit_coach_v2.py tests/test_circuit_coach_v2.py
git commit -m "feat: 记录模型完整请求响应链路"
```

---

### 任务 7：把追踪数据合并进多轮报告

**文件：**
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`src/tuco_ai_backend/evaluation_cli.py`
- 修改：`tests/test_evaluation_scenarios.py`
- 修改：`tests/test_evaluation_cli.py`

- [ ] **步骤 1：编写完整诊断报告集成测试**

Fake Provider 为每轮记录不同的候选和原始响应；报告中的每轮 `trace` 必须按对应 `trace_id` 合并，不得串到其他并发场景。

- [ ] **步骤 2：实现收集器注入**

场景 CLI 创建一个 `EvaluationTraceCollector` 并注入 `CircuitCoachV2Client`。执行器在每轮成功或失败后调用 `collector.take(trace_id)`；若没有追踪事件，仍保存空对象以兼容 Fake Client。

- [ ] **步骤 3：验证脱敏后的落盘内容**

测试报告文件全文不包含测试 secret，同时保留完整 messages、快照和工具参数。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py tests/test_evaluation_cli.py `
  tests/test_circuit_coach_v2.py tests/test_evaluation_tracing.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/evaluation.py src/tuco_ai_backend/evaluation_cli.py `
  tests/test_evaluation_scenarios.py tests/test_evaluation_cli.py
git commit -m "feat: 保存多轮评测完整诊断日志"
```

---

### 任务 8：增加实机 Session 日志转换器

**文件：**
- 创建：`src/tuco_ai_backend/session_log_converter.py`
- 创建：`tests/test_session_log_converter.py`

- [ ] **步骤 1：编写转换测试**

输入 Session JSON 包含三次用户请求、每次完整 `circuit_snapshot` 和旧助手回复。输出场景必须只包含用户问题与快照，不复制旧助手回复；场景名、关卡和来源标签稳定。

- [ ] **步骤 2：编写缺少快照诊断测试**

某轮没有完整快照时转换整体失败，错误必须指出轮次编号，不能静默丢弃。

- [ ] **步骤 3：实现转换入口**

```python
def convert_session_log(payload: dict[str, Any], *, source: Path) -> ConversationScenario: ...

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
```

输出必须再次经过 `ConversationScenario.model_validate`，使用 UTF-8、`ensure_ascii=False` 和两空格缩进。

- [ ] **步骤 4：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_session_log_converter.py tests/test_evaluation_scenarios.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/session_log_converter.py tests/test_session_log_converter.py
git commit -m "feat: 转换实机Session为评测场景"
```

---

### 任务 9：建立 502 多轮回归场景并运行真实模型

**文件：**
- 创建：`tests/scenarios/502-progressive.json`
- 创建：`tests/scenarios/502-wrong-wire.json`
- 修改：`tests/test_evaluation_scenarios.py`

- [ ] **步骤 1：创建逐步搭建场景**

至少包含：摆好输入输出、放置第一个门、完成一条有效连接、加入下一块门、追问“为什么这样接”。每轮快照来自已有固件协议格式并递增 `topology_revision`。

- [ ] **步骤 2：创建错误接线场景**

包含已有部分正确结构和一条错误输出边，后续轮次拆除错误边但保留正确中间结构，用于观察诊断是否随快照改变。

- [ ] **步骤 3：增加 fixture 加载回归**

```python
def test_checked_in_scenarios_are_valid() -> None:
    scenarios = load_conversation_scenarios(sorted(Path("tests/scenarios").glob("*.json")))
    assert {item.scenario.name for item in scenarios} >= {
        "502-逐步搭建局部进位",
        "502-错误接线诊断",
    }
```

- [ ] **步骤 4：运行真实模型评测**

```powershell
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --conversation-scenario tests/scenarios/502-progressive.json `
  --conversation-scenario tests/scenarios/502-wrong-wire.json `
  --concurrency 2 `
  --output-dir output/multi-turn-502
```

人工检查 JSON 中每轮均包含快照、历史、语义计划、Provider 请求、原始响应和规范化决策；Markdown 能快速追踪回复是否重复及是否依据电路变化。

- [ ] **步骤 5：验证并提交场景**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_scenarios.py tests/test_evaluation_cli.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- tests/scenarios tests/test_evaluation_scenarios.py
git commit -m "test: 增加502多轮电路场景"
```

不要提交 `output/multi-turn-502`。

---

### 任务 10：完整验证与文档交接

**文件：**
- 修改：`docs/implementation.md`
- 修改：`PROJECT_HANDOFF.md`

- [ ] **步骤 1：更新使用文档**

记录场景 JSON 格式、CLI 示例、并发语义、失败退出码、完整日志内容、脱敏规则和 Session 转换命令。明确工具调用不会自动修改下一轮快照。

- [ ] **步骤 2：运行完整后端验证**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
git diff --check
```

- [ ] **步骤 3：运行两个 502 场景并保存本地报告**

重新执行任务 9 的 CLI，确认退出码为 0。报告仅保留在 `output/`，不提交。

- [ ] **步骤 4：提交文档**

```powershell
git add -- docs/implementation.md PROJECT_HANDOFF.md
git commit -m "docs: 记录多轮场景评测流程"
```

- [ ] **步骤 5：汇报人工分析入口**

向用户提供本次 JSON 和 Markdown 的绝对路径。后续用户要求分析时，读取 JSON 的完整 trace 和 Markdown 摘要，不依赖自动语言评分。

---

## 最终验收清单

- [ ] 场景每轮使用文件中的完整快照，测试器不自动修改电路。
- [ ] 同一场景共用 Session 且轮次串行，不同场景可并发。
- [ ] 失败轮不会污染后续历史，但不会阻止后续轮次执行。
- [ ] JSON 保留快照、历史、语义计划、最终提示、原始响应和规范化决策。
- [ ] 敏感 Header、令牌、密钥、密码和 URL 查询参数不会落盘。
- [ ] Markdown 能展示轮次间电路变化和回复摘要。
- [ ] 场景模式与旧 CLI 参数冲突时在请求模型前失败。
- [ ] 现有单轮、重复问题和学习活动评测保持兼容。
- [ ] 实机 Session 可以转换为同一标准场景格式。
- [ ] 后端完整 pytest、ruff 和 diff check 通过。
