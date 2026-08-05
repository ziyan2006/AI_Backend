# 通用电路语义规划器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用后端版本化真值表、通用布尔仿真和安全候选动作搜索，替代第 502 关专用规则，并让固件支持红色拆线提示。

**架构：** 后端规则清单是唯一人工维护源，并生成固件关卡规则；后端把快照规范化为电路图，用真值签名仿真当前功能，再搜索一至三个可完成目标的安全候选。大模型只选择候选编号，后端映射成设备工具；固件通过 `rule_version` 校验规则，并用 `intent=disconnect` 的红色高亮提示拆线。

**技术栈：** Python 3.11、Pydantic v2、FastAPI、pytest、ruff、ESP-IDF 5.5.4、C、cJSON、WS2812。

---

## 实施前提

- 后端仓库：`E:\3.6bench`
- 固件仓库：`E:\emb_agent_new`
- 后端当前存在未提交的第 502 关过渡保护代码，应先作为独立检查点提交，之后再由通用规划器替换。
- 固件仓库当前有多项与本计划无关的未提交修改。执行固件任务前必须先由用户确认将这些修改提交或转移到独立工作区；不得自动暂存、覆盖或丢弃它们。
- 固件只执行增量构建，不运行 `fullclean`，不删除 `build`。
- 云端部署和实机烧录不属于本计划的自动步骤，完成本地验证后需再次获得用户确认。

## 文件职责

### 后端 `E:\3.6bench`

- 创建 `src/tuco_ai_backend/data/level_logic_specs.json`：版本化关卡真值表唯一人工维护源。
- 创建 `src/tuco_ai_backend/level_logic.py`：加载并校验 `LevelLogicSpec`。
- 创建 `tools/generate_firmware_level_rules.py`：由后端规则生成固件 C 数组。
- 创建 `src/tuco_ai_backend/circuit_graph.py`：快照规范化、边方向、环路和结构诊断。
- 创建 `src/tuco_ai_backend/circuit_simulator.py`：真值签名传播和输出状态。
- 创建 `src/tuco_ai_backend/circuit_planner.py`：安全候选动作和有界搜索。
- 创建 `src/tuco_ai_backend/semantic_action_gateway.py`：候选编号、模型选择和设备动作映射。
- 修改 `src/tuco_ai_backend/models.py`、`src/tuco_ai_backend/tools.py`、`src/tuco_ai_backend/providers/circuit_coach_v2.py`：协议和集成。
- 修改 `src/tuco_ai_backend/evaluation.py`、`src/tuco_ai_backend/evaluation_cli.py`：语义评测。
- 创建分层测试：`tests/test_level_logic.py`、`tests/test_circuit_graph.py`、`tests/test_circuit_simulator.py`、`tests/test_circuit_planner.py`、`tests/test_semantic_action_gateway.py`。

### 固件 `E:\emb_agent_new`

- 创建 `main/level_rules.generated.inc`：后端生成的规则数组，禁止手工修改。
- 修改 `main/level_rules.h`、`main/level_rules.c`：使用生成规则并暴露 `rule_version`。
- 修改 `main/tuco_agent.c`：序列化 `rule_version`，解析连接与拆线意图。
- 修改 `main/tuco_port_highlight.h`、`main/tuco_port_highlight.c`：保存高亮意图并校验现有连线。
- 修改 `main/main.c`：连接提示保持黄色，拆线提示改为红色。
- 修改 `main/game_logic_self_test.c`：验证生成规则与本地判定。

---

### 任务 0：保存当前过渡修复并隔离工作区

**文件：**
- 后端检查点：`src/tuco_ai_backend/providers/circuit_semantics.py`
- 后端检查点：`src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 后端检查点：`tests/test_circuit_coach_v2.py`
- 固件只检查状态，不修改文件

- [ ] **步骤 1：重新验证当前第 502 关过渡修复**

```powershell
Set-Location E:\3.6bench
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_coach_v2.py -k semantic_guard
.\.venv\Scripts\python.exe -m ruff check src tests
```

预期：两条 `semantic_guard` 测试通过，ruff 输出 `All checks passed!`。

- [ ] **步骤 2：只提交过渡修复文件**

```powershell
git add -- src/tuco_ai_backend/providers/circuit_semantics.py `
  src/tuco_ai_backend/providers/circuit_coach_v2.py `
  tests/test_circuit_coach_v2.py
