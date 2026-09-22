import { createElement } from "react";

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

export function optionalBackupState<T>(status: number, value: unknown) {
  return { available: status >= 200 && status < 300, backups: Array.isArray(value) ? value as T[] : [] };
}

export function normaliseApplicationText<T extends Record<string, unknown>>(application: T) {
  return {
    ...application,
    company: typeof application.company === "string" ? application.company : "",
    position_title: typeof application.position_title === "string" ? application.position_title : "",
    job_description: typeof application.job_description === "string" ? application.job_description : "",
  };
}

export function preservedOrganisation(current: string, extracted: string): string {
  return current || extracted;
}

export function sourceDetailIsThin(action: string, context: string, result: string): boolean {
  return ([...new Set([action, context, result].map(value => value.trim()))].join(" ").match(/[\p{L}\p{N}]+(?:['-][\p{L}\p{N}]+)*/gu) || []).length < 20;
}

export function experienceDetailWarning(experience: Record<string, any>) {
  const responsibility = typeof experience.responsibility === "string" ? experience.responsibility : "";
  const context = typeof experience.context === "string" ? experience.context : "";
  const result = typeof experience.result === "string" ? experience.result : "";
  if (!sourceDetailIsThin(responsibility, context, experience.no_result_data ? "" : result)) return null;
  return createElement("p", { className: "requirementsWarnings full" },
    `${experience.role_title || "This experience"}: source detail may be limited. Describe your specific actions, systems/tools, volume or frequency, and an observed outcome if known. Include only facts you can support; numbers are optional.`);
}
