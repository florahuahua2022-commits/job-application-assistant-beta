export type ResumeReviewIssue = {
  index: number;
  id?: string | null;
  role_title: string;
  organization?: string;
  time_period_text?: string;
  source_excerpt?: string;
  review_reasons: string[];
};

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

export function unlinkedReviewIssues(issues: ResumeReviewIssue[]) {
  return issues.filter((issue) => Boolean(issue.source_excerpt));
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
