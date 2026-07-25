const test = require("node:test");
const assert = require("node:assert/strict");

const simulator = require("../src/tuco_ai_backend/frontend/circuit-simulator.js");

test("exports all nine firmware blocks and the 4x4 physical slot mapping", () => {
  assert.deepEqual(
    simulator.BRICKS.map((brick) => brick.component),
    [
      "input",
      "output",
      "not_gate",
      "and_gate",
      "or_gate",
      "nand_gate",
      "nor_gate",
      "xor_gate",
      "xnor_gate",
    ],
  );
  assert.deepEqual(simulator.SLOT_LAYOUT[0], { slot: 6, board: 4, cascadeSlot: 7 });
  assert.deepEqual(simulator.SLOT_LAYOUT[15], { slot: 15, board: 8, cascadeSlot: 16 });
  assert.equal(simulator.getGlobalPort(0, 0), 0);
  assert.equal(simulator.getGlobalPort(15, 3), 63);
});

test("maps upper and lower physical sockets to the visible P0–P7 labels", () => {
  assert.deepEqual(simulator.getSlotDisplayPorts(0), [
    { side: "top", label: "P3", localPort: 3 },
    { side: "right", label: "P0", localPort: 0 },
    { side: "bottom", label: "P1", localPort: 1 },
    { side: "left", label: "P2", localPort: 2 },
  ]);
  assert.deepEqual(simulator.getSlotDisplayPorts(1), [
    { side: "top", label: "P5", localPort: 1 },
    { side: "right", label: "P6", localPort: 2 },
    { side: "bottom", label: "P7", localPort: 3 },
    { side: "left", label: "P4", localPort: 0 },
  ]);
});

test("classifies valid, direction-error, duplicate, and unused-port links", () => {
  const state = simulator.createState();
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.placeBrick(state, 2, "and_gate");

  assert.equal(
    simulator.classifyLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 }).kind,
    "valid",
  );
  assert.equal(
    simulator.classifyLink(state, { slot: 1, localPort: 0 }, { slot: 2, localPort: 3 }).kind,
    "direction",
  );
  assert.equal(
    simulator.classifyLink(state, { slot: 2, localPort: 2 }, { slot: 1, localPort: 3 }).kind,
    "unused",
  );

  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  assert.equal(
    simulator.classifyLink(state, { slot: 0, localPort: 0 }, { slot: 2, localPort: 3 }).kind,
    "multiple",
  );
});

test("serializes the browser topology as the firmware snapshot shape", () => {
  const state = simulator.createState();
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  const snapshot = simulator.buildSnapshot(state, { level_id: 101, short_goal: "直连" });

  assert.equal(snapshot.schema_version, 2);
  assert.equal(snapshot.slots.length, 16);
  assert.deepEqual(snapshot.slots[2], { slot: 2, present: false });
  assert.equal("component" in snapshot.slots[2], false);
  assert.equal("gate" in snapshot.slots[2], false);
  assert.deepEqual(snapshot.valid_links, [{ from_slot: 0, to_slot: 1 }]);
  assert.deepEqual(snapshot.invalid_links, []);
  assert.deepEqual(snapshot.scan, { ir_scans: 0, i2c_scans: 0 });
});

test("emits a newer snapshot after each topology mutation", () => {
  const state = simulator.createState();
  const revisions = [];
  simulator.subscribe(state, (snapshot) => revisions.push(snapshot.topology_revision));
  simulator.placeBrick(state, 0, "input");
  simulator.placeBrick(state, 1, "output");
  simulator.addLink(state, { slot: 0, localPort: 0 }, { slot: 1, localPort: 0 });
  assert.deepEqual(revisions, [1, 2, 3]);
});