git diff --cached --check
git commit -m "fix: 拦截局部进位错误直连"
```

预期：提交只包含上述三个文件；未跟踪的备份、原型和 `output` 不被暂存。

- [ ] **步骤 3：检查固件工作区**

```powershell
git -C E:\emb_agent_new status --short
```

预期：开始固件任务前，用户已经把现有固件修改提交到明确检查点，或提供干净独立工作区。若仍有无关修改，停止固件任务，不自动 stash 或 commit。

---

### 任务 1：建立后端版本化关卡规则源

**文件：**
- 创建：`src/tuco_ai_backend/data/level_logic_specs.json`
- 创建：`src/tuco_ai_backend/level_logic.py`
- 创建：`tools/generate_firmware_level_rules.py`
- 创建：`tests/test_level_logic.py`

- [ ] **步骤 1：编写失败测试**

```python
from tuco_ai_backend.level_logic import get_level_logic_spec, load_level_logic_specs


def test_level_502_truth_table_matches_majority_function() -> None:
    spec = get_level_logic_spec(502, 1)
    assert spec.expected_outputs == (0, 0, 0, 1, 0, 1, 1, 1)


def test_all_specs_have_complete_truth_tables() -> None:
    for spec in load_level_logic_specs().values():
        assert len(spec.expected_outputs) == 1 << spec.input_count
        assert all(0 <= value < 1 << spec.output_count for value in spec.expected_outputs)
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_level_logic.py
```

预期：FAIL，提示 `tuco_ai_backend.level_logic` 不存在。

- [ ] **步骤 3：录入全部真值表**

`level_logic_specs.json` 必须包含以下 `expected_outputs`：

```text
101 [0,1]                  102 [1,1,1,0]          103 [1,0]
201 [0,0,0,1]              202 [0,1,1,1]          203 [1,0,0,0]
301 [0,1,1,0]              302 [1,0,0,1]
401 [0,1,1,0]              402 [0,0,0,1]          403 [0,1,1,2]
501 [0,1,1,0,1,0,0,1]      502 [0,0,0,1,0,1,1,1]
503 [0,1,1,1,1,1,1,1]      504 [0,1,1,2,1,2,2,3]
601 [0,1,0,1,0,0,1,1]      602 [1,2,4,8]
```

每项同时保存 `level_id=关卡号`、`rule_version=1`、输入输出数量和儿童可见标签。

- [ ] **步骤 4：实现严格规则模型和加载器**

```python
@dataclass(frozen=True)
class LevelLogicSpec:
    level_id: int
    rule_version: int
    input_count: int
    output_count: int
    short_goal: str
    input_labels: tuple[str, ...]
    output_labels: tuple[str, ...]
    expected_outputs: tuple[int, ...]


@lru_cache(maxsize=1)
def load_level_logic_specs() -> dict[tuple[int, int], LevelLogicSpec]:
    path = Path(__file__).with_name("data") / "level_logic_specs.json"
    raw_items = json.loads(path.read_text(encoding="utf-8"))
    specs: dict[tuple[int, int], LevelLogicSpec] = {}
    for item in raw_items:
        spec = LevelLogicSpec(
            level_id=item["level_id"],
            rule_version=item["rule_version"],
            input_count=item["input_count"],
            output_count=item["output_count"],
            short_goal=item["short_goal"],
            input_labels=tuple(item["input_labels"]),
            output_labels=tuple(item["output_labels"]),
            expected_outputs=tuple(item["expected_outputs"]),
        )
        _validate_spec(spec)
        key = (spec.level_id, spec.rule_version)
        if key in specs:
            raise ValueError(f"duplicate level logic spec: {key}")
        specs[key] = spec
    return specs


def get_level_logic_spec(level_id: int, rule_version: int) -> LevelLogicSpec:
    return load_level_logic_specs()[(level_id, rule_version)]
```

每项同时保存固件当前使用的 `short_goal`。同文件实现 `_validate_spec()`：输入输出数量限制为 1 至 4；标签数量必须与计数相同；真值表长度必须是 `1 << input_count`；每行值必须小于 `1 << output_count`。

- [ ] **步骤 5：实现确定性固件生成器**

```powershell
.\.venv\Scripts\python.exe tools/generate_firmware_level_rules.py `
  --output E:\emb_agent_new\main\level_rules.generated.inc
```

生成项格式：

```c
{502, 1, 3, 1, 8, {0, 0, 0, 1, 0, 1, 1, 1},
 "至少两个输入亮时产生进位", "A B C", "进位"},
```

测试连续生成两次内容完全一致，文件头包含“由后端规则生成，禁止手工修改”。

- [ ] **步骤 6：验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_level_logic.py
.\.venv\Scripts\python.exe -m ruff check src tests tools
git add -- src/tuco_ai_backend/data/level_logic_specs.json `
  src/tuco_ai_backend/level_logic.py tools/generate_firmware_level_rules.py `
  tests/test_level_logic.py
