export type ResumeReviewIssue = {
  index: number;
  id?: string | null;
  role_title: string;
  organization?: string;
  time_period_text?: string;
  source_excerpt?: string;
  candidate_id?: string;
  status?: "unresolved" | "excluded_by_user";
  review_reasons: string[];
};

export function normaliseExperienceText<T extends Record<string, any>>(experience: T) {
  return {
    ...experience,
    responsibility: typeof experience.responsibility === "string" ? experience.responsibility : "",
    context: typeof experience.context === "string" ? experience.context : "",
    result: typeof experience.result === "string" ? experience.result : "",
  };
}

export type ResumeReviewDetail = {
  code?: string;
  message?: string;
  experiences?: ResumeReviewIssue[];
};

const reasonMessages: Record<string, string> = {
  duty_shaped_role_title: "The role title looks like a description of duties rather than a job title.",
  excluded_role_header: "A possible job title immediately before this entry may have been left out.",
  possible_merged_experiences: "This entry may contain more than one job and should be split into separate experiences.",
};

export function reviewReasonMessage(reason: string) {
  return reasonMessages[reason] || "The extracted experience structure may be inaccurate.";
}

export function reviewIssuesFromExperiences(experiences: any[]): ResumeReviewIssue[] {
  return experiences.flatMap((experience, offset) => experience?.needs_review ? [{
    index: offset + 1,
    id: experience.id || experience.evidence_id || null,
    role_title: experience.role_title || "",
    organization: experience.organization || "",
    review_reasons: (experience.review_reasons || []).map(reviewReasonMessage),
  }] : []);
}

export function mergeResumeReviewIssues(persisted: ResumeReviewIssue[], scanned: ResumeReviewIssue[]) {
  const seen = new Set<string>();
  return [...persisted, ...scanned].filter((issue) => {
    const key = issue.id || [issue.organization, issue.role_title, issue.time_period_text].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function unlinkedReviewIssues(issues: ResumeReviewIssue[]) {
  return issues.filter((issue) => Boolean(issue.source_excerpt));
}

export function unresolvedReviewIssues(issues: ResumeReviewIssue[]) {
  return issues.filter((issue) => issue.status === "unresolved");
}

export function excludedReviewIssues(issues: ResumeReviewIssue[]) {
  return issues.filter((issue) => issue.status === "excluded_by_user");
}

export function candidateExperienceDraft(issue: ResumeReviewIssue) {
  return {
    role_title: issue.role_title,
    organization: issue.organization || "",
    time_period_text: issue.time_period_text || "",
    responsibility: "",
  };
}

export function resumeSaveSuccessMessage(unresolvedCount: number) {
  return unresolvedCount
    ? `Master Resume saved. ${unresolvedCount} work ${unresolvedCount === 1 ? "experience still needs" : "experiences still need"} your decision before documents can be generated.`
    : "Master Resume saved. You only need to update it when your experience changes.";
}

export function resumeSaveState(result: any) {
  let experiences = [];
  try { experiences = JSON.parse(result.experiences_json || "[]"); } catch { /* Keep the editor usable. */ }
  return {
    resume: result,
    experiences: experiences.map(normaliseExperienceText),
    reviewIssues: result.review_experiences || [],
    contentCheck: result.content_check || null,
  };
}

export function applicationSourcesOpen(sources: { acquisition_status: string; extraction_status: string }[]) {
  return sources.some((source) =>
    ["discovered", "unavailable", "requires_auth", "failed"].includes(source.acquisition_status)
    || source.extraction_status === "failed"
    || source.extraction_status === "partial"
  );
}

export function resumeSaveFailure<T>(experiences: T, detail: ResumeReviewDetail | string | undefined) {
  const structured = typeof detail === "object" && detail?.code === "master_resume_experience_needs_review";
  return {
    experiences,
    reviewIssues: structured ? detail.experiences || [] : [],
    message: structured ? detail.message || "Review the highlighted work experiences before saving."
      : typeof detail === "string" ? detail : "Could not save the Master Resume.",
  };
}
