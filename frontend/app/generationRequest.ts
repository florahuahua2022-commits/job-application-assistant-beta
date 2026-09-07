type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type RequestPayload = { application_id: number; document_type: string; pack_id: string };

// Keep the request ID across a lost connection or page refresh; Retry resumes it.
export async function requestGeneratedDocument(
  api: string, fetcher: Fetcher, payload: RequestPayload, storage: Storage,
  pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
): Promise<Response> {
  const key = `generation:${api}:${payload.application_id}:${payload.document_type}`;
  const request = { ...payload, pack_id: storage.getItem(key) || payload.pack_id };
  storage.setItem(key, request.pack_id);
  const statusUrl = `${api}/applications/${request.application_id}/generation-requests/${request.pack_id}/${request.document_type}`;
  let submitted = false;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (!submitted) {
      let response: Response | undefined;
      try {
        response = await fetcher(`${api}/generate?background=true`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request), signal: AbortSignal.timeout(20_000),
        });
      } catch { /* A lost acknowledgement is safe to retry with the same ID. */ }
      if (response && response.status >= 400 && response.status < 500) {
        storage.removeItem(key);
        return response;
      }
      submitted = response?.status === 202;
    }
    let response: Response | undefined;
    try {
      response = await fetcher(statusUrl, { signal: AbortSignal.timeout(20_000), cache: "no-store" });
    } catch { /* Poll again after a temporary connection failure. */ }
    if (response?.ok) {
      const state = await response.json();
      submitted = true;
      if (state.status === "completed" && state.document) {
        storage.removeItem(key);
        return Response.json(state.document);
      }
      if (state.status === "failed") {
        storage.removeItem(key);
        return Response.json({ detail: { message: state.message || "Generation failed. Please retry.", document_id: state.document_id } }, { status: 502 });
      }
    } else if (response?.status === 401 || response?.status === 403) {
      return response;
    } else if (response?.status === 404) {
      submitted = false;
    }
    await pause(5_000);
  }
  throw new Error("Generation has not finished, or its status could not be reached. Your request is saved; Retry will check the same request without starting another generation.");
}