git commit -m "feat: 建立版本化关卡真值表"
```

---

### 任务 2：让固件使用后端生成规则

**文件：**
- 创建：`E:\emb_agent_new\main\level_rules.generated.inc`
- 修改：`E:\emb_agent_new\main\level_rules.h`
- 修改：`E:\emb_agent_new\main\level_rules.c`
- 修改：`E:\emb_agent_new\main\game_logic_self_test.c`

- [ ] **步骤 1：生成固件规则文件**

```powershell
Set-Location E:\3.6bench
.\.venv\Scripts\python.exe tools/generate_firmware_level_rules.py `
  --output E:\emb_agent_new\main\level_rules.generated.inc
```

预期：生成 17 条规则，包含完整 `expected_outputs`，固件不再运行 switch 计算真值表。

- [ ] **步骤 2：修改 `level_rule_t`**

```c
typedef struct {
    uint16_t level_id;
    uint16_t rule_version;
    uint8_t input_count;
    uint8_t output_count;
    uint8_t case_count;
    uint8_t expected_outputs[LEVEL_RULE_MAX_CASES];
    const char *short_goal;
    const char *input_names;
    const char *output_names;
} level_rule_t;
```

- [ ] **步骤 3：移除运行时规则 switch**

`level_rules.c` 改为：

```c
static const level_rule_t s_rules[] = {
#include "level_rules.generated.inc"
};

const level_rule_t *level_rule_get(uint16_t level_id)
{
    for (size_t index = 0; index < sizeof(s_rules) / sizeof(s_rules[0]); ++index) {
        if (s_rules[index].level_id == level_id) return &s_rules[index];
    }
    return NULL;
}
```

- [ ] **步骤 4：增加固件规则自检**

在 `game_logic_self_test.c` 使用现有自检宏加入：

```c
const level_rule_t *carry = level_rule_get(502);
TEST_ASSERT(carry != NULL && carry->rule_version == 1U);
TEST_ASSERT(carry->expected_outputs[3] == 1U);
TEST_ASSERT(carry->expected_outputs[4] == 0U);

const level_rule_t *full_adder = level_rule_get(504);
TEST_ASSERT(full_adder != NULL && full_adder->expected_outputs[7] == 3U);
```

- [ ] **步骤 5：增量构建并提交**

```powershell
Set-Location E:\emb_agent_new
& 'E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1'
idf.py build
git diff --check -- main/level_rules.h main/level_rules.c `
  main/level_rules.generated.inc main/game_logic_self_test.c
git add -- main/level_rules.h main/level_rules.c `
  main/level_rules.generated.inc main/game_logic_self_test.c
git commit -m "feat: 使用生成的版本化关卡规则"
```

预期：Ninja 增量构建成功；不得执行 `fullclean`。

---

### 任务 3：扩展快照规则版本协议

**文件：**
- 修改：`src/tuco_ai_backend/models.py`
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`tests/test_circuit_coach_v2.py`
- 修改：`tests/test_evaluation.py`
- 修改：`E:\emb_agent_new\main\tuco_agent.c`

- [ ] **步骤 1：编写后端兼容测试**

```python
def test_v2_level_accepts_rule_version() -> None:
    request = build_request(level_id=502, rule_version=1)
    assert request.circuit_snapshot.level.rule_version == 1


def test_missing_rule_version_remains_parseable() -> None:
    request = build_request(level_id=502, rule_version=None)
    assert request.circuit_snapshot.level.rule_version is None
```

旧快照仍可解析，但通用语义规划不得启动。

- [ ] **步骤 2：运行测试验证失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_coach_v2.py -k rule_version
```

预期：FAIL，`CircuitCoachV2Level` 没有 `rule_version`。

- [ ] **步骤 3：扩展后端模型与评测快照**

```python
rule_version: int | None = Field(default=None, ge=1, le=65535)
```

`build_circuit_coach_v2()` 使用 `get_level_logic_spec(case.level_id, 1)` 写入 `rule_version=1`。

- [ ] **步骤 4：固件序列化规则版本**

在 `build_circuit_context()` 的 `level` 对象加入：

```c
cJSON_AddNumberToObject(level, "rule_version", rule ? rule->rule_version : 0U);
```

更新 `tuco_agent_context_self_test_run()`，断言 JSON 包含 `"rule_version":1`。

- [ ] **步骤 5：分别验证并提交**

后端：

```powershell
Set-Location E:\3.6bench
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_coach_v2.py tests/test_evaluation.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/models.py src/tuco_ai_backend/evaluation.py `
  tests/test_circuit_coach_v2.py tests/test_evaluation.py
git commit -m "feat: 支持关卡规则版本"
```

固件：

```powershell
Set-Location E:\emb_agent_new
& 'E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1'
idf.py build
git add -- main/tuco_agent.c
git commit -m "feat: 上传关卡规则版本"
```

---

### 任务 4：规范化电路图并检测结构错误

