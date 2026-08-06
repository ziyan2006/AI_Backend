# LLM 智能路由与固件链路诊断设计

## 背景

当前后端用固定短语把问题分为关卡目标、开始行动、提示、诊断和解释。自然表达未命中关键词时不会获得真值表规划依据，模型可能重复介绍关卡、猜测门类型或无法给出可执行步骤。固件侧则把连接、HTTP、响应格式、模型和工具错误压缩成少量 `esp_err_t`，最终通常只显示“失败”，并在松开右键后立即清除。

本设计同时解决两项问题：后端在一次主模型请求中完成智能命令路由，固件使用结构化错误和持久错误提示，使行为与故障均可诊断。

## 目标

- 自然表达和标准问法在相同快照下获得一致的语义依据。
- 模型明确区分聊天、目标介绍、轻提示、解释、诊断、执行和澄清。
- 模型只能选择后端已验证的候选编号，不能编造端口、槽位或未解锁积木。
- 非执行型回复不会因为存在规划而自动亮灯。
- 固件能够区分网络、HTTP、后端协议、模型、工具、ASR 和 TTS 故障。
- 错误至少显示 6 秒；普通状态不得覆盖，用户重试或退出关卡可提前清除。
- 日志保留请求编号、阶段、状态码、耗时和 `trace_id`，但不记录密钥。

## 非目标

- 不增加第二次独立 LLM 路由请求。
- 不引入向量数据库或额外语义模型。
- 不让模型直接填写硬件动作参数。
- 不修改关卡真值表和解锁规则。

## 后端智能路由

后端对已知关卡始终计算真值表诊断和安全候选，但把结果分为：

- `grounding_plan`：回答时使用的可靠事实，不代表执行。
- `execution_candidate`：只有模型选择执行模式并返回合法编号时才能执行。

主模型在一次请求中返回结构化决策：

```json
{
  "mode": "hint",
  "assistant_text": "先看看两个输入不一样时，结果应该是什么。",
  "candidate_id": null
}
```

允许的模式：

| mode | 用途 | 动作 |
| --- | --- | --- |
| `chat` | 自我介绍和闲聊 | 禁止 |
| `goal` | 介绍关卡目标 | 禁止 |
| `hint` | 给一个小提示 | 禁止 |
| `explain` | 解释候选、积木或连接原理 | 禁止 |
| `diagnose` | 判断当前电路问题 | 禁止 |
| `act` | 用户明确要求立即操作或亮灯 | 允许一个候选 |
| `clarify` | 指代不清或信息不足 | 禁止 |

执行模式示例：

```json
{
  "mode": "act",
  "assistant_text": "先放一块或门，把几路结果汇总起来吧。",
  "candidate_id": "rev27-action-1"
}
```

约束：

- `act` 必须提供 `candidate_id`，其他模式必须为空。
- `assistant_text` 必须是可朗读的儿童中文。
- 模型只能选择候选编号，动作参数由 `SemanticActionGateway` 映射。
- 结构错误、无效候选或过期拓扑均降级为文字回复，不执行动作。
- 删除当前“模型未选择工具时自动执行第一个候选”的逻辑。

历史保留最近六条有效对话，并提供当前积木、有效连线、安全候选、诊断事实和上一轮推荐摘要。遇到“它”“这个”“刚才那个”时，只有对象唯一时才直接解释，否则选择 `clarify`，不得自行补充门类型。

## 后端错误协议

设备接口返回统一错误体：

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

基础错误代码：

- `REQUEST_INVALID`：请求或快照不合法。
- `RULE_UNSUPPORTED`：关卡规则版本不支持。
- `LLM_UNCONFIGURED`：模型配置缺失。
- `LLM_TIMEOUT`：模型响应超时。
- `LLM_UPSTREAM_ERROR`：模型上游错误。
- `LLM_PROTOCOL_ERROR`：模型响应无法解析。
- `ACTION_INVALID`：动作候选无效或过期。
- `INTERNAL_ERROR`：未分类后端错误。

HTTP 状态继续表达大类，JSON 错误体提供可诊断细节。后端日志以 `trace_id` 关联原始输入、规划、模型请求与响应、动作校验和最终设备响应。

