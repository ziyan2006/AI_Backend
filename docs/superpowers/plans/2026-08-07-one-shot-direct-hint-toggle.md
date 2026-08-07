# 游玩页一次性“直接提示”开关实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在固件游玩页加入默认关闭、使用一次后自动关闭的“直接提示”触摸开关，并让后端只在真实操作型问题中执行一个安全亮灯候选。

**架构：** 固件把开关状态作为一次性请求许可 `direct_hint_requested` 发送，后端仍负责语义路由、真值表规划和工具安全校验。开关不复用强制执行的 `interaction_intent: "act"`；后端用模型路由加本地高置信意图闸门处理聊天误触。

**技术栈：** Python 3.11、FastAPI、Pydantic、pytest、httpx、ESP-IDF 5.5.4、C、LVGL、cJSON、GT911 触摸屏。

**提交约束：** 本计划不自动执行 Git commit、push 或云端部署；完成验证后等待用户明确指令。

---

## 文件结构

### 后端仓库 `E:\3.6bench`

- 修改 `src/tuco_ai_backend/models.py`：增加设备请求布尔字段。
- 修改 `src/tuco_ai_backend/providers/circuit_coach_v2.py`：加入直接提示安全闸门、路由提升和误触撤销。
- 修改 `src/tuco_ai_backend/evaluation_scenarios.py`：多轮场景逐轮保存直接提示状态。
- 修改 `src/tuco_ai_backend/evaluation.py`：构造评测请求时透传状态。
- 修改 `src/tuco_ai_backend/evaluation_presets.py`：新增直接提示专项多轮预设。
- 修改 `tests/test_circuit_coach_v2.py`：覆盖直接提示、聊天误触和无候选行为。
- 修改 `tests/test_evaluation_scenarios.py`：覆盖场景字段加载和报告留存。

### 固件仓库 `E:\emb_agent_new`

- 修改 `main/app_ui.c`：创建游玩页开关、触摸回调、样式刷新和关卡重置。
- 修改 `main/volcengine_voice.h`、`main/volcengine_voice.c`：在录音开始时消费一次性状态并锁存到当前语音请求。
- 修改 `main/assistant_router.h`、`main/assistant_router.c`：路由接口透传布尔状态，内置链路忽略。
- 修改 `main/remote_assistant.h`、`main/remote_assistant.c`：远程请求结构和 JSON 增加 `direct_hint_requested`。
- 修改 `main/main.c`：注册新增的固件自检（如实现独立状态自检函数）。
- 修改 `main/CMakeLists.txt`：仅在增加独立状态模块时登记源文件。

---

### 任务 1：扩展后端请求与评测协议

**文件：**
- 修改：`E:\3.6bench\src\tuco_ai_backend\models.py`
- 修改：`E:\3.6bench\src\tuco_ai_backend\evaluation_scenarios.py`
- 修改：`E:\3.6bench\src\tuco_ai_backend\evaluation.py`
- 测试：`E:\3.6bench\tests\test_evaluation_scenarios.py`

- [ ] **步骤 1：编写失败的请求模型测试**

在 `tests/test_evaluation_scenarios.py` 增加默认值和显式值测试：

```python
def test_conversation_turn_defaults_direct_hint_to_false() -> None:
    loaded = load_conversation_presets(["502-guidance-quality"])[0]
    assert loaded.scenario.turns[0].direct_hint_requested is False


def test_conversation_turn_accepts_direct_hint_true() -> None:
    payload = _scenario_payload()
    payload["turns"][0]["direct_hint_requested"] = True
    scenario = ConversationScenario.model_validate(payload)
    assert scenario.turns[0].direct_hint_requested is True
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
Set-Location E:\3.6bench
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation_scenarios.py -k direct_hint -q
```

预期：FAIL，`ConversationScenarioTurn` 不存在 `direct_hint_requested`。

- [ ] **步骤 3：增加请求字段**

在 `CircuitCoachDecisionRequest` 中加入：

