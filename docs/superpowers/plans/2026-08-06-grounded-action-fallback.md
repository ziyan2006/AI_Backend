# 可靠动作降级与关卡事实引导实施计划

> 采用测试驱动方式逐项实现，每个任务先运行定向测试确认失败，再写最小实现并回归。

## 任务 1：区分连接与断线归一化

**文件：**

- 修改：`tests/test_circuit_coach_v2.py`
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`

- [ ] 新增已连接真实边上的 `disconnect` 工具应保留的失败测试。
- [ ] 新增已占用端口上的 `connect` 工具仍应拒绝的测试。
- [ ] 运行两个定向测试，确认断线场景失败。
- [ ] 按 `highlight_ports.intent` 分离最终端口校验。
- [ ] 运行 `tests/test_circuit_coach_v2.py`。

## 任务 2：让错误角色边生成拆线候选

**文件：**

- 修改：现有电路诊断测试文件。
- 修改：电路诊断实现文件。

- [ ] 构造 403 的 `0 -> 48` 错误角色边快照。
- [ ] 断言诊断结果包含该真实边的 `disconnect_edges`。
- [ ] 断言规划优先生成对应拆线候选。
- [ ] 运行定向测试确认失败。
- [ ] 在角色校验失败时记录规范化真实边。
- [ ] 运行诊断与规划相关测试。

## 任务 3：实现可靠动作降级

**文件：**

- 修改：`tests/test_circuit_coach_v2.py`
- 修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`

- [ ] 新增拆线、连线、摆放候选映射失败测试。
- [ ] 断言无工具、无占位句，并包含候选可靠事实或明确动作。
- [ ] 运行定向测试确认失败。
- [ ] 增加按候选动作和 `child_facts` 生成降级文案的纯函数。
- [ ] 候选解析失败、映射失败和最终校验失败统一走可靠降级。
- [ ] 保持候选过期和结构错误不执行工具。
- [ ] 运行 `tests/test_circuit_coach_v2.py`。

## 任务 4：提高实时事实优先级

**文件：**

- 修改：现有关卡引导测试文件。
- 修改：`src/tuco_ai_backend/providers/openai_compatible.py`
- 必要时修改：`src/tuco_ai_backend/providers/circuit_coach_v2.py`

- [ ] 新增 301、403 首次介绍先白话后术语的测试。
- [ ] 新增 601 有实时进度时禁止固定积木职责和顺序的测试。
- [ ] 运行定向测试确认失败。
- [ ] 在关卡引导构建器中加入实时事实优先约束。
- [ ] 限制专用素材在有进度时只能补充概念。
- [ ] 校正 601 对与门筛选、或门汇总的描述边界。
- [ ] 运行关卡引导测试。

## 任务 5：完整验证与评测

- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests\test_circuit_coach_v2.py -q`。
- [ ] 运行诊断和关卡引导相关测试。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest -q`。
- [ ] 运行 `.\.venv\Scripts\python.exe -m ruff check src tests`。
- [ ] 运行 `git diff --check`。
- [ ] 使用 `flexible-routing-quality` 对 301、403、502、601 运行多轮并发评测。
- [ ] 检查原始问答、路由模式、候选编号、工具映射和失败类型。
- [ ] 将验证结果和新提交写入 `PROJECT_HANDOFF.md`。
