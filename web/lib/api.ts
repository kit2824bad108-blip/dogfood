// All browser traffic goes to /api/* on this origin; Next.js rewrites it to the
// FastAPI container, which keeps the session cookie same-origin.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const raw = await response.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = raw;
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, describe(body, response.status));
  }

  return body as T;
}

/**
 * The readable half of a failed response.
 *
 * Most endpoints answer `{"detail": "…"}`, which is already a sentence. Two
 * shapes are not: pydantic's validation errors, which arrive as a list of
 * `{loc, msg}`; and the structured refusals this project returns when the reason
 * is more than one line — a moved deadline refused for being a stale revision
 * carries the current window with it. Rendering either of those with `String()`
 * produces "[object Object]", which is a console that cannot explain itself.
 */
function describe(body: unknown, status: number): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    for (const key of ["error", "detail", "message"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value;
      if (Array.isArray(value)) {
        const first = value[0] as Record<string, unknown> | undefined;
        if (first && typeof first.msg === "string") return first.msg;
      }
    }
  }
  return `Request failed with status ${status}`;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

/**
 * Fetch a JSON document together with its response headers.
 *
 * The bundle export publishes a checksum header that the console shows beside the
 * downloaded file; a caller that only got the body could not display it.
 */
export async function getWithHeaders<T>(
  path: string,
): Promise<{ body: T; headers: Headers }> {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", cache: "no-store" });
  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(response.status, text || `Request failed with status ${response.status}`);
  }
  return { body: JSON.parse(text) as T, headers: response.headers };
}

/**
 * Send body text to an endpoint that returns plain text (a CSV or Markdown export)
 * and hand the result to the browser as a download. Kept here rather than inline in
 * a page so every export behaves the same way.
 */
export async function postForDownload(
  path: string,
  body: unknown,
  filename: string,
): Promise<void> {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()) || "Export failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * Fetch a server-generated file (CSV, Markdown) and hand it to the browser.
 *
 * `downloadText` needs the body in memory already; the export endpoints stream,
 * so this goes through fetch and a blob instead of the JSON helper.
 */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(response.status, detail || `Request failed with status ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Trigger a browser download for a generated archive file. */
export function downloadText(filename: string, content: string, type = "text/plain") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
