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
  t.mock.timers.tick(30_001);
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: null });
  assert.equal(signal.aborted, true);
  complete(Response.json({ id: 9 }));
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: null });
});

test("timeout also covers reading the response body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const pending = saveResponse(async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) }));
  await Promise.resolve();
  t.mock.timers.tick(30_001);
  assert.deepEqual(await pending, { ok: false, kind: "timeout", status: 200 });
});
