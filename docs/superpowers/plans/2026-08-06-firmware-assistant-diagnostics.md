# 固件助教链路诊断实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 ESP32-P4 固件保留助教链路的结构化失败原因，并在游玩界面稳定显示至少 6 秒，同时允许右键立即重试、退出关卡立即清除。

**架构：** 新建无硬件依赖的 `assistant_diagnostics` 负责错误模型、后端错误映射和屏幕文案，新建 `assistant_error_latch` 负责最短显示时间。远程 HTTP 客户端返回完整 `assistant_response_t`，路由器统一包装远程与内置助教结果；语音状态机只负责运行阶段，显示层通过错误锁存决定普通状态是否可覆盖错误。

**技术栈：** ESP-IDF 5.5.4、FreeRTOS、`esp_http_client`、cJSON、LVGL 状态展示、ESP-IDF 内置自测、Ninja 增量构建、pyserial。

---

## 文件结构

### 固件仓库 `E:\emb_agent_new`

- 创建：`main/assistant_diagnostics.h`——结构化助教结果、错误代码、阶段枚举及公共构造/映射接口。
- 创建：`main/assistant_diagnostics.c`——HTTP/后端错误映射、短屏幕文案、诊断日志字段和纯逻辑自测。
- 创建：`main/assistant_error_latch.h`——错误显示锁存状态及无 FreeRTOS 依赖的时间接口。
- 创建：`main/assistant_error_latch.c`——6 秒最短显示、覆盖、重试清除、退出清除和 tick 回绕安全判断。
- 修改：`main/remote_assistant.h`——远程结果接口改为返回 `assistant_response_t`。
- 修改：`main/remote_assistant.c`——保留 HTTP 状态、后端错误 JSON、`trace_id`、耗时和工具执行错误。
- 修改：`main/assistant_router.h`——公开统一的结构化结果接口。
- 修改：`main/assistant_router.c`——包装远程与内置助教结果，不再把所有失败压缩为一个 `esp_err_t`。
- 修改：`main/volcengine_voice.h`——保持现有 UI 状态结构，增加诊断自测入口。
- 修改：`main/volcengine_voice.c`——运行阶段与显示错误分离，按 ASR/后端/TTS 阶段上报具体错误。
- 修改：`main/CMakeLists.txt`——注册两个新源文件。
- 修改：`main/main.c`——启动时运行诊断与锁存自测。

### 后端仓库 `E:\3.6bench`

- 修改：`PROJECT_HANDOFF.md`——记录固件错误协议、增量构建、串口验证方法和已验证后端提交号。

### 明确不修改

- 不修改关卡规则、真值表、积木解锁和快照字段。
- 不修改 `sdkconfig` 中已配置的 URL、语音 Key 和 Wi-Fi。
- 不删除 `build`，不执行 `idf.py fullclean`。
- 不使用不稳定的 `serial_mcp`，串口验证统一使用 pyserial。

## 协议与类型约定

统一结果定义为：

```c
typedef enum {
    ASSISTANT_ERROR_NONE = 0,
    ASSISTANT_ERROR_NETWORK_OFFLINE,
    ASSISTANT_ERROR_CONNECT_FAILED,
    ASSISTANT_ERROR_REQUEST_TIMEOUT,
    ASSISTANT_ERROR_HTTP_UNAUTHORIZED,
    ASSISTANT_ERROR_HTTP_RATE_LIMITED,
    ASSISTANT_ERROR_HTTP_SERVER,
    ASSISTANT_ERROR_EMPTY_RESPONSE,
    ASSISTANT_ERROR_INVALID_JSON,
    ASSISTANT_ERROR_INVALID_PROTOCOL,
    ASSISTANT_ERROR_ACTION_FAILED,
    ASSISTANT_ERROR_ASR_FAILED,
    ASSISTANT_ERROR_TTS_FAILED,
} assistant_error_code_t;

typedef enum {
    ASSISTANT_STAGE_NONE = 0,
    ASSISTANT_STAGE_NETWORK,
    ASSISTANT_STAGE_ASR_CONNECT,
    ASSISTANT_STAGE_ASR_STREAM,
    ASSISTANT_STAGE_ASR_RESPONSE,
    ASSISTANT_STAGE_BACKEND_CONNECT,
    ASSISTANT_STAGE_BACKEND_HTTP,
    ASSISTANT_STAGE_BACKEND_PROTOCOL,
    ASSISTANT_STAGE_ACTION,
    ASSISTANT_STAGE_TTS_CONNECT,
    ASSISTANT_STAGE_TTS_STREAM,
    ASSISTANT_STAGE_TTS_PLAYBACK,
} assistant_stage_t;

typedef struct {
    assistant_error_code_t error_code;
    assistant_stage_t stage;
    esp_err_t esp_error;
    int http_status;
    bool retryable;
    uint32_t request_id;
    uint32_t elapsed_ms;
    char trace_id[48];
    char text[512];
    char detail[96];
} assistant_response_t;
```

