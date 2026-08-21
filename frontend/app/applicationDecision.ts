export type DecisionRequirement = {
  criteria_id: string;
  requirement_text: string;
  importance: "essential" | "desirable" | "unknown";
  hard_gate_status: "not_applicable" | "pass" | "fail" | "unverified";
  evidence_classification: "verified_match" | "adjacent_match" | "unverified_possible" | "confirmed_gap" | null;
  matched_evidence: string[];
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
};

export function decisionLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}
