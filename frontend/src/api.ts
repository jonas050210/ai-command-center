// API client — same-origin relative URLs only (works in dev via Vite
// proxy and in production served by FastAPI). SSE via fetch streaming.

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const err = body?.error ?? {};
    return new ApiError(err.code ?? "ERROR", err.message ?? `HTTP ${res.status}`, res.status);
  } catch {
    return new ApiError("HTTP_" + res.status, `Request failed with status ${res.status}`, res.status);
  }
}

export async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

export async function sendJSON<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

/**
 * Stream Server-Sent Events from a POST endpoint.
 * Returns when the stream completes; supports AbortController.
 */
export async function streamSSE<T = unknown>(
  url: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: signal ?? null,
  });
  if (!res.ok) throw await parseError(res);
  const reader = res.body?.getReader();
  if (!reader) throw new ApiError("STREAM_UNSUPPORTED", "This browser cannot read response streams.", 0);

  const decoder = new TextDecoder();
  let buffer = "";
  const handleBlock = (block: string) => {
    for (const line of block.split("\n")) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)) as T);
        } catch {
          /* skip malformed chunk */
        }
      }
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) if (part.trim()) handleBlock(part);
  }
  if (buffer.trim()) handleBlock(buffer);
}
