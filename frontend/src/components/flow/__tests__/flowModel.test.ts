import { describe, expect, test } from "bun:test";
import {
  addableEdgeShapes,
  danglingEdgeNote,
  diffFlowPatch,
  EDGE_SHAPES,
  emptyCondition,
  EQUATION_OPERATORS,
  flowReducer,
  iterNodeEdges,
  knownVariables,
  newNodeId,
  newToolId,
  seedFlow,
  stampToolIds,
  summarizeCondition,
  transferNumberStatus,
  type FlowEdge,
  type FlowNode,
} from "../flowModel";
import type { RawConversationFlow } from "@/lib/api";
import { load, NAMES } from "./fixtures";

describe("fidelity", () => {
  test.each(NAMES.map((n) => [n] as const))("%s survives a no-op edit byte for byte", (name) => {
    const flow = load(name);
    const before = JSON.stringify(flow);
    // Rename a node to X and back: two real reducer passes, not a clone.
    const nodeId = (flow.nodes as FlowNode[])[0].id;
    const renamed = flowReducer(flow, {
      type: "patchNode",
      nodeId,
      patch: { name: "___temp___" },
    });
    const restored = flowReducer(renamed, {
      type: "patchNode",
      nodeId,
      patch: { name: (flow.nodes as FlowNode[])[0].name },
    });
    expect(JSON.stringify(restored)).toBe(before);
  });

  test.each(NAMES.map((n) => [n] as const))("%s keeps keys the editor does not model", (name) => {
    const flow = load(name);
    const next = flowReducer(flow, { type: "patchFlow", patch: { global_prompt: "changed" } });
    for (const key of Object.keys(flow)) expect(next).toHaveProperty(key);
    // is_transfer_cf / flex_mode / tool_call_strict_mode are exactly the keys
    // a rebuild-from-typed-model would drop.
    expect(next.is_transfer_cf).toEqual(flow.is_transfer_cf);
  });

  test("the reducer never mutates its input", () => {
    const flow = load("prior_auth_hotline.json");
    const snapshot = JSON.stringify(flow);
    flowReducer(flow, { type: "patchFlow", patch: { global_prompt: "changed" } });
    expect(JSON.stringify(flow)).toBe(snapshot);
  });
});

describe("iterNodeEdges", () => {
  test("yields all five shapes in a stable order", () => {
    const node: FlowNode = {
      id: "n1",
      type: "conversation",
      edges: [{ id: "e1" }, { id: "e2" }],
      else_edge: { id: "else" },
      edge: { id: "single" },
      always_edge: { id: "always" },
      skip_response_edge: { id: "skip" },
    };
    expect(iterNodeEdges(node).map((e) => [e.shape, e.edge.id])).toEqual([
      ["edges", "e1"],
      ["edges", "e2"],
      ["else_edge", "else"],
      ["edge", "single"],
      ["always_edge", "always"],
      ["skip_response_edge", "skip"],
    ]);
  });

  test("matches the worker's shape list", () => {
    expect([...EDGE_SHAPES]).toEqual([
      "edges",
      "else_edge",
      "edge",
      "always_edge",
      "skip_response_edge",
    ]);
  });

  test("the real prior-auth fixture has edges in four of the five shapes", () => {
    const flow = load("prior_auth_hotline.json");
    const shapes = new Set(
      (flow.nodes as FlowNode[]).flatMap((n) => iterNodeEdges(n).map((e) => e.shape)),
    );
    expect(shapes.has("edges")).toBe(true);
    expect(shapes.has("else_edge")).toBe(true);
    expect(shapes.has("edge")).toBe(true);
    expect(shapes.has("skip_response_edge")).toBe(true);
  });
});