```python
direct_hint_requested: bool = False
```

在 `ConversationScenarioTurn` 中加入相同字段，并在 `_evaluate_conversation_scenario` 创建请求时显式传入：

```python
request = CircuitCoachDecisionRequest(
    session_id=session_id,
    user_text=turn.user_text,
    interaction_intent=turn.interaction_intent,
    direct_hint_requested=turn.direct_hint_requested,
    circuit_snapshot=turn.snapshot,
)
```

- [ ] **步骤 4：让报告保留开关状态**

为 `ScenarioTurnEvaluationResult` 增加：

```python
direct_hint_requested: bool
```

写入 JSON 和 Markdown 时显示该值，确保后续能区分普通提示和一次性直接提示。

- [ ] **步骤 5：运行协议测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation_scenarios.py tests/test_evaluation.py -q
```

预期：全部 PASS。

---

### 任务 2：实现后端直接提示安全闸门

**文件：**
- 修改：`E:\3.6bench\src\tuco_ai_backend\providers\circuit_coach_v2.py`
- 测试：`E:\3.6bench\tests\test_circuit_coach_v2.py`

- [ ] **步骤 1：编写“开启后下一步执行”失败测试**

使用 `502-guidance-quality` 最后一轮快照，模拟模型返回 `hint`：

```python
request = CircuitCoachDecisionRequest(
    session_id="direct-hint-next-step",
    user_text="接下来应该怎么做？",
    direct_hint_requested=True,
    circuit_snapshot=snapshot,
)
```

断言：

```python
assert decision.tool_call is not None
assert decision.tool_call.name == "highlight_ports"
assert decision.tool_call.arguments.intent == "connect"
```

- [ ] **步骤 2：编写聊天误触失败测试**

同一快照下开启开关，模型故意返回带候选的 `act`：

```python
request = CircuitCoachDecisionRequest(
    session_id="direct-hint-chat-mistouch",
    user_text="你是谁？",
    direct_hint_requested=True,
    circuit_snapshot=snapshot,
)
```

断言：

```python
assert decision.tool_call is None
assert decision.assistant_text
```

- [ ] **步骤 3：编写目标、原理和无候选失败测试**

分别验证以下问题零工具调用：

```python
("这关要做什么？", "goal")
("为什么要这样接？", "explain")
```

再构造规划器没有候选或 `degraded_reason` 非空的请求，断言直接提示不能绕过规划失败。

- [ ] **步骤 4：运行新测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py -k "direct_hint" -q
```

预期：至少“开启后下一步执行”和“聊天 act 撤销”失败。

- [ ] **步骤 5：实现高置信安全闸门**

增加纯函数：

```python
_DIRECT_HINT_ACTION_INTENTS = {"开始行动", "提示求助", "检查诊断"}


def _direct_hint_action_allowed(
    request: CircuitCoachDecisionRequest,
    turn: AssistantTurnDecision,
    plan: CircuitPlan | None,
) -> bool:
    return (
        request.direct_hint_requested
        and plan is not None
        and bool(plan.candidates)
        and _level_question_intent(request.user_text) in _DIRECT_HINT_ACTION_INTENTS
        and turn.mode in {"hint", "diagnose", "act"}
    )
```

本函数只决定是否允许把首选安全候选提升为动作，不生成端口或槽位。

- [ ] **步骤 6：集成到 `decide` 路由**

计算：

```python
direct_hint_action = _direct_hint_action_allowed(request, turn, execution_plan)
```

把它加入 `force_preferred_action` 条件。若 `direct_hint_requested=True`、模型返回 `act`，但安全闸门为假，则明确走非工具文字分支，并记录：

```python
route_trace.update({
    "direct_hint_requested": True,
    "direct_hint_allowed": False,
    "direct_hint_vetoed": True,
    "model_mode": turn.mode,
})
```

若允许执行则记录：

```python
route_trace.update({
    "direct_hint_requested": True,
    "direct_hint_allowed": True,
    "forced_act": True,
})
```

