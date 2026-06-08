import assert from "node:assert/strict";
import test from "node:test";

import {
  applyRunSnapshot,
  mergeConversationMessages,
} from "../../src/hydrators/runHydrator.js";
import { cleanAssistantMessageForDisplay } from "../../src/core/messageDisplay.js";
import {
  activityText,
  isExplicitAgentWorkEvent,
} from "../../src/store/actions/eventActions.js";
import {
  canStartRun,
  mapConversationMessages,
} from "../../src/store/actions/runActions.js";
import {
  agentActivityKey,
  buildAgentActivityQueue,
  currentAgentActivities,
  latestUserMessageIndex,
  updateAgentActivityQueue,
} from "../../src/state/chatState.js";
import {
  blankArtifactCenter,
  blankEphemeralAgents,
  blankRecoveryCenter,
  blankReport,
} from "../../src/state/runDefaults.js";


function state(overrides = {}) {
  return {
    currentThreadId: "run-1",
    currentConversationId: "conv-1",
    currentRunStrategy: "",
    status: "running",
    currentRunStatus: "running",
    messages: [],
    events: [],
    replay: {},
    tasks: [],
    metrics: { tasks: 0, files: 0, toolCalls: 0, tokens: "--", tests: "--" },
    report: blankReport(),
    recoveryCenter: blankRecoveryCenter("safe"),
    artifactCenter: blankArtifactCenter("idle"),
    ephemeralAgents: blankEphemeralAgents(),
    agentActivities: [],
    team: [],
    approval: { status: "idle" },
    runOutcome: null,
    ...overrides,
  };
}


function snapshot(overrides = {}) {
  return {
    run: { thread_id: "run-1", status: "running", strategy: "feature_delivery", is_active: true },
    workspace: {},
    conversation: { conversation_id: "conv-1", messages: [] },
    tasks: [],
    changes: {},
    quality: {},
    timeline: [],
    activity: { items: [] },
    agents: [],
    approvals: [],
    artifacts: [],
    outcome: null,
    ...overrides,
  };
}


function apply(target, value, options = {}) {
  applyRunSnapshot({
    state: target,
    snapshot: value,
    replaceMessages: Boolean(options.replaceMessages),
    setDiffState: () => {},
  });
}


test("continuous conversation merges new messages without losing previous turns", () => {
  const target = state({
    messages: [
      { role: "user", author: "用户", content: "first" },
      { role: "assistant", author: "Lead Agent", content: "first answer" },
    ],
  });

  apply(target, snapshot({
    conversation: {
      conversation_id: "conv-1",
      messages: [
        { role: "user", content: "first" },
        { role: "assistant", agent: "Lead", content: "first answer" },
        { role: "user", content: "second" },
        { role: "assistant", agent: "Lead", content: "second answer" },
      ],
    },
  }));

  assert.deepEqual(target.messages.map((item) => item.content), [
    "first",
    "first answer",
    "second",
    "second answer",
  ]);
});


test("snapshot for another thread cannot overwrite current run state", () => {
  const target = state({ messages: [{ role: "user", author: "用户", content: "keep me" }] });

  apply(target, snapshot({
    run: { thread_id: "run-other", status: "completed" },
    conversation: { conversation_id: "conv-other", messages: [{ role: "user", content: "wrong" }] },
  }));

  assert.equal(target.currentThreadId, "run-1");
  assert.equal(target.messages[0].content, "keep me");
});


test("lead direct reply never leaves implementation tasks or stale activity", () => {
  const target = state({
    tasks: [{ id: "old-task", title: "old", status: "running" }],
    agentActivities: [{ agent: "Tester", text: "old activity" }],
    diff: "old diff",
    diffFiles: [{ path: "old.py" }],
    selectedDiffFile: "old.py",
    metrics: { tasks: 1, files: 9, insertions: 30, deletions: 4, toolCalls: 0, tokens: "--", tests: "--" },
    report: { ...blankReport(), changedFiles: ["old.py"] },
  });

  apply(target, snapshot({
    run: { thread_id: "run-1", status: "completed", strategy: "lead_direct_reply", is_active: false },
    tasks: [{ id: "unexpected", title: "should not render", status: "completed" }],
    activity: { items: [{ agent: "Tester", action: "should not render" }] },
  }));

  assert.deepEqual(target.tasks, []);
  assert.deepEqual(target.agentActivities, []);
  assert.equal(target.metrics.tasks, 0);
  assert.equal(target.metrics.files, 0);
  assert.equal(target.metrics.insertions, 0);
  assert.equal(target.metrics.deletions, 0);
  assert.equal(target.diff, "");
  assert.deepEqual(target.diffFiles, []);
  assert.equal(target.selectedDiffFile, "");
  assert.deepEqual(target.report.changedFiles, []);
});