**文件：**
- 创建：`src/tuco_ai_backend/circuit_graph.py`
- 创建：`tests/test_circuit_graph.py`

- [ ] **步骤 1：编写方向和环路失败测试**

```python
def test_graph_orients_valid_edge_from_output_to_input() -> None:
    graph = build_circuit_graph(snapshot_with_reversed_edge_record())
    assert graph.edges == (CircuitEdge(output_port=24, input_port=17),)


def test_graph_reports_cycle_without_propagating_it() -> None:
    graph = build_circuit_graph(snapshot_with_two_gate_cycle())
    assert graph.cycles
    assert graph.blocked_slots == frozenset({4, 5})
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_graph.py
```

预期：FAIL，模块不存在。

- [ ] **步骤 3：实现不可变规范图模型**

```python
@dataclass(frozen=True)
class CircuitEdge:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class CircuitPort:
    port_id: int
    slot_id: int
    role: Literal["input", "output", "unused"]


@dataclass(frozen=True)
class CircuitSlot:
    slot_id: int
    gate: str | None
    state: Literal["empty", "unidentified", "present"]
    input_ports: tuple[int, ...]
    output_ports: tuple[int, ...]


@dataclass(frozen=True)
class RawEdgeDiagnostic:
    port_a: int
    port_b: int
    reason: Literal["unknown_port", "same_slot", "direction", "multiple_sources"]


@dataclass(frozen=True)
class CircuitGraph:
    slots_by_id: dict[int, CircuitSlot]
    ports_by_id: dict[int, CircuitPort]
    edges: tuple[CircuitEdge, ...]
    source_by_input: dict[int, int]
    connected_ports: frozenset[int]
    invalid_edges: tuple[RawEdgeDiagnostic, ...]
    cycles: tuple[tuple[int, ...], ...]
    blocked_slots: frozenset[int]
```

`build_circuit_graph(snapshot)` 必须统一方向为输出到输入，记录同槽连接、多源输入、未知端口和方向错误，并检测组合环路。

测试文件同时定义 `snapshot_with_reversed_edge_record()` 和 `snapshot_with_two_gate_cycle()` 两个最小快照构造器：前者使用同一条有效边的反向数组顺序，后者使用两块门互相输出到对方输入。

- [ ] **步骤 4：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_graph.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/circuit_graph.py tests/test_circuit_graph.py
git commit -m "feat: 规范化电路快照图"
```

---

### 任务 5：实现通用真值签名仿真器

**文件：**
- 创建：`src/tuco_ai_backend/circuit_simulator.py`
- 创建：`tests/test_circuit_simulator.py`

- [ ] **步骤 1：编写逻辑门穷举测试**

```python
@pytest.mark.parametrize(
    ("gate", "inputs", "expected"),
    [
        ("NOT", (0b0011,), 0b1100),
        ("AND", (0b0011, 0b0101), 0b0001),
        ("OR", (0b0011, 0b0101), 0b0111),
        ("NAND", (0b0011, 0b0101), 0b1110),
        ("NOR", (0b0011, 0b0101), 0b1000),
        ("XOR", (0b0011, 0b0101), 0b0110),
        ("XNOR", (0b0011, 0b0101), 0b1001),
    ],
)
def test_gate_signature(gate: str, inputs: tuple[int, ...], expected: int) -> None:
    assert apply_gate_signature(gate, inputs, mask=0b1111) == expected
```

- [ ] **步骤 2：编写等价电路和输出状态测试**

```python
def test_nand_then_not_is_equivalent_to_and() -> None:
    result = simulate(
        build_circuit_graph(snapshot_nand_then_not()),
        get_level_logic_spec(201, 1),
    )
    assert result.output_states[0].status == "correct"


def test_incomplete_gate_has_no_output_signature() -> None:
    result = simulate(
        build_circuit_graph(snapshot_with_one_empty_and_input()),
        get_level_logic_spec(201, 1),
    )
    assert result.signal_by_output_port.get(16) is None
```

- [ ] **步骤 3：运行测试验证失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_simulator.py
```

预期：FAIL，仿真接口不存在。

- [ ] **步骤 4：实现签名和输出分类**

```python
@dataclass(frozen=True)
class OutputSemanticState:
    output_index: int
    slot_id: int
    current_signature: int | None
    target_signature: int
    status: Literal["correct", "wrong", "incomplete", "blocked"]


@dataclass(frozen=True)
class SimulationResult:
    input_signature_by_slot: dict[int, int]
    signal_by_output_port: dict[int, int]
    output_states: tuple[OutputSemanticState, ...]
    unresolved_slots: frozenset[int]
```

`simulate(graph, spec)` 按以下固定算法实现：为排序后的输入槽生成周期签名；按拓扑顺序遍历无环逻辑门；输入未齐的门加入 `unresolved_slots`；将门签名复制到该门全部输出端；最后按排序后的输出槽读取信号，并从 `expected_outputs` 每一位构造目标签名。输入和输出积木均按 `slot_id` 排序。

