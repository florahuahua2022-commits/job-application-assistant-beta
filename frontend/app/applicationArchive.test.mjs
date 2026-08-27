import assert from "node:assert/strict";
import test from "node:test";
import { activeApplications, archivedApplications } from "./applicationArchive.ts";

const applications = [
  { id: 1, status: "draft", archived_at: null },
  { id: 2, status: "applied", archived_at: "2026-08-26T01:00:00Z" },
];

test("archived applications are excluded from active counts and lists", () => {
  assert.deepEqual(activeApplications(applications).map(({ id }) => id), [1]);
});

test("the archived view shows archived applications", () => {
  assert.deepEqual(archivedApplications(applications).map(({ id }) => id), [2]);
});

test("a restored application returns to the active list", () => {
  assert.deepEqual(activeApplications(applications.map((application) => application.id === 2 ? { ...application, archived_at: null } : application)).map(({ id }) => id), [1, 2]);
});