`assistant_router_take_response()` 的返回值只表示“是否取到结果”：

- `ESP_ERR_NOT_FOUND`：请求仍在进行。
- `ESP_OK`：已取到结果，业务成功或失败由 `assistant_response_t.error_code` 判断。
- `ESP_ERR_INVALID_ARG`：调用接口本身错误。

这样后端的 HTTP、协议和动作错误不会再次被压缩成一个 `esp_err_t`。

### 任务 1：建立结构化诊断模型

**文件：**
- 创建：`main/assistant_diagnostics.h`
- 创建：`main/assistant_diagnostics.c`
- 修改：`main/CMakeLists.txt`
- 修改：`main/main.c:506`

- [ ] **步骤 1：先声明失败的自测入口和公共类型**

在 `assistant_diagnostics.h` 写入上述两个枚举和 `assistant_response_t`，并声明：

```c
void assistant_response_reset(assistant_response_t *response, uint32_t request_id);
void assistant_response_fail(assistant_response_t *response,
                             assistant_error_code_t code,
                             assistant_stage_t stage,
                             esp_err_t esp_error,
                             int http_status,
                             bool retryable,
                             const char *detail);
assistant_error_code_t assistant_error_from_backend(const char *backend_code,
                                                    int http_status);
const char *assistant_error_display_text(assistant_error_code_t code,
                                         assistant_stage_t stage);
const char *assistant_stage_name(assistant_stage_t stage);
esp_err_t assistant_diagnostics_self_test_run(void);
```

- [ ] **步骤 2：注册空实现并运行增量构建确认自测失败**

先让 `assistant_diagnostics_self_test_run()` 返回 `ESP_FAIL`，在 `main/CMakeLists.txt` 的 `SRCS` 加入 `assistant_diagnostics.c`，在 `app_main()` 现有自测序列中加入：

```c
ESP_ERROR_CHECK(assistant_diagnostics_self_test_run());
```

运行：

```powershell
cd E:\emb_agent_new
idf.py build
```

预期：Ninja 仅重编译受影响源文件，应用启动后自测会因诊断模块尚未实现而失败；不得删除 `build`。

- [ ] **步骤 3：实现错误构造与映射**

`assistant_response_reset()` 必须清零并保留请求号；`assistant_response_fail()` 必须写入全部字段且保证字符串结尾为 `\0`。后端代码映射固定为：

```c
if (strcmp(backend_code, "LLM_TIMEOUT") == 0) return ASSISTANT_ERROR_REQUEST_TIMEOUT;
if (strcmp(backend_code, "ACTION_INVALID") == 0) return ASSISTANT_ERROR_ACTION_FAILED;
if (strcmp(backend_code, "LLM_PROTOCOL_ERROR") == 0 ||
    strcmp(backend_code, "REQUEST_INVALID") == 0 ||
    strcmp(backend_code, "RULE_UNSUPPORTED") == 0) {
    return ASSISTANT_ERROR_INVALID_PROTOCOL;
}
if (http_status == 401 || http_status == 403) return ASSISTANT_ERROR_HTTP_UNAUTHORIZED;
if (http_status == 429) return ASSISTANT_ERROR_HTTP_RATE_LIMITED;
if (http_status >= 500) return ASSISTANT_ERROR_HTTP_SERVER;
return ASSISTANT_ERROR_INVALID_PROTOCOL;
```

