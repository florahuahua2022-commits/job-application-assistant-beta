import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { parsedSelectionCriteria, preservedOrganisation, releaseFailureState, resumeEditorVersion, shouldExpireSession, uploadFailureState, withBusyReset } from "./betaOperations.ts";

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

test("job extraction preserves an existing organisation", () => {
  assert.equal(preservedOrganisation("Private", "Guessed Company"), "Private");
  assert.equal(preservedOrganisation("", "Explicit Company"), "Explicit Company");
});

test("pasted JD replaces stale identity and has a confirmed local reset", () => {
  const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
  const parse = page.slice(page.indexOf("async function parseFullJobAd()"), page.indexOf("function resetJobInput()"));
  assert.match(parse, /company: result.company \|\| ""/);
  assert.match(parse, /position_title: result.position_title \|\| ""/);
  const reset = page.slice(page.indexOf("function resetJobInput()"), page.indexOf("async function createApplication("));
  for (const setter of ["setJobFields", "setRawJobAd", "setAdWarnings", "setAdParseState", "setJobImportState"]) assert.ok(reset.includes(setter));
  assert.match(reset, /window.confirm/);
  assert.doesNotMatch(reset, /authenticatedFetch/);
  assert.match(page, /onClick=\{deleteJobInput\} disabled=\{adParseState === "parsing" \|\| jobImportState === "importing"\}/);
});
