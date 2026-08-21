import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { activationIntent, activationTransition } from "./authActivation.ts";

test("valid invite and recovery callbacks reach password setup", () => {
  assert.deepEqual(activationIntent("https://app.example/?code=abc&type=invite"), { mode: "password_setup", code: "abc" });
  assert.equal(activationIntent("https://app.example/#access_token=a&refresh_token=b&type=recovery").mode, "password_setup");
});

test("invalid or expired callback shows a useful failure", () => {
  const result = activationIntent("https://app.example/?error=access_denied&error_description=Link+expired");
  assert.equal(result.mode, "error"); assert.equal(result.message, "Link expired");
});

test("activation transition completes password setup", () => {
  let state = activationTransition({ mode: "idle" }, "start");
  state = activationTransition(state, "session_ready");
  state = activationTransition(state, "save");
  state = activationTransition(state, "success");
  assert.equal(state.mode, "complete");
});

test("the frontend exposes no public signup control or call", async () => {
  const source = await readFile(new URL("./page.tsx", import.meta.url), "utf8");
  assert.equal(source.includes("auth.signUp"), false);
  assert.equal(source.includes("Create account"), false);
});
