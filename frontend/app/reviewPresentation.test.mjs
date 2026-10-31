import assert from "node:assert/strict";
import test from "node:test";
import * as presentation from "./releaseWorkflow.ts";

test("N18 presentation deduplicates the same finding but keeps different facts", () => {
  assert.equal(typeof presentation.groupReviewFindings, "function", "finding grouping is missing");
  const finding = { code: "reviewer_unsupported_claim", message: "Unsupported 30% result", severity: "error", document_type: "tailored_resume" };
  const groups = presentation.groupReviewFindings([finding, { ...finding }, { ...finding, message: "Unsupported 40% result" }]);
  assert.equal(groups.error.length, 2);
});

test("N14 Dayforce remains available in collapsed advisory data", () => {
  assert.equal(typeof presentation.groupReviewFindings, "function", "finding grouping is missing");
  const groups = presentation.groupReviewFindings([{ type: "generation_under_utilized", severity: "advisory", blocks_release: false, category: "consistency", description: "Dayforce is repeated in Technical Skills and Work Experience." }]);
  assert.equal(groups.advisory.length, 1);
  assert.equal(groups.error.length, 0);
});

test("N19 blank and pure No issue findings are hidden but mixed errors remain", () => {
  assert.equal(typeof presentation.groupReviewFindings, "function", "finding grouping is missing");
  const groups = presentation.groupReviewFindings([
    { message: "", severity: "error" }, { description: "No issue with content.", severity: "critical" },
    { description: "No issue except the phone number is wrong.", severity: "critical", blocks_release: true },
  ]);
  assert.equal(groups.error.length, 1);
  assert.match(groups.error[0].text, /phone/);
});

test("N17 confirmation reminder does not produce a generation error", () => {
  assert.equal(typeof presentation.groupReviewFindings, "function", "finding grouping is missing");
  const groups = presentation.groupReviewFindings([{ code: "application_requirements_confirmation_required", message: "Confirm employer requirements", severity: "advisory", blocks_release: false }]);
  assert.equal(groups.advisory.length, 1);
  assert.equal(groups.error.length, 0);
});

test("N20 warnings remain separate and true errors cannot be folded by display metadata", () => {
  assert.equal(typeof presentation.groupReviewFindings, "function", "finding grouping is missing");
  const groups = presentation.groupReviewFindings([
    { description: "Unknown check needs review", severity: "major", display_level: "warning", blocks_release: true },
    { description: "Unsupported experience", severity: "critical", display_level: "advisory", blocks_release: true },
  ]);
  assert.equal(groups.warning.length, 1);
  assert.equal(groups.error.length, 1);
  assert.equal(groups.advisory.length, 0);
});
