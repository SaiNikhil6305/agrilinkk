import { api, setSession, token, esc } from "./api.js";
import * as fb from "./firebase-auth.js";

const $ = s => document.querySelector(s);
const msg = (text, kind = "err") => { $("#msg").innerHTML = text ? `<div class="alert alert-${kind}" style="margin-bottom:14px">${esc(text)}</div>` : ""; };

if (token()) location.replace("/");
if (new URLSearchParams(location.search).get("expired")) msg("Your session expired. Please sign in again.", "warn");

document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b));
  $("#signin").hidden = b.dataset.tab !== "signin"; $("#signup").hidden = b.dataset.tab !== "signup"; msg("");
});
document.querySelectorAll("input[name=role]").forEach(r => r.onchange = () => { $("#orgField").hidden = r.value === "farmer"; });

function done(res) { setSession(res.token, res.user); location.replace("/"); }
function busy(btn, on) { btn.disabled = on; btn.dataset.l ??= btn.innerHTML; btn.innerHTML = on ? '<i class="fa-solid fa-spinner fa-spin"></i>' : btn.dataset.l; }

$("#signin").onsubmit = async e => {
  e.preventDefault(); const f = new FormData(e.target), btn = e.target.querySelector("button");
  if (!f.get("identifier") || !f.get("password")) return msg("Enter your email/phone and password.");
  busy(btn, true);
  try {
    // Firebase email accounts (verified email) take priority when configured; local accounts are the fallback.
    if (fb.firebaseConfigured && String(f.get("identifier")).includes("@")) {
      try { return await exchange(await fb.emailSignIn(f.get("identifier"), f.get("password"))); }
      catch (err) { if (!/user-not-found|invalid-credential/.test(err.code || "")) throw err; }
    }
    done(await api("/api/auth/login", { method: "POST", body: { identifier: f.get("identifier"), password: f.get("password") } }));
  } catch (err) { msg(err.message); } finally { busy(btn, false); }
};

$("#signup").onsubmit = async e => {
  e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)), btn = e.target.querySelector("button");
  if (!f.email && !f.phone) return msg("Enter an email or mobile number.");
  if ((f.password || "").length < 8) return msg("Password must be at least 8 characters.");
  for (const k of Object.keys(f)) if (f[k] === "") f[k] = null;
  busy(btn, true);
  try { done(await api("/api/auth/register", { method: "POST", body: f })); }
  catch (err) { msg(err.message); } finally { busy(btn, false); }
};

$("#forgot").onclick = async e => {
  e.preventDefault();
  const email = $("#signin [name=identifier]").value.trim();
  if (!fb.firebaseConfigured) return msg("Password reset by email needs Firebase to be configured. Contact your FPO administrator.", "warn");
  if (!email.includes("@")) return msg("Type your email address above first.");
  try { await fb.resetPassword(email); msg("Password reset link sent to " + email, "ok"); } catch (err) { msg(err.message); }
};

let pendingToken = null;
async function exchange(idToken, role) {
  const res = await api("/api/auth/firebase-login", { method: "POST", body: { id_token: idToken, role } });
  if (res.needs_role) { pendingToken = idToken; $("#roleModal").hidden = false; return; }
  done(res);
}
$("#roleGo").onclick = async () => {
  const role = document.querySelector("input[name=frole]:checked").value;
  try { await exchange(pendingToken, role); } catch (err) { $("#roleModal").hidden = true; msg(err.message); }
};

(async () => {
  let enabled = false;
  try { enabled = fb.firebaseConfigured && (await api("/api/auth/config")).firebase_enabled; } catch {}
  $("#fbBlock").hidden = !enabled; $("#fbOff").hidden = enabled;
})();

$("#google").onclick = async () => { try { await exchange(await fb.googleSignIn()); } catch (err) { msg(err.message); } };
$("#sendOtp").onclick = async () => {
  const p = $("#otpPhone").value.replace(/\s/g, "");
  if (!/^\+\d{10,13}$/.test(p)) return msg("Enter the mobile number with country code, e.g. +919876543210");
  try { await fb.sendOtp(p, "recaptcha"); $("#otpRow").hidden = false; msg("OTP sent to " + p, "ok"); } catch (err) { msg(err.message); }
};
$("#verifyOtp").onclick = async () => { try { await exchange(await fb.verifyOtp($("#otpCode").value.trim())); } catch (err) { msg(err.message); } };
