# 图灵号 AI 后端项目交接文档 (Project Handoff Document)

**交接日期**：2026-07-25  
**目标对象**：接棒 AI Agent / 协同开发工程师  
**项目名称**：TUCO AI Backend (图灵号 AI 电路语音助手后端及协议测试台)  

---

## 📖 1. 项目背景与技术架构 (Project Overview)

### 1.1 业务背景
本项目为儿童硬件逻辑电路教具“图灵号”的 AI 后端服务。
硬件设备通过传感器扫描当前电路板上的积木槽位与连线，生成 `CircuitSnapshot` 快照上报。AI 后端负责分析电路连接是否满足当前关卡目标、回答玩家问题、指示端口亮灯（`highlight_ports` 工具调用），并支持通过火山引擎 ASR/TTS 进行实时语音对讲。

### 1.2 技术栈 (Tech Stack)
- **后端框架**：Python 3.11+ / FastAPI / Uvicorn (以 `--reload` 实时重载模式运行)
- **包管理与依赖**：`uv` (支持 `uv run pytest` / `uv run ruff check .`)
- **数据校验**：Pydantic v2 (设为宽容校验模式 `extra="allow"`)
- **AI / 语音服务**：
  - OpenAI 兼容格式大模型接口 (OpenAI / 火山引擎方舟大模型)
  - 火山引擎 Volcengine ASR (语音识别) & TTS (语音合成)
- **前端测试台**：Vanilla HTML5 + Modern CSS + JavaScript (位于 `src/tuco_ai_backend/frontend/`)

### 1.3 本地项目绝对路径指南 (Local File Paths)
- **AI 后端本地项目根目录**：`e:\3.6bench`
- **嵌入式/固件端本地代码目录**：`e:\ai_emb` / `e:\emb` / `e:\boardmode` （存放 ESP32 C 语言固件源码及关卡文件 `campaign_content.c`）
- **后端核心代码路径**：`e:\3.6bench\src\tuco_ai_backend\`
  - `main.py` - FastAPI 路由主入口及 REST 服务
  - `device_ws.py` - 设备 WebSocket 长连协议与 Trace 日志拦截器
  - `voice_pipeline.py` - ASR ➔ LLM ➔ TTS 语音全流程管道
  - `session_store.py` - 关卡 Session 全生命周期日志存储与管理器
  - `models.py` - Pydantic 数据模型 (`CircuitSnapshot`, `LevelContext` 等)
  - `providers/` - 大模型 (`openai_compatible.py`)、ASR (`volcengine_asr.py`) & TTS (`volcengine_tts.py`) 客户端
- **前端测试台路径**：`e:\3.6bench\src\tuco_ai_backend\frontend\`
  - `index.html` - 前端测试台 HTML 结构与 Session 日志看板 DOM
  - `app.js` - 测试台交互逻辑、WebSocket 连接、Session 卡片渲染及彩色日志解析
  - `styles.css` - 前端主题样式、卡片 Grid 布局及弹窗模态框样式
- **单元测试路径**：`e:\3.6bench\tests\`
- **本地配置文件**：
  - `e:\3.6bench\.env` - 本地环境变量 (大模型 BaseURL、APIKey、火山引擎秘钥)
  - `e:\3.6bench\pyproject.toml` & `uv.lock` - 项目依赖配置
- **项目交接文档**：`e:\3.6bench\PROJECT_HANDOFF.md`

---

## 🔗 2. 远程仓库信息 (Git Remotes)

1. **AI 后端仓库 (Primary Remote)**：
   - **名称**：`origin`
   - **地址**：`https://github.com/ziyan2006/AI_Backend`
   - **作用**：存放 FastAPI 后端服务、WebSocket 协议实现、LLM 决策引擎及前端测试台网页。