屏幕文案固定为短句，日志保留详细字段：

```c
case ASSISTANT_ERROR_NETWORK_OFFLINE: return "网络未连接";
case ASSISTANT_ERROR_CONNECT_FAILED: return "后端连接失败";
case ASSISTANT_ERROR_REQUEST_TIMEOUT: return stage == ASSISTANT_STAGE_ASR_RESPONSE ? "语音识别超时" : "助教响应超时";
case ASSISTANT_ERROR_HTTP_UNAUTHORIZED: return "后端未授权";
case ASSISTANT_ERROR_HTTP_RATE_LIMITED: return "请求太频繁";
case ASSISTANT_ERROR_HTTP_SERVER: return "后端服务异常";
case ASSISTANT_ERROR_EMPTY_RESPONSE: return "后端没有回复";
case ASSISTANT_ERROR_INVALID_JSON: return "后端格式错误";
case ASSISTANT_ERROR_INVALID_PROTOCOL: return "后端协议错误";
case ASSISTANT_ERROR_ACTION_FAILED: return "亮灯执行失败";
case ASSISTANT_ERROR_ASR_FAILED: return "语音识别失败";
case ASSISTANT_ERROR_TTS_FAILED: return "语音播放失败";
```

- [ ] **步骤 4：完成纯逻辑映射自测**

使用 `ESP_RETURN_ON_FALSE` 覆盖：401、403、429、500、`LLM_TIMEOUT`、`LLM_PROTOCOL_ERROR`、`ACTION_INVALID`、未知代码、空代码、ASR 超时文案和 TTS 文案。自测不得发网络请求。

- [ ] **步骤 5：增量构建并提交**

运行：

```powershell
idf.py build
git diff --check
git add main/assistant_diagnostics.h main/assistant_diagnostics.c main/CMakeLists.txt main/main.c
git commit -m "feat: 增加助教链路诊断模型"
```

预期：构建通过，启动日志包含 `assistant diagnostics self-test passed`。

### 任务 2：保留远程后端完整失败信息

**文件：**
- 修改：`main/remote_assistant.h`
- 修改：`main/remote_assistant.c:26`

- [ ] **步骤 1：把结果缓存改为结构化响应**

将：

```c
typedef struct { bool ready; uint32_t id; esp_err_t err; char text[REMOTE_TEXT_MAX]; } remote_result_t;
```

替换为：

```c
typedef struct {
    bool ready;
    uint32_t id;
    assistant_response_t response;
} remote_result_t;
```

并将头文件接口改为：

```c
esp_err_t remote_assistant_take_response(uint32_t request_id,
                                         assistant_response_t *out_response);
```

- [ ] **步骤 2：记录请求开始时间和 HTTP 传输错误**

在 `request_remote()` 开头保存 `esp_timer_get_time()`，函数改为填充 `assistant_response_t *response`。`esp_http_client_open()`、写入和读取错误映射为 `ASSISTANT_ERROR_CONNECT_FAILED` / `ASSISTANT_STAGE_BACKEND_CONNECT`；返回 `ESP_ERR_TIMEOUT` 时映射为 `ASSISTANT_ERROR_REQUEST_TIMEOUT`。同时加入 `#include "esp_timer.h"`。

每个完成分支都写入：

```c
response->elapsed_ms = (uint32_t)((esp_timer_get_time() - started_us) / 1000);
```

- [ ] **步骤 3：区分空响应、非法 JSON 和非法成功协议**

固定判断顺序：

