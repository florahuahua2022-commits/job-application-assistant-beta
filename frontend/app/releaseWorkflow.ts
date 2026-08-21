export type ReleaseChecklist = {
  status: "draft" | "needs_attention" | "content_reviewed" | "artifact_verified" | "ready_to_apply" | "applied";
  ready: boolean;
  checks: {
    documents: { ready: boolean; required: string[] };
    details_confirmation: { ready: boolean };
    selection_confirmations: { ready: boolean };
    final_check: { ready: boolean; issues: ReleaseIssue[] };
    pack_review: { ready: boolean; current: boolean; result: PackReviewResult };
    ats: { ready: boolean; document_id: number | null; format: "docx" | "pdf"; template: string; result: AtsResult | null };
  };
  warnings: ReleaseIssue[];
};

export type ReleaseIssue = { code?: string; message: string; document_type?: string; blocks_release?: boolean };
export type PackReviewResult = { status?: "pass" | "fail"; skipped?: boolean; skip_reason?: string; blocks_release?: boolean; results?: { document_type: string; issues: ({ description: string; blocks_release?: boolean } & Record<string, unknown>)[] }[] };
export type AtsResult = { ready: boolean; status: "pass" | "fail"; document_id: number; format: "docx" | "pdf"; template: string; checks: { code: string; state: string; message: string; blocking: boolean }[]; keywords: { term: string; status: string; message: string; advisory: boolean }[] };

export function canGenerate(decisionStatus: string | undefined, detailsConfirmed: boolean): boolean {
  return decisionStatus === "ready" && detailsConfirmed;
}

export function artifactMatches(result: AtsResult | null, documentId: number | undefined, format: string, template: string): boolean {
  return Boolean(result?.ready && result.document_id === documentId && result.format === format && result.template === template);
}

export function releaseCanProceed(checklist: ReleaseChecklist | null): boolean {
  return checklist?.ready === true;
}
