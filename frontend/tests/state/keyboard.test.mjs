import assert from "node:assert/strict";
import test from "node:test";

import { shouldSubmitOnEnter } from "../../src/core/keyboard.js";

test("plain Enter submits", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter" }), true);
});

test("Shift Enter does not submit", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter", shiftKey: true }), false);
});

test("IME composition Enter does not submit", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter", nativeEvent: { isComposing: true } }), false);
  assert.equal(shouldSubmitOnEnter({ key: "Enter", isComposing: true }), false);
  assert.equal(shouldSubmitOnEnter({ key: "Enter" }, true), false);
});

test("IME keyCode 229 does not submit", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter", keyCode: 229 }), false);
  assert.equal(shouldSubmitOnEnter({ key: "Enter", nativeEvent: { keyCode: 229 } }), false);
});
