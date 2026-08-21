export type RequirementsReviewStatus = "needs_confirmation" | "confirmed" | "user_overridden";
export type RequirementValue = "required" | "optional" | "not_required" | "unknown";
export type DocumentFormat = "standalone" | "embedded_in_cover_letter" | "embedded_in_resume" | "portal_fields" | "not_applicable" | "unknown";
export type RequirementBasis = "employer_explicit" | "user_confirmed" | "product_default" | "unknown";
export type LimitUnit = "words" | "characters" | "pages";
export type LimitScope = "document" | "per_criterion" | "combined_documents";
export type LimitConstraint = "maximum" | "minimum" | "exact" | "recommended";

export type SubmissionLimit = {
  value: number;
  unit: LimitUnit;
  scope: LimitScope;
  constraint: LimitConstraint;
  source_text: string;
};

export type DocumentRequirement = {
  requirement: RequirementValue;
  format: DocumentFormat;
  basis: RequirementBasis;
  limit: SubmissionLimit | null;
  criteria_count?: number | null;
};

export type ApplicationRequirements = {
  schema_version: string;
  review_status: RequirementsReviewStatus;
  source: string;
  documents: {
    resume: DocumentRequirement;
    cover_letter: DocumentRequirement;
    selection_criteria: DocumentRequirement;
  };
  additional_documents: string[];
  source_text: string;
  source_excerpt: string;
  completeness?: "complete" | "incomplete";
  warnings: string[];
};

export type ApplicationRequirementsResponse = {
  application_id: number;
  requirements: ApplicationRequirements;
};

export type ApplicationRequirementsCorrectionDraft = {
  documents: ApplicationRequirements["documents"];
  additional_documents: string[];
};

const requirementLabels: Record<RequirementValue, string> = {
  required: "Required",
  optional: "Optional",
  not_required: "Not required",
  unknown: "Unknown",
};

const formatLabels: Record<DocumentFormat, string> = {
  standalone: "Standalone",
  embedded_in_cover_letter: "Embedded in Cover Letter",
  embedded_in_resume: "Embedded in Resume",
  portal_fields: "Portal fields",
  not_applicable: "Not applicable",
  unknown: "Unknown",
};

const statusLabels: Record<RequirementsReviewStatus, string> = {
  needs_confirmation: "Needs confirmation",
  confirmed: "Confirmed",
  user_overridden: "Corrected by you",
};

export function formatRequirementLabel(value: RequirementValue): string {
  return requirementLabels[value];
}

export function formatDocumentFormat(value: DocumentFormat): string {
  return formatLabels[value];
}

export function documentChoiceLabel(document: DocumentRequirement): string {
  if (document.requirement === "unknown" || document.basis === "unknown") return "Not determined";
  if (document.requirement === "not_required") return "Not requested";
  if (document.basis === "employer_explicit") return document.requirement === "optional" ? "Optional from employer" : "Required by employer";
  if (document.basis === "user_confirmed") return "Included by you";
  return "Recommended";
}

export function getRequirementsStatusLabel(value: RequirementsReviewStatus): string {
  return statusLabels[value];
}

export function formatSubmissionLimit(limit: SubmissionLimit | null): string {
  if (!limit) return "No limit specified";
  const constraint = limit.constraint === "maximum" ? "Maximum" : limit.constraint === "minimum" ? "Minimum" : limit.constraint === "recommended" ? "Recommended" : "Exactly";
  const scope = limit.scope === "per_criterion" ? " per criterion" : limit.scope === "combined_documents" ? " across combined documents" : "";
  const unit = limit.value === 1 ? { pages: "page", words: "word", characters: "character" }[limit.unit] : limit.unit;
  return `${constraint} ${limit.value.toLocaleString()} ${unit}${scope}`;
}

export function requirementsHasUnknown(requirements: ApplicationRequirements): boolean {
  return Object.values(requirements.documents).some((document) => document.requirement === "unknown" || (["required", "optional"].includes(document.requirement) && document.format === "unknown"));
}

export function requirementsNeedConfirmation(requirements: ApplicationRequirements): boolean {
  return requirements.review_status === "needs_confirmation";
}

export function unresolvedRequirementLabels(requirements: ApplicationRequirements): string[] {
  const names = { resume: "Resume", cover_letter: "Cover Letter", selection_criteria: "Selection Criteria" };
  return (Object.keys(names) as (keyof typeof names)[]).flatMap((name) => {
    const document = requirements.documents[name];
    if (document.requirement === "unknown") return [`${names[name]} requirement`];
    if (["required", "optional"].includes(document.requirement) && document.format === "unknown") return [`${names[name]} format`];
    return [];
  });
}

export function requiredGeneratedDocumentTypes(requirements: ApplicationRequirements | null): ("tailored_resume" | "cover_letter" | "selection_criteria")[] {
  if (!requirements) return [];
  const mapping = { resume: "tailored_resume", cover_letter: "cover_letter", selection_criteria: "selection_criteria" } as const;
  return (Object.keys(mapping) as (keyof typeof mapping)[]).filter((key) => {
    const document = requirements.documents[key];
    return document.requirement === "required" && document.format === "standalone";
  }).map((key) => mapping[key]);
}

export function createCorrectionDraft(requirements: ApplicationRequirements): ApplicationRequirementsCorrectionDraft {
  return {
    documents: structuredClone(requirements.documents),
    additional_documents: [...requirements.additional_documents],
  };
}
