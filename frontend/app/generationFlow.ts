export type GenerationPhase = "idle" | "diagnosing" | "generating";
export type DiagnosisStatus = "ready" | "needs_confirmation" | "blocked";

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
