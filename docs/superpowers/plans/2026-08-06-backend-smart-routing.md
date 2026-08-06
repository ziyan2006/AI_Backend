# 后端智能路由与设备错误协议实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用一次主模型结构化决策替代电路助教的关键词动作路由，并为设备接口提供稳定、可诊断的错误协议。

**架构：** 后端始终计算当前关卡的安全规划与诊断，把它们作为模型事实依据；模型通过统一 `decide_circuit_turn` 工具选择响应模式和可选候选编号。只有 `mode=act` 且候选通过 `SemanticActionGateway` 校验时才产生固件工具调用。设备接口统一返回 `trace_id`，异常使用结构化错误体。

**技术栈：** Python 3.11+、FastAPI、Pydantic v2、httpx、pytest、pytest-asyncio、Ruff。

---

## 文件结构

- 创建：`src/tuco_ai_backend/assistant_turn.py`——响应模式、结构化模型决策和工具 Schema。
- 创建：`src/tuco_ai_backend/device_errors.py`——设备错误代码、错误体和异常映射。
- 修改：`src/tuco_ai_backend/models.py`——为设备成功响应增加可选 `trace_id`。
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`——语义上下文、单次智能路由、安全候选映射。
- 修改：`src/tuco_ai_backend/evaluation_tracing.py`——记录模型路由模式。
- 修改：`src/tuco_ai_backend/evaluation.py`——将路由模式写入行为测评报告。
- 修改：`src/tuco_ai_backend/evaluation_presets.py`——增加自然表达路由测评预设。
- 修改：`src/tuco_ai_backend/main.py`——结构化设备错误响应与 `trace_id` 回传。
- 创建：`tests/test_assistant_turn.py`——路由决策模型和 Schema 测试。
- 修改：`tests/test_circuit_coach_v2.py`——智能路由、候选执行和降级测试。
- 修改：`tests/test_api.py`——成功响应和错误协议测试。
- 修改：`tests/test_evaluation.py`、`tests/test_evaluation_presets.py`——路由日志与预设测试。

### 任务 1：定义结构化响应模式

**文件：**
- 创建：`src/tuco_ai_backend/assistant_turn.py`
- 创建：`tests/test_assistant_turn.py`

- [ ] **步骤 1：编写失败的模式约束测试**

```python
import pytest
from pydantic import ValidationError

from tuco_ai_backend.assistant_turn import AssistantTurnDecision


def test_act_requires_candidate_id() -> None:
    with pytest.raises(ValidationError):
        AssistantTurnDecision(mode="act", assistant_text="现在开始操作。")


def test_non_act_rejects_candidate_id() -> None:
    with pytest.raises(ValidationError):
        AssistantTurnDecision(
            mode="hint",
            assistant_text="先观察两个输入。",
            candidate_id="rev4-action-1",
        )
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_assistant_turn.py -q`

预期：FAIL，报错 `ModuleNotFoundError: tuco_ai_backend.assistant_turn`。

- [ ] **步骤 3：实现决策模型和工具 Schema**

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssistantTurnMode = Literal[
    "chat", "goal", "hint", "explain", "diagnose", "act", "clarify"
]


class AssistantTurnDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AssistantTurnMode
    assistant_text: str = Field(min_length=1, max_length=500)
    candidate_id: str | None = Field(default=None, pattern=r"^rev\d+-action-\d+$")

    @model_validator(mode="after")
    def validate_candidate_usage(self) -> "AssistantTurnDecision":
        if self.mode == "act" and self.candidate_id is None:
            raise ValueError("act mode requires candidate_id")
        if self.mode != "act" and self.candidate_id is not None:
            raise ValueError("non-act mode cannot include candidate_id")
        return self


def decide_circuit_turn_tool() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "decide_circuit_turn",
            "description": "选择本轮响应模式；只有用户明确要求立即操作时才能选择 act。",
            "strict": True,
            "parameters": AssistantTurnDecision.model_json_schema(),
        },
    }
```

- [ ] **步骤 4：补充全部模式和 Schema 测试并验证通过**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_assistant_turn.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/tuco_ai_backend/assistant_turn.py tests/test_assistant_turn.py
git commit -m "feat: 定义电路助教智能路由协议"
```

### 任务 2：让语义规划脱离关键词分类

**文件：**
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py:371`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] **步骤 1：编写自然表达仍能获得规划的失败测试**

