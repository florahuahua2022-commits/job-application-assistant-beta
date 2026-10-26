export type SaveFailure = { ok: false; kind: "http" | "network" | "timeout" | "invalid_response"; status: number | null };
export type SaveResult<T> = { ok: true; data: T } | SaveFailure;

export async function saveResponse<T extends { id: number }>(
  request: (signal: AbortSignal) => Promise<Response>, timeoutMs = 30_000,
): Promise<SaveResult<T>> {
  const controller = new AbortController();
  let status: number | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      (async (): Promise<SaveResult<T>> => {
        const response = await request(controller.signal);
        status = response.status;
        if (!response.ok) return { ok: false, kind: "http", status };
        const data = await response.json();
        if (!data || Array.isArray(data) || !Number.isInteger(data.id) || data.id <= 0) {
          return { ok: false, kind: "invalid_response", status };
        }
        return { ok: true, data };
      })(),
      new Promise<SaveFailure>((resolve) => {
        timer = setTimeout(() => {
          resolve({ ok: false, kind: "timeout", status });
          controller.abort();
        }, timeoutMs);
      }),
    ]);
  } catch {
    return { ok: false, kind: status === null ? "network" : "invalid_response", status };
  } finally {
    clearTimeout(timer);
  }
}
