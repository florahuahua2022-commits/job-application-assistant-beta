import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
const documentCard = page.slice(page.indexOf('<section className={`requirementsCard'), page.indexOf('{generationFailure &&'));

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

test("document choices and generation gates stay outside collapsed document details", () => {
  const choices = documentCard.indexOf('<div className="documentChoices">');
  const details = documentCard.indexOf('<details className="requirementsSource documentDetails"');
  const generate = documentCard.indexOf('<div className="generateAction">');
  assert.ok(choices > 0 && details > choices && generate > details);
  assert.match(documentCard.slice(0, details), /unknownRequirementNotice/);
  assert.match(documentCard.slice(details, generate), /Advanced format options/);
  assert.match(documentCard.slice(generate), /currentCreditStatus\.message/);
  assert.match(documentCard.slice(generate), /onClick=\{generatePack\}/);
});

test("document details identify requirements and source without claiming Evidence Match", () => {
  const details = documentCard.slice(documentCard.indexOf('<details className="requirementsSource documentDetails"'), documentCard.indexOf('{applicationRequirements.additional_documents.length'));
  assert.match(details, /<summary>Document details<\/summary>/);
  assert.match(details, /<strong>Document requirements and source<\/strong>/);
  assert.match(details, /<dt>Choice<\/dt>/);
  assert.match(details, /<dt>Format<\/dt>/);
  assert.match(details, /<dt>Limit<\/dt>/);
  assert.match(details, /applicationRequirements\.source_excerpt/);
  assert.match(details, /<summary>Show full source<\/summary>/);
  assert.doesNotMatch(documentCard, /View matching basis/);
});

test("unresolved document requirements expose the format correction path", () => {
  assert.match(documentCard, /<details className="requirementsSource documentDetails" open=\{requirementsHasUnknown\(applicationRequirements\) \|\| isEditingRequirements\}>/);
});