- [ ] **步骤 5：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_simulator.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/circuit_simulator.py tests/test_circuit_simulator.py
git commit -m "feat: 仿真电路真值签名"
```

---

### 任务 6：搜索连接和摆放候选动作

**文件：**
- 创建：`src/tuco_ai_backend/circuit_planner.py`
- 创建：`tests/test_circuit_planner.py`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] **步骤 1：编写第 502 关通用回归测试**

```python
def test_majority_pairwise_and_requires_or_before_output() -> None:
    plan = plan_circuit_actions(
        real_502_pairwise_and_snapshot(),
        get_level_logic_spec(502, 1),
    )
    assert plan.candidates
    assert all(
        not isinstance(candidate.action, ConnectPortsAction)
        or candidate.action.input_port != 2
        for candidate in plan.candidates
    )
    assert isinstance(plan.candidates[0].action, PlaceGateAction)
    assert plan.candidates[0].action.gate == "OR"
```

- [ ] **步骤 2：编写非唯一解和复用测试**

```python
def test_xor_level_accepts_nand_only_partial_solution() -> None:
    plan = plan_circuit_actions(
        nand_only_xor_partial_snapshot(),
        get_level_logic_spec(301, 1),
    )
    assert plan.candidates
    assert all(
        not isinstance(candidate.action, PlaceGateAction)
        or candidate.action.gate == "NAND"
        for candidate in plan.candidates
    )


def test_planner_finishes_existing_gate_before_placing_new_gate() -> None:
    plan = plan_circuit_actions(
        partially_connected_or_snapshot(),
        get_level_logic_spec(503, 1),
    )
    assert isinstance(plan.candidates[0].action, ConnectPortsAction)


def test_multi_output_plan_preserves_already_correct_sum_output() -> None:
    plan = plan_circuit_actions(
        full_adder_snapshot_with_correct_sum_only(),
        get_level_logic_spec(504, 1),
    )
    assert plan.preserved_output_indexes == frozenset({0})
    assert all(0 not in candidate.invalidated_output_indexes for candidate in plan.candidates)
```

- [ ] **步骤 3：运行测试验证失败**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_planner.py
```

预期：FAIL，规划器不存在。

- [ ] **步骤 4：定义候选动作和规划结果**

```python
@dataclass(frozen=True)
class ConnectPortsAction:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class PlaceGateAction:
    slot: int
    gate: str


@dataclass(frozen=True)
class DisconnectPortsAction:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class PlannedCandidate:
    candidate_id: str
    topology_revision: int
    action: ConnectPortsAction | PlaceGateAction | DisconnectPortsAction
    score: tuple[int, ...]
    child_facts: tuple[str, ...]
    invalidated_output_indexes: frozenset[int]


@dataclass(frozen=True)
class CircuitPlan:
    candidates: tuple[PlannedCandidate, ...]
    preserved_output_indexes: frozenset[int]
    search_states: int
    elapsed_ms: float
    degraded_reason: str | None = None
```

- [ ] **步骤 5：实现有界最佳优先搜索**

实现要求：

- 初始可用签名来自输入和已完成逻辑门。
- 搜索成本按连接、摆放和拆线次数累加。
- 优先复用现有部分门，再添加新门。
- 候选来自前三条低成本完成路线的不同第一步。
- 多输出关卡联合计算剩余成本，已正确输出加入不可破坏集合，中间信号允许被多个输出复用。
- 相同语义状态去重；默认上限 50 毫秒、20000 状态。
- 搜索超限返回 `degraded_reason="search_budget_exceeded"`，候选为空。
- 本任务先生成连接和摆放动作；拆线动作在任务 8 开启。

- [ ] **步骤 6：迁移真实 502 快照测试**

保留现有真实快照构造器，断言改为调用通用 `plan_circuit_actions()`；暂时保留专用保护作为双保险，直到任务 7 完成。

- [ ] **步骤 7：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_circuit_planner.py tests/test_circuit_coach_v2.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/circuit_planner.py `
  tests/test_circuit_planner.py tests/test_circuit_coach_v2.py
git commit -m "feat: 搜索安全电路候选动作"
```

---

### 任务 7：让大模型只选择候选编号

**文件：**
- 创建：`src/tuco_ai_backend/semantic_action_gateway.py`
- 创建：`tests/test_semantic_action_gateway.py`
- 修改：`src/tuco_ai_backend/tools.py`
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 修改：`tests/test_circuit_coach_v2.py`
- 删除：`src/tuco_ai_backend/providers/circuit_semantics.py`

- [ ] **步骤 1：编写候选安全测试**

