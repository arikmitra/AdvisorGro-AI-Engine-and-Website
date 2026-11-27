// Thin API client. The bearer token comes from the OIDC sign-in (Keycloak) or, in dev, a pasted token.
// The tenant is never sent by the client: it is inside the signed token.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
let token = sessionStorage.getItem("ag_token") ?? "";
export const setToken = (t: string) => { token = t; t ? sessionStorage.setItem("ag_token", t) : sessionStorage.removeItem("ag_token"); };
export const hasToken = () => !!token;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers ?? {}) },
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.error ?? "error", body.message ?? res.statusText);
  return body as T;
}

export type Campaign = { id: string; offer_id: string; platform: string; state: string; current_version: number; live_version: number | null; monthly_budget_cents: number; budget_ceiling_cents: number; pause_reason: string | null; firm_id: string;
  version?: { version: number; content_hash: string | null; precheck: PreCheck | null; page_html: string | null; creative_set: any; change_note: string; rendered: any } };
export type PreCheck = { status: string; material: Flag[]; minor: Flag[]; unsupported_claims: { text: string; kind: string }[]; missing_disclosures: string[]; ai_review: string; note: string };
export type Flag = { category?: string; title?: string; snippet: string; reason: string };
