# 图灵号 AI 电路助教项目交接文档

**更新时间：** 2026-08-08
**后端工作区：** `E:\3.6bench`
**固件工作区：** `E:\emb_agent_new`

---

## 1. 当前交接结论

### 后端

- GitHub 仓库：`https://github.com/ziyan2006/AI_Backend`
- 当前本地分支：`codex/smart-routing-diagnostics`
- 当前功能基线：`1811db4 fix: 优化电路动作降级与事实引导`
- 当前未提交的关卡内容调整：删除旧 503「进位汇聚」，原 504 全加器迁移为 503「全加主控」；501、502 分别更名为「个位引擎」「进位引擎」。后端规则、评测、测试台和学习活动已按新编号同步，并于 2026-08-08 部署云端。
- 前一功能提交：`2250494 feat: 增加设备端结构化错误诊断`
- 该分支已经完成单次主模型智能路由：模型在 `chat / goal / hint / explain / diagnose / act / clarify` 中选择本轮模式；只有 `act` 且返回后端生成的合法 `candidate_id` 时才映射为设备动作。
- `highlight_ports` 现在按 `connect / disconnect` 分开校验：新连接仍要求两个端口空闲；拆线则要求快照中真实存在该边，不会再因为端口已经连接而被最终归一化误删。
- 已完成关卡角色标签协议：固件在输入输出数量放齐后，为 `401/402/403/501/502/503/601/602` 的积木 OLED 分配 `A/B/C/选/个/进/Y/0/1/2/3`，并将同一结果写入快照。标签只是信号角色，不是积木名称，也不参与真值表判题。
- 紧凑槽位快照现支持七字段 `[slot_id,row,column,state,gate_or_raw_id,ports,role_label]`；后端兼容旧六字段格式。输入输出数量未齐时标签保持为空，后端不得自行猜测角色。
- 物理方向错误、同槽连接和多源输入等可定位的真实错误边会进入 `disconnect_edges`，优先生成拆线候选；动作映射失败时改为依据候选事实给出明确文字提示，不再返回“我先确认一下”或“再问我一次”。
- 有实时电路进度时，诊断事实和安全候选优先于关卡固定素材；301 不再把与门描述为汇总，601 明确由与门筛选条件、或门汇总结果，403 的错误连线不再与“继续接线”提示冲突。
- 设备成功响应新增 `trace_id`；设备失败响应统一为 `error.code / error.stage / error.retryable / error.message + trace_id`，覆盖超时、未配置、上游 HTTP、模型协议、请求校验和内部异常。
- 最近验证：完整 `pytest -q` 共 283 项通过；`ruff check src tests` 和 `git diff --check` 通过。唯一警告是 Starlette 测试客户端的 `httpx` 弃用提示。
- 修复后的 `flexible-routing-quality` 报告位于 `runtime/llm_evaluations/grounded-action-fallback-rerun/scenario-20260806T161841Z.json`：成功 16/20，4 次失败为 3 个上游 `ReadError` 和 1 个 `ConnectError`；所有成功执行轮中 301、403、502、601 都选择了真实拆线候选并得到 `highlight_ports(intent=disconnect)`。
- 再次重跑报告位于 `runtime/llm_evaluations/grounded-action-fallback-final/scenario-20260806T162057Z.json`：301、403、601 行为正常；502 的 4 轮失败为 1 个 `ReadTimeout` 和 3 个上游 HTTP 502，属于当前模型转发站不稳定，不是本地提示词、候选解析或工具映射回归。
- 本地分支尚未推送或合并；云端已通过带备份的源码覆盖部署到当前工作区版本，`.env` 未改动。

### 固件