```c
if (total == 0) {
    assistant_response_fail(response, ASSISTANT_ERROR_EMPTY_RESPONSE,
                            ASSISTANT_STAGE_BACKEND_HTTP, ESP_ERR_INVALID_SIZE,
                            status, true, "empty HTTP body");
    return;
}
cJSON *reply = cJSON_Parse(response_body);
if (reply == NULL) {
    assistant_response_fail(response, ASSISTANT_ERROR_INVALID_JSON,
                            ASSISTANT_STAGE_BACKEND_PROTOCOL, ESP_ERR_INVALID_RESPONSE,
                            status, false, "response body is not JSON");
    return;
}
```

2xx 成功响应必须至少有非空 `assistant_text` 或合法 `tool_call`；否则为 `ASSISTANT_ERROR_INVALID_PROTOCOL`。成功时写入 `response->http_status`、解析可选 `trace_id` 并复制到 `response->trace_id`。

- [ ] **步骤 4：解析后端结构化错误体**

非 2xx 响应读取：

```json
{
  "error": {
    "code": "LLM_TIMEOUT",
    "stage": "llm_provider",
    "retryable": true,
    "message": "模型服务响应超时"
  },
  "trace_id": "tr_xxx"
}
```

缺少 `error.code` 时按 HTTP 状态映射；存在时优先调用 `assistant_error_from_backend()`。复制 `message` 到 `detail`、复制 `trace_id`，并把后端 `stage` 记录到日志，不直接显示长消息。

- [ ] **步骤 5：让动作执行失败保留为独立阶段**

`tuco_agent_execute_remote_tool()` 失败时不得在已有文字下返回 `ESP_OK`。改为：

```c
assistant_response_fail(response, ASSISTANT_ERROR_ACTION_FAILED,
                        ASSISTANT_STAGE_ACTION, err, status, true,
                        "remote tool execution failed");
```

日志必须包含 `request_id`、`http_status`、`elapsed_ms`、`trace_id`、错误代码和阶段；不得记录 URL 中可能存在的密钥，也不得打印 HTTP Authorization 头。

- [ ] **步骤 6：完成结果获取与取消语义**

`remote_assistant_take_response()` 在结果就绪时复制整个结构并清空缓存，返回 `ESP_OK`；未就绪返回 `ESP_ERR_NOT_FOUND`。`remote_task()` 不再覆盖成“后端助教暂时无法连接”，错误屏幕文案统一由显示层生成。

- [ ] **步骤 7：增量构建并提交**

运行：

```powershell
idf.py build
git diff --check
git add main/remote_assistant.h main/remote_assistant.c
git commit -m "feat: 保留远程助教结构化错误"
```

### 任务 3：让助教路由器传递扩展结果

**文件：**
- 修改：`main/assistant_router.h`
- 修改：`main/assistant_router.c`

- [ ] **步骤 1：增加统一结果接口**

头文件声明：

```c
esp_err_t assistant_router_take_response(uint32_t request_id,
                                         assistant_response_t *out_response);
```

删除仅返回文本的 `assistant_router_take_result()` 声明和实现，并更新唯一调用方到新接口。

- [ ] **步骤 2：远程模式直接透传结构化响应**

远程模式调用 `remote_assistant_take_response()`；结果被取走或发生接口错误时清除 `s_active_request_id`，仅 `ESP_ERR_NOT_FOUND` 保持请求活跃。

- [ ] **步骤 3：包装内置助教结果**

内置模式继续调用现有 `tuco_agent_take_result()`：

```c
char text[sizeof(out_response->text)] = {0};
const esp_err_t err = tuco_agent_take_result(request_id, text, sizeof(text));
if (err == ESP_ERR_NOT_FOUND) return err;
assistant_response_reset(out_response, request_id);
if (err == ESP_OK && text[0] != '\0') {
    strlcpy(out_response->text, text, sizeof(out_response->text));
} else if (err == ESP_OK) {
    assistant_response_fail(out_response, ASSISTANT_ERROR_EMPTY_RESPONSE,
                            ASSISTANT_STAGE_BACKEND_PROTOCOL,
                            ESP_ERR_INVALID_RESPONSE, 0, true,
                            "local assistant returned empty text");
} else {
    assistant_response_fail(out_response, ASSISTANT_ERROR_CONNECT_FAILED,
                            ASSISTANT_STAGE_BACKEND_CONNECT,
                            err, 0, true, esp_err_to_name(err));
}
return ESP_OK;
```