describe("addableEdgeShapes", () => {
  test("a fresh transfer_call node (no edge field at all) can add its failure edge", () => {
    // flowModel's defaultsFor("transfer_call") gives a new node no `edge`
    // field at all -- confirmed against the reducer, not just asserted here.
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "transfer_call",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(added.type).toBe("transfer_call");
    expect(added.edge).toBeUndefined();
    expect(addableEdgeShapes(added)).toEqual(["edge"]);
  });

  test("a transfer_call node that already has its edge offers nothing", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773866123876")!;
    expect(node.type).toBe("transfer_call");
    expect(node.edge).toBeDefined();
    expect(addableEdgeShapes(node)).toEqual([]);
  });

  test("a branch node that already has else_edge does not offer it again", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773864774353")!;
    expect(node.type).toBe("branch");
    expect(node.else_edge).toBeDefined();
    expect(addableEdgeShapes(node)).toEqual([]);
  });

  test("a function node that already has else_edge does not offer it again", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773865257897")!;
    expect(node.type).toBe("function");
    expect(node.else_edge).toBeDefined();
    expect(addableEdgeShapes(node)).not.toContain("else_edge");
  });

  // `flow_runtime.py`'s `_handlers` maps `function` and
  // `extract_dynamic_variables` to `_enter_conversation` alongside
  // `conversation`/`subagent` (its docstring names all four), so the worker
  // takes their `skip_response_edge` on entry and their `always_edge` in
  // `on_user_turn` exactly as it does for a conversation node. The editor
  // offered them `else_edge` only, so a graph the worker executes fine could
  // not be authored here.
  test("a fresh function node can add always_edge and skip_response_edge too", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "function",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(addableEdgeShapes(added)).toEqual(["else_edge", "always_edge", "skip_response_edge"]);
  });

  test("a function node with an else_edge can still add the two auto-follow shapes", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773865257897")!;
    expect(addableEdgeShapes(node)).toEqual(["always_edge", "skip_response_edge"]);
  });

  // `branch` stays fallback-only, unlike `function`/`extract_dynamic_variables`
  // above: it routes away on entry (`_enter_branch`) and is never the current
  // node when `on_user_turn` runs, so an `always_edge` on one could never fire.
  test("a fresh branch node (no else_edge) can add its fallback", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "branch",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(added.else_edge).toBeUndefined();
    expect(addableEdgeShapes(added)).toEqual(["else_edge"]);
  });

  test("a fresh extract_dynamic_variables node can add its fallback", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "extract_dynamic_variables",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(added.else_edge).toBeUndefined();
    expect(addableEdgeShapes(added)).toEqual(["else_edge", "always_edge", "skip_response_edge"]);
  });

  test("a fresh conversation node can add always_edge and skip_response_edge", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "conversation",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(addableEdgeShapes(added)).toEqual(["always_edge", "skip_response_edge"]);
  });

  test("a fresh subagent node can add always_edge and skip_response_edge", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "subagent",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(addableEdgeShapes(added)).toEqual(["always_edge", "skip_response_edge"]);
  });

  test("a conversation node that already has skip_response_edge only offers always_edge", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773865396835")!;
    expect(node.type).toBe("conversation");
    expect(node.skip_response_edge).toBeDefined();
    expect(node.always_edge).toBeUndefined();
    expect(addableEdgeShapes(node)).toEqual(["always_edge"]);
  });

  test("never offers 'edges' -- it is a list, unbounded, and creatable by dragging", () => {
    const flow = load("clara_outbound.json");
    for (const node of flow.nodes as FlowNode[]) {
      expect(addableEdgeShapes(node)).not.toContain("edges");
    }
  });

  test("an unmodelled node type (end) offers nothing", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "end",
      position: { x: 0, y: 0 },
    });
    const added = (next.nodes as FlowNode[]).at(-1)!;
    expect(addableEdgeShapes(added)).toEqual([]);
  });
});