```python
def test_flexible_action_question_has_grounding_plan() -> None:
    request = _request_for_snapshot(
        _placed_io_snapshot(502),
        user_text="我该从哪儿下手？",
    )

    plan, diagnosis = _semantic_context_for_request(request)

    assert plan is not None
    assert plan.candidates
    assert diagnosis is not None
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py::test_flexible_action_question_has_grounding_plan -q`

预期：FAIL，报错 `_semantic_context_for_request` 尚不存在。

- [ ] **步骤 3：实现统一语义上下文函数**

```python
def _semantic_context_for_request(
    request: CircuitCoachDecisionRequest,
) -> tuple[CircuitPlan | None, CircuitDiagnosis | None]:
    if request.learning_activity is not None:
        return None, None
    spec = _logic_spec_for_snapshot(request.circuit_snapshot)
    if spec is None:
        return None, None
    diagnosis = diagnose_circuit(request.circuit_snapshot, spec)
    plan = plan_circuit_actions(request.circuit_snapshot, spec)
    usable_plan = plan if plan.candidates and plan.degraded_reason is None else None
    return usable_plan, diagnosis
```

调用端不再使用 `_level_question_intent()` 决定是否计算规划。关键词分类可以保留给旧协议，但不得继续控制 `circuit-v2` 的语义规划。

- [ ] **步骤 4：加入闲聊、解释和学习活动边界测试**

断言闲聊也可以计算背景计划，但后续路由不自动执行；学习活动继续返回 `(None, None)`。

- [ ] **步骤 5：运行相关测试**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py -q`

预期：PASS。

- [ ] **步骤 6：提交**

```powershell
git add src/tuco_ai_backend/providers/circuit_coach_v2.py tests/test_circuit_coach_v2.py
git commit -m "refactor: 将电路规划与关键词意图解耦"
```

### 任务 3：实现单次模型智能路由与安全执行

**文件：**
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py:731`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] **步骤 1：编写三类失败测试**

```python
@pytest.mark.asyncio
async def test_hint_mode_returns_text_without_tool() -> None:
    response = _provider_tool_response(
        "decide_circuit_turn",
        {"mode": "hint", "assistant_text": "先观察两个输入。", "candidate_id": None},
    )
    decision = await _client_with_response(response).decide(_placed_io_request(403))
    assert decision.assistant_text == "先观察两个输入。"
    assert decision.tool_call is None


@pytest.mark.asyncio
async def test_act_mode_maps_valid_candidate() -> None:
    request = _placed_io_request(403)
    response = _provider_tool_response(
        "decide_circuit_turn",
        {
            "mode": "act",
            "assistant_text": "先放一块异或门吧。",
            "candidate_id": "rev4-action-2",
        },
    )
    decision = await _client_with_response(response).decide(request)
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"


@pytest.mark.asyncio
async def test_invalid_candidate_never_falls_back_to_first_action() -> None:
    response = _provider_tool_response(
        "decide_circuit_turn",
        {
            "mode": "act",
            "assistant_text": "我先确认一下。",
            "candidate_id": "rev999-action-9",
        },
    )
    decision = await _client_with_response(response).decide(_placed_io_request(403))
    assert decision.tool_call is None


@pytest.mark.asyncio
async def test_clarify_mode_never_executes_ambiguous_reference() -> None:
    response = _provider_tool_response(
        "decide_circuit_turn",
        {
            "mode": "clarify",
            "assistant_text": "你说的是刚才那块积木，还是亮起的两个接口？",
            "candidate_id": None,
        },
    )
    decision = await _client_with_response(response).decide(
        _request_for_snapshot(_placed_io_snapshot(502), user_text="为什么是这个？")
    )
    assert decision.tool_call is None
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py -q`

预期：至少上述三个测试 FAIL。

- [ ] **步骤 3：修改模型载荷**

普通电路请求始终提供一个强制结构化工具：

```python
payload = {
    "model": self._config.model,
    "stream": False,
    "messages": messages,
    "tools": [decide_circuit_turn_tool()],
    "tool_choice": {
        "type": "function",
        "function": {"name": "decide_circuit_turn"},
    },
    "parallel_tool_calls": False,
}
```