```python
def test_unknown_candidate_id_is_rejected() -> None:
    gateway = SemanticActionGateway(plan_with_two_candidates())
    assert gateway.resolve("invented-action", topology_revision=31) is None


def test_candidate_is_bound_to_topology_revision() -> None:
    gateway = SemanticActionGateway(plan_for_revision(31))
    assert gateway.resolve("rev31-action-1", topology_revision=32) is None
```

- [ ] **步骤 2：编写 LLM 集成失败测试**

Mock 模型返回：

```json
{"name":"choose_circuit_action","arguments":{"candidate_id":"invented-action"}}
```

断言：未知编号不能映射端口；明确索取下一步时使用最高分候选和本地提示，普通聊天时不自动执行候选。

- [ ] **步骤 3：实现内部选择工具**

```python
class ChooseCircuitActionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(pattern=r"^rev\d+-action-\d+$")
```

该工具只发给大模型，不直接返回固件。

- [ ] **步骤 4：实现候选网关**

```python
class SemanticActionGateway:
    def __init__(self, plan: CircuitPlan) -> None:
        self._candidates = {candidate.candidate_id: candidate for candidate in plan.candidates}

    def resolve(self, candidate_id: str, topology_revision: int) -> PlannedCandidate | None:
        candidate = self._candidates.get(candidate_id)
        if candidate is None or candidate.topology_revision != topology_revision:
            return None
        return candidate
```

- [ ] **步骤 5：重构 `CircuitCoachV2Client.decide()`**

固定顺序：规则校验 → 构图仿真 → 意图判断 → 生成候选 → LLM 选择编号 → 网关解析 → 设备动作映射 → 最终拓扑校验。

普通聊天和原理解释使用 `tool_choice="none"`；明确索取下一步或亮灯时才提供 `choose_circuit_action`。

- [ ] **步骤 6：删除第 502 关专用规划器**

删除 `providers/circuit_semantics.py` 和专用接入函数；保留真实 502 快照回归测试，改为验证通用候选网关。

- [ ] **步骤 7：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_action_gateway.py `
  tests/test_circuit_coach_v2.py tests/test_tools.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/semantic_action_gateway.py `
  src/tuco_ai_backend/tools.py src/tuco_ai_backend/providers/circuit_coach_v2.py `
  tests/test_semantic_action_gateway.py tests/test_circuit_coach_v2.py tests/test_tools.py
git add -u -- src/tuco_ai_backend/providers/circuit_semantics.py
git commit -m "feat: 让模型选择安全电路候选"
```

---

### 任务 8：增加后端拆线候选和工具意图

**文件：**
- 修改：`src/tuco_ai_backend/tools.py`
- 修改：`src/tuco_ai_backend/circuit_planner.py`
- 修改：`src/tuco_ai_backend/semantic_action_gateway.py`
- 修改：`tests/test_tools.py`
- 修改：`tests/test_circuit_planner.py`
- 修改：`tests/test_semantic_action_gateway.py`

- [ ] **步骤 1：编写拆线协议测试**

```python
def test_highlight_ports_defaults_to_connect() -> None:
    args = CircuitCoachHighlightPortsArgs(output_port=16, input_port=2)
    assert args.intent == "connect"


def test_disconnect_requires_existing_edge() -> None:
    result = map_candidate_to_device_tool(
        disconnect_candidate(16, 2),
        snapshot_without_edge(16, 2),
    )
    assert result is None
```

- [ ] **步骤 2：扩展工具参数**

```python
class CircuitCoachHighlightPortsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_port: CircuitCoachPortNumber
    input_port: CircuitCoachPortNumber
    intent: Literal["connect", "disconnect"] = "connect"
```

- [ ] **步骤 3：实现拆线候选规则**

只有以下情况才生成 `DisconnectPortsAction`：当前边结构非法或位于环路；保留该边时没有完成路线而拆除后存在；该边占用完成全部目标所需的唯一输入端。拆线排序始终低于可行的连接和摆放动作。

- [ ] **步骤 4：映射拆线工具和本地语音**

```python
CircuitCoachHighlightPortsArgs(
    output_port=action.output_port,
    input_port=action.input_port,
    intent="disconnect",
)
```

本地安全语音使用“先拆掉亮红灯的这条线”，不得复用连接文案。

- [ ] **步骤 5：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_tools.py `
  tests/test_circuit_planner.py tests/test_semantic_action_gateway.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/tools.py src/tuco_ai_backend/circuit_planner.py `
  src/tuco_ai_backend/semantic_action_gateway.py tests/test_tools.py `
  tests/test_circuit_planner.py tests/test_semantic_action_gateway.py
git commit -m "feat: 支持安全拆线候选"
```

---

### 任务 9：固件实现红色拆线提示

