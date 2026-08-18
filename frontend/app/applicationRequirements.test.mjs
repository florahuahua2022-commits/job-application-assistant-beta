import assert from "node:assert/strict";
import test from "node:test";

import { formatSubmissionLimit } from "./applicationRequirements.ts";

test("submission limits use singular units for one", () => {
  assert.equal(formatSubmissionLimit({ value: 1, unit: "pages", scope: "document", constraint: "maximum", source_text: "one page" }), "Maximum 1 page");
  assert.equal(formatSubmissionLimit({ value: 1, unit: "words", scope: "document", constraint: "maximum", source_text: "one word" }), "Maximum 1 word");
  assert.equal(formatSubmissionLimit({ value: 1, unit: "characters", scope: "document", constraint: "maximum", source_text: "one character" }), "Maximum 1 character");
});
