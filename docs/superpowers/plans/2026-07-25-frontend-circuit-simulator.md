# 前端电路测试台模拟器实现计划

> 实施完成于 2026-07-25：核心模拟器、测试台集成、快照同步与 Node 回归测试均已落地；空槽序列化已更新为固件一致的 `present: false` 语义。

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把现有前端测试台的抽象端口网格改造成按固件规则驱动的可拖放、可连线电路模拟器，并在拓扑变更时更新 HTTP 与 WebSocket 使用的电路快照。

**架构：** 新建一个无依赖的浏览器/Node 兼容模拟器核心，集中维护 16 个软件槽位、物理端口、导线和快照序列化。该核心以 `window.TucoCircuitSimulator` 供现有 `app.js` 挂载和调用，以 CommonJS 导出供 `node --test` 覆盖映射、连线分类与快照契约。页面采用 DOM 生成的 ABS 面板与 SVG 连线层，`app.js` 继续负责现有 HTTP、WebSocket、日志和模型端口高亮。

**技术栈：** 原生 HTML/CSS/JavaScript、SVG、Node 内置测试运行器、FastAPI 既有静态文件服务。

---

## 文件结构

- 创建：`src/tuco_ai_backend/frontend/circuit-simulator.js` — 9 类积木常量、实物槽位映射、端口角色、导线校验、快照序列化和 DOM/SVG 渲染。
- 创建：`tests/frontend_circuit_simulator.test.cjs` — 用 Node 内置测试运行器验证纯状态/快照逻辑，无需浏览器或第三方依赖。
- 修改：`src/tuco_ai_backend/frontend/index.html` — 加载模拟器脚本，替换旧端口网格 DOM，保留 JSON、HTTP、WebSocket 和 Session 面板。
- 修改：`src/tuco_ai_backend/frontend/styles.css` — 新增素材栏、4×4 ABS 面板、端口状态、SVG 导线和窄屏布局样式，删除只服务于旧方格网格的样式。
- 修改：`src/tuco_ai_backend/frontend/app.js` — 挂载模拟器；以模拟器快照替代示例 JSON；把模型高亮、关卡变更和 WebSocket 快照发送接入模拟器。

### 任务 1：建立可测试的固件映射与快照核心

**文件：**
- 创建：`tests/frontend_circuit_simulator.test.cjs`
- 创建：`src/tuco_ai_backend/frontend/circuit-simulator.js`

- [ ] **步骤 1：先写映射和积木清单的失败测试**

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const simulator = require("../src/tuco_ai_backend/frontend/circuit-simulator.js");

test("exports all nine firmware blocks and the 4x4 physical slot mapping", () => {
  assert.deepEqual(
    simulator.BRICKS.map((brick) => brick.component),
    ["input", "output", "not_gate", "and_gate", "or_gate", "nand_gate", "nor_gate", "xor_gate", "xnor_gate"],
  );
  assert.deepEqual(simulator.SLOT_LAYOUT[0], { slot: 6, board: 4, cascadeSlot: 7 });
  assert.deepEqual(simulator.SLOT_LAYOUT[15], { slot: 15, board: 8, cascadeSlot: 16 });
  assert.equal(simulator.getGlobalPort(0, 0), 0);
  assert.equal(simulator.getGlobalPort(15, 3), 63);
});
```

- [ ] **步骤 2：运行测试，确认它因缺少模块而失败**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：失败，报错找不到 `circuit-simulator.js`。

- [ ] **步骤 3：实现积木、槽位和端口映射导出**

```javascript
const BRICKS = [
  { gate: 0, component: "input", displayName: "输入积木" },
  { gate: 1, component: "output", displayName: "输出积木" },
  { gate: 2, component: "not_gate", displayName: "非门积木" },
  { gate: 3, component: "and_gate", displayName: "与门积木" },
  { gate: 4, component: "or_gate", displayName: "或门积木" },
  { gate: 5, component: "nand_gate", displayName: "与非门积木" },
  { gate: 6, component: "nor_gate", displayName: "或非门积木" },
  { gate: 7, component: "xor_gate", displayName: "异或门积木" },
  { gate: 8, component: "xnor_gate", displayName: "同或门积木" },
];