describe("reducer actions", () => {
  test("addNode appends a node and mints a unique id", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "end",
      position: { x: 10, y: 20 },
    });
    const nodes = next.nodes as FlowNode[];
    expect(nodes.length).toBe((flow.nodes as FlowNode[]).length + 1);
    const added = nodes[nodes.length - 1];
    expect(added.type).toBe("end");
    expect(added.display_position).toEqual({ x: 10, y: 20 });
    expect(new Set(nodes.map((n) => n.id)).size).toBe(nodes.length);
  });

  test("moveNode writes display_position and nothing else", () => {
    const flow = load("clara_outbound.json");
    const id = (flow.nodes as FlowNode[])[0].id;
    const next = flowReducer(flow, { type: "moveNode", nodeId: id, position: { x: 7, y: 9 } });
    const [before] = flow.nodes as FlowNode[];
    const after = (next.nodes as FlowNode[]).find((n) => n.id === id)!;
    expect(after.display_position).toEqual({ x: 7, y: 9 });
    expect({ ...after, display_position: null }).toEqual({ ...before, display_position: null });
  });

  test("deleteNode also drops every edge pointing at it", () => {
    const flow = load("prior_auth_hotline.json");
    const target = flow.start_node_id as string;
    const next = flowReducer(flow, { type: "deleteNode", nodeId: target });
    const dangling = (next.nodes as FlowNode[]).flatMap((n) =>
      iterNodeEdges(n).filter((e) => e.edge.destination_node_id === target),
    );
    expect(dangling).toEqual([]);
  });

  test("deleting the start node re-points start_node_id at a survivor", () => {
    const flow = load("prior_auth_hotline.json");
    const next = flowReducer(flow, { type: "deleteNode", nodeId: flow.start_node_id as string });
    const ids = (next.nodes as FlowNode[]).map((n) => n.id);
    // `start_node_id` is `string | null | undefined` on the wire, so assert it
    // is actually a string before the membership check — otherwise a reducer
    // that dropped it to null/undefined would fail on a confusing `toContain`
    // message rather than saying what went wrong.
    expect(typeof next.start_node_id).toBe("string");
    expect(ids).toContain(next.start_node_id as string);
  });

  test("connect adds an edges[] entry with a prompt condition", () => {
    const flow = load("clara_outbound.json");
    const [a, b] = flow.nodes as FlowNode[];
    const next = flowReducer(flow, {
      type: "connect",
      nodeId: a.id,
      shape: "edges",
      destinationNodeId: b.id,
    });
    // NOTE: not `.at(-1)` — clara_outbound.json's real Welcome Node (nodes[0])
    // already carries an `always_edge`, which sorts after `edges[]` in
    // iterNodeEdges' worker-matching order. `.at(-1)` would pick that
    // pre-existing edge instead of the one `connect` just appended. Find the
    // added edge by its destination instead of assuming position.
    const added = iterNodeEdges((next.nodes as FlowNode[])[0]).find(
      (e) => e.shape === "edges" && e.edge.destination_node_id === b.id,
    )!;
    expect(added).toBeDefined();
    expect(added.edge.destination_node_id).toBe(b.id);
    expect(added.edge.transition_condition).toEqual({ type: "prompt", prompt: "" });
    expect(typeof added.edge.id).toBe("string");
    // Exactly one edge gained, in the shape asked for: catches a `connect`
    // that appends twice, or that writes a single-edge shape as well.
    expect(iterNodeEdges((next.nodes as FlowNode[])[0]).length).toBe(iterNodeEdges(a).length + 1);
  });

  test("a single-edge shape replaces rather than appends", () => {
    const flow = load("clara_outbound.json");
    const [a, b] = flow.nodes as FlowNode[];
    const once = flowReducer(flow, {
      type: "connect",
      nodeId: a.id,
      shape: "else_edge",
      destinationNodeId: b.id,
    });
    const twice = flowReducer(once, {
      type: "connect",
      nodeId: a.id,
      shape: "else_edge",
      destinationNodeId: a.id,
    });
    const node = (twice.nodes as FlowNode[]).find((n) => n.id === a.id)!;
    expect(iterNodeEdges(node).filter((e) => e.shape === "else_edge").length).toBe(1);
  });
});

describe("notes", () => {
  // The canvas renders `notes[]` as stickies and persists their positions, so
  // these three actions round-trip real authored content. The prior-auth
  // fixture carries real notes, which is what makes these non-synthetic.
  test("addNote appends to a flow that already has notes", () => {
    const flow = load("prior_auth_hotline.json");
    const before = (flow.notes as { id: string }[]).length;
    expect(before).toBeGreaterThan(0);
    const next = flowReducer(flow, { type: "addNote", position: { x: 3, y: 4 }, content: "hi" });
    const notes = next.notes as Record<string, unknown>[];
    expect(notes.length).toBe(before + 1);
    expect(notes[notes.length - 1]).toMatchObject({
      content: "hi",
      display_position: { x: 3, y: 4 },
    });
    expect(new Set(notes.map((n) => n.id)).size).toBe(notes.length);
  });

  test("addNote works on a flow with no notes key at all", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, { type: "addNote", position: { x: 0, y: 0 } });
    expect((next.notes as unknown[]).length).toBe(((flow.notes as unknown[]) ?? []).length + 1);
  });

  test("patchNote edits one note and leaves its siblings and unknown keys alone", () => {
    const flow = load("prior_auth_hotline.json");
    const [first, ...rest] = flow.notes as { id: string }[];
    const next = flowReducer(flow, {
      type: "patchNote",
      noteId: first.id,
      patch: { content: "rewritten" },
    });
    const notes = next.notes as Record<string, unknown>[];
    expect(notes[0].content).toBe("rewritten");
    // `size` is a real key on the fixture's notes that the editor never models.
    expect(notes[0].size).toEqual((first as Record<string, unknown>).size);
    expect(notes.slice(1)).toEqual(rest as unknown as Record<string, unknown>[]);
  });

  test("deleteNote removes only the named note", () => {
    const flow = load("prior_auth_hotline.json");
    const notes = flow.notes as { id: string }[];
    const next = flowReducer(flow, { type: "deleteNote", noteId: notes[0].id });
    const ids = (next.notes as { id: string }[]).map((n) => n.id);
    expect(ids).not.toContain(notes[0].id);
    expect(ids.length).toBe(notes.length - 1);
  });
});

