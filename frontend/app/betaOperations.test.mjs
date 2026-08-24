import assert from "node:assert/strict";
import test from "node:test";
import { parsedSelectionCriteria, releaseFailureState, resumeEditorVersion, shouldExpireSession, uploadFailureState, withBusyReset } from "./betaOperations.ts";

test("Pack Review and ATS network failures clear busy state", async () => {
  for (const operation of ["pack", "ats"]) {
    let busy = operation;
    await assert.rejects(withBusyReset(async () => { throw new TypeError("network"); }, () => { busy = "idle"; }));
    assert.equal(busy, "idle");
  }
});

test("Resume upload network failure leaves the uploading state", () => {
  assert.equal(uploadFailureState(), "error");
});

test("failed checklist reload fails closed", () => {
  const state = releaseFailureState();
  assert.equal(state.ready, false); assert.equal(state.checklist, null); assert.equal(state.ats, null);
});

test("only 401 invokes centralized session expiry", () => {
  assert.equal(shouldExpireSession(401), true); assert.equal(shouldExpireSession(429), false); assert.equal(shouldExpireSession(502), false);
});

test("an in-place Resume upload changes the editor version", () => {
  assert.notEqual(
    resumeEditorVersion({ id: 2, updated_at: "2026-08-22T12:00:00Z" }),
    resumeEditorVersion({ id: 2, updated_at: "2026-08-22T13:05:35Z" }),
  );
  assert.equal(resumeEditorVersion(), "new");
});

test("a successful JD parse replaces rather than retains Selection Criteria", () => {
  assert.equal(parsedSelectionCriteria(""), "");
  assert.equal(parsedSelectionCriteria("NEW CRITERIA"), "NEW CRITERIA");
  assert.equal(parsedSelectionCriteria(undefined), "");
});