候选说明必须列出每个 `candidate_id` 对应的“摆放、连接或拆线”事实，同时明确非 `act` 模式不得填写候选。

载荷继续保留最近六条有效 `user` / `assistant` 历史，并追加指代规则：遇到“它”“这个”“刚才那个”时，只有历史文字、当前快照和安全候选共同指向唯一对象才允许 `explain`；否则必须选择 `clarify`。测试载荷中七条历史只保留最后六条，且上一轮助教建议仍在模型消息中。

- [ ] **步骤 4：解析结构化决策**

新增 `_parse_turn_decision()`：只接受 `decide_circuit_turn`，使用 `AssistantTurnDecision.model_validate()` 校验参数。若供应商返回带正文但没有工具调用，将正文安全降级为 `chat` 文本；没有正文也没有合法工具时抛出 `LlmProtocolError`。

- [ ] **步骤 5：删除自动首候选执行**

删除 `candidate = plan.candidates[0]` 的自动回退。只有 `mode=act` 才调用 `SemanticActionGateway.resolve()`；无效候选返回模型文字或固定澄清语，不产生工具调用。

- [ ] **步骤 6：保留动作文字安全校验**

`act` 模式仍经过 `_map_candidate_to_decision()`、`_normalize_decision()` 和 `_requires_tool_guidance_regeneration()`；位置词、端口号、英文或传输确认继续触发安全改写。

- [ ] **步骤 7：运行测试**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py tests/test_assistant_turn.py -q`

预期：PASS。

- [ ] **步骤 8：提交**

```powershell
git add src/tuco_ai_backend/providers/circuit_coach_v2.py tests/test_circuit_coach_v2.py
git commit -m "feat: 使用主模型完成电路助教智能路由"
```

### 任务 4：记录路由模式并扩展行为测评

**文件：**
- 修改：`src/tuco_ai_backend/evaluation_tracing.py`
- 修改：`src/tuco_ai_backend/evaluation.py`
- 修改：`src/tuco_ai_backend/evaluation_presets.py`
- 修改：`tests/test_evaluation.py`
- 修改：`tests/test_evaluation_presets.py`
- 修改：`tests/test_circuit_coach_v2.py`

- [ ] **步骤 1：编写失败测试**

断言决策 Trace 包含：

```python
assert trace["route_decision"] == {
    "mode": "hint",
    "candidate_id": None,
}
```

并断言 Markdown 报告每轮显示“路由模式”。

- [ ] **步骤 2：增加 `route_decision` Trace 事件**

在模型结构化结果解析后、动作映射前记录 `mode`、`candidate_id` 和是否成功映射；不得把该事件与最终 `normalized_decision` 合并。

- [ ] **步骤 3：扩展测评结果**

为 `TurnEvaluationResult` 和 `ScenarioTurnEvaluationResult` 增加 `route_mode: str | None`，从 Trace 收集器读取 `route_decision.mode`，JSON 和 Markdown 均保留。

- [ ] **步骤 4：增加自然表达预设**

新增 `flexible-routing-quality`，至少覆盖 301、403、502、601，每关包含：

```text
我该从哪儿下手？
给我一点方向，别直接公布答案。
为什么是这个，不是别的？
我照你说的想了，但还是绕不过来，换个简单例子讲讲。
好，那此刻我只需要动哪一下？
```

- [ ] **步骤 5：运行测试并提交**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py tests/test_evaluation_presets.py tests/test_circuit_coach_v2.py -q`

```powershell
git add src/tuco_ai_backend/evaluation_tracing.py src/tuco_ai_backend/evaluation.py src/tuco_ai_backend/evaluation_presets.py tests/test_evaluation.py tests/test_evaluation_presets.py tests/test_circuit_coach_v2.py
git commit -m "test: 增加智能路由行为测评"
```

### 任务 5：实现设备结构化错误协议

**文件：**
- 创建：`src/tuco_ai_backend/device_errors.py`
- 修改：`src/tuco_ai_backend/models.py:96`
- 修改：`src/tuco_ai_backend/main.py:190`
- 修改：`tests/test_api.py`