test("authoritative snapshot clears stale quality, risks, and outcome", () => {
  const target = state({
    report: { ...blankReport(), quality: { score: 12 }, risks: [{ title: "old" }] },
    recoveryCenter: { ...blankRecoveryCenter("risk"), risks: [{ title: "old" }] },
    runOutcome: { status: "failed", quality: { score: 12 } },
  });

  apply(target, snapshot({ quality: {}, outcome: null }));

  assert.equal(target.runOutcome, null);
  assert.equal(target.report.quality, null);
  assert.deepEqual(target.report.risks, []);
  assert.deepEqual(target.recoveryCenter.risks, []);
});


test("snapshot approval keeps backend run id for resolution", () => {
  const target = state({ currentThreadId: "run-1" });

  apply(target, snapshot({
    approvals: [{
      decision_id: "approval-1",
      thread_id: "run-approval-owner",
      tool: "bash",
      reason: "needs approval",
      status: "pending",
    }],
  }));

  assert.equal(target.approval.status, "pending");
  assert.equal(target.approval.decisionId, "approval-1");
  assert.equal(target.approval.threadId, "run-approval-owner");
});


test("run submission guard rejects overlapping active states", () => {
  assert.equal(canStartRun("idle"), true);
  assert.equal(canStartRun("completed"), true);
  assert.equal(canStartRun("failed"), true);
  assert.equal(canStartRun("running"), false);
  assert.equal(canStartRun("waiting_approval"), false);
  assert.equal(canStartRun("cancelling"), false);
});


test("conversation message mapping preserves every recorded turn", () => {
  const mapped = mapConversationMessages({
    run_records: [
      { prompt: "first", summary: "first answer" },
      { prompt: "second", summary: "second answer" },
    ],
  });

  assert.deepEqual(mapped.map((item) => item.content), [
    "first",
    "first answer",
    "second",
    "second answer",
  ]);
});

test("conversation restore leaves the current run reply to the authoritative snapshot", () => {
  const mapped = mapConversationMessages({
    current_thread_id: "run-2",
    run_records: [
      { thread_id: "run-1", prompt: "first", summary: "first answer" },
      { thread_id: "run-2", prompt: "second", summary: "truncated second answer" },
    ],
  });

  assert.deepEqual(mapped.map((item) => item.content), [
    "first",
    "first answer",
    "second",
  ]);
});


test("message merge deduplicates snapshot replay", () => {
  const existing = [{ role: "user", author: "用户", content: "same" }];
  const incoming = [
    { role: "user", author: "用户", content: "same" },
    { role: "assistant", author: "Lead Agent", content: "new" },
  ];

  assert.deepEqual(mergeConversationMessages(existing, incoming), [
    existing[0],
    incoming[1],
  ]);
});


test("runtime activity belongs only below the latest user turn", () => {
  const messages = [
    { role: "user", content: "first" },
    { role: "assistant", content: "answer" },
    { role: "user", content: "second" },
  ];
  const activities = [
    { agent: "Lead", text: "latest lead work", eventType: "agent_activity", explicitAgentWork: true },
    { agent: "Coder", text: "editing", eventType: "agent_activity", explicitAgentWork: true },
  ];

  assert.equal(latestUserMessageIndex(messages), 2);
  assert.deepEqual(
    currentAgentActivities(activities, { running: true }).map((item) => item.text),
    ["latest lead work", "editing"],
  );
  assert.deepEqual(currentAgentActivities(activities, { running: false }), []);
});


test("tool calls are folded into the owning agent activity instead of separate chat cards", () => {
  const first = {
    agent: "Coder Agent",
    text: "正在编辑文件",
    eventType: "agent_activity",
    explicitAgentWork: true,
    time: "10:00",
  };
  const tool = {
    agent: "Coder",
    text: activityText({
      eventType: "tool_call_finished",
      payload: {
        tool: "write_file",
        path: "/Users/huali/code/python/nanoCursor/tests/src/solution.py",
        capability_trace: { agent: "Coder" },
      },
      content: "Created /Users/huali/code/python/nanoCursor/tests/src/solution.py",
    }),
    eventType: "tool_call_finished",
    explicitAgentWork: isExplicitAgentWorkEvent("tool_call_finished"),
    time: "10:01",
    payload: { tool: "write_file", capability_trace: { agent: "Coder" } },
  };

  const queue = buildAgentActivityQueue([first, tool]);

  assert.equal(queue.length, 1);
  assert.equal(agentActivityKey(first), agentActivityKey(tool));
  assert.equal(queue[0].text, "写入：.../nanoCursor/tests/src/solution.py");
});