- GitHub 仓库：`https://github.com/ziyan2006/esp32s3-ai-circuit-toy`
- 当前本地分支：`codex/assistant-diagnostics`
- 当前本地提交：`8ebddfb feat: 显示助教链路阶段错误`
- 当前未提交的关卡内容调整：关卡树改为 `501 -> 502 -> 503`，503 是原全加器；旧 503 和 504 均不再作为可玩关卡。全加器教程、学习小游戏、判题规则和进度同步表已迁移至新 503；升级时清除旧 `done_503`，并把旧 `done_504` 的全加器完成状态迁移到新 `done_503`。
- 前一提交：`c924b0a feat: 增加助教链路诊断模型`
- 当前烧录到设备的固件对应 `8ebddfb`；启动日志确认 `assistant diagnostics`、`assistant error latch`、`assistant router` 和 `voice assistant diagnostics` 四组自测通过。
- 远程助教现在保留 HTTP 状态、后端错误码、阶段、重试标记、耗时、`trace_id` 和本地动作执行错误；内置与远程模式通过统一 `assistant_response_t` 返回。
- 游玩界面错误至少锁存 6 秒；普通“录音中 / 识别中 / 思考中 / 播放中 / 可说话”不能覆盖。再次按右键立即清除并重试，退出关卡立即清除。
- 设备：ESP32-P4，串口 `COM9`，烧录使用 ESP-IDF `v5.5.4` 的已有 `build` 目录执行增量 `flash`。
- `sdkconfig` 有本机私密配置改动（包括网络/后端地址/密钥）；**禁止暂存、提交、复制到日志或交接文档**。

### 云端

- 主机：`root@8.137.182.96`（阿里云 1）。
- 后端目录：`/opt/apps/AI_Backend-circuit-coach-v2`。
- systemd 服务：`tuco-ai-backend.service`。本次没有登录服务器，也没有部署或重启服务，运行提交需要下次部署前重新核对。
- 2026-08-06 实机串口直接请求得到 HTTP 200 和正常文本，但成功响应的 `trace_id` 为空；结合本地新协议会强制返回 `trace_id`，可判断固件当前指向的云端仍是旧后端协议。
- 2026-08-08 已部署新的关卡目录与当前后端源码，服务 `tuco-ai-backend.service` 为 active；公网 `/api/health` 返回 200。503「全加主控」真实设备 HTTP 冒烟请求返回 200、正常儿童引导文本和非空 `trace_id`。
- 部署前备份位于云端 `/opt/apps/AI_Backend-circuit-coach-v2/.deploy-backups/20260808T151856/source-before-deploy.tar.gz`；不要使用破坏性 `reset --hard` 覆盖云端 `.env` 和运行配置。

---

## 2. 系统结构与主要路径

### 后端电路助教 V2

1. 固件在关卡内创建远程助教 Session，并构造 `tuco_circuit_v2` 电路快照。
2. 固件向 `POST /api/device/circuit-coach/decision` 提交 `session_id`、用户文字和快照。
3. 后端先根据真值表、快照和物理端口约束生成候选动作，再要求主模型一次性选择响应模式和可选 `candidate_id`。模型不能自行生成槽位、端口或积木参数。
4. 设备动作仍限制为：
   - `highlight_ports(intent=connect)`：只允许一个未连接输出端到另一个槽位未连接输入端；
   - `highlight_ports(intent=disconnect)`：只允许快照中真实存在的一条边，包括物理方向错误边；
   - `highlight_empty_slot`：只允许一个空槽与一个已解锁积木。
5. 成功响应返回 `assistant_text`、可选 `tool_call`、`topology_revision` 和 `trace_id`；失败响应返回结构化 `error` 与同一 `trace_id`。
6. 固件的后端助教执行器先执行合法工具动作，再把后端 `assistant_text` 交给 TTS；动作执行失败会保留为 `ACTION` 阶段错误，不再伪装成成功文本。

### 两种助教模式

- **内置助教：** 使用原固件 `tuco_agent` 链路和内置的硬件动作/固定语音提示。
- **后端助教：** 使用 `remote_assistant` 向云端后端请求决策；工具动作仍在设备本地执行，但语音文本由后端生成。
- 设置页面会显示当前模式，并在关卡进入、退出或模式切换时分别维护对应 Session。
- 后端地址和 Token 当前固定在本机 `sdkconfig`；比赛用途无需额外设备 Token，但仍不得把密钥提交到 Git。

### 工具调用后的语音文本

- 普通电路请求强制调用 `decide_circuit_turn`，同时返回自然中文和路由模式。只有合法 `act + candidate_id` 才产生设备工具。
- 若模型返回 `act` 但没有候选编号，后端降级为 `clarify`，保留文字但不执行动作，避免整轮协议失败。
- 固件仍兼容只有工具、没有文字的旧响应，并使用本地短句 `请看亮起的提示，再完成这一步。`；新后端应尽量始终给出可朗读文字。

