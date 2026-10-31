// Thin fetch wrapper: attaches the session token and turns API errors into readable messages.
export const API = import.meta.env.VITE_API_BASE || "";

export function token() { try { return localStorage.getItem("agri_token"); } catch { return null; } }
export function setSession(tok, user) {
  try { localStorage.setItem("agri_token", tok); localStorage.setItem("agri_user", JSON.stringify(user)); } catch {}
}
export function clearSession() { try { localStorage.removeItem("agri_token"); localStorage.removeItem("agri_user"); } catch {} }

export async function api(path, { method = "GET", body, form, blob } = {}) {
  const headers = {};
  const tok = token();
  if (tok) headers.Authorization = "Bearer " + tok;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(API + path, { method, headers, body: payload }); }
  catch { throw new Error("Cannot reach the Agri-Link server. Check your connection."); }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    clearSession(); location.href = "/login.html?expired=1"; throw new Error("Session expired");
  }
  if (blob && res.ok) return res.blob();
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Request failed (${res.status})`;
    if (Array.isArray(msg)) msg = msg.map(e => `${(e.loc || []).slice(-1)[0]}: ${e.msg.replace("Value error, ", "")}`).join("; ");
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

export const inr = (n, d = 0) => "₹" + Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
