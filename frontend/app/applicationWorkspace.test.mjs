import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

test("job and resume editing share one collapsed entry above the main workflow", () => {
  const top = page.slice(page.indexOf('<div className="selectedJob">'), page.indexOf('<p className="helper"><strong>Steps:'));
  assert.match(top, /<details className="jobEditPanel applicationEditOptions"[^>]*>\s*<summary>Edit job or resume details<\/summary>/);
  assert.ok(top.indexOf("Edit saved job details") > top.indexOf("Edit job or resume details"));
  assert.ok(top.indexOf("Change resume materials") > top.indexOf("Edit saved job details"));
  assert.doesNotMatch(top, /Previous document versions/);
});

test("previous document versions remain available below the current documents", () => {
  const history = page.indexOf("<summary>Previous document versions</summary>");
  assert.ok(history > page.indexOf("Your required application documents will appear here."));
  assert.match(page.slice(history - 350, history + 100), /onToggle=\{async \(event\) => \{/);
  assert.match(page.slice(history, history + 700), /downloadDocument\("docx", document\)/);
  assert.match(page.slice(history, history + 800), /downloadDocument\("pdf", document\)/);
});
