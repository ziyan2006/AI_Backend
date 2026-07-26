# 图灵号 AI 后端项目交接文档

**更新时间：** 2026-07-26
**项目：** TUCO AI Backend（图灵号 AI 电路语音助手、协议测试台与浏览器电路模拟器）
**工作区：** `E:\3.6bench`

---

## 1. 当前状态

- 当前分支：`feature/improve-llm-response-quality`，跟踪 `origin/feature/improve-llm-response-quality`。
- 该分支基于 `main` 的 `523998a`，已推送两项 LLM 引导改进：`674d7dd`、`28eb8d0`。本次配置持久化修复已在本地提交，尚未推送。
- 后端和前端测试台均由同一个 FastAPI 服务提供；前端没有独立构建或启动步骤。
- 2026-07-26 已验证 `http://127.0.0.1:8000/api/health` 返回正常；服务使用 Uvicorn `--reload` 运行时，源码变更会自动重载。
- 真实固件工作区位于 `E:\emb_agent_new\main`；远程仓库为 `https://github.com/ziyan2006/esp32s3-ai-circuit-toy`。
- 根目录 `.env` 已被 `.gitignore` 忽略，禁止提交或在日志、文档中输出密钥。

## 2. 目录与职责

| 路径 | 职责 |
| --- | --- |
| `src/tuco_ai_backend/main.py` | FastAPI 路由、HTTP 决策端点、Session API、静态前端服务 |
| `src/tuco_ai_backend/config.py` | `.env` 加载、运行时配置更新与持久化 |
| `src/tuco_ai_backend/providers/openai_compatible.py` | OpenAI 兼容 LLM、动态提示注入、单对端口高亮校验 |
| `src/tuco_ai_backend/voice_pipeline.py` | ASR → LLM → 工具调用 → TTS 语音链路 |
| `src/tuco_ai_backend/device_ws.py` | 设备 WebSocket 协议、语音链路 Trace 转发 |
| `src/tuco_ai_backend/session_store.py` | 内存型 Session 生命周期、短期会话历史、日志分类与详情查询 |
| `src/tuco_ai_backend/frontend/circuit-simulator.js` | 16 槽位、9 类积木、64 端口、连线判定和快照序列化 |
| `src/tuco_ai_backend/frontend/app.js` | 测试台交互、模拟器桥接、HTTP/WS 请求、Session 看板 |
| `tests/test_config.py` / `tests/test_api.py` | 配置持久化、配置 API 与纯文字决策链路测试 |
| `tests/frontend_circuit_simulator.test.cjs` | 浏览器模拟器核心 Node 测试 |
| `E:\emb_agent_new\main\block_i2c.c` | 固件槽位 I²C 扫描与积木识别 |
| `E:\emb_agent_new\main\board_snapshot.c` | 固件 16 槽位 / 64 端口快照与连线判定 |

## 3. 已完成能力

### 前端电路模拟器与快照协议

- 以实物风格 4×4 面板呈现 16 个槽位和 64 个物理端口，支持 9 类与固件 EEPROM 定义一致的积木：输入、输出、非、与、或、与非、或非、异或、同或。
- 支持拖放、替换、移除积木和端口点击连线；每次拓扑变更都会递增 `topology_revision`，同步给 HTTP 请求和已连接的 WebSocket 会话。
- 软件槽位为 `0–15`，每槽固定 4 个全局端口：`global_port = slot * 4 + local_port`。
- 空槽保留在 16 项数组中，但只能上报 `{ "slot": n, "present": false }`；不得把普通空槽上报为 `unknown`、`gate`、`component` 或携带端口字段。
- 固件也仅在 `present && id_valid` 时为端口赋角色；`unknown` 只表示读到无法识别的 EEPROM ID。

### LLM 引导与工具高亮

- 缺少输入或输出积木时不再直接走本地兜底；后端根据电路快照生成“缺积木状态”，完整调用 LLM，并按需注入儿童友好的补齐提醒。
- 缺积木提示禁止把关卡目标、信号标签等虚构为积木名称；仅能提示实际存在的输入积木、输出积木和缺少数量。
- 半加器、全加器关卡在用户询问任务或原理时，优先说明“本关要做什么”，再用十进制个位/进位类比引入二进制，不使用“奇偶”“多数信号”等抽象表述。
- 文字 HTTP 链路和语音链路均传入当前 Session 的短期上下文；文字链路仅省略 ASR/TTS。Session 关闭或后端重启后，内存历史会清空。
- “亮灯提示”工具每次只允许指导一对有效端口，且必须是输出端连接到输入端；模型给出无效方向或多个端口对时会取消错误高亮。

