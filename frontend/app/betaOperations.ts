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
