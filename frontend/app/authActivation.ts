export type ActivationIntent = { mode: "none" | "password_setup" | "error"; code?: string; message?: string };
export type ActivationState = { mode: "idle" | "checking" | "password_setup" | "saving" | "complete" | "error"; message?: string };

export function activationIntent(url: string): ActivationIntent {
  const parsed = new URL(url);
  const hash = new URLSearchParams(parsed.hash.replace(/^#/, ""));
  const error = parsed.searchParams.get("error_description") || hash.get("error_description") || parsed.searchParams.get("error") || hash.get("error");
  if (error) return { mode: "error", message: decodeURIComponent(error.replaceAll("+", " ")) };
  const type = parsed.searchParams.get("type") || hash.get("type");
  const code = parsed.searchParams.get("code") || undefined;
  const hasImplicitSession = Boolean(hash.get("access_token") && hash.get("refresh_token"));
  if (code || hasImplicitSession || type === "invite" || type === "recovery") return { mode: "password_setup", code };
  return { mode: "none" };
}

export function activationTransition(state: ActivationState, event: "start" | "session_ready" | "save" | "success" | "failure", message?: string): ActivationState {
  if (event === "start") return { mode: "checking" };
  if (event === "session_ready") return { mode: "password_setup" };
  if (event === "save") return { mode: "saving" };
  if (event === "success") return { mode: "complete" };
  if (event === "failure") return { mode: "error", message: message || "This invitation or recovery link is invalid or has expired." };
  return state;
}
