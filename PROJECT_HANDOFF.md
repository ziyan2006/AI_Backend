# 图灵号 AI 电路助教项目交接文档

**更新时间：** 2026-07-26
**后端工作区：** `E:\3.6bench`
**固件工作区：** `E:\emb_agent_new`

---

## 1. 当前交接结论

### 后端

- GitHub 仓库：`https://github.com/ziyan2006/AI_Backend`
- 当前分支：`codex/backend-circuit-coach-v2`
- 当前提交：`896affa Revert "feat(助教): 调整启发式提示与亮灯策略"`
- 该提交是一次**可追溯回退**：当前代码内容恢复到 `cf7db51`（工具调用后补全语音提示）对应的版本，未改写远程 Git 历史。
- 本地分支、GitHub 远程分支和云端运行目录均已核对为同一提交：`896affabcc0243e78f2c88ce4215161b9b2338aa`。
- 最近验证：`tests/test_circuit_coach_v2.py` 为 `5 passed`；云端健康检查正常。

### 固件

- GitHub 仓库：`https://github.com/ziyan2006/esp32s3-ai-circuit-toy`
- 当前分支：`codex/firmware-backend-integration`
- 已推送提交：`28b756b refactor(助教): 拆分内置与后端工具执行`
- 其父提交 `c486ec3` 已包含中文字体修复；当前烧录到设备的固件对应 `28b756b`。
- 设备：ESP32-P4，串口 `COM9`，烧录使用 ESP-IDF `v5.5.4` 的已有 `build` 目录执行增量 `flash`。
- `sdkconfig` 有本机私密配置改动（包括网络/后端地址/密钥）；**禁止暂存、提交、复制到日志或交接文档**。

### 云端

- 主机：`root@8.137.182.96`（阿里云 1）。
- 后端目录：`/opt/apps/AI_Backend-circuit-coach-v2`。
- systemd 服务：`tuco-ai-backend.service`，当前为 `active`。
- 服务健康检查：`http://127.0.0.1:8000/api/health`，当前返回 `status: ok`。
- 部署方法：在上述目录快进拉取 `codex/backend-circuit-coach-v2`，然后执行 `systemctl restart tuco-ai-backend.service`；不要使用破坏性 `reset --hard` 覆盖云端配置。
- 云端日志时钟显示为 **2026-07-27**，比本地交接日期 **2026-07-26** 快一天；排查日志时需按此偏差换算。

---

## 2. 系统结构与主要路径

### 后端电路助教 V2

1. 固件在关卡内创建远程助教 Session，并构造 `tuco_circuit_v2` 电路快照。
2. 固件向 `POST /api/device/circuit-coach/decision` 提交 `session_id`、用户文字和快照。
3. 后端 `CircuitCoachV2Client` 将关卡、已解锁积木、槽位、端口角色和已连线交给 LLM，并限制工具调用为：
   - `highlight_ports`：只允许一个未连接输出端到另一个槽位未连接输入端；
   - `highlight_empty_slot`：只允许一个空槽与一个已解锁积木。
4. 后端返回 `assistant_text`、可选 `tool_call` 和 `topology_revision`。
5. 固件的后端助教执行器先执行合法工具动作，再把后端 `assistant_text` 交给 TTS 播放。

### 两种助教模式

- **内置助教：** 使用原固件 `tuco_agent` 链路和内置的硬件动作/固定语音提示。
- **后端助教：** 使用 `remote_assistant` 向云端后端请求决策；工具动作仍在设备本地执行，但语音文本由后端生成。
- 设置页面会显示当前模式，并在关卡进入、退出或模式切换时分别维护对应 Session。
- 后端地址和 Token 当前固定在本机 `sdkconfig`；比赛用途无需额外设备 Token，但仍不得把密钥提交到 Git。

### 工具调用后的语音文本

- 某些模型会在工具调用时返回空 `content`。后端的 `cf7db51` 逻辑会在首轮规划工具动作后，发起第二次**不带 tools**的请求补充可朗读文本。
- 固件 `remote_assistant.c` 可同时处理 `assistant_text` 与 `tool_call`；若响应只有工具而没有文本，才会使用本地兜底句 `请看亮起的提示，再完成这一步。`。
- `77aac1f` 曾尝试将泛化提示改为先提问、延迟工具调用；实测效果不佳，已由 `896affa` 回退。后续如再次优化儿童引导，应在新分支中用并发测评验证后再部署。

