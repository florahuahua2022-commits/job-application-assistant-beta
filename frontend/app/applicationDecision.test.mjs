import assert from "node:assert/strict";
import test from "node:test";

import { decisionLabel } from "./applicationDecision.ts";

test("decision labels are readable", () => {
  assert.equal(decisionLabel("apply_with_caveats"), "Apply with caveats");
  assert.equal(decisionLabel("verified_match"), "Verified match");
});