- [ ] **步骤 7：更新模型路由提示**

修改 `_turn_routing_instruction`，传入完整请求状态而不是只有 `interaction_intent`。加入明确规则：

```text
直接提示已开启只表示允许执行。聊天、关卡目标、术语和原理问题仍禁止选择 act；
只有孩子正在索取下一步、直接求助或检查当前电路时才可选择 act。
```

- [ ] **步骤 8：运行后端专项与完整测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_circuit_coach_v2.py -q
.\.venv\Scripts\ruff.exe check src tests
git diff --check
```

预期：全部 PASS。

---

### 任务 3：增加直接提示并发测评预设

**文件：**
- 修改：`E:\3.6bench\src\tuco_ai_backend\evaluation_presets.py`
- 修改：`E:\3.6bench\tests\test_evaluation_scenarios.py`

- [ ] **步骤 1：编写预设结构失败测试**

```python
def test_direct_hint_toggle_preset_covers_action_and_mistouch() -> None:
    loaded = load_conversation_presets(["direct-hint-toggle-quality"])
    assert {item.scenario.level_id for item in loaded} == {301, 403, 502, 504, 601}
    for item in loaded:
        states = [turn.direct_hint_requested for turn in item.scenario.turns]
        assert states == [False, True, True, True]
```

- [ ] **步骤 2：运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation_scenarios.py -k direct_hint_toggle -q
```

预期：FAIL，预设不存在。

- [ ] **步骤 3：创建四轮场景**

每个关卡使用同一份可操作快照或对应错误快照，四轮问题固定为：

```python
turns = (
    (False, "给我一点方向，别直接公布答案。"),
    (True, "接下来应该怎么做？"),
    (True, "你是谁？"),
    (True, "为什么要这样接？"),
)
```

第二轮预期一个工具调用；其余三轮预期零工具调用。场景标签加入 `direct-hint`、`mistouch` 和 `quality`。

- [ ] **步骤 4：运行预设结构测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation_scenarios.py -q
```

预期：PASS。

- [ ] **步骤 5：运行真实模型小规模并发**

```powershell
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --conversation-preset direct-hint-toggle-quality `
  --concurrency 2 `
  --env-file .env `
  --output-dir output\direct-hint-toggle-quality
```

验收：20/20 请求成功；每关第二轮恰好一个工具调用，聊天和原理轮工具调用数为零；每次只包含一个槽位或一对端口。

---

### 任务 4：扩展固件远程请求协议

**文件：**
- 修改：`E:\emb_agent_new\main\assistant_router.h`
- 修改：`E:\emb_agent_new\main\assistant_router.c`
- 修改：`E:\emb_agent_new\main\remote_assistant.h`
- 修改：`E:\emb_agent_new\main\remote_assistant.c`

- [ ] **步骤 1：先扩展路由接口签名**

```c
esp_err_t assistant_router_submit(const char *text,
                                  uint16_t level_id,
                                  bool direct_hint_requested,
                                  uint32_t *out_request_id);
```

`assistant_router.c` 的远程分支透传布尔值；内置分支继续调用原有 `tuco_agent_submit`，忽略布尔值。

- [ ] **步骤 2：扩展远程接口和请求结构**

```c
esp_err_t remote_assistant_submit(const char *text,
                                  uint16_t level_id,
                                  bool direct_hint_requested,
                                  uint32_t *out_request_id);
```

在 `remote_request_t` 增加：

```c
bool direct_hint_requested;
```

- [ ] **步骤 3：把字段写入 JSON**

在 `request_remote` 创建根对象后加入：

```c
cJSON_AddBoolToObject(root, "direct_hint_requested",
                      request->direct_hint_requested);
```

保留 `interaction_intent` 的现有默认行为，不发送 `"act"`。

- [ ] **步骤 4：增加协议自检**

把请求 JSON 构造提取为可独立释放的静态函数，例如：

```c
static cJSON *build_remote_request_json(const remote_request_t *request,
                                        cJSON *circuit_snapshot);
```