describe("knownVariables", () => {
  test("collects defaults, extract-node variables and tool response_variables", () => {
    const flow = {
      conversation_flow_id: "f",
      version: 0,
      default_dynamic_variables: { caller_name: "friend" },
      tools: [{ tool_id: "t1", response_variables: { case_id: "$.id" } }],
      nodes: [
        {
          id: "n1",
          type: "extract_dynamic_variables",
          variables: [{ name: "dob", type: "string" }],
        },
      ],
    } as unknown as RawConversationFlow;
    expect(knownVariables(flow).sort()).toEqual(["caller_name", "case_id", "dob"]);
  });
});

describe("seedFlow", () => {
  test("is a runnable two-node graph the worker accepts", () => {
    const seed = seedFlow();
    const nodes = seed.nodes as FlowNode[];
    expect(nodes.map((n) => n.type)).toEqual(["conversation", "end"]);
    expect(seed.start_node_id).toBe(nodes[0].id);
    // The start node reaches the end node, so the graph has no dead end.
    expect(iterNodeEdges(nodes[0])[0].edge.destination_node_id).toBe(nodes[1].id);
  });
});

describe("newNodeId", () => {
  test("is unique across rapid successive calls", () => {
    const ids = Array.from({ length: 50 }, () => newNodeId("conversation"));
    expect(new Set(ids).size).toBe(50);
  });
});

describe("newToolId", () => {
  test("is unique across rapid successive calls", () => {
    const ids = Array.from({ length: 50 }, () => newToolId());
    expect(new Set(ids).size).toBe(50);
  });

  test("looks like a Retell tool id", () => {
    expect(newToolId()).toMatch(/^tool-\d+-\d+$/);
  });
});

describe("stampToolIds", () => {
  test("stamps a tool_id onto tools that lack one, leaves an existing one alone", () => {
    const tools = [
      { name: "a", type: "custom" },
      { name: "b", type: "custom", tool_id: "tool-existing" },
      { name: "c", type: "end_call" },
    ];
    const stamped = stampToolIds(tools);
    expect(typeof stamped[0].tool_id).toBe("string");
    expect(stamped[0].tool_id).not.toBe("");
    expect(stamped[1].tool_id).toBe("tool-existing");
    expect(typeof stamped[2].tool_id).toBe("string");
    // Two tools stamped in the same pass must not collide.
    expect(stamped[0].tool_id).not.toBe(stamped[2].tool_id);
  });

  test("treats an empty-string tool_id as missing", () => {
    const stamped = stampToolIds([{ name: "a", tool_id: "" }]);
    expect(stamped[0].tool_id).not.toBe("");
  });

  test("is pure: never mutates the input array or its entries", () => {
    const tools = [{ name: "a", type: "custom" }, { name: "b", tool_id: "tool-existing" }];
    const snapshot = JSON.stringify(tools);
    stampToolIds(tools);
    expect(JSON.stringify(tools)).toBe(snapshot);
  });

  test("a fully-stamped list round-trips unchanged (values, not just presence)", () => {
    const tools = [{ name: "a", tool_id: "tool-1" }, { name: "b", tool_id: "tool-2" }];
    expect(stampToolIds(tools)).toEqual(tools);
  });
});