---

## 3. 协议与实现位置

| 位置 | 职责 |
| --- | --- |
| `src/tuco_ai_backend/main.py` | FastAPI 路由、Session、设备电路助教 HTTP 端点 |
| `src/tuco_ai_backend/models.py` | `tuco_circuit_v2` 快照、关卡、槽位、端口、边模型 |
| `src/tuco_ai_backend/providers/circuit_coach_v2.py` | V2 提示词、合法工具校验、工具后补语音 |
| `src/tuco_ai_backend/providers/openai_compatible.py` | 旧/通用 OpenAI 兼容客户端、关卡儿童引导规则 |
| `src/tuco_ai_backend/tools.py` | `highlight_ports`、`highlight_empty_slot` 的严格参数定义 |
| `src/tuco_ai_backend/evaluation.py` | 构造固件 V2 测评快照与并发测评逻辑 |
| `src/tuco_ai_backend/evaluation_cli.py` | 并发测评 CLI |
| `tests/test_circuit_coach_v2.py` | V2 解码、工具、补语音和 Session 行为测试 |
| `E:\emb_agent_new\main\remote_assistant.c` | 后端 HTTP 请求、响应解析、后端模式的本地工具执行 |
| `E:\emb_agent_new\main\tuco_agent.c` | 内置助教链路和硬件动作实现 |
| `E:\emb_agent_new\main\assistant_router.c` | 两种助教模式路由、Session 生命周期 |
| `E:\emb_agent_new\main\board_snapshot.c` | 16 槽位/64 端口 V2 快照构造 |

### 快照语义

- 面板有 16 个槽位、每槽 4 个端口，共 64 个端口。
- 空槽是 `state: "empty"`；只有实际检测到但 EEPROM ID 无法识别时才是 `state: "unidentified"`。
- 后端只可基于快照中真实存在的积木、端口角色和 `unlocked_gates` 给建议；关卡信号标签不是积木名称。
- 高亮端口必须是一对：一个输出端 + 一个输入端，且两端均未连接、属于不同槽位。

---

## 4. 常用命令

### 后端本地验证

在 `E:\3.6bench`：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest tests\test_circuit_coach_v2.py -q
.\.venv\Scripts\python.exe -m ruff check src\tuco_ai_backend tests
git diff --check
```

### 后端并发 LLM 测评

在 `E:\3.6bench`，确保 `.env` 有可用 LLM 配置：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m tuco_ai_backend.evaluation_cli `
  --protocol circuit-v2 `
  --circuit-setup placed-io `
  --levels 101,301,401,403 `
  --question '接下来应该怎么做？给我点提示。' `
  --concurrency 4
```

报告写入 `runtime\llm_evaluations\`。工具调用、语音文本、延迟与失败原因都应先在报告中审阅，再修改云端。

### 固件增量烧录

在 `E:\emb_agent_new`：

```powershell
. E:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1
idf.py -p COM9 flash
```

- 只要 `build` 目录和 `sdkconfig` 未改动，上述命令使用 Ninja 增量构建；避免主动执行 `fullclean` 或删除 `build`。
- 烧录前关闭串口监视器，否则会报 `COM9 ... PermissionError(13)`；烧录后可再以 `115200` 连接串口。

### 云端部署与检查

```powershell
ssh root@8.137.182.96
cd /opt/apps/AI_Backend-circuit-coach-v2
git status --short
git pull --ff-only origin codex/backend-circuit-coach-v2
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
4. 如果后端响应工具调用后无语音，检查云端是否仍为 `896affa`（代码内容等同 `cf7db51`），并检查 LLM 的第二次无 tools 请求是否成功。
5. 固件的本地 `sdkconfig` 不在 Git；新机器接手时需自行配置后端 URL、火山语音配置和网络，再增量构建烧录。

---

## 6. 推荐后续工作

1. 先在实机确认“后端助教”模式下工具动作和后端语音均生效，再考虑新的儿童引导优化。
2. 儿童引导优化必须新建分支，并同时测：泛化求提示、明确亮灯、明确直接答案、缺输入输出、已摆好输入输出五种状态；不要只看单个关卡。
3. 后端提示词改动后，用 `evaluation_cli` 并发测试代表关卡，并在真实设备上确认没有退回固件固定文案。
4. 如继续改固件设置页或后端接入，优先维护 `tuco_circuit_v2` 的槽位/端口/空槽语义一致性。