### Session 与异常日志

- HTTP 纯文字请求、WebSocket 语音链路均归属关卡 Session，前端日志看板可查看 `[PRE-CHECK]`、`[LLM-ERROR]`、`[FALLBACK]` 等记录。
- LLM 配置、协议、HTTP 状态和网络请求异常会以模块 `LLM`、级别 `ERROR` 写入 `[LLM-ERROR]`。
- HTTP 链路直接写入 `GLOBAL_SESSION_STORE`，避免运行时 logger 级别过滤造成异常日志丢失。
- `POST /api/test/sessions/start` 可正确解析 JSON 请求体；“进入关卡”会创建活动 Session。

### 配置持久化（本次更新）

- `Settings` 会从项目根目录 `.env` 加载以 `TUCO_` 开头的配置；前端配置页经 `PUT /api/config` 更新后，默认也会写回该 `.env`，所以重启无需重复填写。
- 先前问题的根因是测试通过 `RuntimeConfigStore.update()` 和 `/api/config` 写入了工作区真实 `.env`，用测试占位参数覆盖了本地配置。
- `RuntimeConfigStore` 现支持注入 `env_path`，`create_app()` 支持 `config_env_path`；测试统一写入 pytest 临时 `.env`，不再触碰项目根目录真实配置。
- 如果旧测试已覆盖过本机的真实 API Key，需要在前端配置页或 `E:\3.6bench\.env` 重新填入一次有效值；之后重启和测试均会保留该配置。

## 4. 已知运行注意事项

1. **LLM 配置：** 请求返回 `502` / `503` 时，先检查前端配置页或 `.env` 的 Base URL、模型和 API Key。配置字段是 `TUCO_LLM_BASE_URL`、`TUCO_LLM_MODEL`、`TUCO_LLM_API_KEY`；不要写入密钥值到 Git、日志或交接文档。
2. **服务重载：** 使用 `--reload` 时如果页面仍表现为旧代码，应停止遗留 Uvicorn worker 后再启动，不要只依赖浏览器刷新。
3. **静态缓存：** 修改 `circuit-simulator.js` 时，必须同步递增 `index.html` 中脚本的版本参数，避免浏览器继续使用旧缓存。
4. **Session 存储：** 当前仅在进程内保存；重启后端会清除日志和会话记忆，这是预期行为。
5. **实机联调：** 设备入口为 `ws://<本机局域网 IP>:8000/ws/device`；必须保持固件和后端的空槽 `present:false` 语义一致。

## 5. 验证命令

在 `E:\3.6bench` 执行：

```powershell
uv run ruff check .
uv run pytest -q
node --test tests/frontend_circuit_simulator.test.cjs
git diff --check
```

本次提交前已验证：

- Python：`58 passed`。
- Ruff：通过。
- `git diff --check`：通过。
- Windows 上 pytest 退出时可能打印临时目录清理的 `PermissionError`；只要命令退出码为 `0`，不影响测试结果。

## 6. 本地启动

```powershell
uv run uvicorn tuco_ai_backend.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 `http://127.0.0.1:8000` 后：

1. 如有必要，在配置页填入一次有效 LLM / 火山配置；配置会保存至 `.env`。
2. 选择关卡并点击“进入关卡”，创建活动 Session。
3. 从素材栏拖入输入、输出或逻辑门；点击两个端口创建导线。
4. 查看只读 JSON 快照，确认空槽只有 `present:false`。
5. 使用“请求模型”验证文字链路；连接 WebSocket 后验证完整语音链路和实时快照同步。
6. 在 Session 看板中检查模型、预检和异常日志。

## 7. 后续建议顺序

1. 确认 `E:\3.6bench\.env` 中为实际可用配置；不要运行旧版本测试或手工脚本覆盖该文件。
2. 分别用有效配置验证一次 HTTP 文字决策、一次 WebSocket 语音链路，以及一次单对端口亮灯提示。
3. 在 `E:\emb_agent_new\main` 做实机扫描与端口映射复核；不要把空槽映射为 `unknown`。
4. 若继续优化儿童引导，优先为 `build_missing_components_instruction()` 和 `build_binary_adder_instruction()` 增加稳定的提示词行为测试。
5. 准备推送时先运行 `git log origin/main..HEAD`；当前应推送分支为 `feature/improve-llm-response-quality`，不是直接推到 `main`。