describe("diffFlowPatch", () => {
  // `dispatchFlow` (frontend/src/app/agents/[id]/page.tsx) runs the reducer
  // then diffs current vs. next to build the autosave PATCH body. The reducer
  // `structuredClone`s the whole flow every time, so an `Object.is` diff would
  // see a fresh reference on every non-primitive key regardless of whether it
  // changed -- these pin the value-based diff against a real fixture.

  test("a single-field edit against a real fixture yields a one-key patch", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "patchFlow",
      patch: { global_prompt: "changed" },
    });
    expect(Object.keys(diffFlowPatch(flow, next))).toEqual(["global_prompt"]);
  });

  test("a genuine nodes edit still yields nodes", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "end",
      position: { x: 0, y: 0 },
    });
    const patch = diffFlowPatch(flow, next);
    expect(Object.keys(patch)).toEqual(["nodes"]);
  });

  test("a reducer pass whose net effect is a no-op yields an empty patch", () => {
    // Renaming a node to X and back is two real reducer passes -- each one
    // `structuredClone`s the flow, so `nodes` ends up a different reference
    // from the original even though its content is identical again. A
    // reference-based diff would (wrongly) include `nodes` here.
    const flow = load("clara_outbound.json");
    const nodeId = (flow.nodes as FlowNode[])[0].id;
    const renamed = flowReducer(flow, {
      type: "patchNode",
      nodeId,
      patch: { name: "___temp___" },
    });
    const restored = flowReducer(renamed, {
      type: "patchNode",
      nodeId,
      patch: { name: (flow.nodes as FlowNode[])[0].name },
    });
    expect(diffFlowPatch(flow, restored)).toEqual({});
  });

  test("reassigning a field to its own existing value is not reported as changed", () => {
    const flow = load("prior_auth_hotline.json");
    const next = flowReducer(flow, {
      type: "patchFlow",
      patch: { global_prompt: flow.global_prompt },
    });
    // `global_prompt` was reassigned to its own value: still expected to
    // report unchanged, since the diff is by value, not by whether an
    // assignment happened.
    expect(diffFlowPatch(flow, next)).toEqual({});
  });
});

describe("conditions", () => {
  test("the operator list matches the worker's", () => {
    // worker/src/arhiteq_worker/flow.py: _NUMERIC_OPERATORS, _EQUALITY_OPERATORS,
    // _CONTAINMENT_OPERATORS, plus the unary `exists`. Offering an operator the
    // worker does not implement produces an edge that silently never fires.
    expect([...EQUATION_OPERATORS]).toEqual([
      "==", "!=", ">", "<", "CONTAINS", "NOT CONTAINS", "exists",
    ]);
  });

  test("switching condition type produces the shape the worker parses", () => {
    expect(emptyCondition("prompt")).toEqual({ type: "prompt", prompt: "" });
    expect(emptyCondition("equation")).toEqual({
      type: "equation",
      operator: "&&",
      equations: [{ left: "", operator: "==", right: "" }],
    });
  });

  test("an equation condition with no equations is rejected by the worker", () => {
    // `evaluate_equation_condition` returns False for an empty `equations`
    // list, so the editor must never produce one -- hence the seeded row above.
    expect((emptyCondition("equation").equations as unknown[]).length).toBeGreaterThan(0);
  });
});

describe("summarizeCondition", () => {
  // ONE summariser for both readers: the settings panel's collapsed row and
  // the canvas edge label. They were two copies, and the copies drifted --
  // see the `exists` case below, which the panel got right and the canvas did
  // not. `flowGraph.test.ts` pins the canvas end of the same rule.
  test("a unary exists never shows a stale right operand", () => {
    // `EquationBuilder` deliberately keeps `right` when the operator switches
    // to `exists`, so a real edge carries one; the worker's
    // `_evaluate_single_equation` never reads it.
    const edge: FlowEdge = {
      id: "e1",
      transition_condition: {
        type: "equation",
        operator: "&&",
        equations: [{ left: "{{policy_id}}", operator: "exists", right: "42" }],
      },
    };
    expect(summarizeCondition(edge)).toBe("{{policy_id}} exists");
  });

  test("multiple equations are joined by the condition's own operator", () => {
    const edge: FlowEdge = {
      id: "e1",
      transition_condition: {
        type: "equation",
        operator: "||",
        equations: [
          { left: "{{age}}", operator: ">", right: "18" },
          { left: "{{state}}", operator: "==", right: "CA" },
        ],
      },
    };
    expect(summarizeCondition(edge)).toBe("{{age}} > 18 || {{state}} == CA");
  });

  test("returns null when there is nothing readable to show", () => {
    // Each caller supplies its own fallback ("No condition set" in the panel,
    // the shape's word on the canvas), so this must distinguish "no text"
    // from an actual summary rather than baking one in.
    expect(summarizeCondition({ id: "e1" })).toBeNull();
    expect(summarizeCondition({ id: "e1", transition_condition: { type: "prompt", prompt: "" } })).toBeNull();
    expect(
      summarizeCondition({ id: "e1", transition_condition: { type: "prompt", prompt: "   " } }),
    ).toBeNull();
    expect(
      summarizeCondition({ id: "e1", transition_condition: { type: "equation", equations: [] } }),
    ).toBeNull();
    expect(
      summarizeCondition({ id: "e1", transition_condition: { type: "someday_new_kind" } }),
    ).toBeNull();
  });

  test("summarises every condition on every real fixture without throwing", () => {
    for (const name of NAMES) {
      const flow = load(name);
      for (const node of flow.nodes as FlowNode[]) {
        for (const { edge } of iterNodeEdges(node)) {
          const summary = summarizeCondition(edge);
          expect(summary === null || typeof summary === "string").toBe(true);
        }
      }
    }
  });
});

