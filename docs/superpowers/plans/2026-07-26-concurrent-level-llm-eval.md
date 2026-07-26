# LLM 全关卡并发评测实现计划

> **面向 AI 代理的工作者：** 在当前 feature 分支内按 TDD 内联执行，不提交或推送，除非用户明确要求。

**目标：** 提供一个无需启动 FastAPI 服务、可并发评测全部关卡文字链路并生成 JSON 与 Markdown 报告的脚本。

**架构：** 评测器直接复用 `OpenAICompatibleClient.decide()`、`DecisionRequest` 和 `CircuitSnapshot`。不同关卡作为互相隔离的异步任务并发执行，同一关卡的多个问题串行执行并维护独立短期历史。

**技术栈：** Python 3.11、`asyncio`、现有 `httpx` LLM 客户端、pytest。

---

### 任务 1：定义评测核心契约

**文件：**
- 创建：`src/tuco_ai_backend/evaluation.py`
- 测试：`tests/test_evaluation.py`

- [ ] 定义完整关卡目录和空电路快照构造函数。
- [ ] 定义逐轮、逐关和整次运行的结构化结果。
- [ ] 通过失败测试固定 16 槽位、64 端口和关卡上下文格式。

### 任务 2：实现并发与上下文隔离

**文件：**
- 创建：`src/tuco_ai_backend/evaluation.py`
- 测试：`tests/test_evaluation.py`

- [ ] 使用 `asyncio.Semaphore` 限制同时进行的关卡任务。
- [ ] 同一关卡内顺序发送问题并维护独立 `history`。
- [ ] 单关异常只记录到该轮结果，不取消其他关卡。

### 任务 3：生成可分析报告

**文件：**
- 创建：`src/tuco_ai_backend/evaluation.py`
- 测试：`tests/test_evaluation.py`

- [ ] 输出包含运行配置、耗时、回复、工具调用和错误的 JSON。
- [ ] 输出按关卡排序的 Markdown 汇总。

### 任务 4：提供命令行脚本

**文件：**
- 创建：`scripts/run_concurrent_level_llm_eval.py`
- 修改：`README.md`

- [ ] 支持并发数、重复问题、关卡筛选、环境文件和输出目录参数。
- [ ] 从 `.env` 读取后端现有 LLM 配置，不输出 API Key。
- [ ] 返回非零退出码表示存在失败轮次，同时保留完整报告。

### 任务 5：验证

- [ ] 运行 `uv run pytest -q tests/test_evaluation.py`。
- [ ] 运行 `uv run ruff check src/tuco_ai_backend scripts tests`。
- [ ] 运行 `uv run pytest -q`。
- [ ] 运行 `git diff --check`。