增加 `remote_assistant_protocol_self_test_run()`，分别构造 `true` 和 `false` 请求，使用 `cJSON_IsTrue`、`cJSON_IsFalse` 验证字段，并在 `main.c` 启动自检中调用。

- [ ] **步骤 5：执行增量编译验证接口完整性**

```powershell
Set-Location E:\emb_agent_new
. E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1
idf.py build
```

预期：Ninja 增量构建成功；不得运行 `fullclean`，不得删除 `build`。

---

### 任务 5：实现固件一次性状态锁存

**文件：**
- 修改：`E:\emb_agent_new\main\volcengine_voice.h`
- 修改：`E:\emb_agent_new\main\volcengine_voice.c`
- 修改：`E:\emb_agent_new\main\app_ui.c`

- [ ] **步骤 1：在语音模块增加当前请求状态**

```c
static bool s_direct_hint_requested;
```

扩展更新接口，使 UI 传入当前开关状态并返回是否被本次录音消费：

```c
bool voice_assistant_update(bool play_active,
                            bool programmer_owns_input,
                            bool key0_pressed,
                            uint16_t level_id,
                            bool direct_hint_armed);
```

- [ ] **步骤 2：在 `press_started` 时原子消费**

```c
bool consumed = false;
if (press_started) {
    s_direct_hint_requested = direct_hint_armed;
    consumed = direct_hint_armed;
}
```

函数返回 `consumed`。退出游玩页、取消请求和初始化时把 `s_direct_hint_requested` 清为 `false`。

- [ ] **步骤 3：提交 ASR 文本时透传锁存值**

```c
assistant_router_submit(s_final_text,
                        s_level_id,
                        s_direct_hint_requested,
                        &s_agent_request_id);
s_direct_hint_requested = false;
```

无论提交成功或失败都清除当前请求值，防止下一轮继承。

- [ ] **步骤 4：扩展语音诊断自检**

把一次性消费判断提取为纯函数：

```c
static bool consume_direct_hint(bool press_started,
                                bool direct_hint_armed,
                                bool *request_value);
```

在 `voice_assistant_diagnostics_self_test_run()` 中验证：未按下不消费、关闭状态不消费、开启且首次按下返回 true、下一次请求默认 false。

- [ ] **步骤 5：执行增量编译**

```powershell
idf.py build
```

预期：PASS，启动自检函数可链接。

---

### 任务 6：在游玩页创建触摸开关

**文件：**
- 修改：`E:\emb_agent_new\main\app_ui.c`

- [ ] **步骤 1：增加 UI 对象和状态**

在 UI 对象结构中加入：

```c
lv_obj_t *play_direct_hint;
lv_obj_t *play_direct_hint_label;
```

增加：

```c
static bool s_direct_hint_armed;
```

- [ ] **步骤 2：创建开关样式刷新函数**

```c
static void ui_refresh_direct_hint(void)
{
    lv_label_set_text(s_ui.play_direct_hint_label,
                      s_direct_hint_armed ? "直接提示  开启" : "直接提示  关闭");
    lv_obj_set_style_bg_color(
        s_ui.play_direct_hint,
        lv_color_hex(s_direct_hint_armed ? UI_COLOR_YELLOW : UI_COLOR_SURFACE_ALT),
        0);
}
```

开启状态文字使用深色以保证黄色背景可读；关闭状态使用现有白色文字。

- [ ] **步骤 3：增加触摸回调**

```c
static void ui_direct_hint_event_cb(lv_event_t *event)
{
    (void)event;
    s_direct_hint_armed = !s_direct_hint_armed;
    audio_self_test_play_effect(AUDIO_EFFECT_SELECT);
    ui_refresh_direct_hint();
}
```

- [ ] **步骤 4：调整游玩页布局**

在右侧面板增加 36 像素高的开关。优先压缩 `play_status` 和真值表之间的空白，不缩小现有中文字体；若空间不足，将“启动飞船检查”和“开启开关试玩”各缩短到 34 像素高并保持至少 8 像素间距。