const SLOT_LAYOUT = [
  { slot: 6, board: 4, cascadeSlot: 7 }, { slot: 4, board: 3, cascadeSlot: 5 },
  { slot: 2, board: 2, cascadeSlot: 3 }, { slot: 0, board: 1, cascadeSlot: 1 },
  { slot: 7, board: 4, cascadeSlot: 8 }, { slot: 5, board: 3, cascadeSlot: 6 },
  { slot: 3, board: 2, cascadeSlot: 4 }, { slot: 1, board: 1, cascadeSlot: 2 },
  { slot: 8, board: 5, cascadeSlot: 9 }, { slot: 10, board: 6, cascadeSlot: 11 },
  { slot: 12, board: 7, cascadeSlot: 13 }, { slot: 14, board: 8, cascadeSlot: 15 },
  { slot: 9, board: 5, cascadeSlot: 10 }, { slot: 11, board: 6, cascadeSlot: 12 },
  { slot: 13, board: 7, cascadeSlot: 14 }, { slot: 15, board: 8, cascadeSlot: 16 },
];

function getGlobalPort(slot, localPort) {
  return slot * 4 + localPort;
}
```

封装到 IIFE；在浏览器设置 `window.TucoCircuitSimulator`，在 Node 设置 `module.exports`。

- [ ] **步骤 4：重新运行映射测试**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：`pass 1`，且没有失败项目。

### 任务 2：以 TDD 实现端口角色、连线分类和快照序列化

**文件：**
- 修改：`tests/frontend_circuit_simulator.test.cjs`
- 修改：`src/tuco_ai_backend/frontend/circuit-simulator.js`

- [ ] **步骤 1：先写端口角色、连线结果和快照的失败测试**

```javascript
test("classifies valid, direction-error, duplicate, and unused-port links", () => {
  const state = simulator.createState();
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.placeBrick(state, 2, "and_gate");

  assert.equal(simulator.classifyLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 }).kind, "valid");
  assert.equal(simulator.classifyLink(state, { slot: 1, localPort: 0 }, { slot: 2, localPort: 3 }).kind, "direction");
  assert.equal(simulator.classifyLink(state, { slot: 2, localPort: 2 }, { slot: 1, localPort: 3 }).kind, "unused");

  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  assert.equal(simulator.classifyLink(state, { slot: 0, localPort: 0 }, { slot: 2, localPort: 3 }).kind, "multiple");
});

test("serializes the browser topology as the firmware snapshot shape", () => {
  const state = simulator.createState();
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  const snapshot = simulator.buildSnapshot(state, { level_id: 101, short_goal: "直连" });

  assert.equal(snapshot.schema_version, 2);
  assert.equal(snapshot.slots.length, 16);
  assert.deepEqual(snapshot.valid_links, [{ from_slot: 0, to_slot: 1 }]);
  assert.deepEqual(snapshot.invalid_links, []);
  assert.deepEqual(snapshot.scan, { ir_scans: 0, i2c_scans: 0 });
});
```

- [ ] **步骤 2：运行测试，确认缺少状态 API 时失败**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：失败，报错 `createState is not a function`。

- [ ] **步骤 3：实现纯状态 API 和固件同构序列化**

```javascript
function portRole(component, visualSide) {
  if (component === "input") return "output";
  if (component === "output") return visualSide === "left" ? "input" : "unused";
  if (component === "not_gate") return visualSide === "left" ? "input" : visualSide === "right" ? "output" : "unused";
  return visualSide === "right" ? "output" : (visualSide === "top" || visualSide === "bottom") ? "input" : "unused";
}