2. **硬件/固件仓库 (Hardware / Firmware Remote)**：
   - **名称**：`firmware` / `embedded` (硬件端仓库)
   - **地址**：`https://github.com/ziyan2006/AI_Firmware` （或对应的 ESP32 / C 语言硬件固件仓库）
   - **作用**：存放 ESP32 板卡硬件 C 语言代码、传感器驱动及嵌入式关卡定义。
   - **核心关联**：关卡电路数据定义源文件为固件库中的 `campaign_content.c`（已按此源码全数补全后端 17 个正常数字电路关卡）。

---

## 📌 3. 当前 Working Tree & Git 状态 (Git Status)

- **当前分支**：`main`
- **工作区状态**：`working tree clean` (所有代码改动与新增功能均已提交)
- **提交差距**：领先 `origin/main` 11 个 Commit（可随时执行 `git push origin main` 向上推送）。
- **最新 5 条 Commit 记录**：
  - `966951e` (`feat: implement Session Logs Dashboard with cards grid and modal inspector`)
  - `2f1837c` (`feat: implement full-trace logging system across backend and frontend`)
  - `292a22f` (`fix: strip UI-only fields before sending snapshot via WebSocket or HTTP`)
  - `0163c8b` (`fix: change LevelContext config extra to allow to bypass pydantic validation error`)
  - `823eb51` (`fix: bypass browser cache by adding version query params in index.html`)

---

## ✅ 4. 已完成的核心 Feature 与关键修复 (Completed Features)

### 4.1 关卡补全与真值表前端可视化
- 移除了 REWARD 特殊战斗关卡，完整收录 17 个标准电路逻辑设计关卡（101 ~ 602）。
- 前端测试台支持在下拉框切换关卡时，动态渲染对应的 `[Inputs] ➔ [Expected Output]` 目标真值表。

### 4.2 强缓存旁路与 Pydantic 校验修复
- 给前端 `index.html` 引入的 CSS 和 JS 链接挂载了版本参数 `?v=20260725_v5`，穿透浏览器强缓存。
- 修改 `LevelContext` 的 Pydantic 校验为 `extra="allow"`，同时在前端出口层自动剥离 `title` 与 `truth_table`，彻底消除了 `extra_forbidden` 连接中断错误。

### 4.3 全链路 Trace 日志系统 (Trace System)
- 为每次交互生成唯一 `trace_id`（如 `tr_1784968128_a3b1`），并在 `[ASR]`、`[PRE-CHECK]`、`[LLM]`、`[FALLBACK]` 和 `[TTS]` 节点中结构化记录。

### 4.4 关卡 Session 日志查看系统 (Session Dashboard)
- 定义了从【进入关卡】到【退出关卡】的完整生命周期 Session。
- 在前端呈现 Session 小方格卡片 (Grid)，并提供状态徽章（🟢 正常 / 🟡 含拦截 / 🔴 含兜底）。
- 点击卡片可弹出模态框 (Inspector Modal)，查看该 Session 下所有纯文字 HTTP 请求与 WebSocket 语音模式的全流水日志。

---

## 🧪 5. 测试与运行验证 (Testing & Verification)

### 5.1 自动化单元测试
在项目根目录运行以下命令：
```bash
uv run ruff check .
uv run pytest
```
**结果**：`42 passed in 1.00s`，42 项单元测试 **100% 全部通过**，Linter 零警告。

### 5.2 本地运行 Uvicorn 服务
```bash
uv run uvicorn tuco_ai_backend.main:app --host 0.0.0.0 --port 8000 --reload
```
服务成功绑定在 `http://127.0.0.1:8000`，且支持代码修改秒级自动重载。

---

## 🚀 6. 给后续 Agent 的接棒建议 (Next Steps for Next Agent)

1. **推送 Commit 到远程**：
   运行 `git push origin main` 将本地领先的 11 个提交推送到 GitHub 远程仓库。
2. **硬件/实机对讲联调**：
   通过物理 ESP32 设备连接 `ws://<local_ip>:8000/ws/device` 测试真实语音与端口高亮交互。
3. **Session 日志观察**：
   在前端 `http://127.0.0.1:8000` 体验【进入关卡】与【退出关卡】，点击小方块校验日志流水。