### 结构化错误与显示锁存

- 后端错误格式：`{"error":{"code","stage","retryable","message"},"trace_id":"tr_..."}`。
- 固件本地错误阶段包括网络、ASR 连接/上传/响应、后端连接/HTTP/协议、动作、TTS 连接/传输/播放。
- 屏幕只显示短文案，例如“网络未连接”“助教响应超时”“后端协议错误”“亮灯执行失败”；串口保留 `request_id`、阶段、错误代码、`http_status`、`elapsed_ms`、`trace_id` 和详细原因。
- 错误至少显示 6 秒；新错误覆盖旧错误并重新计时。再次按右键和退出关卡都可立即清除。
- 固定游戏提示的 TTS 失败仍为低优先级静默失败，不覆盖手动助教错误。

---

## 3. 协议与实现位置

| 位置 | 职责 |
| --- | --- |
| `src/tuco_ai_backend/main.py` | FastAPI 路由、Session、设备电路助教 HTTP 端点 |
| `src/tuco_ai_backend/models.py` | `tuco_circuit_v2` 快照、关卡、槽位、端口、边模型 |
| `src/tuco_ai_backend/assistant_turn.py` | 主模型路由模式与 `decide_circuit_turn` 严格工具协议 |
| `src/tuco_ai_backend/device_errors.py` | 设备结构化错误模型和异常映射 |
| `src/tuco_ai_backend/providers/circuit_coach_v2.py` | V2 提示词、语义规划、路由解析和合法工具映射 |
| `src/tuco_ai_backend/circuit_diagnostics.py` | 真值表诊断、物理错误边识别和拆线事实 |
| `src/tuco_ai_backend/providers/openai_compatible.py` | 旧/通用 OpenAI 兼容客户端、关卡儿童引导规则 |
| `src/tuco_ai_backend/tools.py` | `highlight_ports`、`highlight_empty_slot` 的严格参数定义 |
| `src/tuco_ai_backend/evaluation.py` | 构造固件 V2 测评快照与并发测评逻辑 |
| `src/tuco_ai_backend/evaluation_cli.py` | 并发测评 CLI |
| `tests/test_circuit_coach_v2.py` | V2 解码、路由、工具、诊断和 Session 行为测试 |
| `docs/superpowers/specs/2026-08-06-grounded-action-fallback-design.md` | 可靠动作降级与实时事实优先设计 |
| `docs/superpowers/plans/2026-08-06-grounded-action-fallback.md` | 对应 TDD 实施与验证计划 |
| `E:\emb_agent_new\main\assistant_diagnostics.c` | 固件错误代码、阶段、后端错误映射和短文案 |
| `E:\emb_agent_new\main\assistant_error_latch.c` | 6 秒最短显示、tick 回绕、重试和退出清除 |
| `E:\emb_agent_new\main\remote_assistant.c` | 后端 HTTP 请求、结构化响应解析、`trace_id` 和工具执行 |
| `E:\emb_agent_new\main\tuco_agent.c` | 内置助教链路和硬件动作实现 |
| `E:\emb_agent_new\main\assistant_router.c` | 两种助教模式路由、Session 生命周期和统一结果协议 |
| `E:\emb_agent_new\main\volcengine_voice.c` | ASR/后端/TTS 阶段诊断、错误锁存和右键重试 |
| `E:\emb_agent_new\main\board_snapshot.c` | 16 槽位/64 端口 V2 快照构造 |

### 快照语义

- 面板有 16 个槽位、每槽 4 个端口，共 64 个端口。
- 空槽是 `state: "empty"`；只有实际检测到但 EEPROM ID 无法识别时才是 `state: "unidentified"`。
- 后端只可基于快照中真实存在的积木、端口角色和 `unlocked_gates` 给建议；关卡信号标签不是积木名称。
- 高亮端口必须是一对：一个输出端 + 一个输入端，且两端均未连接、属于不同槽位。
- 对带标签关卡，后端提示优先引用快照中的实际 `role_label`，例如“标记为 A 的输入积木”；不得把角色标签当作积木名称或凭空创造元件。

