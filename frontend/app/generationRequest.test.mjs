import test from "node:test";
import assert from "node:assert/strict";
import { requestGeneratedDocument } from "./generationRequest.ts";

const payload = { application_id: 1, document_type: "tailored_resume", pack_id: "new-request" };
function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key), values };
}

test("lost acknowledgement and polling connection recover without a second generation", async () => {
  const saved = storage();
  let posts = 0, polls = 0;
  const response = await requestGeneratedDocument("https://api", async (url, init) => {
    if (init.method === "POST") { posts++; throw new TypeError("Failed to fetch"); }
    polls++;
    if (polls === 1) return Response.json({ status: "running" });
    if (polls === 2) throw new TypeError("Failed to fetch");
    return Response.json({ status: "completed", document: { id: 7, content: "draft" } });
  }, payload, saved, async () => {});
  assert.equal(posts, 1);
  assert.equal((await response.json()).id, 7);
  assert.equal(saved.values.size, 0);
});

test("Retry resumes the saved ID and surfaces a terminal failure with the retained draft", async () => {
  const saved = storage();
  saved.setItem("generation:https://api:1:tailored_resume", "existing-request");
  const response = await requestGeneratedDocument("https://api", async (url, init) => {
    if (init.method === "POST") {
      assert.equal(JSON.parse(init.body).pack_id, "existing-request");
      return Response.json({ status: "failed" }, { status: 202 });
    }
    assert.match(url, /existing-request/);
    return Response.json({ status: "failed", message: "Review unavailable", document_id: 7 });
  }, payload, saved, async () => {});
  assert.equal(response.status, 502);
  assert.deepEqual((await response.json()).detail, { message: "Review unavailable", document_id: 7 });
  assert.equal(saved.values.size, 0);
});