test("tool activity summary hides runtime internals", () => {
  const text = activityText({
    eventType: "tool_call_finished",
    payload: { tool: "bash", args: { command: "pytest -q" } },
    content: "I0607 16:47:06.372977 11316507 ev_poll_posix.cc:593 FD from fork parent still in poll list",
  });

  assert.equal(text, "执行命令：pytest -q");
  assert.equal(/ev_poll_posix|FD from fork/.test(text), false);
});


test("assistant display cleanup removes phase glue and low level runtime noise", () => {
  const cleaned = cleanAssistantMessageForDisplay(`
好的，我来完成。
阶段 1：接收需求与上下文边界
## 阶段 2：任务拆解与验收标准
I0607 16:47:06.372977 11316507 ev_poll_posix.cc:593 FD from fork parent still in poll list

完成内容
- 已创建测试文件
`);

  assert.equal(cleaned.includes("阶段 1"), false);
  assert.equal(cleaned.includes("阶段 2"), false);
  assert.equal(cleaned.includes("ev_poll_posix"), false);
  assert.equal(cleaned.includes("完成内容"), true);
});


test("active agent queue keeps first-seen order while updating agents in place", () => {
  const leadStarted = { agent: "Lead", text: "planning", eventType: "agent_activity", explicitAgentWork: true };
  const coderStarted = { agent: "Coder", text: "reading files", eventType: "agent_activity", explicitAgentWork: true };
  const testerStarted = { agent: "Tester", text: "preparing tests", eventType: "agent_activity", explicitAgentWork: true };
  const leadUpdated = { agent: "Lead", text: "merging results", eventType: "agent_activity", explicitAgentWork: true };

  const queue = [leadStarted, coderStarted, testerStarted, leadUpdated].reduce(
    (current, activity) => updateAgentActivityQueue(current, activity),
    [],
  );

  assert.deepEqual(queue.map((item) => item.agent), ["Lead", "Coder", "Tester"]);
  assert.deepEqual(queue.map((item) => item.text), ["merging results", "reading files", "preparing tests"]);
});


test("completed agent exits queue and following agents move up", () => {
  const history = [
    { agent: "Lead", text: "planning", eventType: "agent_activity", explicitAgentWork: true },
    { agent: "Coder", text: "editing", eventType: "agent_activity", explicitAgentWork: true },
    { agent: "Tester", text: "testing", eventType: "agent_activity", explicitAgentWork: true },
    { agent: "Coder", text: "done", eventType: "ephemeral_agent_completed", explicitAgentWork: true },
  ];

  assert.deepEqual(
    buildAgentActivityQueue(history).map((item) => item.agent),
    ["Lead", "Tester"],
  );
});


test("snapshot hydration rebuilds only the active agent queue", () => {
  const target = state();

  apply(target, snapshot({
    activity: {
      items: [
        { agent: "Lead", type: "agent_activity", action: "planning" },
        { agent: "Coder", type: "agent_run_started", action: "editing" },
        { agent: "Tester", type: "agent_run_started", action: "testing" },
        { agent: "Coder", type: "agent_result_merged", action: "done" },
      ],
    },
  }));

  assert.deepEqual(target.agentActivities.map((item) => item.agent), ["Lead", "Tester"]);
});


test("active snapshot updates queue content without reordering existing agents", () => {
  const target = state({
    agentActivities: [
      { agent: "Lead", text: "planning", eventType: "agent_activity", explicitAgentWork: true },
      { agent: "Coder", text: "editing", eventType: "agent_activity", explicitAgentWork: true },
    ],
  });

  apply(target, snapshot({
    activity: {
      items: [
        { agent: "Coder", type: "agent_activity", action: "writing tests" },
        { agent: "Tester", type: "agent_run_started", action: "testing" },
      ],
    },
  }));

  assert.deepEqual(target.agentActivities.map((item) => item.agent), ["Lead", "Coder", "Tester"]);
  assert.deepEqual(target.agentActivities.map((item) => item.text), ["planning", "writing tests", "testing"]);
});


test("terminal snapshot clears the active agent queue", () => {
  const target = state({
    agentActivities: [
      { agent: "Lead", text: "planning", eventType: "agent_activity", explicitAgentWork: true },
      { agent: "Coder", text: "editing", eventType: "agent_activity", explicitAgentWork: true },
    ],
  });

  apply(target, snapshot({
    run: { thread_id: "run-1", status: "completed", strategy: "feature_delivery", is_active: false },
  }));

  assert.deepEqual(target.agentActivities, []);
});
