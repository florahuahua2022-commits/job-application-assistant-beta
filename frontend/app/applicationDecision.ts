export type DecisionRequirement = {
  criteria_id: string;
  requirement_text: string;
  importance: "essential" | "desirable" | "unknown";
  hard_gate_status: "not_applicable" | "pass" | "fail" | "unverified";
  evidence_classification: "verified_match" | "adjacent_match" | "unverified_possible" | "confirmed_gap" | null;
  matched_evidence: string[];
  supported_atoms?: string[];
  unsupported_atoms?: string[];
  risk: "low" | "medium" | "high";
  recommended_action: "use" | "reframe" | "ask_user" | "disclose" | "omit";
  disclosure_strategy: "none" | "bridge" | "explicit_gap";
};

export type DecisionQuestion = {
  question_id: string;
  criteria_id: string;
  prompt: string;
  material: boolean;
  answer: boolean | null;
  provenance?: string | null;
};

export type ApplicationDecision = {
  status: "needs_confirmation" | "ready" | "blocked";
  application_recommendation: "apply" | "apply_with_caveats" | "reconsider" | "do_not_apply";
  requirements: DecisionRequirement[];
  questions: DecisionQuestion[];
  blocking_issues: { criteria_id: string; code: string; message: string }[];
  diagnosed_at?: string;
};

export function decisionLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function atomLabel(atom: string): string {
  const value = atom.replace(/^(?:term|tool):/, "").replaceAll("_", " ");
  return value === "admin" ? "administration" : value === "warehouse" ? "warehouse knowledge" : value;
}

export function adjacentEvidenceGuidance(item: Pick<DecisionRequirement, "evidence_classification" | "supported_atoms" | "unsupported_atoms">): string | null {
  if (item.evidence_classification !== "adjacent_match") return null;
  const supported = (item.supported_atoms || []).map(atomLabel).join(", ");
  const unsupported = (item.unsupported_atoms || []).map(atomLabel).join(", ");
  if (!supported && !unsupported) return null;
  const parts = [
    supported && `${supported} ${item.supported_atoms?.length === 1 ? "is" : "are"} supported`,
    unsupported && `${unsupported} ${item.unsupported_atoms?.length === 1 ? "is" : "are"} not evidenced and must not be claimed`,
  ].filter(Boolean);
  return `Use supported transferable evidence only: ${parts.join("; ")}.`;
}