**文件：**
- 修改：`E:\emb_agent_new\main\tuco_port_highlight.h`
- 修改：`E:\emb_agent_new\main\tuco_port_highlight.c`
- 修改：`E:\emb_agent_new\main\tuco_agent.c`
- 修改：`E:\emb_agent_new\main\main.c`

- [ ] **步骤 1：扩展高亮模式接口**

```c
typedef enum {
    TUCO_PORT_HIGHLIGHT_CONNECT = 0,
    TUCO_PORT_HIGHLIGHT_DISCONNECT,
} tuco_port_highlight_intent_t;

void tuco_port_highlight_show(uint8_t output_port,
                              uint8_t input_port,
                              tuco_port_highlight_intent_t intent);

bool tuco_port_highlight_get(const board_snapshot_t *snapshot,
                             uint8_t ports[BOARD_SNAPSHOT_PORTS_PER_SLOT],
                             uint8_t *port_count,
                             bool *visible,
                             tuco_port_highlight_intent_t *intent);
```

- [ ] **步骤 2：区分连接与拆线有效期**

新增：

```c
static bool ports_form_existing_link(const board_snapshot_t *snapshot,
                                     uint8_t first,
                                     uint8_t second);
```

`CONNECT` 在任一端口已经连接时清除；`DISCONNECT` 只有当两个端口不再构成同一条现有边时清除；槽位高亮保持现有逻辑。

- [ ] **步骤 3：解析可选 `intent`**

`tuco_agent_execute_remote_tool()` 不再要求 JSON 对象大小严格等于 2：

- `intent` 缺失或为 `connect`：走现有空闲端口校验。
- `intent=disconnect`：要求两个端口构成快照中的真实现有边。
- 其他值：拒绝执行。

调用：

```c
tuco_port_highlight_show(first, second,
    disconnect ? TUCO_PORT_HIGHLIGHT_DISCONNECT : TUCO_PORT_HIGHLIGHT_CONNECT);
```

- [ ] **步骤 4：渲染红色拆线灯**

```c
static const ws2812_color_t connect_color = {.green = 180, .red = 180, .blue = 0};
static const ws2812_color_t disconnect_color = {.green = 0, .red = 180, .blue = 0};
```

根据 `intent` 选择颜色；连接保持黄色，拆线为红色。

- [ ] **步骤 5：增加启动自检**

新增 `tuco_port_highlight_self_test_run()`，验证连接提示在端口接上后失效、拆线提示在边存在时保持、边消失后失效、`intent` 能从 `get()` 原样返回。由 `main.c` 启动自检区域调用。

- [ ] **步骤 6：增量构建并提交**

```powershell
Set-Location E:\emb_agent_new
& 'E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1'
idf.py build
git diff --check -- main/tuco_port_highlight.h main/tuco_port_highlight.c `
  main/tuco_agent.c main/main.c
git add -- main/tuco_port_highlight.h main/tuco_port_highlight.c `
  main/tuco_agent.c main/main.c
git commit -m "feat: 用红灯提示拆除错误连线"
```

预期：增量构建成功，不烧录。

---

### 任务 10：扩展并发评测与语义指标

**文件：**
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`src/tuco_ai_backend/evaluation_cli.py`
- 修改：`tests/test_evaluation.py`
- 修改：`tests/test_evaluation_cli.py`
- 创建：`tests/fixtures/circuit_semantics/502-pairwise-and.json`
- 创建：`tests/fixtures/circuit_semantics/504-equivalent-solution.json`
- 创建：`tests/fixtures/circuit_semantics/602-wrong-output-edge.json`

- [ ] **步骤 1：定义语义评测字段**

```python
semantic_candidate_count: int = 0
semantic_action_valid: bool | None = None
preserved_correct_structure: bool | None = None
disconnect_was_required: bool | None = None
search_elapsed_ms: float | None = None
semantic_degraded_reason: str | None = None
```

汇总增加非法端口率、错误拆线率、正确结构保留率、搜索超时率和候选选择有效率。

- [ ] **步骤 2：增加 fixture 评测入口**

CLI 新增可重复的 `--circuit-fixture`。每个 fixture 格式：

```json
{
  "name": "502-pairwise-and",
  "expected_action_kinds": ["place_gate"],
  "forbidden_port_pairs": [[16, 2], [22, 2], [40, 2]],
  "snapshot": {}
}
```

第 504 关使用不同但真值等价的电路；第 602 关包含一条必须拆除的错误输出边。

- [ ] **步骤 3：编写汇总测试**

