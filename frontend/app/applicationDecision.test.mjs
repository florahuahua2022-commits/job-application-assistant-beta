import assert from "node:assert/strict";
import test from "node:test";

import { adjacentEvidenceGuidance, decisionLabel } from "./applicationDecision.ts";

test("decision labels are readable", () => {
  assert.equal(decisionLabel("apply_with_caveats"), "Apply with caveats");
  assert.equal(decisionLabel("verified_match"), "Verified match");
});

test("adjacent matches explain supported and unsupported evidence atoms", () => {
  assert.equal(
    adjacentEvidenceGuidance({
      evidence_classification: "adjacent_match",
      supported_atoms: ["term:admin"],
      unsupported_atoms: ["term:warehouse"],
    }),
    "Use supported transferable evidence only: administration is supported; warehouse knowledge is not evidenced and must not be claimed.",
  );
});

test("non-adjacent cards do not show adjacent evidence guidance", () => {
  for (const evidence_classification of ["verified_match", "unverified_possible", "confirmed_gap", null]) {
    assert.equal(adjacentEvidenceGuidance({
      evidence_classification,
      supported_atoms: ["term:admin"],
      unsupported_atoms: ["term:warehouse"],
    }), null);
  }
});