这里不修改内置助教实现，只保证上层得到统一结构；远程后端的 HTTP/协议细分不丢失。

- [ ] **步骤 4：扩展路由器自测**

`assistant_router_self_test_run()` 至少验证：当前模式合法、空结果结构可初始化、错误结果 `error_code != ASSISTANT_ERROR_NONE` 时不会被当成成功文本。

- [ ] **步骤 5：增量构建并提交**

```powershell
idf.py build
git diff --check
git add main/assistant_router.h main/assistant_router.c
git commit -m "refactor: 统一助教路由结果协议"
```

### 任务 4：实现 6 秒错误显示锁存

**文件：**
- 创建：`main/assistant_error_latch.h`
- 创建：`main/assistant_error_latch.c`
- 修改：`main/CMakeLists.txt`
- 修改：`main/main.c:506`

- [ ] **步骤 1：先定义锁存接口和失败自测**

```c
typedef struct {
    bool active;
    uint32_t shown_at_ms;
    uint32_t minimum_until_ms;
    assistant_error_code_t code;
    assistant_stage_t stage;
    char text[96];
} assistant_error_latch_t;

void assistant_error_latch_reset(assistant_error_latch_t *latch);
void assistant_error_latch_raise(assistant_error_latch_t *latch,
                                 uint32_t now_ms,
                                 uint32_t minimum_duration_ms,
                                 assistant_error_code_t code,
                                 assistant_stage_t stage,
                                 const char *text);
bool assistant_error_latch_blocks_status(const assistant_error_latch_t *latch,
                                         uint32_t now_ms);
void assistant_error_latch_clear_for_retry(assistant_error_latch_t *latch);
void assistant_error_latch_clear_for_exit(assistant_error_latch_t *latch);
esp_err_t assistant_error_latch_self_test_run(void);
```

先让自测返回 `ESP_FAIL`，注册源文件并在启动自测序列调用。

- [ ] **步骤 2：实现 tick 回绕安全的最短显示判断**

```c
static bool deadline_pending(uint32_t now_ms, uint32_t deadline_ms)
{
    return (int32_t)(deadline_ms - now_ms) > 0;
}

bool assistant_error_latch_blocks_status(const assistant_error_latch_t *latch,
                                         uint32_t now_ms)
{
    return latch != NULL && latch->active &&
           deadline_pending(now_ms, latch->minimum_until_ms);
}
```

超过 6 秒后锁存可以继续保留为当前错误，但普通状态可以覆盖；右键重试和退出必须立即清空整个结构。

- [ ] **步骤 3：完成纯逻辑锁存自测**

覆盖以下时序：

1. `now=1000` 报错，`6999` 仍阻止普通状态。
2. `7000` 不再阻止普通状态。
3. 新错误在 `3000` 覆盖旧错误，截止时间更新到 `9000`。
4. `clear_for_retry()` 立即取消。
5. `clear_for_exit()` 立即取消。
6. `now=UINT32_MAX-1000` 时跨回绕仍正确阻止 6 秒。

- [ ] **步骤 4：增量构建并提交**

```powershell
idf.py build
git diff --check
git add main/assistant_error_latch.h main/assistant_error_latch.c main/CMakeLists.txt main/main.c
git commit -m "feat: 增加助教错误显示锁存"
```

### 任务 5：把错误锁存接入语音状态机

**文件：**
- 修改：`main/volcengine_voice.h`
- 修改：`main/volcengine_voice.c:43`

- [ ] **步骤 1：新增显示锁存状态和统一上报函数**

在文件静态状态中加入：