function classifyLink(state, first, second) {
  if (state.links.some((link) => endpointEquals(link.first, first) || endpointEquals(link.second, first) || endpointEquals(link.first, second) || endpointEquals(link.second, second))) return { kind: "multiple", error: 4 };
  const firstRole = getEndpointRole(state, first);
  const secondRole = getEndpointRole(state, second);
  if (firstRole === "unused" || secondRole === "unused") return { kind: "unused" };
  if (firstRole === "output" && secondRole === "input") return { kind: "valid", from: first, to: second };
  if (firstRole === "input" && secondRole === "output") return { kind: "valid", from: second, to: first };
  return { kind: "direction", error: 3 };
}
```

`buildSnapshot` 必须包含 16 个含 `slot`、`gate`、`component`、`display_name`、`present` 的槽位，且仅将 `kind === "valid"` 的连线输出到 `valid_links`；`direction` 与 `multiple` 仅以 `{ error }` 输出到 `invalid_links`；`unused` 不进入上传数据。

- [ ] **步骤 4：重新运行核心测试**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：`pass 3`，所有断言通过。

### 任务 3：接入模拟器面板 DOM、拖放和 SVG 导线

**文件：**
- 修改：`src/tuco_ai_backend/frontend/index.html:77-96`
- 修改：`src/tuco_ai_backend/frontend/styles.css:48-90`
- 修改：`src/tuco_ai_backend/frontend/circuit-simulator.js`

- [ ] **步骤 1：写出 DOM 挂载所需的可验证断言**

在 `tests/frontend_circuit_simulator.test.cjs` 增加对 `getSlotDisplayPorts(0)` 与 `getSlotDisplayPorts(1)` 的断言：

```javascript
test("maps upper and lower physical sockets to the visible P0–P7 labels", () => {
  assert.deepEqual(simulator.getSlotDisplayPorts(0), [
    { side: "top", label: "P3", localPort: 3 }, { side: "right", label: "P0", localPort: 0 },
    { side: "bottom", label: "P1", localPort: 1 }, { side: "left", label: "P2", localPort: 2 },
  ]);
  assert.deepEqual(simulator.getSlotDisplayPorts(1), [
    { side: "top", label: "P5", localPort: 1 }, { side: "right", label: "P6", localPort: 2 },
    { side: "bottom", label: "P7", localPort: 3 }, { side: "left", label: "P4", localPort: 0 },
  ]);
});
```

- [ ] **步骤 2：运行测试，确认端口显示映射尚未实现**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：失败，报错 `getSlotDisplayPorts is not a function`。

- [ ] **步骤 3：替换旧端口网格的 HTML 结构并加载新脚本**

将 `#port-grid` 替换为以下结构，并在 `app.js` 前加载模拟器脚本：

```html
<aside class="panel ports-panel simulator-panel">
  <div class="section-heading compact">
    <div><span class="step">03</span><h2>电路模拟器</h2></div>
    <div class="actions">
      <button id="simulator-clear" class="button secondary" type="button">清空面板</button>
    </div>
  </div>
  <p class="hint">拖放积木到物理槽位；依次点击两个端口创建导线。</p>
  <div id="circuit-simulator" class="circuit-simulator" aria-label="16 槽位电路模拟器"></div>
  <div id="tool-summary" class="tool-summary">尚未收到 highlight_ports</div>
</aside>
<script src="/static/circuit-simulator.js?v=20260725_v6" defer></script>
<script src="/static/app.js?v=20260725_v6" defer></script>
```

- [ ] **步骤 4：实现面板渲染和交互事件**

`mount(container, options)` 负责生成素材栏、4×4 槽位、端口 button 和与面板同尺寸的 SVG 层。拖放事件调用 `placeBrick`；端口点击按“首次选择 / 第二次建线”状态调用 `addLink`；每一次状态变更调用 `options.onChange(snapshot)`。

SVG 线必须根据端口元素的 `getBoundingClientRect()` 计算端点，并在 `ResizeObserver`、窗口 `resize` 和状态变更时重绘。导线元素带 `data-link-id`，点击后调用 `removeLink`。样式类固定为：`wire-valid`、`wire-invalid`、`wire-ignored`、`port-role-input`、`port-role-output`、`port-role-unused` 与 `port-highlighted`。

- [ ] **步骤 5：增加 ABS 面板与响应式样式**

新增 `.simulator-shell`、`.brick-palette`、`.simulator-board`、`.simulator-slot`、`.simulator-port`、`.simulator-wires` 和对应状态样式。面板在大屏使用 4 列网格；小于 `980px` 时素材栏改为横向滚动条，面板保持 4 列并允许其容器横向滚动，避免改变实物行列顺序。

- [ ] **步骤 6：重新运行纯映射测试**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：`pass 4`，包括上/右/下/左的 `P0–P7` 显示映射断言。

### 任务 4：把现有 HTTP、模型高亮和 WebSocket 快照改接到模拟器

**文件：**
- 修改：`src/tuco_ai_backend/frontend/app.js:19-96`
- 修改：`src/tuco_ai_backend/frontend/app.js:138-167`
- 修改：`src/tuco_ai_backend/frontend/app.js:197-240`
- 修改：`src/tuco_ai_backend/frontend/app.js:446-460`
- 修改：`src/tuco_ai_backend/frontend/app.js:655-670`

- [ ] **步骤 1：写出自动发送约束的失败测试**

在 `tests/frontend_circuit_simulator.test.cjs` 增加纯回调测试：

```javascript
test("emits a newer snapshot after each topology mutation", () => {
  const state = simulator.createState();
  const revisions = [];
  simulator.subscribe(state, (snapshot) => revisions.push(snapshot.topology_revision));
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  assert.deepEqual(revisions, [1, 2, 3]);
});
```

