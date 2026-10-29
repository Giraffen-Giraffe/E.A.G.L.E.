// API 呼び出し。書き込み系には X-Eagle-Request ヘッダを付ける(サーバー側の CSRF 対策)。

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Json = Record<string, unknown> | unknown[];

async function request<T>(method: string, url: string, body?: Json | FormData): Promise<T> {
  const headers: Record<string, string> = { "X-Eagle-Request": "1" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") msg = j.detail;
      else if (j && Array.isArray(j.detail)) msg = j.detail.map((d: { msg?: string }) => d.msg).join(", ");
    } catch {
      /* 本文が JSON でない */
    }
    if (res.status === 401) window.dispatchEvent(new CustomEvent("eagle:unauthorized"));
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : (res.text() as unknown)) as Promise<T>;
}

export const api = {
  get: <T,>(url: string) => request<T>("GET", url),
  post: <T,>(url: string, body?: Json | FormData) => request<T>("POST", url, body ?? {}),
  put: <T,>(url: string, body: Json) => request<T>("PUT", url, body),
  del: <T,>(url: string) => request<T>("DELETE", url),
};

export function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function uploadFiles(files: File[], templateId?: number | null) {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  if (templateId) fd.append("template_id", String(templateId));
  return api.post<{ results: { id?: number; filename: string; error?: string; duplicate?: boolean }[] }>(
    "/api/upload",
    fd,
  );
}

export function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
