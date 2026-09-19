import test from "node:test";
import assert from "node:assert/strict";
import { candidateExperienceDraft, excludedReviewIssues, mergeResumeReviewIssues, resumeSaveFailure, resumeSaveSuccessMessage, reviewReasonMessage, unresolvedReviewIssues, unlinkedReviewIssues } from "./resumeReview.ts";

test("review reason codes are translated for ordinary users", () => {
  assert.equal(
    reviewReasonMessage("duty_shaped_role_title"),
    "The role title looks like a description of duties rather than a job title.",
  );
  assert.equal(
    reviewReasonMessage("possible_merged_experiences"),
    "This entry may contain more than one job and should be split into separate experiences.",
  );
  assert.equal(
    reviewReasonMessage("internal_future_reason"),
    "The extracted experience structure may be inaccurate.",
  );
});

test("a rejected save returns review details without replacing the user's draft", () => {
  const draft = [{ id: "draft-edit", role_title: "Corrected title" }];
  const result = resumeSaveFailure(draft, {
    code: "master_resume_experience_needs_review",
    message: "Review the highlighted experience.",
    experiences: [{ index: 1, id: "draft-edit", role_title: "Corrected title", review_reasons: ["Still needs attention."] }],
  });

  assert.strictEqual(result.experiences, draft);
  assert.equal(result.message, "Review the highlighted experience.");
  assert.equal(result.reviewIssues[0].id, "draft-edit");
});

test("missing source experiences remain visible even when no editor card exists", () => {
  const missing = {
    index: 6, id: null, role_title: "Independent Support Worker",
    organization: "Self-employed via Mable", time_period_text: "August 2025 - January 2026",
    source_excerpt: "Self-employed via Mable August 2025 - January 2026\nIndependent Support Worker",
    review_reasons: ["This work experience appears in the Resume text but is missing from the structured experience list."],
  };

  assert.deepEqual(unlinkedReviewIssues([missing]), [missing]);
});

test("page-load scan keeps uncovered experiences alongside persisted review flags", () => {
  const persisted = [{ index: 2, id: "bad-title", role_title: "Provided support...", review_reasons: ["Title needs review."] }];
  const uncovered = [{
    index: 10, id: null, role_title: "Independent Support Worker", organization: "Self-employed via Mable",
    time_period_text: "August 2025 - January 2026", source_excerpt: "Self-employed via Mable...",
    review_reasons: ["This work experience appears in the Resume text but is missing from the structured experience list."],
  }];

  assert.deepEqual(mergeResumeReviewIssues(persisted, uncovered), [...persisted, ...uncovered]);
  assert.deepEqual(mergeResumeReviewIssues(persisted, [...uncovered, persisted[0]]), [...persisted, ...uncovered]);
});

test("uncovered candidates support add, exclude and restore without inventing duties", () => {
  const candidate = {
    index: 10, id: null, candidate_id: "EX123", status: "unresolved",
    role_title: "Utility", organization: "Sodex", time_period_text: "December 2023 - April 2024",
    source_excerpt: "Sodex: Utility, December 2023 - April 2024.", review_reasons: ["Missing."],
  };
  assert.deepEqual(unresolvedReviewIssues([candidate]), [candidate]);
  assert.deepEqual(excludedReviewIssues([{ ...candidate, status: "excluded_by_user" }]), [{ ...candidate, status: "excluded_by_user" }]);
  assert.deepEqual(candidateExperienceDraft(candidate), {
    role_title: "Utility", organization: "Sodex", time_period_text: "December 2023 - April 2024", responsibility: "",
  });
});

test("successful save explains unresolved candidates still block downstream work", () => {
  assert.equal(resumeSaveSuccessMessage(3), "Master Resume saved. 3 work experiences still need your decision before documents can be generated.");
  assert.equal(resumeSaveSuccessMessage(0), "Master Resume saved. You only need to update it when your experience changes.");
});