```python
def test_report_contains_semantic_metrics() -> None:
    report = EvaluationReport(
        run_id="semantic-test",
        model="fake",
        concurrency=1,
        circuit_setup="fixture",
        circuit_protocol="circuit-v2",
        learning_activity_setup="none",
        questions=["接下来怎么做"],
        started_at="2026-08-05T00:00:00+00:00",
        completed_at="2026-08-05T00:00:01+00:00",
        duration_ms=1,
        levels=[
            LevelEvaluationResult(
                session_id="fixture-502",
                level_id=502,
                title="局部进位",
                duration_ms=1,
                turns=[
                    TurnEvaluationResult(
                        question="接下来怎么做",
                        trace_id="fixture-turn",
                        duration_ms=1,
                        semantic_action_valid=True,
                        preserved_correct_structure=True,
                        disconnect_was_required=False,
                    )
                ],
            )
        ],
    ).to_dict()
    assert report["summary"]["invalid_port_rate"] == 0
    assert report["summary"]["wrong_disconnect_rate"] == 0
```

- [ ] **步骤 4：运行测试和提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation.py tests/test_evaluation_cli.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add -- src/tuco_ai_backend/evaluation.py src/tuco_ai_backend/evaluation_cli.py `
  tests/test_evaluation.py tests/test_evaluation_cli.py tests/fixtures/circuit_semantics
git commit -m "feat: 增加电路语义评测指标"
```

---

### 任务 11：完整验证、文档更新和本地切换

**文件：**
- 修改：`src/tuco_ai_backend/config.py`
- 修改：`tests/test_config.py`
- 修改：`docs/device-protocol.md`
- 修改：`docs/embedded-integration.md`
- 修改：`docs/implementation.md`
- 修改：`PROJECT_HANDOFF.md`

- [ ] **步骤 1：增加安全回退配置**

```python
semantic_planner_enabled: bool = True
semantic_search_timeout_ms: int = Field(default=50, ge=5, le=500)
semantic_search_max_states: int = Field(default=20000, ge=100, le=200000)
```

关闭时回退为纯文本保守模式，不恢复按关卡编号硬编码动作。

- [ ] **步骤 2：更新协议文档**

文档明确：`level.rule_version` 缺失时不启用语义工具；后端规则是唯一人工维护源；`highlight_ports.intent` 默认 `connect`；`disconnect` 必须指向真实边且固件显示红色；模型只选择候选编号。

- [ ] **步骤 3：运行后端完整验证**

```powershell
Set-Location E:\3.6bench
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
git diff --check
```

预期：全部测试通过，ruff 无错误，diff check 无输出。

- [ ] **步骤 4：运行固件增量构建**

```powershell
Set-Location E:\emb_agent_new
& 'E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1'
idf.py build
git diff --check
```

预期：Ninja 增量构建成功，不删除 `build`，不执行 `fullclean`。

- [ ] **步骤 5：运行本地语义 fixture 评测**

```powershell
Set-Location E:\3.6bench
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --protocol circuit-v2 `
  --circuit-fixture tests/fixtures/circuit_semantics/502-pairwise-and.json `
  --circuit-fixture tests/fixtures/circuit_semantics/504-equivalent-solution.json `
  --circuit-fixture tests/fixtures/circuit_semantics/602-wrong-output-edge.json `
  --question "接下来应该怎么做？给我点提示"
```

预期：非法端口率和错误拆线率均为 0；502 不出现 `16 -> 2`、`22 -> 2` 或 `40 -> 2`。

- [ ] **步骤 6：提交配置和文档**

```powershell
git add -- src/tuco_ai_backend/config.py tests/test_config.py `
  docs/device-protocol.md docs/embedded-integration.md docs/implementation.md `
  PROJECT_HANDOFF.md
git commit -m "docs: 完成通用电路语义规划接入"
```

- [ ] **步骤 7：等待部署和烧录确认**

本地验证完成后汇报后端与固件提交、增量构建和 fixture 指标。只有用户再次确认后，才部署云端并执行：

```powershell
Set-Location E:\emb_agent_new
& 'E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1'
idf.py -p COM9 flash
```

---

## 最终验收清单

- [ ] 所有关卡均通过同一 `LevelLogicRegistry` 和 `CircuitPlanner` 处理。
- [ ] 后端不存在 `_plan_level_502` 或其他按关卡编号决定下一步的函数。
- [ ] 标准解和不同等价解都能继续获得合理候选。
- [ ] 大模型不能执行候选集合外的端口调用。
- [ ] 第 502 关真实快照不会把单块与门直接连接最终输出。
- [ ] 拆线候选只指向真实边，固件显示红色。
- [ ] 正确输出和可复用中间结构不会被无故拆除。
- [ ] 规则不匹配、搜索超限和模型协议错误均安全降级。
- [ ] 后端完整 pytest、ruff、diff check 通过。
- [ ] 固件 ESP-IDF 5.5.4 增量构建通过。
- [ ] 未经用户再次确认，不部署云端、不烧录设备。
