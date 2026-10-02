export type GenerationPhase = "idle" | "diagnosing" | "generating";
export type DiagnosisStatus = "ready" | "needs_confirmation" | "blocked";

type CreditRequirements = { documents?: { selection_criteria?: { requirement?: string; format?: string } } };

export function packCreditCost(requirements: CreditRequirements | null): 1 | 2 {
  const selection = requirements?.documents?.selection_criteria;
  return selection?.requirement === "required" && selection.format === "standalone" ? 2 : 1;
}

export function creditStatus(balance: number, cost: number, contact: string) {
  if (balance >= cost) return { blocked: false, message: `${balance} credit${balance === 1 ? "" : "s"} remaining · This pack uses ${cost} credit${cost === 1 ? "" : "s"}.` };
  if (balance === 0) return { blocked: true, message: `Your free credits have been used. Contact ${contact} to add more credits.` };
  return { blocked: true, message: `This pack needs ${cost} credits, but you have ${balance}. Contact ${contact} to add more credits.` };
}

export function generationWorkflowIsBusy(phase: GenerationPhase, generating: boolean, diagnosing: boolean): boolean {
  return phase !== "idle" || generating || diagnosing;
}

export async function diagnoseThenGenerate<TDecision extends { status: DiagnosisStatus }, TValue>(
  diagnose: () => Promise<TDecision>,
  generate: () => Promise<TValue>,
  setPhase: (phase: GenerationPhase) => void,
): Promise<{ decision: TDecision; generated: false } | { decision: TDecision; generated: true; value: TValue }> {
  setPhase("diagnosing");
  try {
    const decision = await diagnose();
    if (decision.status !== "ready") return { decision, generated: false };
    setPhase("generating");
    return { decision, generated: true, value: await generate() };
  } finally {
    setPhase("idle");
  }
}
