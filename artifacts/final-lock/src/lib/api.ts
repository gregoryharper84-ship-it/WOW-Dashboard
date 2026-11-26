// API routing is independent of the asset/router BASE_URL. Both development
// and the production server expose /api at the same origin.
export const API_ROOT = "/api";
const ACCESS_KEY = "wow_dashboard_access_key";

let sessionKey = "";
export function getDashboardKey(): string {
  try { return sessionStorage.getItem(ACCESS_KEY) ?? sessionKey; }
  catch { return sessionKey; }
}

export function setDashboardKey(value: string): void {
  sessionKey = value;
  try {
    if (value) sessionStorage.setItem(ACCESS_KEY, value);
    else sessionStorage.removeItem(ACCESS_KEY);
  } catch { /* A blocked storage API still permits this tab's in-memory key. */ }
}

export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const target = new URL(input instanceof Request ? input.url : String(input), origin);
  if (target.origin !== origin || !target.pathname.startsWith(`${API_ROOT}/`)) {
    throw new Error("Dashboard credentials may only be sent to the same-origin /api service.");
  }
  const headers = new Headers(init.headers);
  const key = getDashboardKey();
  if (key) headers.set("X-Dashboard-Key", key);
  return fetch(input, { ...init, headers, redirect: "error" });
}
