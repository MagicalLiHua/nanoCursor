import assert from "node:assert/strict";
import test from "node:test";

import {
  clearActiveSession,
  loadActiveSession,
  saveActiveSession,
} from "../../src/core/storage.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

test("active session selection is isolated by workspace", () => {
  const storage = memoryStorage();
  saveActiveSession({
    workspaceDir: "/workspace/a",
    conversationId: "conv-a",
    threadId: "run-a",
  }, storage);
  saveActiveSession({
    workspaceDir: "/workspace/b",
    conversationId: "conv-b",
    threadId: "run-b",
  }, storage);

  assert.deepEqual(loadActiveSession("/workspace/a", storage), {
    workspaceDir: "/workspace/a",
    conversationId: "conv-a",
    threadId: "run-a",
  });
  assert.deepEqual(loadActiveSession("/workspace/b", storage), {
    workspaceDir: "/workspace/b",
    conversationId: "conv-b",
    threadId: "run-b",
  });
});

test("pending thread is not persisted and workspace selection can be cleared", () => {
  const storage = memoryStorage();
  saveActiveSession({
    workspaceDir: "/workspace/a",
    conversationId: "conv-a",
    threadId: "pending",
  }, storage);

  assert.equal(loadActiveSession("/workspace/a", storage)?.threadId, "");
  clearActiveSession("/workspace/a", storage);
  assert.equal(loadActiveSession("/workspace/a", storage), null);
});