## 固件结构化诊断

固件使用明确错误代码：

```c
typedef enum {
    ASSISTANT_ERROR_NONE,
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
```

失败结果至少携带：`code`、`esp_error`、`http_status`、`retryable`、`request_id`、`elapsed_ms`、`stage`、`trace_id` 和短屏幕文案。

映射规则：

| 情况 | 固件错误 |
| --- | --- |
| Wi-Fi 未连接 | `NETWORK_OFFLINE` |
| DNS、TLS、TCP 或写入失败 | `CONNECT_FAILED` |
| 请求超时 | `REQUEST_TIMEOUT` |
| HTTP 401/403 | `HTTP_UNAUTHORIZED` |
| HTTP 429 | `HTTP_RATE_LIMITED` |
| HTTP 5xx | `HTTP_SERVER` |
| 2xx 空正文 | `EMPTY_RESPONSE` |
| JSON 解析失败 | `INVALID_JSON` |
| 缺少合法文字和工具 | `INVALID_PROTOCOL` |
| 本地亮灯工具失败 | `ACTION_FAILED` |

若后端返回结构化错误，优先使用后端的 `code`、`stage`、`retryable` 和 `trace_id`；否则根据 HTTP 和本地错误映射。

屏幕使用短文案，例如“网络未连接”“后端响应超时”“后端密钥无效”“后端服务异常”“后端返回格式错误”“亮灯提示执行失败”。串口输出完整诊断，但禁止打印 Token、Authorization 头或带凭据 URL。

## 错误提示锁存

运行阶段和屏幕状态分离。发生错误后立即清理请求、录音和播放资源，使链路恢复可重试；屏幕单独保存错误锁存：

```c
typedef struct {
    bool active;
    TickType_t visible_until;
    assistant_failure_t failure;
} assistant_error_latch_t;
```

规则：

1. 错误至少显示 6 秒。
2. “识别中”“思考中”“播放中”、关卡提示和默认文字不能覆盖有效锁存。
3. 用户再次按下右键时立即清除锁存并开始新录音。
4. 退出关卡时立即清除。
5. 新错误覆盖旧错误并重新计时。
6. 到期后恢复页面当前默认提示。

错误锁存只影响显示，不阻塞内部任务和用户重试。

## 可观测性

后端每轮记录路由模式、语义计划、诊断、模型原始输入输出、候选选择、动作校验、阶段耗时和 `trace_id`。固件每轮记录本地请求编号、后端 `trace_id`、助教模式、HTTP 状态、响应字节数、错误阶段、错误代码、工具结果以及 ASR、后端和 TTS 独立耗时。

## 测试

后端行为测试覆盖：

- “我该从哪儿下手”选择 `act`。
- “给我一点方向，别公布答案”选择 `hint` 且不执行工具。
- “为什么是这个”选择 `explain` 并引用真实候选。
- “桌上的这块能用吗”根据实际快照回答。
- “你是谁”选择 `chat`，即使有候选也不执行。
- 指代不唯一时选择 `clarify`。
- 无效结构和候选均不执行。

固件测试覆盖断网、连接失败、超时、401、429、500、空响应、错误 JSON 和工具失败；验证错误 6 秒内不被覆盖，右键重试和退出关卡可立即清除，且日志不包含密钥。

集成测试使用可控假后端注入错误，并在 301、403、502、504、601 关运行标准问法与自然表达对照测试。

## 分期实施

1. 后端结构化智能路由和自然表达测评。
2. 后端结构化错误协议与阶段日志。
3. 固件错误类型、HTTP/协议映射和日志透传。
4. 固件 6 秒错误锁存、增量编译烧录和实机异常注入。

## 验收标准

- 自然操作问法不再依赖固定关键词。
- 标准问法和自然问法选择相同或等价候选。
- `chat`、`hint`、`explain` 和 `diagnose` 不执行工具。
- 模型不能执行候选集合之外的动作。
- 自由回复与规划结果不再互相矛盾。
- 后端异常具有稳定代码、阶段和 `trace_id`。
- 固件能区分网络、超时、鉴权、服务端、协议和工具错误。
- 错误在 6 秒内不被普通状态覆盖，用户可以立即主动重试。