- [ ] **步骤 2：运行测试，确认变更通知尚未实现**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：失败，报错 `subscribe is not a function`。

- [ ] **步骤 3：实现订阅和更新 `app.js` 的模拟器桥接**

在 `app.js` 维护 `let circuitSimulator;`，初始化时使用：

```javascript
circuitSimulator = window.TucoCircuitSimulator.mount(elements.circuitSimulator, {
  level: currentLevelPayload(),
  onChange(snapshot) {
    elements.snapshot.value = JSON.stringify(snapshot, null, 2);
    if (websocket && websocket.readyState === WebSocket.OPEN && voiceReady) {
      sendJson({ type: "circuit.snapshot", session_id: voiceSessionId, ...snapshot });
    }
  },
});
```

`updateLevelInfo()` 改为调用 `circuitSimulator.setLevel(currentLevelPayload())`，不得再从文本框解析或写入样例快照。`runDecision()` 仍从 `elements.snapshot.value` 读取，以维持 HTTP 请求路径。`handleProtocolMessage()` 收到 `session.ready` 时以当前模拟器快照发送初始 `circuit.snapshot`。`highlightPorts()` 改为调用 `circuitSimulator.highlightPorts(toolCall.arguments.ports, toolCall.arguments.pattern)`。

- [ ] **步骤 4：让 JSON 成为同步只读视图并移除旧网格函数**

在 `index.html` 中给 `#snapshot` 增加 `readonly`，在 `app.js` 删除 `sampleSnapshot`、`buildPortGrid()`、`clearPorts()` 和依赖 `elements.portGrid` 的代码。将 `elements` 中的 `portGrid` 改为 `circuitSimulator` 与 `simulatorClear`。

- [ ] **步骤 5：重新运行全部 Node 核心测试**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：`pass 5`，包含拓扑变更 revision 通知断言。

### 任务 5：运行后端回归与浏览器验收

**文件：**
- 测试：`tests/frontend_circuit_simulator.test.cjs`
- 测试：`tests/test_api.py`
- 测试：`tests/test_device_ws.py`
- 测试：`tests/test_llm_service.py`

- [ ] **步骤 1：运行前端纯逻辑测试**

运行：`node --test tests/frontend_circuit_simulator.test.cjs`

预期：所有子测试通过，测试项至少覆盖 9 种积木、16 槽位映射、端口方向、正常/红色/灰色连线、快照字段和 revision。

- [ ] **步骤 2：运行既有 Python 测试与静态检查**

运行：`uv run ruff check . && uv run pytest`

预期：Ruff 无错误，pytest 全部通过；不得通过修改后端校验来规避前端问题。

- [ ] **步骤 3：启动现有服务并执行浏览器验收**

运行：`uv run uvicorn tuco_ai_backend.main:app --host 127.0.0.1 --port 8000 --reload`

在浏览器执行：

1. 打开 `http://127.0.0.1:8000`，确认没有控制台错误。
2. 将输入积木放入显示为 `1-1` 的槽位、输出积木放入 `1-2`，连接输入的右侧 `P0` 到输出左侧 `P4`；确认快照含 `valid_links: [{"from_slot":0,"to_slot":1}]`。
3. 再尝试输入端口接输入端口，确认出现红线与 `invalid_links: [{"error":3}]`。
4. 对已占用端口再连线，确认出现红线与 `invalid_links: [{"error":4}]`。
5. 在未使用端口之间连线，确认灰线可见且不进入 `invalid_links`。
6. 点击“请求模型”，确认请求使用当前 JSON；建立 WebSocket 后改变任意拓扑，确认协议日志立刻发送新的 `circuit.snapshot`，其 `topology_revision` 与 JSON 相同。
7. 触发或模拟 `highlight_ports`，确认对应的 ABS 面板端口高亮仍正确。

- [ ] **步骤 4：检查变更边界**

运行：`git diff --check && git diff -- src/tuco_ai_backend/frontend/index.html src/tuco_ai_backend/frontend/styles.css src/tuco_ai_backend/frontend/app.js src/tuco_ai_backend/frontend/circuit-simulator.js tests/frontend_circuit_simulator.test.cjs`

预期：无空白错误；变更仅覆盖模拟器、其测试和必要的静态资源版本参数，不包含无关后端文件。

## 交付约束

- 不创建提交或分支，除非用户随后明确要求。
- 不修改固件源码；其路径只用于映射与协议核验。
- 若浏览器验证发现连线协议与规格不一致，先补充失败测试，再按失败症状调整模拟器核心。
