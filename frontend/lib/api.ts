/** Tiny typed client for the same-origin BFF proxy at /api/backend/<path>.
 *
 * The browser NEVER talks to the FastAPI backend directly and NEVER holds
 * the X-Stock-Agent-Token — that only lives in the server route handler at
 * app/api/backend/[...path]/route.ts. Every call here hits our own Next.js
 * origin, which forwards to the backend server-side. */

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(ApiError.detailToMessage(detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  static detailToMessage(detail: unknown): string {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) =>
          d && typeof d === "object" && "msg" in (d as Record<string, unknown>)
            ? String((d as Record<string, unknown>).msg)
            : JSON.stringify(d)
        )
        .join("; ");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return "request failed";
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  const data = await parseBody(res);
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in (data as Record<string, unknown>)
        ? (data as Record<string, unknown>).detail
        : data;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const res = await fetch(`/api/backend/${path}`, { cache: "no-store" });
  return handleResponse<T>(res);
}

export async function apiPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/backend/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  });
  return handleResponse<T>(res);
}