```c
#define ERROR_MINIMUM_VISIBLE_MS 6000U
static assistant_error_latch_t s_error_latch;
```

将 `report_error(const char *text)` 替换为：

```c
static void report_assistant_error(assistant_error_code_t code,
                                   assistant_stage_t stage,
                                   esp_err_t esp_error,
                                   const char *detail,
                                   const char *trace_id)
```

该函数负责停止当前录音/播放/请求、把运行阶段恢复为 `VOICE_READY`、写详细日志、调用 `assistant_error_latch_raise()`，再把短文案写入 `voice_assistant_status_t`。错误锁存不得阻塞内部状态机。

- [ ] **步骤 2：让普通状态更新尊重锁存**

把现有 `status_set()` 拆成：

```c
static void status_publish(voice_assistant_state_t state,
                           bool visible,
                           bool error,
                           const char *text,
                           bool force)
```

当 `force == false` 且 `assistant_error_latch_blocks_status(..., now_ms)` 为真时直接返回；错误上报使用 `force=true`。`录音中`、`识别中`、`思考中`、`播放中`、`可说话` 均为普通状态，不得覆盖 6 秒内的错误。

`assistant_error_latch_t` 与 `s_status` 的所有读写都使用现有 `s_lock` 临界区保护；当最短显示时间已到且普通状态成功发布时，同时重置锁存，避免过期错误残留为活动状态。

- [ ] **步骤 3：明确映射 ASR 错误阶段**

逐处替换：

- Wi-Fi 未连接且用户正在请求：`NETWORK_OFFLINE / NETWORK / ESP_ERR_INVALID_STATE`。
- ASR WebSocket 打开失败：`ASR_FAILED / ASR_CONNECT`。
- ASR 上传失败：`ASR_FAILED / ASR_STREAM`。
- ASR 协议帧、压缩帧、服务错误：`ASR_FAILED / ASR_RESPONSE`。
- ASR 截止时间：`REQUEST_TIMEOUT / ASR_RESPONSE`。

回调线程只写入结构化的待处理错误，不直接修改 UI；语音任务线程取出后调用统一上报函数。

- [ ] **步骤 4：接入结构化后端结果**

`poll_agent_result()` 改为接收 `assistant_response_t`：

```c
assistant_response_t response;
const esp_err_t poll = assistant_router_take_response(s_agent_request_id, &response);
if (poll == ESP_ERR_NOT_FOUND) { /* 检查本地总截止时间 */ }
else if (poll != ESP_OK) { /* 接口调用错误 */ }
else if (response.error_code != ASSISTANT_ERROR_NONE) {
    report_assistant_error(response.error_code, response.stage,
                           response.esp_error, response.detail,
                           response.trace_id);
} else {
    strlcpy(s_agent_reply, response.text, sizeof(s_agent_reply));
    s_agent_reply_ready = true;
}
```

成功日志打印 `request_id`、`elapsed_ms` 和 `trace_id`；禁止打印密钥。

- [ ] **步骤 5：明确映射 TTS 错误阶段**

- TTS WebSocket 打开失败：`TTS_FAILED / TTS_CONNECT`。
- TTS 请求发送和帧协议错误：`TTS_FAILED / TTS_STREAM`。
- TTS 响应超时：`REQUEST_TIMEOUT / TTS_STREAM`。
- 本地播放失败：`TTS_FAILED / TTS_PLAYBACK`。

固定游戏提示 `TTS_KIND_GAMEPLAY_PROMPT` 仍保持低优先级静默失败，不覆盖手动助教错误；手动对话的 TTS 错误必须显示。

- [ ] **步骤 6：实现重试和退出清除**

在 `voice_assistant_update()` 中通过持有 `s_lock` 的小型清理助手执行：

```c
if (press_started) {
    assistant_error_latch_clear_for_retry(&s_error_latch);
    if (s_phase == VOICE_ERROR) s_phase = VOICE_READY;
}
if (!play_active && s_session_play_active) {
    assistant_error_latch_clear_for_exit(&s_error_latch);
    status_publish(VOICE_ASSISTANT_READY, false, false, "", true);
}
```

