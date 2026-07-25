# 缺积木 LLM 状态提示实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 输入或输出积木不足时，继续调用一次 LLM，并通过按状态动态拼接的系统提示要求模型自然提醒玩家补齐积木。

**架构：** 在 provider 内由电路快照和关卡目标计算 `READY`、`MISSING_INPUT`、`MISSING_OUTPUT`、`MISSING_INPUT_OUTPUT` 四种状态及缺失数量。`_build_payload()` 将非就绪状态转换为额外系统消息；`decide()` 删除本地预检短路，保留一次 LLM 请求、解析和工具调用流程。

**技术栈：** Python 3.11、Pydantic、httpx、pytest、ruff。

---

### 任务 1：用回归测试锁定单次 LLM 状态行为

**文件：**
- 修改：`tests/test_llm_service.py:163-176`
- 测试：`tests/test_llm_service.py`

- [ ] **步骤 1：把旧硬拦截测试改为失败测试**

用 `httpx.MockTransport` 记录请求载荷，并让模型返回自我介绍：

```python
captured: dict[str, object] = {}

async def handler(request: httpx.Request) -> httpx.Response:
    captured.update(json.loads(request.content))
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "我是图灵号的电路小伙伴。"}}]},
    )
```

调用一个缺输入和输出的空快照，并断言：结果为原始模型文本、只发出一次 HTTP 请求、`messages` 含“输入积木”和“输出积木”的额外系统策略消息。

- [ ] **步骤 2：运行失败测试**

运行：`uv run pytest tests/test_llm_service.py -k missing_required_io -q`

预期：失败，因为当前实现直接返回本地积木兜底，且没有发出 HTTP 请求。

- [ ] **步骤 3：增加就绪状态测试**

构造已满足一个输入和一个输出要求的快照，调用 `_build_payload()`，断言系统消息列表没有“当前电路尚未就绪”策略消息。

- [ ] **步骤 4：运行失败测试**

运行：`uv run pytest tests/test_llm_service.py -k "missing_required_io or ready" -q`

预期：缺积木测试失败；就绪状态测试可先通过，作为状态提示不应无条件注入的保护。

### 任务 2：实现电路准备状态和动态提示构造

**文件：**
- 修改：`src/tuco_ai_backend/providers/openai_compatible.py:77-88`
- 测试：`tests/test_llm_service.py`

- [ ] **步骤 1：定义状态和数据载体**

加入：

```python
class CircuitReadiness(StrEnum):
    READY = "ready"
    MISSING_INPUT = "missing_input"
    MISSING_OUTPUT = "missing_output"
    MISSING_INPUT_OUTPUT = "missing_input_output"

@dataclass(frozen=True)
class MissingComponentsState:
    readiness: CircuitReadiness
    missing_input_count: int
    missing_output_count: int
```

复用 `_component_counts()` 和 `LevelContext`，并在没有关卡时返回 `READY`。

- [ ] **步骤 2：构造非固定话术的动态系统提示**

实现 `build_missing_components_instruction(circuit)`：当任一数量大于零时，返回动态系统提示，要求模型先回答用户问题、用自然措辞提醒具体缺失数量、且不指导接线；就绪时返回 `None`。

- [ ] **步骤 3：运行状态测试**

运行：`uv run pytest tests/test_llm_service.py -k "missing_required_io or ready" -q`

预期：仍失败，直到载荷构建接入新的提示函数。

### 任务 3：移除硬拦截并接入完整 LLM 链路

**文件：**
- 修改：`src/tuco_ai_backend/providers/openai_compatible.py:286-304`
- 修改：`src/tuco_ai_backend/providers/openai_compatible.py:390-413`
- 测试：`tests/test_llm_service.py`

- [ ] **步骤 1：删除本地兜底返回分支**

从 `decide()` 移除：

```python
missing = _missing_component_reply(request.circuit)
if missing is not None:
    return DecisionResponse(...)
```

保留 API Key 校验、请求日志、`_post()`、响应解析和后置高亮逻辑不变。

- [ ] **步骤 2：将状态提示加入系统消息**

在 `_build_payload()` 内、基础 `SYSTEM_PROMPT` 后加入：

```python
missing_instruction = build_missing_components_instruction(request.circuit)
if missing_instruction:
    messages.append({"role": "system", "content": missing_instruction})
```

不要在模型文本返回后追加固定文本，也不要发出第二次模型请求。

- [ ] **步骤 3：运行定向测试验证通过**

运行：`uv run pytest tests/test_llm_service.py -k "missing_required_io or ready" -q`

预期：通过；缺积木测试确认一次模型请求和动态策略消息，就绪测试确认未注入策略消息。

### 任务 4：执行完整验证与运行时重载

**文件：**
- 修改：无
- 测试：`tests/test_llm_service.py`

- [ ] **步骤 1：执行静态与全量测试**

运行：

```powershell
uv run ruff check .
uv run pytest
node --test tests/frontend_circuit_simulator.test.cjs
git diff --check
```

预期：全部命令以退出码 `0` 完成。

- [ ] **步骤 2：重载后端并进行 HTTP 验证**

触发现有 `uvicorn --reload` 实例重新加载，或重启 `8000` 服务。用 `httpx.MockTransport` 测试已经证明模型载荷；运行时仅确认 `http://127.0.0.1:8000/` 返回 `200`，不向真实 LLM 发送测试请求。

## 自检

- 设计中的四种状态均由任务 2 实现。
- “完整 LLM 链路、单次调用、动态提示、无固定追加”由任务 3 覆盖。
- 回归、静态检查、前端现有测试、运行时健康检查均在任务 4 中验证。
- 本计划不包含提交步骤；提交由用户单独指示。