---

## 4. 常用命令

### 后端本地验证

在 `E:\3.6bench`：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
git diff --check
```

### 后端并发 LLM 测评

在 `E:\3.6bench`，确保 `.env` 有可用 LLM 配置：

```powershell
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --conversation-preset flexible-routing-quality `
  --concurrency 4 `
  --output-dir runtime\llm_evaluations\smart-routing-validation
```

会话预设已经自带 `circuit-v2` 协议，不能再同时传 `--protocol` 或 `--levels`。报告写入 `runtime\llm_evaluations\`；重点检查 `route_mode`、原始回复、工具调用和异常 Trace。

### 固件增量烧录

在 `E:\emb_agent_new`：

```powershell
. E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1
idf.py -p COM9 flash
```

- 只要 `build` 目录和 `sdkconfig` 未改动，上述命令使用 Ninja 增量构建；避免主动执行 `fullclean` 或删除 `build`。
- 烧录前关闭串口监视器，否则会报 `COM9 ... PermissionError(13)`；烧录后可再以 `115200` 连接串口。

### 固件串口诊断

禁止使用不稳定的 `serial_mcp`，统一使用 pyserial：

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
'@ | E:\Espressif\python_env\idf5.5_py3.14_env\Scripts\python.exe -
```

2026-08-06 已验证：固件版本 `8ebddfb` 启动，自测全部通过；串口 `/agent 你是谁` 得到 HTTP 200 和正常文本，日志记录耗时约 6.9 秒。由于旧云端成功响应没有 `trace_id`，日志中的 `trace_id` 为空。

### 云端部署与检查

```powershell
ssh root@8.137.182.96
cd /opt/apps/AI_Backend-circuit-coach-v2
git status --short
git pull --ff-only origin codex/smart-routing-diagnostics
systemctl restart tuco-ai-backend.service
systemctl is-active tuco-ai-backend.service
curl -fsS http://127.0.0.1:8000/api/health
journalctl -u tuco-ai-backend.service --no-pager -n 100
```

云端目录含未跟踪 `.venv`，这是预期运行时文件；部署时保留它。若出现除 `.venv` 外的未提交/未跟踪变更，应先停下核对，不要覆盖。

---

## 5. 安全与运行注意事项

1. `.env`、`sdkconfig`、云端 `.env` 均可能含 API Key、Wi-Fi 与服务地址；不得打印、提交或发送到交接文档。
2. 后端当前部署在公网主机；无需变更防火墙、Nginx 或旧目录 `/opt/apps/AI_Backend`，除非明确安排迁移。
3. 若设备听到内置固定话术，先在设置页确认已选择“后端助教”，再查看串口 `remote_assistant` / `assistant_router` 日志和云端 `journalctl`。
4. 如果设备成功日志中的 `trace_id` 为空，说明云端仍未升级到 `2250494` 或其后续合并提交；先核对云端 Git 提交，不要在固件侧伪造 `trace_id`。
5. 固件的本地 `sdkconfig` 不在 Git；新机器接手时需自行配置后端 URL、火山语音配置和网络，再增量构建烧录。

---

## 6. 推荐后续工作

1. 用实机进入新 503「全加主控」，确认关卡树、教程、小游戏、云端助教和判题均使用新编号；成功响应已在云端冒烟测试中确认带非空 `trace_id`。
2. 实机补测三项交互：断网错误至少显示 6 秒；6 秒内再次按右键立即清除并录音；6 秒内退出关卡立即清除且重新进入不残留。
3. 使用受控本地代理分别返回 401、429、500、空响应、非法 JSON、`LLM_TIMEOUT` 和 `ACTION_INVALID`，不要通过破坏云端生产服务制造错误。
4. 儿童引导优化继续使用 `flexible-routing-quality` 多轮并发预设；上游 `ReadError`、`ConnectError`、`ReadTimeout` 和 HTTP 502 要单独统计，不能误判为提示词或协议回归。当前模型转发站连续评测时失败率偏高，必要时更换稳定上游后再比较模型质量。
5. 两个当前分支尚未推送或合并：后端 `codex/smart-routing-diagnostics`，固件 `codex/assistant-diagnostics`。推送前再次执行各自完整验证。
