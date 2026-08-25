export async function withBusyReset<T>(operation: () => Promise<T>, reset: () => void): Promise<T> {
  try { return await operation(); } finally { reset(); }
}

export function shouldExpireSession(status: number): boolean {
  return status === 401;
}

export function releaseFailureState() {
  return { checklist: null, packReview: null, ats: null, ready: false } as const;
}

export function uploadFailureState() {
  return "error" as const;
}

export function resumeEditorVersion(resume?: { id: number; updated_at: string }) {
  return resume ? `${resume.id}:${resume.updated_at}` : "new";
}

export function parsedSelectionCriteria(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function preservedOrganisation(current: string, extracted: string): string {
  return current || extracted;
}
