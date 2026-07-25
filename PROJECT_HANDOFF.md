# 图灵号 AI 后端项目交接文档

**更新时间：** 2026-07-25
**项目：** TUCO AI Backend（图灵号 AI 电路语音助手、协议测试台与浏览器电路模拟器）
**工作区：** `E:\3.6bench`

---

## 1. 当前状态

- 主分支：`main`。
- 本次交接提交完成后，本地预计领先 `origin/main` 17 个提交；未执行推送。
- 后端与前端测试台均由 FastAPI 静态服务提供；前端无构建步骤。
- 真实固件工作区位于 `E:\emb_agent\main`；远程仓库地址为 `https://github.com/ziyan2006/AI_Firmware`。
- 根目录 `.playwright-cli/` 是浏览器调试临时产物，已加入 `.gitignore`；由于当前平台删除策略限制，目录可能仍留在本机，但不会进入 Git。

## 2. 目录与职责

| 路径 | 职责 |
| --- | --- |
| `src/tuco_ai_backend/main.py` | FastAPI 路由、HTTP 决策端点、Session API、静态前端服务 |
| `src/tuco_ai_backend/device_ws.py` | 设备 WebSocket 协议、语音链路 Trace 转发 |
| `src/tuco_ai_backend/session_store.py` | 内存型 Session 生命周期、日志分类与详情查询 |
| `src/tuco_ai_backend/providers/openai_compatible.py` | OpenAI 兼容 LLM、预检兜底、快照上下文构建 |
| `src/tuco_ai_backend/frontend/circuit-simulator.js` | 16 槽位、9 类积木、64 端口、连线判定和快照序列化 |
| `src/tuco_ai_backend/frontend/app.js` | 测试台交互、模拟器桥接、HTTP/WS 请求、Session 看板 |
| `src/tuco_ai_backend/frontend/index.html` / `styles.css` | 测试台页面与 ABS 风格模拟器样式 |
| `tests/frontend_circuit_simulator.test.cjs` | 浏览器模拟器核心的 Node 测试 |
| `E:\emb_agent\main\block_i2c.c` | 固件槽位 I²C 扫描与积木识别 |
| `E:\emb_agent\main\board_snapshot.c` | 固件 16 槽位 / 64 端口快照与连线判定 |

## 3. 已完成能力

### 前端电路模拟器

- 以实物风格 4×4 面板呈现 16 个槽位与 64 个物理端口。
- 支持 9 类与固件 EEPROM 定义一致的积木：输入、输出、非、与、或、与非、或非、异或、同或。
- 支持拖放、替换、移除积木；端口点击连线；有效、方向错误、重复端口和未使用端口的不同判定与显示。
- 每次拓扑变更自动更新 `topology_revision`，并同步给 HTTP 决策请求和已连接的 WebSocket 会话。
- JSON 快照文本框是模拟器状态的只读视图，避免手工编辑导致上报数据分叉。

### 固件一致的快照语义

- 软件槽位 `0–15`，每槽固定 4 个全局端口：`global_port = slot * 4 + local_port`。
- 空槽仍保留在 16 项数组中，但只能上报 `{ "slot": n, "present": false }`；不得上报 `unknown`、`gate`、`component` 或端口字段。
- 固件也仅在 `present && id_valid` 时为端口赋角色；空槽端口保持 `BOARD_PORT_UNUSED`。
- `unknown` 只表示真实硬件读到未识别 EEPROM ID，不代表普通空槽。

### Session 与异常日志

- HTTP 纯文字请求、WebSocket 语音流水均归属关卡 Session，并可在前端日志看板查看。
- LLM 配置、协议、HTTP 状态及网络请求异常会写入 `[LLM-ERROR]`，模块为 `LLM`、级别为 `ERROR`。
- 为避免运行时 logger 级别过滤导致 Session 漏记，HTTP LLM 异常直接写入 `GLOBAL_SESSION_STORE`。
- `POST /api/test/sessions/start` 已修复为正确解析 JSON 请求体；前端“进入关卡”可正常创建活动 Session。
- Session 存储只在进程内；后端重启会清空历史记录。

## 4. 已知运行注意事项

1. **LLM 配置**：请求返回 `502`/`503` 时，先检查前端配置或 `.env` 中的 Base URL、模型和 API Key。不要在文档或提交中写入密钥。
2. **服务重载**：使用 `--reload` 时若页面行为仍像旧代码，应完全停止遗留 Uvicorn worker 后再启动；不要只依赖浏览器刷新。
3. **静态缓存**：`circuit-simulator.js` 当前引用版本为 `20260725_v7`。后续修改该脚本时，必须同步递增 `index.html` 中的版本参数。
4. **实机联调**：设备协议入口为 `ws://<本机局域网 IP>:8000/ws/device`。固件目前直接维护槽位和端口快照；与后端联调时必须保持空槽 `present:false` 语义。

## 5. 验证命令

在 `E:\3.6bench` 执行：

```powershell
uv run ruff check .
uv run pytest
node --test tests/frontend_circuit_simulator.test.cjs
```

本次交接前已验证：

- Ruff 通过。
- Python：`45 passed`。
- Node 模拟器：`5 passed`。
- Windows 上 pytest 退出时可能打印临时目录清理的 `PermissionError`；在本次验证中退出码为 `0`，不影响测试结果。

## 6. 本地启动

```powershell
uv run uvicorn tuco_ai_backend.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 `http://127.0.0.1:8000` 后：

1. 选择关卡，点击“进入关卡”。
2. 从素材栏拖入输入、输出或逻辑门；点击两个端口创建导线。
3. 查看只读 JSON 快照，确认空槽只有 `present:false`。
4. 使用“请求模型”验证 HTTP 路径；连接 WebSocket 后验证实时快照同步。
5. 在 Session 看板中检查 `[PRE-CHECK]`、`[LLM-ERROR]`、`[FALLBACK]` 等日志。

## 7. 交接后的建议顺序

1. 启动一个干净的 `8000` 后端实例，确认没有遗留 worker 占端口。
2. 用有效 LLM 配置做一次 HTTP 决策和一次 WebSocket 语音链路检查。
3. 根据需要在 `E:\emb_agent\main` 做实机扫描与端口映射复核；不要把空槽映射为 `unknown`。
4. 审阅 `docs/superpowers/` 下的设计与计划记录；独立样式稿位于 `docs/prototypes/port-board-preview.html`。
5. 需要同步远程时，先检查 `git log origin/main..HEAD`，再决定是否执行 `git push origin main`。
