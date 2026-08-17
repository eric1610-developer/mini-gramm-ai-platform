export function getToken() {
  return localStorage.getItem("mg_token") || "";
}

export const API_BASE = (import.meta as any).env?.VITE_API_BASE || "/api/v1";

function urlFor(path: string) {
  return path.startsWith("http") ? path : `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}

export async function apiGet<T>(path: string): Promise<T> {
  const t = getToken();
  const res = await fetch(urlFor(path), { headers: t ? { Authorization: `Bearer ${t}` } : undefined });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

export async function apiPost<T>(path: string, body: any): Promise<T> {
  const t = getToken();
  const res = await fetch(urlFor(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

export async function apiUpload<T>(path: string, file: File, caption = ""): Promise<T> {
  const t = getToken();
  const fd = new FormData(); fd.append("file", file); fd.append("caption", caption);
  const res = await fetch(urlFor(path), { method: "POST", headers: t ? { Authorization: `Bearer ${t}` } : undefined, body: fd });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

export async function downloadFile(path: string, filename: string) {
  const t = getToken();
  const res = await fetch(urlFor(path.replace(/^\/api\/v1/, "")), { headers: t ? { Authorization: `Bearer ${t}` } : undefined });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  const blob = await res.blob();
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
