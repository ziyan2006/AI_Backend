# 积木角色标签与关卡提示实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变现有固件和后端真值表判题方式的前提下，为二进制及多角色关卡的输入输出积木 OLED 自动显示角色标签，并把同一标签写入电路快照，优化后端儿童引导文案。

**架构：** 固件根据当前关卡、已识别积木和槽位编号从小到大计算标签；OLED 显示标签，快照携带 `role_label`。后端对 `role_label` 做可选字段解析，优先使用固件标签指导模型，旧版快照继续按原协议工作。

**技术栈：** ESP-IDF C/CJSON/SSD1315 帧缓冲；Python/Pydantic/FastAPI；pytest。

---

## 关卡范围

- `401`：输入 `A/B`，输出 `个`
- `402`：输入 `A/B`，输出 `进`
- `403`：输入 `A/B`，输出 `个/进`
- `501`：输入 `A/B/C`，输出 `个`
- `502`：输入 `A/B/C`，输出 `进`
- `503`：输入 `A/B/C`，输出 `个/进`
- `601`：输入 `A/B/选`，输出 `Y`
- `602`：输入 `A/B`，输出 `0/1/2/3`

早期关卡 `101-302` 不显示角色标签，保持现有 OLED 视觉和儿童认知负担。

## 数据契约

- 紧凑槽位记录从旧格式 `[slot,row,column,state,gate,ports]` 扩展为 `[slot,row,column,state,gate,ports,role_label]`。
- `role_label` 是可选字符串；空槽和未完成角色分配使用 `null` 或空字符串。
- 后端 Pydantic 同时接受 6 字段旧记录和 7 字段新记录。
- 角色标签只描述输入输出角色，不改变 `gate`、端口角色、连线或真值表。

## 任务 1：固件角色标签纯函数

**文件：**
- 创建：`E:/emb_agent_new/main/circuit_role_labels.h`
- 创建：`E:/emb_agent_new/main/circuit_role_labels.c`
- 修改：`E:/emb_agent_new/main/CMakeLists.txt`
- 测试：`E:/emb_agent_new/main/game_logic_self_test.c`

- [ ] 编写覆盖 8 个关卡、未放齐不显示、按槽位排序的失败测试。
- [ ] 实现固定标签表与输入输出计数完整性检查。
- [ ] 让同一函数为 OLED 和快照生成标签。
- [ ] 运行固件自测或主机可编译检查。

## 任务 2：固件 OLED 和关卡状态接入

**文件：**
- 修改：`E:/emb_agent_new/main/play_mode.h`
- 修改：`E:/emb_agent_new/main/play_mode.c`
- 修改：`E:/emb_agent_new/main/app_ui.c`
- 修改：`E:/emb_agent_new/main/ssd1315_oled.h`
- 修改：`E:/emb_agent_new/main/ssd1315_oled.c`
- 修改：`E:/emb_agent_new/main/block_i2c.c`

- [ ] 进入游玩页时记录当前关卡 ID，离开时清除。
- [ ] OLED API 接受可选角色标签并在图案空白区显示。
- [ ] 槽位扫描变化后重新计算角色标签，标签不完整时保持原图案。
- [ ] 保留成功页和编程器 OLED 行为。

## 任务 3：固件快照携带角色标签

**文件：**
- 修改：`E:/emb_agent_new/main/board_snapshot.h`
- 修改：`E:/emb_agent_new/main/board_snapshot.c`
- 修改：`E:/emb_agent_new/main/block_i2c.c`
- 修改：`E:/emb_agent_new/main/tuco_agent.c`
- 修改：`E:/emb_agent_new/main/game_logic_self_test.c`

- [ ] 给槽位身份增加固定长度的 `role_label`。
- [ ] 发布槽位快照时同步标签。
- [ ] 生成 v2 紧凑槽位记录的第 7 个字段。
- [ ] 自测 601/602 标签与 OLED 使用结果一致。

## 任务 4：后端协议与提示词兼容

**文件：**
- 修改：`E:/3.6bench/src/tuco_ai_backend/models.py`
- 修改：`E:/3.6bench/src/tuco_ai_backend/providers/circuit_coach_v2.py`
- 修改：`E:/3.6bench/src/tuco_ai_backend/providers/openai_compatible.py`
- 修改：`E:/3.6bench/tests/test_circuit_coach_v2.py`
- 修改：`E:/3.6bench/tests/test_llm_service.py`

- [ ] Pydantic 兼容 6/7 字段槽位记录。
- [ ] 上下文优先使用快照标签，缺失时保留旧的槽位描述。
- [ ] 增加 8 个关卡的儿童化角色说明，重点优化 403/503/601/602。
- [ ] 禁止模型把标签当成积木名称或要求放置“选择器/个位/进位”积木。

## 任务 5：验证与交接

- [ ] 运行后端 `.venv/Scripts/python.exe -m pytest -q`。
- [ ] 运行 `.venv/Scripts/python.exe -m ruff check src tests`。
- [ ] 固件开启增量编译并烧录前先检查镜像生成。
- [ ] 用 401、403、503、601、602 快照验证标签字段和提示质量。
- [ ] 更新 `PROJECT_HANDOFF.md`，记录协议兼容和固件烧录注意事项。

## 自检

- 需求覆盖：8 个关卡、OLED 标签、快照字段、旧协议兼容、提示词优化、测试与交接均有对应任务。
- 范围控制：不改真值表判题，不改早期关卡，不重构无关诊断和评测代码。
- 兼容性：后端接受旧 6 字段快照，固件发送新 7 字段快照。
- 同源性：OLED 和快照都调用同一固件标签分配函数，避免显示与后端看到的标签不一致。