删除当前“松开右键立即清除错误”的逻辑：

```c
if (s_phase == VOICE_ERROR && !s_key_pressed) { ... }
```

再次按右键后必须在同一次按压开始新的 ASR，不要求用户先等待 6 秒。

- [ ] **步骤 7：增加语音诊断自测**

新增 `voice_assistant_diagnostics_self_test_run()`，只测试状态发布策略，不连接网络：错误发布后立即尝试发布“思考中”应仍显示错误；模拟 6 秒后普通状态可覆盖；重试和退出均立即清除。

- [ ] **步骤 8：增量构建并提交**

```powershell
idf.py build
git diff --check
git add main/volcengine_voice.h main/volcengine_voice.c
git commit -m "feat: 显示助教链路阶段错误"
```

### 任务 6：增量烧录与实机诊断验证

**文件：**
- 修改：`E:\3.6bench\PROJECT_HANDOFF.md`

- [ ] **步骤 1：确认工作区和后端协议版本**

```powershell
git -C E:\emb_agent_new status --short --branch
git -C E:\3.6bench rev-parse HEAD
```

记录后端智能路由计划执行后的提交号；确认 `sdkconfig` 和已有 `build` 目录存在，不执行 `fullclean`。

- [ ] **步骤 2：运行增量构建与烧录**

关闭占用 `COM9` 的串口程序后运行：

```powershell
cd E:\emb_agent_new
idf.py -p COM9 flash
```

预期：Ninja 只编译改动源文件并成功烧录 ESP32-P4。

- [ ] **步骤 3：用 pyserial 采集完整日志**

```powershell
@'
import time
import serial

with serial.Serial("COM9", 115200, timeout=0.2) as port:
    deadline = time.time() + 90
    while time.time() < deadline:
        line = port.readline().decode("utf-8", errors="replace").rstrip()
        if line:
            print(line)
'@ | python -
```

禁止改用 `serial_mcp`。日志验收字段：`request_id`、`stage`、错误代码、`http_status`、`elapsed_ms`、`trace_id`；任何日志不得出现 API Key。

- [ ] **步骤 4：验证成功链路**

进入 502 关，按住右键提问“接下来应该怎么做”，松开后确认：录音、识别、后端、TTS 均完成；成功日志带后端 `trace_id`；错误提示未出现。

- [ ] **步骤 5：验证网络错误与 6 秒锁存**

临时关闭已连接热点，按右键请求。确认屏幕显示“网络未连接”至少 6 秒，期间普通“可说话”或“思考中”不覆盖。恢复网络后再次按右键，错误立即清除并开始录音。

- [ ] **步骤 6：验证退出清除**

再次制造网络错误，在 6 秒内按左键退出到关卡树；确认错误立即消失，重新进入关卡不残留上一关错误。

- [ ] **步骤 7：验证后端结构化错误映射**

在本地测试后端或受控代理返回 401、429、500、空响应、非法 JSON、`LLM_TIMEOUT` 和 `ACTION_INVALID`，逐项确认屏幕短文案与串口详细日志一致。不得通过修改云端生产服务制造错误。

- [ ] **步骤 8：更新交接并提交**

在 `PROJECT_HANDOFF.md` 记录：新增模块、错误代码、6 秒规则、右键/退出行为、增量烧录命令、pyserial 命令、已验证固件提交号和后端提交号。

```powershell
git -C E:\3.6bench add PROJECT_HANDOFF.md
git -C E:\3.6bench commit -m "docs: 更新固件助教链路诊断交接"
```

- [ ] **步骤 9：提交固件实机验证结果**

```powershell
git -C E:\emb_agent_new status --short
git -C E:\emb_agent_new add main
git -C E:\emb_agent_new commit -m "test: 完成助教链路诊断实机验证"
```

仅提交本计划涉及文件；保持 `.superpowers/`、`runtime/` 和本地 `sdkconfig` 不进入 Git。
