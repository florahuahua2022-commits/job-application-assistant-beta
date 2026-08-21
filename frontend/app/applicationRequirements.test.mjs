import assert from "node:assert/strict";
import test from "node:test";

import { documentChoiceLabel, formatSubmissionLimit, requiredGeneratedDocumentTypes, requirementsHasUnknown, requirementsNeedConfirmation, unresolvedRequirementLabels } from "./applicationRequirements.ts";

test("submission limits use singular units for one", () => {
  assert.equal(formatSubmissionLimit({ value: 1, unit: "pages", scope: "document", constraint: "maximum", source_text: "one page" }), "Maximum 1 page");
  assert.equal(formatSubmissionLimit({ value: 1, unit: "words", scope: "document", constraint: "maximum", source_text: "one word" }), "Maximum 1 word");
  assert.equal(formatSubmissionLimit({ value: 1, unit: "characters", scope: "document", constraint: "maximum", source_text: "one character" }), "Maximum 1 character");
});

test("structured requirements alone control standalone document tabs and generation", () => {
  const requirements = {
    documents: {
      resume: { requirement: "required", format: "standalone", limit: null },
      cover_letter: { requirement: "required", format: "standalone", limit: null },
      selection_criteria: { requirement: "not_required", format: "not_applicable", limit: null },
    },
  };
  assert.deepEqual(requiredGeneratedDocumentTypes(requirements), ["tailored_resume", "cover_letter"]);
});

test("a material unknown format remains unresolved", () => {
  const requirements = {
    documents: {
      resume: { requirement: "required", format: "unknown", limit: null },
      cover_letter: { requirement: "not_required", format: "not_applicable", limit: null },
      selection_criteria: { requirement: "not_required", format: "not_applicable", limit: null },
    },
  };
  assert.equal(requirementsHasUnknown(requirements), true);
  assert.deepEqual(requiredGeneratedDocumentTypes(requirements), []);
  assert.deepEqual(unresolvedRequirementLabels(requirements), ["Resume format"]);
});

test("document choices use human authority labels", () => {
  assert.equal(documentChoiceLabel({ requirement: "required", format: "standalone", basis: "employer_explicit", limit: null }), "Required by employer");
  assert.equal(documentChoiceLabel({ requirement: "required", format: "standalone", basis: "user_confirmed", limit: null }), "Included by you");
  assert.equal(documentChoiceLabel({ requirement: "required", format: "standalone", basis: "product_default", limit: null }), "Recommended");
  assert.equal(documentChoiceLabel({ requirement: "not_required", format: "not_applicable", basis: "employer_explicit", limit: null }), "Not requested");
});

test("saved resolved corrections need no second confirmation", () => {
  const requirements = {
    review_status: "user_overridden",
    documents: {
      resume: { requirement: "required", format: "standalone", basis: "user_confirmed", limit: null },
      cover_letter: { requirement: "required", format: "standalone", basis: "user_confirmed", limit: null },
      selection_criteria: { requirement: "not_required", format: "not_applicable", basis: "user_confirmed", limit: null, criteria_count: null },
    },
  };
  assert.equal(requirementsHasUnknown(requirements), false);
  assert.equal(requirementsNeedConfirmation(requirements), false);
  assert.deepEqual(requiredGeneratedDocumentTypes(requirements), ["tailored_resume", "cover_letter"]);
  assert.equal(requirementsNeedConfirmation({ ...requirements, review_status: "needs_confirmation" }), true);
});