开关必须注册：

```c
lv_obj_add_event_cb(s_ui.play_direct_hint,
                    ui_direct_hint_event_cb,
                    LV_EVENT_CLICKED,
                    NULL);
```

- [ ] **步骤 5：实现进入、离开和消费重置**

进入 `UI_PAGE_PLAY` 时：

```c
s_direct_hint_armed = false;
ui_refresh_direct_hint();
```

在取得 LVGL 锁之前调用 `voice_assistant_update` 并保存返回值；取得 LVGL 锁后再修改可见状态和对象样式：

```c
const bool direct_hint_consumed = voice_assistant_update(
    play_active,
    programmer_owns_input,
    keys->key0_pressed,
    ui_nodes()[s_selected_node].id,
    s_page == UI_PAGE_PLAY && s_direct_hint_armed);

ESP_RETURN_ON_ERROR(esp_lv_adapter_lock(-1), TAG, "lock LVGL");
if (direct_hint_consumed) {
    s_direct_hint_armed = false;
    ui_refresh_direct_hint();
}
```

离开游玩页时同样清零。学习小游戏传入 `false`，不显示开关。

- [ ] **步骤 6：运行增量编译**

```powershell
idf.py build
```

预期：PASS，无 LVGL 对象空指针或字体链接错误。

---

### 任务 7：完整验证、部署与烧录检查点

**文件：**
- 验证：`E:\3.6bench`
- 验证：`E:\emb_agent_new`

- [ ] **步骤 1：运行后端完整回归**

```powershell
Set-Location E:\3.6bench
$env:PYTHONUTF8 = '1'
git diff --check
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q
```

预期：全部 PASS，仅允许既有 Starlette/httpx 弃用警告。

- [ ] **步骤 2：运行直接提示真实模型并发测试**

```powershell
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --conversation-preset direct-hint-toggle-quality `
  --concurrency 2 `
  --env-file .env `
  --output-dir output\direct-hint-toggle-quality-final
```

逐轮检查原始文字、`route_mode`、`direct_hint_requested`、候选编号和工具调用。

- [ ] **步骤 3：确认部署前兼容性**

使用本地 FastAPI 测试客户端发送不含新字段的旧请求，预期 HTTP 200；发送 `direct_hint_requested: true` 的新请求，预期模型正常解析。

- [ ] **步骤 4：等待用户明确授权后部署后端**

部署前确认云端工作树除预期 `.venv` 外干净，并按交接文档使用 `git pull --ff-only`、`systemctl restart` 和 `/api/health` 验证。未收到明确授权时停止在此检查点。

- [ ] **步骤 5：增量构建并烧录固件**

仅在后端已部署且用户明确要求烧录后运行：

```powershell
Set-Location E:\emb_agent_new
. E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1
idf.py -p COM9 flash
```

不得执行 `fullclean`；烧录前关闭串口占用程序。

- [ ] **步骤 6：实机验收**

按顺序验证：

1. 进入关卡显示“直接提示 关闭”。
2. 触摸开启后显示黄色状态。
3. 开启后按右键，录音开始时立即恢复关闭。
4. 说“接下来应该怎么做”，只亮一个槽位或一对端口并正常播放语音。
5. 重新开启后说“你是谁”，正常聊天且不亮灯。
6. 重新开启后说“为什么这样接”，只解释且不亮灯。
7. 退出再进入关卡，状态仍为关闭。
8. 串口确认每轮只发送一次 `direct_hint_requested=true`，且日志不包含密钥。

---

## 完成定义

- 后端协议、路由安全闸门、评测预设和全部自动化测试通过。
- 固件触摸开关默认关闭、一次性消费且不会跨关卡继承。
- 开启后操作型问题只执行一个规划器候选。
- 开启后聊天、目标和原理问题零工具调用。
- 旧固件请求继续兼容。
- 未经用户明确要求，不提交、推送、部署或烧录。