- [ ] **步骤 1：编写错误协议失败测试**

```python
def test_circuit_coach_timeout_returns_structured_device_error() -> None:
    service = RaisingCircuitCoachService(httpx.ReadTimeout("slow"))
    app = create_app(Settings(llm_api_key="configured"), circuit_coach_service=service)
    response = TestClient(app).post(
        "/api/device/circuit-coach/decision",
        json=_circuit_coach_request_body(),
    )
    assert response.status_code == 504
    payload = response.json()
    assert payload["error"]["code"] == "LLM_TIMEOUT"
    assert payload["error"]["stage"] == "llm_provider"
    assert payload["error"]["retryable"] is True
    assert payload["trace_id"].startswith("tr_")
```

同时覆盖配置缺失、协议错误、HTTP 401/429/500 和普通连接错误。

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q`

预期：新增错误协议测试 FAIL，当前响应仍为 `{"detail": ...}`。

- [ ] **步骤 3：实现错误模型与响应助手**

```python
class DeviceErrorBody(BaseModel):
    code: Literal[
        "REQUEST_INVALID", "RULE_UNSUPPORTED", "LLM_UNCONFIGURED",
        "LLM_TIMEOUT", "LLM_UPSTREAM_ERROR", "LLM_PROTOCOL_ERROR",
        "ACTION_INVALID", "INTERNAL_ERROR",
    ]
    stage: str
    retryable: bool
    message: str


def device_error_response(*, status_code: int, trace_id: str, error: DeviceErrorBody) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error.model_dump(), "trace_id": trace_id},
    )
```

- [ ] **步骤 4：映射异常**

- `LlmConfigurationError` → 503 / `LLM_UNCONFIGURED`。
- `httpx.TimeoutException` → 504 / `LLM_TIMEOUT`。
- `httpx.HTTPStatusError` → 502 / `LLM_UPSTREAM_ERROR`，保留上游状态到日志，不返回上游正文。
- `LlmProtocolError`、供应商响应 `ValidationError` → 502 / `LLM_PROTOCOL_ERROR`。
- 其他 `httpx.HTTPError` → 502 / `LLM_UPSTREAM_ERROR`。
- 未分类异常 → 500 / `INTERNAL_ERROR`，记录堆栈。

为设备路由注册 `RequestValidationError` 处理，设备路径返回 422 / `REQUEST_INVALID`；其他 API 保持原有 FastAPI 行为。

- [ ] **步骤 5：成功响应加入 `trace_id`**

在 `DecisionResponse` 增加：

```python
trace_id: str | None = None
```

设备端点返回前使用 `decision.model_copy(update={"trace_id": trace_id})`，并把同一对象写入 Session 日志。

- [ ] **步骤 6：运行测试并提交**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_session_store.py -q`

```powershell
git add src/tuco_ai_backend/device_errors.py src/tuco_ai_backend/models.py src/tuco_ai_backend/main.py tests/test_api.py
git commit -m "feat: 为设备接口增加结构化错误协议"
```

### 任务 6：完成后端回归与并发验证

**文件：**
- 修改：`PROJECT_HANDOFF.md`

- [ ] **步骤 1：运行全量测试和静态检查**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
git diff --check
```

预期：全部通过。

- [ ] **步骤 2：运行自然表达并发预设**

```powershell
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --protocol circuit-v2 `
  --conversation-preset flexible-routing-quality `
  --concurrency 4 `
  --output-dir runtime\llm_evaluations\smart-routing-validation
```

验收：标准动作问法和自然动作问法产生等价候选；`hint`、`explain`、`diagnose`、`chat` 无工具调用；无回复提及不存在积木或剧情物体入口。

- [ ] **步骤 3：更新交接文档**

记录新路由模式、设备错误结构、测评命令和固件接入依赖，不写入任何密钥。

- [ ] **步骤 4：提交**

```powershell
git add PROJECT_HANDOFF.md
git commit -m "docs: 更新智能路由与设备错误协议交接"
```

- [ ] **步骤 5：在进入固件计划前记录后端提交号**

运行：`git rev-parse HEAD`

将输出写入固件计划执行记录，确保固件联调针对确定的后端协议版本。
