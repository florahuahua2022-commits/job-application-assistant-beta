import assert from "node:assert/strict";
import test from "node:test";
import { creditStatus, diagnoseThenGenerate, generationWorkflowIsBusy, packCreditCost } from "./generationFlow.ts";

test("diagnosis runs before generation", async () => {
  const calls = [];
  await diagnoseThenGenerate(
    async () => { calls.push("diagnose"); return { status: "ready" }; },
    async () => { calls.push("generate"); },
    () => {},
  );
  assert.deepEqual(calls, ["diagnose", "generate"]);
});

test("a ready diagnosis continues to generation", async () => {
  const result = await diagnoseThenGenerate(
    async () => ({ status: "ready" }),
    async () => "generated",
    () => {},
  );
  assert.deepEqual(result, { decision: { status: "ready" }, generated: true, value: "generated" });
});

test("a diagnosis needing confirmation stops generation", async () => {
  let generated = false;
  const result = await diagnoseThenGenerate(
    async () => ({ status: "needs_confirmation" }),
    async () => { generated = true; },
    () => {},
  );
  assert.equal(generated, false);
  assert.equal(result.generated, false);
});

test("a blocked diagnosis stops generation", async () => {
  let generated = false;
  const result = await diagnoseThenGenerate(
    async () => ({ status: "blocked" }),
    async () => { generated = true; },
    () => {},
  );
  assert.equal(generated, false);
  assert.equal(result.generated, false);
});

test("a diagnosis failure stops generation and restores the idle phase", async () => {
  let generated = false;
  const phases = [];
  await assert.rejects(() => diagnoseThenGenerate(
    async () => { throw new Error("diagnosis unavailable"); },
    async () => { generated = true; },
    (phase) => phases.push(phase),
  ), /diagnosis unavailable/);
  assert.equal(generated, false);
  assert.deepEqual(phases, ["diagnosing", "idle"]);
});

test("the unified flow reports diagnosing then generating phases", async () => {
  const phases = [];
  await diagnoseThenGenerate(
    async () => ({ status: "ready" }),
    async () => {},
    (phase) => phases.push(phase),
  );
  assert.deepEqual(phases, ["diagnosing", "generating", "idle"]);
});

test("diagnosis and generation controls share one busy gate", () => {
  assert.equal(generationWorkflowIsBusy("idle", false, false), false);
  assert.equal(generationWorkflowIsBusy("diagnosing", false, false), true);
  assert.equal(generationWorkflowIsBusy("generating", false, false), true);
  assert.equal(generationWorkflowIsBusy("idle", true, false), true);
  assert.equal(generationWorkflowIsBusy("idle", false, true), true);
});

test("pack cost is visible before generation and includes standalone Selection Criteria", () => {
  assert.equal(packCreditCost(null), 1);
  assert.equal(packCreditCost({ documents: { selection_criteria: { requirement: "optional", format: "standalone" } } }), 1);
  assert.equal(packCreditCost({ documents: { selection_criteria: { requirement: "required", format: "standalone" } } }), 2);
});

test("credit status disables generation with a clear top-up path", () => {
  assert.deepEqual(creditStatus(2, 1, "support@example.com"), {
    blocked: false, message: "2 credits remaining · This pack uses 1 credit.",
  });
  assert.deepEqual(creditStatus(0, 1, "support@example.com"), {
    blocked: true, message: "Your free credits have been used. Contact support@example.com to add more credits.",
  });
  assert.deepEqual(creditStatus(1, 2, "support@example.com"), {
    blocked: true, message: "This pack needs 2 credits, but you have 1. Contact support@example.com to add more credits.",
  });
});
