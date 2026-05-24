import { strict as assert } from "node:assert";
import { formatSummary, summarizeTasks, completionRate } from "./app.js";

const summary = summarizeTasks([
  { title: "Plan", done: true },
  { title: "Build", done: false },
  { title: "Review", done: true }
]);

assert.deepEqual(summary, { total: 3, done: 2, remaining: 1 });
assert.equal(formatSummary(summary), "2/3 done");

// completionRate tests
assert.equal(completionRate(summary), "67%");
assert.equal(completionRate({ total: 0, done: 0, remaining: 0 }), "0%");
assert.equal(completionRate({ total: 3, done: 3, remaining: 0 }), "100%");
assert.equal(completionRate({ total: 4, done: 1, remaining: 3 }), "25%");

console.log("tests passed");
