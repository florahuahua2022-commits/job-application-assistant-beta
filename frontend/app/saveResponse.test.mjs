import assert from "node:assert/strict";
import test from "node:test";
import { saveResponse } from "./saveResponse.ts";

test("save confirms only an object with a persisted id", async () => {
  assert.deepEqual(await saveResponse(async () => Response.json({ id: 7, name: "Synthetic" })),
    { ok: true, data: { id: 7, name: "Synthetic" } });
  for (const body of [null, [], {}, { id: null }, { id: "7" }]) {
    assert.deepEqual(await saveResponse(async () => Response.json(body)),
      { ok: false, kind: "invalid_response", status: 200 });
  }
});

test("HTTP failure retains status but never server details", async () => {
  for (const status of [422, 500]) {
    assert.deepEqual(await saveResponse(async () => Response.json({ detail: "PRIVATE_SERVER_DETAIL" }, { status })),
      { ok: false, kind: "http", status });
  }
});

test("network and invalid JSON failures are contained", async () => {
  assert.deepEqual(await saveResponse(async () => { throw new TypeError("PRIVATE_EXCEPTION"); }),
    { ok: false, kind: "network", status: null });
  assert.deepEqual(await saveResponse(async () => new Response("not-json")),
    { ok: false, kind: "invalid_response", status: 200 });
  assert.deepEqual(await saveResponse(async () => new Response(null, { status: 204 })),
    { ok: false, kind: "invalid_response", status: 204 });
});

test("timeout ends waiting, aborts the request, and ignores a late response", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let signal, complete;
  const pending = saveResponse((input) => {
    signal = input;
    return new Promise((resolve) => { complete = resolve; });
  });
  let settled = false;
  pending.then(() => { settled = true; });
  t.mock.timers.tick(89_999);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(settled, false);
  assert.equal(signal.aborted, false);
  t.mock.timers.tick(2);
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: null });
  assert.equal(signal.aborted, true);
  complete(Response.json({ id: 9 }));
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: null });
});

test("timeout also covers reading the response body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let headers;
  const pending = saveResponse(() => new Promise((resolve) => { headers = resolve; }));
  let settled = false;
  pending.then(() => { settled = true; });
  t.mock.timers.tick(60_000);
  headers({ ok: true, status: 200, json: () => new Promise(() => {}) });
  await Promise.resolve();
  await Promise.resolve();
  t.mock.timers.tick(29_999);
  await Promise.resolve();
  assert.equal(settled, false);
  t.mock.timers.tick(2);
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: 200 });
});

test("slow notice starts at eight seconds and a sixty-second response still succeeds", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let complete, signal, slowCalls = 0;
  const pending = saveResponse((input) => {
    signal = input;
    return new Promise((resolve) => { complete = resolve; });
  }, undefined, () => { slowCalls++; });
  t.mock.timers.tick(7_999);
  assert.equal(slowCalls, 0);
  t.mock.timers.tick(1);
  assert.equal(slowCalls, 1);
  t.mock.timers.tick(52_000);
  assert.equal(signal.aborted, false);
  complete(Response.json({ id: 3 }));
  assert.deepEqual(await pending, { ok: true, data: { id: 3 } });
  t.mock.timers.tick(90_001);
  assert.equal(signal.aborted, false);
  assert.equal(slowCalls, 1);
});

test("fast success and failure cancel both notice and timeout timers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const status of [200, 422, 500]) {
    let slowCalls = 0, signal;
    const result = await saveResponse(async (input) => {
      signal = input;
      return Response.json({ id: 4 }, { status });
    }, undefined, () => { slowCalls++; });
    assert.equal(result.ok, status === 200);
    t.mock.timers.tick(90_001);
    assert.equal(slowCalls, 0);
    assert.equal(signal.aborted, false);
  }
});
