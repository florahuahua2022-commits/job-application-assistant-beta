import assert from "node:assert/strict";
import test from "node:test";
import { artifactMatches, canGenerate, releaseCanProceed } from "./releaseWorkflow.ts";

test("diagnosis and detail confirmation gate generation", () => {
  assert.equal(canGenerate("needs_confirmation", true), false);
  assert.equal(canGenerate("ready", false), false);
  assert.equal(canGenerate("ready", true), true);
});

test("generated or partially checked packs are not ready", () => {
  assert.equal(releaseCanProceed(null), false);
  assert.equal(releaseCanProceed({ ready: false }), false);
  assert.equal(releaseCanProceed({ ready: true }), true);
});

test("ATS verification is exact to document, format and template", () => {
  const result = { ready: true, document_id: 17, format: "docx", template: "classic" };
  assert.equal(artifactMatches(result, 17, "docx", "classic"), true);
  assert.equal(artifactMatches(result, 17, "pdf", "classic"), false);
  assert.equal(artifactMatches(result, 17, "docx", "modern"), false);
  assert.equal(artifactMatches(result, 18, "docx", "classic"), false);
});