describe("danglingEdgeNote", () => {
  // Only a dangling FALLBACK on a node type that can do nothing but route
  // actually ends the call. The other four combinations are inert, and the
  // editor used to call all five a dead end -- on real fixture data.
  test("a dangling else_edge on a function node ends the call", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773865257897")!;
    expect((node.else_edge as Record<string, unknown>).destination_node_id).toBeUndefined();
    const note = danglingEdgeNote("else_edge", String(node.type));
    expect(note.tone).toBe("error");
    expect(note.text).toContain("call ends here");
  });

  test("a dangling failure edge on a transfer_call node ends the call", () => {
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1773866123876")!;
    expect((node.edge as Record<string, unknown>).destination_node_id).toBeUndefined();
    expect(danglingEdgeNote("edge", String(node.type)).tone).toBe("error");
  });

  test("the fixture's dangling edges[] entry is not reported as a dead end", () => {
    // `prior_auth_hotline.json` really ships one, on a `subagent` node.
    // `prompt_edges` excludes a destination-less authored edge and
    // `select_equation_edge` continues past it: the node keeps every other
    // option, and nothing about the call ends.
    const flow = load("prior_auth_hotline.json");
    const node = (flow.nodes as FlowNode[]).find((n) => n.id === "node-1785347159284")!;
    const dangling = (node.edges as Record<string, unknown>[]).filter(
      (e) => !e.destination_node_id,
    );
    expect(dangling.length).toBe(1);
    const note = danglingEdgeNote("edges", String(node.type));
    expect(note.tone).toBe("info");
    expect(note.text).toContain("never offers this transition");
  });

  test("a dangling always_edge / skip_response_edge is inert, not fatal", () => {
    // Both follow sites are gated on `destination_node_id`
    // (`on_user_turn`, `_enter_conversation`), so the node just waits.
    for (const shape of ["always_edge", "skip_response_edge"] as const) {
      for (const nodeType of ["conversation", "subagent", "function"]) {
        expect(danglingEdgeNote(shape, nodeType).tone).toBe("info");
      }
    }
  });

  test("a dangling fallback on a node that can hold the conversation stays put", () => {
    // `_dead_end` ends the call only for `_ROUTING_NODE_TYPES`;
    // conversation/subagent keep talking, which is a legitimate state.
    expect(danglingEdgeNote("else_edge", "conversation").tone).toBe("info");
    expect(danglingEdgeNote("edge", "subagent").tone).toBe("info");
  });
});

describe("transferNumberStatus", () => {
  // `flow_runtime.py:_transfer_number` runs `resolve_template` on the
  // authored value BEFORE matching E.164, so a `{{variable}}` destination is
  // a working configuration the editor cannot judge -- and must not red-flag.
  test("a bare placeholder is pending resolution, not an error", () => {
    expect(transferNumberStatus("{{transfer_number}}")).toBe("template");
  });

  test("a placeholder mixed with literal digits is also pending resolution", () => {
    expect(transferNumberStatus("+1{{extension}}")).toBe("template");
    expect(transferNumberStatus("{{ transfer_number }}")).toBe("template");
  });

  test("a plain E.164 number is valid, with or without surrounding space", () => {
    expect(transferNumberStatus("+14155551234")).toBe("e164");
    expect(transferNumberStatus("  +14155551234  ")).toBe("e164");
  });

  test("an empty value is neither valid nor an error", () => {
    expect(transferNumberStatus("")).toBe("empty");
    expect(transferNumberStatus("   ")).toBe("empty");
  });

  test("a placeholder-free non-E.164 value is the one real error", () => {
    expect(transferNumberStatus("415-555-1234")).toBe("invalid");
    expect(transferNumberStatus("+0123456789")).toBe("invalid"); // leading 0 after +
    expect(transferNumberStatus("call the clinic")).toBe("invalid");
  });
});
