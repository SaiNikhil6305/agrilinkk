import { api, token, clearSession, inr, esc } from "./api.js";
import { LANGS, t, getLang, setLang } from "./i18n.js";
import { firebaseSignOut } from "./firebase-auth.js";

const $ = s => document.querySelector(s);
const view = $("#view");
let ME = null, MSP = [], HEALTH = { integrations: {} };
const state = { scan: null, weather: null, selected: new Map() };
const CROPS = ["Wheat", "Paddy", "Rice", "Maize", "Jute", "Sugarcane", "Soybean", "Cotton", "Groundnut", "Tur", "Moong", "Bajra", "Jowar", "Ragi",
  "Apple", "Banana", "Guava", "Lemon", "Lime", "Orange", "Pomegranate", "Mango", "Grapes", "Tomato", "Onion", "Potato", "Brinjal", "Cabbage", "Cauliflower", "Okra", "Chilli"];
const MODEL_LABEL = { crop: "Crop type", defect: "Maize defects", quality: "Rice kernel quality", quality_produce: "Fruit & vegetable quality", quality_wheat: "Wheat quality", quality_maize: "Maize quality", variety: "Rice variety" };
const gradedCrops = models => Object.entries(models).filter(([k, m]) => k.startsWith("quality") && m.installed).flatMap(([, m]) => m.meta.supported_crops || []);
const STATES = ["Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh","Goa","Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka","Kerala","Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram","Nagaland","Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu","Telangana","Tripura","Uttar Pradesh","Uttarakhand","West Bengal","Andaman and Nicobar Islands","Chandigarh","Dadra and Nagar Haveli and Daman and Diu","Delhi","Jammu and Kashmir","Ladakh","Lakshadweep","Puducherry"];

// ---------------------------------------------------------------- ui helpers
const toast = (m, err) => { const d = document.createElement("div"); d.className = "toast" + (err ? " err" : ""); d.textContent = m; $("#toasts").append(d); setTimeout(() => d.remove(), 4500); };
const loading = (n = 4) => `<div class="card">${'<div class="skel"></div>'.repeat(n)}</div>`;
const empty = (icon, text) => `<div class="empty"><i class="fa-solid ${icon}"></i>${esc(text)}</div>`;
const errBox = e => `<div class="alert alert-err"><i class="fa-solid fa-circle-exclamation"></i><div>${esc(e.message || e)}</div></div>`;
const title = (icon, h, p) => `<div class="section-title"><h2><i class="fa-solid ${icon}"></i>${esc(h)}</h2>${p ? `<p>${esc(p)}</p>` : ""}</div>`;
const gp = g => `<span class="pill pill-${g}">${g === "U" ? "Awaiting inspection" : `Grade ${g} (${{ A: "Premium", B: "Good", C: "Fair" }[g]})`}</span>`;
const statusPill = s => `<span class="pill ${/Sold|Settled|Paid|Delivered|Fulfilled|In Yard/.test(s) ? "pill-done" : /Reject|Cancel/.test(s) ? "pill-red" : "pill-status"}">${esc(s)}</span>`;
const opts = (list, sel = "", blank) => (blank ? `<option value="">${blank}</option>` : "") + list.map(v => `<option ${v === sel ? "selected" : ""}>${esc(v)}</option>`).join("");
const today = () => new Date().toISOString().slice(0, 10);
const fmtDate = s => s ? new Date(s.replace(" ", "T") + (s.length > 10 ? "Z" : "")).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";
const mspFor = crop => MSP.find(m => m.commodity.toLowerCase() === (crop === "Rice" ? "paddy" : crop.toLowerCase()));
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

function modal(html, onMount) {
  const bg = document.createElement("div"); bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => bg.remove();
  bg.addEventListener("click", e => { if (e.target === bg || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
  document.body.append(bg); onMount && onMount(bg.querySelector(".modal"), close);
  bg.querySelector("input,select,button")?.focus();
}
async function withBusy(btn, fn) {
  const l = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Working…';
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.innerHTML = l; }
}
async function openInvoice(id) {
  try { const html = await api(`/api/transactions/${id}/invoice`); const w = window.open(URL.createObjectURL(new Blob([html], { type: "text/html" }))); if (!w) toast("Allow pop-ups to view the invoice", true); }
  catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- navigation
const NAV = {
  farmer: [["dashboard","fa-house"],["weather","fa-cloud-sun-rain"],["rainfall","fa-cloud-showers-heavy"],["news","fa-newspaper"],["scanner","fa-seedling"],["gallery","fa-layer-group"],["register","fa-circle-plus"],["lots_payouts","fa-wallet"],["mandi","fa-chart-line"],["centres","fa-location-dot"]],
  aggregator: [["dashboard","fa-house"],["inflow","fa-inbox"],["demand","fa-clipboard-list"],["lots","fa-boxes-stacked"],["gallery","fa-layer-group"],["orders","fa-truck"],["ledger","fa-book"],["weather","fa-cloud-sun-rain"],["mandi","fa-chart-line"],["centres","fa-location-dot"]],
  buyer: [["dashboard","fa-house"],["post_req","fa-file-signature"],["marketplace","fa-basket-shopping"],["matching","fa-gears"],["gallery","fa-layer-group"],["orders","fa-truck"],["expenditure","fa-indian-rupee-sign"],["mandi","fa-chart-line"],["centres","fa-location-dot"]],
};
function renderChrome() {
  document.body.className = "role-" + ME.role;
  $("#rolePill").innerHTML = `<i class="fa-solid ${{ farmer: "fa-tractor", aggregator: "fa-warehouse", buyer: "fa-building" }[ME.role]}"></i><div><b>${esc(t(ME.role).toUpperCase())} PORTAL</b><span>${esc(ME.name)}${ME.location ? " (" + esc(ME.location) + ")" : ""}</span></div>`;
  $("#nav").innerHTML = NAV[ME.role].map(([k, i]) => `<a href="#${k}" data-k="${k}"><i class="fa-solid ${i}"></i><span>${esc(t(k))}</span></a>`).join("");
  document.querySelectorAll("[data-i18n]").forEach(e => e.textContent = t(e.dataset.i18n));
  $("#lang").innerHTML = Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${k === getLang() ? "selected" : ""}>${v}</option>`).join("");
  $("#ticker").innerHTML = MSP.length ? (MSP.map(m => `<span><i class="fa-solid fa-arrow-trend-up"></i>MSP ${esc(m.commodity)}${m.variety ? " (" + esc(m.variety) + ")" : ""} ${m.marketing_year}: ${inr(m.msp_per_qtl)}/Qtl</span>`).join("")).repeat(2) : "";
}
$("#lang").onchange = e => { setLang(e.target.value); renderChrome(); route(); api("/api/auth/profile", { method: "PUT", body: { language: e.target.value } }).catch(() => {}); };
$("#menuBtn").onclick = () => { const o = $("#nav").classList.toggle("open"); $("#menuBtn").setAttribute("aria-expanded", o); };
$("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); await firebaseSignOut(); clearSession(); location.href = "/login.html"; };

async function route() {
  const key = (location.hash.slice(1) || "dashboard").split("?")[0];
  const allowed = NAV[ME.role].map(n => n[0]);
  const k = allowed.includes(key) ? key : "dashboard";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.k === k));
  $("#nav").classList.remove("open");
  $("#heroSlot").innerHTML = k === "dashboard" ? hero() : "";
  view.innerHTML = loading();
  try { await VIEWS[ME.role][k](); } catch (e) { view.innerHTML = errBox(e); }
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- notifications (persisted rows, polled)
let lastUnread = 0;
async function pollNotes() {
  try {
    const n = await api("/api/notifications");
    $("#unread").hidden = !n.unread; $("#unread").textContent = n.unread;
    if (n.unread > lastUnread && lastUnread !== -1) n.items.filter(i => !i.is_read).slice(0, n.unread - lastUnread).forEach(i => toast(i.title + ": " + i.message));
    lastUnread = n.unread;
    $("#noteList").innerHTML = n.items.length ? n.items.map(i => `<div class="note ${i.is_read ? "" : "unread"}"><b>${esc(i.title)}</b><div class="small">${esc(i.message)}</div><small>${fmtDate(i.created_at)}</small></div>`).join("") : empty("fa-bell-slash", "No notifications yet");
  } catch {}
}
$("#bell").onclick = async () => { $("#drawer").classList.add("open"); await api("/api/notifications/read", { method: "POST" }).catch(() => {}); lastUnread = 0; $("#unread").hidden = true; };
$("#closeDrawer").onclick = () => $("#drawer").classList.remove("open");

// ---------------------------------------------------------------- hero
function hero() {
  const H = {
    farmer: ["fa-seedling", "Kisan Producer Hub", "Maximize Your Crop Value with AI Quality & Fair Aggregation",
      "Grade your harvest with deep learning models, protect your crop with real-time weather stations, and get pooled into high-paying buyer lots.",
      [["#scanner", "fa-camera", t("scan_btn"), "btn-white"], ["#register", "fa-plus", t("register_btn"), "btn-ghost"]],
      [["Itemised", "Every deduction shown with a reason"], ["Grade A/B/C", "AI visual grading"], ["Open-Meteo", "Real weather data"], ["Direct MSP", "Govt. reference rates"]]],
    aggregator: ["fa-warehouse", "FPO Aggregation Hub", "Pool Smallholder Harvests into Certified Commercial Lots",
      "Review farmer inflow, inspect quality, assemble single-grade lots, match buyer demand and settle every farmer transparently.",
      [["#inflow", "fa-inbox", t("inflow"), "btn-white"], ["#demand", "fa-clipboard-list", t("demand"), "btn-ghost"]],
      [["Zero", "Overselling — atomic allocation"], ["Traceable", "Batch → lot → buyer"], ["Ledger", "Margin & logistics derived"], ["Alerts", "New inflow & demand"]]],
    buyer: ["fa-building", "Institutional Procurement", "Procure Certified Lots Directly from Farmer Collectives",
      "Post bulk requirements, get explainable lot matches, and see exactly what reaches farmers, the FPO and logistics.",
      [["#post_req", "fa-file-signature", t("post_req"), "btn-white"], ["#marketplace", "fa-basket-shopping", t("marketplace"), "btn-ghost"]],
      [["Traceability", "Farm-gate batch records"], ["Matching", "Strict checks + score"], ["Invoices", "Generated at delivery"], ["Transparency", "Farmer share visible"]]],
  }[ME.role];
  return `<section class="hero"><div><span class="chip"><i class="fa-solid ${H[0]}"></i>${H[1]}</span><h1>${H[2]}</h1><p>${H[3]}</p>
    <div class="hero-actions">${H[4].map(([h, i, l, c]) => `<a class="btn ${c}" href="${h}"><i class="fa-solid ${i}"></i>${esc(l)}</a>`).join("")}</div></div>
    <div class="protect"><h3><i class="fa-solid fa-shield-halved"></i>${ME.role === "farmer" ? "Farmer Protections Active" : "Platform Guarantees"}</h3>
    <div class="protect-grid">${H[5].map(([b, s]) => `<div><b>${b}</b><small>${s}</small></div>`).join("")}</div></div></section>`;
}
const FLOW = `<div class="card"><h3><i class="fa-solid fa-diagram-project"></i>How Agri-Link works</h3><div class="flow">${["Farmer registration", "AI crop assessment", "Produce registration", "FPO inflow & inspection", "Commercial lot", "Buyer procurement", "Dispatch", "Delivery confirmation", "Settlement & ledger"].map(s => `<span>${s}</span>`).join('<i class="fa-solid fa-arrow-right"></i>')}</div></div>`;

// ---------------------------------------------------------------- shared views
async function weatherView(rainOnly = false) {
  const w = state.weather;
  view.innerHTML = title(rainOnly ? "fa-cloud-showers-heavy" : "fa-cloud-sun-rain", rainOnly ? "Next Days Rainfall Outlook" : "Real-Time Agricultural Weather Station",
    "Meteorological data fetched on request from the Open-Meteo forecast API") +
    `<div class="card"><div class="wx-search"><input id="wxq" placeholder="Village, district or city" value="${esc(w?.place || ME.location || "")}" aria-label="Location">
    <button class="btn btn-outline" id="wxGo"><i class="fa-solid fa-magnifying-glass"></i>Fetch Weather</button>
    <button class="btn btn-outline" id="wxGps"><i class="fa-solid fa-location-crosshairs"></i>Use My GPS</button></div><div id="wxOut">${w ? "" : empty("fa-cloud", "Search a place or use GPS to load the forecast")}</div></div>`;
  const show = () => { $("#wxOut").innerHTML = rainOnly ? rainHtml(state.weather) : weatherHtml(state.weather); };
  if (w) show();
  $("#wxGo").onclick = e => withBusy(e.currentTarget, async () => {
    const q = $("#wxq").value.trim(); if (!q) throw new Error("Type a place name");
    const r = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=in&q=${encodeURIComponent(q)}`).then(r => r.json());
    if (!r.length) throw new Error("Place not found");
    await loadWeather(+r[0].lat, +r[0].lon, r[0].display_name.split(",").slice(0, 3).join(","), false); show();
  });
  $("#wxGps").onclick = e => withBusy(e.currentTarget, async () => {
    const { lat, lon, place } = await gpsPlace(); await loadWeather(lat, lon, place, true); show();
  });
  if (!w && ME.latitude) { await loadWeather(ME.latitude, ME.longitude, ME.location, false).catch(() => {}); if (state.weather) show(); }
}
function gpsPlace() {
  return new Promise((res, rej) => {
    if (!navigator.geolocation) return rej(new Error("GPS is not available in this browser"));
    navigator.geolocation.getCurrentPosition(async p => {
      const lat = p.coords.latitude, lon = p.coords.longitude;
      let g = {};
      try { g = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=en`).then(r => r.json()); }
      catch { try { const n = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`).then(r => r.json()); g = { locality: n.address?.village || n.address?.town || n.address?.city, city: n.address?.county, principalSubdivision: n.address?.state }; } catch {} }
      const place = [g.locality, g.city, g.principalSubdivision].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(", ") || `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
      res({ lat, lon, place, state: g.principalSubdivision, district: g.city });
    }, () => rej(new Error("Location permission denied")), { enableHighAccuracy: true, timeout: 15000 });
  });
}
async function loadWeather(lat, lon, place, gps) {
  const u = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,surface_pressure,cloud_cover,weather_code,precipitation&hourly=precipitation_probability&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max&forecast_days=14&timezone=auto`;
  const r = await fetch(u); if (!r.ok) throw new Error("Open-Meteo did not respond");
  const d = await r.json();
  const hourIdx = d.hourly.time.findIndex(x => x >= d.current.time.slice(0, 13));
  state.weather = { lat, lon, place, gps, d, fetched: new Date(), precipNow: d.hourly.precipitation_probability[Math.max(0, hourIdx)] };
  if (gps) api("/api/auth/profile", { method: "PUT", body: { latitude: lat, longitude: lon } }).catch(() => {});
}
const WX = c => c === 0 ? ["fa-sun", "Clear"] : c <= 3 ? ["fa-cloud-sun", "Partly cloudy"] : c <= 48 ? ["fa-smog", "Fog"] : c <= 67 ? ["fa-cloud-rain", "Rain"] : c <= 77 ? ["fa-snowflake", "Snow"] : c <= 82 ? ["fa-cloud-showers-heavy", "Showers"] : ["fa-cloud-bolt", "Thunderstorm"];
function advisories(w) {
  const c = w.d.current, dl = w.d.daily, a = [];
  if (w.precipNow > 50 || dl.precipitation_probability_max[0] > 50) a.push(["High rainfall risk (>50%)", "Postpone foliar pesticide or nitrogen spraying to prevent runoff. Keep field drainage channels clear and cover harvested produce."]);
  if (c.wind_speed_10m > 20) a.push(["Strong wind", "Avoid spraying — drift will reduce effectiveness and harm neighbouring crops."]);
  if (c.relative_humidity_2m > 85) a.push(["High humidity", "Elevated fungal disease risk. Scout for blast/blight and ensure stored grain stays aerated."]);
  if (c.temperature_2m > 35) a.push(["Heat stress", "Irrigate in early morning or evening; avoid transplanting during peak heat."]);
  const dry = dl.precipitation_sum.slice(0, 3).every(p => p < 1);
  if (dry && c.relative_humidity_2m < 70) a.push(["Dry window ahead", "Next 3 days look dry — suitable for harvesting and sun-drying grain to safe moisture."]);
  return a.length ? a : [["Normal conditions", "No weather risk flagged for routine field work today."]];
}
function weatherHtml(w) {
  const c = w.d.current, dl = w.d.daily, [ic, lbl] = WX(c.weather_code);
  return `<div class="wx-place"><i class="fa-solid fa-location-dot" style="color:var(--g700)"></i>${esc(w.place)} ${w.gps ? '<span class="verified"><i class="fa-solid fa-circle-check"></i> GPS</span>' : ""}</div>
  <div class="small muted">Lat ${w.lat.toFixed(2)}°, Lon ${w.lon.toFixed(2)}° · Source: Open-Meteo · Observation ${esc(c.time.replace("T", " "))} (${esc(w.d.timezone_abbreviation)}) · fetched ${w.fetched.toLocaleTimeString()}</div>
  <div class="wx-now"><div class="wx-tile wx-main"><i class="fa-solid ${ic}" style="font-size:36px"></i><div><b>${Math.round(c.temperature_2m)}°C</b><div class="small">${lbl}</div></div></div>
  ${[["fa-droplet", "Relative humidity", c.relative_humidity_2m + "%"], ["fa-cloud-rain", "Precipitation chance", (w.precipNow ?? "—") + "%"], ["fa-wind", "Wind velocity", c.wind_speed_10m + " km/h"], ["fa-gauge", "Surface pressure", Math.round(c.surface_pressure) + " hPa"], ["fa-cloud", "Cloud cover", c.cloud_cover + "%"]]
    .map(([i, l, v]) => `<div class="wx-tile"><i class="fa-solid ${i}"></i><div><small>${l}</small><b>${v}</b></div></div>`).join("")}</div>
  ${advisories(w).map(([h, b]) => `<div class="alert alert-warn" style="margin-bottom:8px"><i class="fa-solid fa-circle-exclamation"></i><div><b>${h}:</b> ${b}</div></div>`).join("")}
  <h3 style="margin-top:20px"><i class="fa-solid fa-calendar-days"></i> 7-Day Forecast</h3>
  <div class="wx-days">${dl.time.slice(0, 7).map((d, i) => `<div class="wx-day"><b>${i ? new Date(d).toLocaleDateString("en-IN", { weekday: "short" }) : "Today"}</b><br><i class="fa-solid ${WX(dl.weather_code[i])[0]}"></i><br>${Math.round(dl.temperature_2m_max[i])}° / ${Math.round(dl.temperature_2m_min[i])}°<div class="rain"><i class="fa-solid fa-droplet"></i> ${dl.precipitation_probability_max[i] ?? "—"}% · ${dl.precipitation_sum[i]} mm</div></div>`).join("")}</div>`;
}
function rainHtml(w) {
  const dl = w.d.daily, mx = Math.max(5, ...dl.precipitation_sum);
  const total = dl.precipitation_sum.reduce((a, b) => a + b, 0);
  return `<div class="wx-place"><i class="fa-solid fa-location-dot" style="color:var(--g700)"></i>${esc(w.place)}</div>
  <div class="grid g3" style="margin:14px 0"><div class="stat"><small>14-day total</small><b>${total.toFixed(1)} mm</b></div><div class="stat"><small>Rainy days (≥2.5 mm)</small><b>${dl.precipitation_sum.filter(p => p >= 2.5).length}</b></div><div class="stat"><small>Wettest day</small><b>${dl.time[dl.precipitation_sum.indexOf(Math.max(...dl.precipitation_sum))]}</b></div></div>
  <div class="bars" role="img" aria-label="Daily rainfall for the next 14 days">${dl.precipitation_sum.map((p, i) => `<div class="bar" title="${dl.time[i]}: ${p} mm, ${dl.precipitation_probability_max[i]}% chance"><span class="small">${p}</span><div style="height:${(p / mx) * 100}%"></div></div>`).join("")}</div>
  <div class="bar-lbl">${dl.time.map(d => `<span>${new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}</span>`).join("")}</div>
  <p class="small muted">Bars show forecast precipitation sum (mm/day); hover for probability. Source: Open-Meteo, fetched ${w.fetched.toLocaleTimeString()}.</p>`;
}

async function mandiView() {
  MSP = MSP.length ? MSP : await api("/api/msp");
  view.innerHTML = title("fa-chart-line", "Mandi Prices & MSP Benchmarks", "Official MSP reference and Agmarknet mandi prices — each with its source and date") +
    `<div class="card"><div class="form-grid three"><label class="f">State<select id="mS">${opts(STATES, ME.state || "Punjab")}</select></label>
    <label class="f">Commodity<select id="mC">${opts(["Wheat", "Paddy(Dhan)(Common)", "Maize", "Rice", "Jute", "Soyabean", "Cotton", "Groundnut", "Onion", "Tomato"], "Wheat")}</select></label>
    <label class="f">Arrival date<input type="date" id="mD" max="${today()}"><span class="hint">Leave empty for latest available</span></label></div><br>
    <button class="btn btn-primary" id="mGo"><i class="fa-solid fa-magnifying-glass"></i>Query Mandis</button><div id="mOut" style="margin-top:16px"></div></div>
    <div class="card"><h3><i class="fa-solid fa-landmark"></i>Minimum Support Prices (Govt. of India)</h3><div class="table-wrap"><table class="t"><thead><tr><th>Commodity</th><th>Variety</th><th>Season</th><th>Year</th><th>MSP (₹/Qtl)</th><th>Source</th></tr></thead>
    <tbody>${MSP.map(m => `<tr><td><b>${esc(m.commodity)}</b></td><td>${esc(m.variety || "—")}</td><td>${esc(m.season)}</td><td>${esc(m.marketing_year)}</td><td class="money" style="color:var(--g700)">${inr(m.msp_per_qtl)}</td><td class="small muted">${esc(m.source)}</td></tr>`).join("")}</tbody></table></div>
    <p class="small muted">Stored reference values. Re-verify each season at pib.gov.in / cacp.da.gov.in.</p></div>`;
  $("#mGo").onclick = e => withBusy(e.currentTarget, async () => {
    $("#mOut").innerHTML = '<div class="skel"></div>';
    try {
      const p = new URLSearchParams({ state: $("#mS").value, commodity: $("#mC").value }); if ($("#mD").value) p.set("date", $("#mD").value);
      const r = await api("/api/mandi-prices?" + p);
      const crop = $("#mC").value.startsWith("Paddy") ? "Paddy" : $("#mC").value; const m = mspFor(crop);
      $("#mOut").innerHTML = `<p class="small muted">Source: ${esc(r.source)} · fetched ${esc(r.fetched_at)} UTC</p>` + (r.records.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Market (Mandi)</th><th>District</th><th>Variety</th><th>Arrival date</th><th>Min ₹/Qtl</th><th>Max ₹/Qtl</th><th>Modal ₹/Qtl</th><th>MSP ₹/Qtl</th></tr></thead><tbody>${r.records.map(x => `<tr><td><b>${esc(x.market)}</b></td><td>${esc(x.district)}</td><td>${esc(x.variety)}</td><td>${esc(x.arrival_date)}</td><td>${inr(x.min_price)}</td><td>${inr(x.max_price)}</td><td class="money" style="color:var(--g700)">${inr(x.modal_price)}</td><td>${m ? inr(m.msp_per_qtl) : "—"}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-store-slash", "No arrivals reported for this filter. Try another date or leave the date empty."));
    } catch (err) { $("#mOut").innerHTML = errBox(err); }
  });
}

async function centresView() {
  const states = await api("/api/procurement-centers/states");
  view.innerHTML = title("fa-location-dot", "Procurement Centre Directory", "Regulated markets and procurement points. Verify timings and contacts with the state marketing board before travelling.") +
    `<div class="filters"><select id="cS"><option value="">All states</option>${states.map(s => `<option value="${esc(s.state)}">${esc(s.state)} (${s.count})</option>`).join("")}</select>
    <input id="cC" placeholder="Crop (e.g. Wheat)"><input id="cQ" placeholder="Search name / district"></div><div id="cOut"></div>`;
  const load = async () => {
    const p = new URLSearchParams(); if ($("#cS").value) p.set("state", $("#cS").value); if ($("#cC").value) p.set("crop", $("#cC").value); if ($("#cQ").value) p.set("q", $("#cQ").value);
    const r = await api("/api/procurement-centers?" + p);
    $("#cOut").innerHTML = r.items.length ? `<p class="small muted">${r.total} centres</p><div class="table-wrap"><table class="t"><thead><tr><th>Centre</th><th>Type</th><th>District</th><th>State</th><th>Principal crops</th><th>Map</th></tr></thead><tbody>${r.items.map(c => `<tr><td><b>${esc(c.name)}</b></td><td>${esc(c.facility_type)}</td><td>${esc(c.district)}</td><td>${esc(c.state)}</td><td class="small">${esc(c.crops)}</td><td><a target="_blank" rel="noopener" href="https://www.openstreetmap.org/search?query=${encodeURIComponent(c.name + ", " + c.district + ", " + c.state)}"><i class="fa-solid fa-map-location-dot"></i></a></td></tr>`).join("")}</tbody></table></div>` : empty("fa-map", "No centres match");
  };
  let tm; ["#cS", "#cC", "#cQ"].forEach(s => $(s).oninput = () => { clearTimeout(tm); tm = setTimeout(load, 250); });
  await load();
}

// ---------------------------------------------------------------- quality segregation gallery (all roles)
async function galleryView() {
  const [g, models] = await Promise.all([api("/api/crop-quality/folders"), api("/api/models")]);
  const tabs = [["Premium", "Grade A", "pill-A"], ["Good", "Grade B", "pill-B"], ["Bad", "Grade C · rejected", "pill-C"]];
  const qms = Object.entries(models).filter(([k, m]) => k.startsWith("quality") && m.installed && m.meta.benchmark);
  const benchCard = ([k, q]) => { const b = q.meta.benchmark, best = q.meta.arch; return `<div class="card"><h3><i class="fa-solid fa-ranking-star"></i>${esc(MODEL_LABEL[k] || k)} — 5 architectures compared on the same split</h3>
      <p class="small"><b>Grades:</b> ${esc((q.meta.supported_crops || []).join(", "))}</p>
      <div class="table-wrap"><table class="t"><thead><tr><th>Model</th><th>Val macro-F1</th><th>Test accuracy</th><th>Test macro precision</th><th>Test macro recall</th><th>Test macro-F1</th><th>Parameters</th><th>CPU ms / image</th></tr></thead><tbody>
      ${Object.entries(b).map(([n, r]) => `<tr ${n === best ? 'class="sel"' : ""}><td><b>${esc(n)}</b> ${n === best ? '<span class="pill pill-done">selected</span>' : ""}</td><td>${(r.val_macro_f1 * 100).toFixed(1)}%</td><td>${(r.test_accuracy * 100).toFixed(1)}%</td><td>${(r.test_macro_precision * 100).toFixed(1)}%</td><td>${(r.test_macro_recall * 100).toFixed(1)}%</td><td><b>${(r.test_macro_f1 * 100).toFixed(1)}%</b></td><td>${(r.params / 1e6).toFixed(2)}M</td><td>${r.cpu_inference_ms_per_image}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Selected on validation macro-F1; test set (${q.meta.test_metrics.test_images} images) used only for reporting. Dataset: ${esc(q.meta.dataset.name)} (${esc(q.meta.dataset.license)}). Grade mapping: ${esc(q.meta.dataset.label_mapping)}.</p></div>`; };
  const crops = [...new Set(Object.values(g.folders).flatMap(f => f.items.map(i => i.crop)).filter(Boolean))].sort();
  view.innerHTML = title("fa-layer-group", "AI Crop Quality Segregation", "Every graded image is filed automatically into Premium, Good or Bad storage by the trained model") +
    qms.map(benchCard).join("") +
    (g.labelled_checked ? `<div class="alert alert-ok" style="margin:14px 0"><i class="fa-solid fa-circle-check"></i><div><b>Model check on unseen images:</b> ${g.labelled_checked} held-out images (never used in training) were filed automatically and ${(g.labelled_accuracy * 100).toFixed(1)}% landed in the folder matching their true grade${g.manual_inspection ? `; ${g.manual_inspection} low-confidence image(s) were sent to manual inspection instead` : ""}. Mismatches are marked ✗ below.</div></div>` : "") +
    `<div class="filters"><label class="small" for="galCrop"><b>Crop</b></label><select id="galCrop"><option value="">All crops</option>${crops.map(c => `<option>${esc(c)}</option>`).join("")}</select></div>
    <div class="grid g3" style="margin-bottom:14px">${tabs.map(([f, l, pc]) => `<button class="stat" data-tab="${f}" style="text-align:left;cursor:pointer"><small>${f} · ${l}</small><b data-count="${f}">${g.folders[f].count} images <span class="small muted">${g.folders[f].percent}%</span></b></button>`).join("")}</div>
    <div id="galOut"></div>`;
  let cur = "Premium";
  const show = async f => {
    view.querySelectorAll("[data-tab]").forEach(x => x.style.borderColor = x.dataset.tab === f ? "var(--g600)" : "");
    cur = f; const cf = $("#galCrop").value;
    const items = g.folders[f].items.filter(i => !cf || i.crop === cf);
    const n = Object.fromEntries(Object.entries(g.folders).map(([k, v]) => [k, v.items.filter(i => !cf || i.crop === cf).length]));
    const tot = Object.values(n).reduce((a, b) => a + b, 0) || 1;
    view.querySelectorAll("[data-count]").forEach(b => b.innerHTML = `${n[b.dataset.count]} images <span class="small muted">${(100 * n[b.dataset.count] / tot).toFixed(1)}%${cf ? " of " + esc(cf) : ""}</span>`);
    $("#galOut").innerHTML = items.length ? `<div class="grid g4">${items.map(i => `<figure class="lot-card" style="margin:0;padding:10px">
      <img data-src="${f}/${i.file}" alt="${esc(f)} sample" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;background:#f1f5f9">
      <figcaption class="small"><b>${esc(f)}</b> · ${esc(i.crop || "")} · confidence ${(i.confidence * 100).toFixed(0)}%
      ${i.true_grade ? `<br>True: ${({ A: "Premium", B: "Good", C: "Bad" })[i.true_grade]} ${i.true_grade === i.grade ? '<span style="color:var(--g700)">✓</span>' : '<span style="color:var(--red)">✗</span>'} <span class="muted">(${esc(i.source_label)})</span>` : `<br><span class="muted">${esc(i.source || "")}</span>`}</figcaption></figure>`).join("")}</div>`
      : empty("fa-images", `No images in ${f} yet — scan a crop to add one`);
    for (const img of $("#galOut").querySelectorAll("img[data-src]")) {
      api("/api/crop-quality/image/" + img.dataset.src, { blob: true }).then(bl => img.src = URL.createObjectURL(bl)).catch(() => {});
    }
  };
  view.querySelectorAll("[data-tab]").forEach(x => x.onclick = () => show(x.dataset.tab));
  $("#galCrop").onchange = () => show(cur);
  await show("Premium");
}

// ---------------------------------------------------------------- FARMER
const farmer = {
  async dashboard() {
    const [subs, e] = await Promise.all([api("/api/farmers/submissions"), api("/api/earnings/farmer")]);
    const avail = subs.reduce((a, s) => a + s.available_quantity, 0);
    view.innerHTML = `<div class="grid g4">${[["Registered batches", subs.length], ["Unallocated stock", avail.toLocaleString("en-IN") + " Qtl"], ["Net earnings", inr(e.net)], ["Pending payments", inr(e.pending)]].map(([l, v], i) => `<div class="stat"><small>${l}</small><b class="${i > 1 ? "money" : ""}">${v}</b></div>`).join("")}</div><br>${FLOW}
    <div class="card"><h3><i class="fa-solid fa-clock-rotate-left"></i>Recent batches</h3>${subs.length ? batchTable(subs.slice(0, 5)) : empty("fa-seedling", "No produce registered yet — scan your crop to begin.")}</div>`;
  },
  weather: () => weatherView(false),
  rainfall: () => weatherView(true),
  async news() {
    const n = await api("/api/notifications");
    view.innerHTML = title("fa-newspaper", "Krishi News & Alerts", "Your platform alerts, weather risk advisories and official sources") +
      `<div class="grid g2"><div class="card"><h3><i class="fa-solid fa-triangle-exclamation"></i>Weather advisories</h3><div id="advOut">${state.weather ? advisories(state.weather).map(([h, b]) => `<div class="alert alert-warn" style="margin-bottom:8px"><div><b>${h}:</b> ${b}</div></div>`).join("") : `<p class="muted">Load your location in <a href="#weather">Live Weather Station</a> to see field advisories.</p>`}</div></div>
      <div class="card"><h3><i class="fa-solid fa-bell"></i>Your Agri-Link alerts</h3>${n.items.length ? n.items.slice(0, 10).map(i => `<div class="note"><b>${esc(i.title)}</b><div class="small">${esc(i.message)}</div><small class="muted">${fmtDate(i.created_at)}</small></div>`).join("") : empty("fa-bell-slash", "No alerts yet")}</div></div>
      <div class="card"><h3><i class="fa-solid fa-link"></i>Official agricultural news & advisories</h3><div class="grid g3">${[["PIB — Agriculture releases", "https://pib.gov.in/allRel.aspx", "Government decisions incl. MSP"], ["IMD Agromet Advisory", "https://mausam.imd.gov.in/responsive/agromet_adv_ser_state_current.php", "District agromet bulletins"], ["e-NAM", "https://enam.gov.in/web/", "National Agriculture Market"], ["Agmarknet", "https://agmarknet.gov.in/", "Daily mandi arrivals & prices"], ["PM-KISAN", "https://pmkisan.gov.in/", "Beneficiary status"], ["Kisan Call Centre", "tel:18001801551", "1800-180-1551 (toll-free)"]].map(([h, u, d]) => `<a class="stat" style="text-decoration:none;color:inherit" href="${u}" target="_blank" rel="noopener"><b style="font-size:15px">${h} <i class="fa-solid fa-arrow-up-right-from-square small"></i></b><span class="small muted">${d}</span></a>`).join("")}</div></div>`;
  },
  async scanner() {
    const models = await api("/api/models");
    const installed = Object.values(models).some(m => m.installed);
    view.innerHTML = title("fa-seedling", "AI Crop Quality Scanner", "Upload a clear, well-lit close-up of your grain or crop sample") +
      `<div class="grid g2"><div class="card">
      ${installed ? "" : `<div class="alert alert-warn" style="margin-bottom:12px"><i class="fa-solid fa-triangle-exclamation"></i><div>No trained model is installed on this server yet, so scans cannot be graded. You can still register produce — the FPO will inspect it at the yard.</div></div>`}
      <div class="drop" id="drop" tabindex="0" role="button" aria-label="Choose crop image"><i class="fa-solid fa-camera" style="font-size:32px;color:var(--g700)"></i><p><b>Tap to take or choose a photo</b><br><span class="small muted">JPEG / PNG / WEBP, up to 8 MB</span></p></div>
      <input type="file" id="file" accept="image/jpeg,image/png,image/webp" capture="environment" hidden><br>
      <div class="form-grid"><label class="f">Crop<select id="sCrop"><optgroup label="AI grading available">${opts([...new Set(gradedCrops(models))], "Rice")}</optgroup><optgroup label="Yard inspection only (no validated model yet)">${opts(CROPS.filter(c => !gradedCrops(models).includes(c)))}</optgroup></select><span class="hint">AI grading validated for: ${esc([...new Set(gradedCrops(models))].join(", ") || "none")}</span></label>
      <label class="f">Moisture-meter reading (%)<input id="sMoist" type="number" step="0.1" min="1" max="39" placeholder="optional"><span class="hint">Only from a physical meter — photos can't measure moisture</span></label></div><br>
      <button class="btn btn-primary" id="scanGo" style="width:100%" disabled><i class="fa-solid fa-wand-magic-sparkles"></i>Analyse Crop</button></div>
      <div class="card" id="scanOut">${empty("fa-microscope", "Results appear here: crop, defects, quality class, grade and advice")}
      <h3 style="margin-top:18px"><i class="fa-solid fa-circle-info"></i>Model information</h3>${Object.entries(models).filter(([k, m]) => m.installed || !k.startsWith("quality_")).map(([k, m]) => `<div class="small" style="margin:6px 0"><b>${MODEL_LABEL[k] || k}:</b> ${m.installed ? `${esc(m.meta.version)} · ${m.meta.arch ? esc(m.meta.arch) + " · " : ""}test accuracy ${(m.meta.test_metrics.accuracy * 100).toFixed(1)}%, macro-F1 ${(m.meta.test_metrics.macro_f1 * 100).toFixed(1)}% on ${m.meta.test_metrics.test_images} held-out images` : '<span class="muted">not installed</span>'}</div>`).join("")}</div></div>`;
    let file;
    const pick = f => { if (!f) return; if (!/^image\/(jpeg|png|webp)$/.test(f.type)) return toast("Use a JPEG, PNG or WEBP image", true); if (f.size > 8 * 1024 * 1024) return toast("Image is larger than 8 MB", true); file = f; $("#drop").innerHTML = `<img src="${URL.createObjectURL(f)}" alt="Selected crop">`; $("#scanGo").disabled = !installed; };
    $("#drop").onclick = () => $("#file").click(); $("#drop").onkeydown = e => (e.key === "Enter" || e.key === " ") && $("#file").click();
    $("#file").onchange = e => pick(e.target.files[0]);
    $("#drop").ondragover = e => { e.preventDefault(); $("#drop").classList.add("drag"); };
    $("#drop").ondragleave = () => $("#drop").classList.remove("drag");
    $("#drop").ondrop = e => { e.preventDefault(); $("#drop").classList.remove("drag"); pick(e.dataTransfer.files[0]); };
    $("#scanGo").onclick = e => withBusy(e.currentTarget, async () => {
      const fd = new FormData(); fd.append("image", file); if ($("#sCrop").value) fd.append("crop", $("#sCrop").value); if ($("#sMoist").value) fd.append("moisture", $("#sMoist").value);
      const r = await api("/api/predict", { method: "POST", form: fd }); state.scan = r; $("#scanOut").innerHTML = scanResult(r);
      $("#useScan")?.addEventListener("click", () => { location.hash = "register"; });
    });
  },
  async register() {
    const s = state.scan, crop = s?.effective_crop || "";
    view.innerHTML = title("fa-tractor", "Register Your Produce for Pooling", "Submit your harvest volume to be aggregated into high-volume wholesale commercial lots") +
      `<div class="card narrow"><form id="rf" novalidate>
      ${s ? `<div class="alert ${s.grade === "C" ? "alert-err" : s.grade === "MANUAL" ? "alert-info" : "alert-ok"}" style="margin-bottom:14px"><i class="fa-solid fa-clipboard-check"></i><div>Linked AI assessment #${s.prediction_id}: <b>${esc(s.grade_label)}</b>${s.effective_crop ? " · " + esc(s.effective_crop) : ""}. ${s.grade === "MANUAL" ? "The FPO will grade this batch on inspection." : ""} <a href="#" id="unlink">Unlink</a></div></div>` :
        `<div class="alert alert-info" style="margin-bottom:14px"><i class="fa-solid fa-circle-info"></i><div>No scan linked. <a href="#scanner">Scan your crop first</a> for an instant grade, or submit now and the FPO will inspect at the yard.</div></div>`}
      <div class="form-grid"><label class="f">Farmer name<input value="${esc(ME.name)}" disabled></label><label class="f">Phone / Kisan ID<input value="${esc(ME.phone || ME.email || "")}" disabled></label>
      <label class="f">Crop / commodity<select name="crop" required>${opts(CROPS, crop, "Select crop")}</select></label><label class="f">Variety<input name="variety" placeholder="e.g. HD-3086, Sharbati" value="${esc(s?.variety && s.variety.confidence >= 0.6 ? s.variety.label : "")}"></label>
      <label class="f">Quantity (Quintals)<input name="quantity_qtl" type="number" step="0.01" min="0.01" required></label>
      <label class="f">Expected price (₹/Qtl)<input name="expected_price_per_qtl" type="number" step="1" min="1" required><span class="hint" id="mspHint"></span></label>
      <label class="f">Storage / warehouse location<input name="location" required value="${esc(ME.location || "")}"><button type="button" class="btn btn-outline btn-sm" id="gps" style="margin-top:4px"><i class="fa-solid fa-location-crosshairs"></i>Detect my location</button></label>
      <label class="f">Harvest date<input name="harvest_date" type="date" max="${today()}" required></label>
      <label class="f">Moisture-meter reading (%)<input name="moisture" type="number" step="0.1" min="1" max="39" value="${s?.moisture?.value ?? ""}"><span class="hint">Optional, physical meter only</span></label></div>
      <input type="hidden" name="latitude"><input type="hidden" name="longitude"><input type="hidden" name="state"><input type="hidden" name="district"><br>
      <button class="btn btn-soft" id="rfSubmit" type="submit"><i class="fa-solid fa-circle-check" style="color:var(--g700)"></i>Submit Produce for Aggregator Lot</button></form></div>`;
    const f = $("#rf");
    const hint = () => { const m = mspFor(f.crop.value || ""); $("#mspHint").textContent = m ? `MSP ${m.marketing_year}: ${inr(m.msp_per_qtl)}/Qtl` : ""; };
    f.crop.onchange = hint; hint();
    $("#unlink")?.addEventListener("click", e => { e.preventDefault(); state.scan = null; farmer.register(); });
    $("#gps").onclick = e => withBusy(e.currentTarget, async () => { const g = await gpsPlace(); f.location.value = g.place; f.latitude.value = g.lat; f.longitude.value = g.lon; f.state.value = g.state || ""; f.district.value = g.district || ""; });
    f.onsubmit = ev => { ev.preventDefault(); withBusy($("#rfSubmit"), async () => {
      const d = Object.fromEntries(new FormData(f)); let bad = false;
      ["crop", "quantity_qtl", "expected_price_per_qtl", "location", "harvest_date"].forEach(k => { const v = !d[k]; f[k].closest(".f").classList.toggle("invalid", v); bad ||= v; });
      if (bad) throw new Error("Please fill the highlighted fields");
      for (const k of ["quantity_qtl", "expected_price_per_qtl", "moisture", "latitude", "longitude"]) d[k] = d[k] === "" ? null : Number(d[k]);
      for (const k of ["state", "district", "variety"]) if (d[k] === "") d[k] = null;
      if (s) d.prediction_id = s.prediction_id;
      const r = await api("/api/farmers/register", { method: "POST", body: d });
      state.scan = null; toast(`${r.batch_code} registered — sent to FPO inflow`); location.hash = "lots_payouts";
    }); };
  },
  async lots_payouts() {
    const [subs, e] = await Promise.all([api("/api/farmers/submissions"), api("/api/earnings/farmer")]);
    view.innerHTML = title("fa-wallet", "My Lots & Payouts", "Track every batch from registration to payment") +
      `<div class="grid g4">${[["Gross earnings", e.gross], ["Deductions", e.deductions], ["Net earnings", e.net], ["Pending payments", e.pending]].map(([l, v]) => `<div class="stat"><small>${l}</small><b class="money">${inr(v, 2)}</b></div>`).join("")}</div>
      ${e.in_unsold_lots ? `<p class="small muted">${inr(e.in_unsold_lots)} more is allocated in lots not yet sold.</p>` : ""}<br>
      <div class="card"><h3><i class="fa-solid fa-boxes-stacked"></i>Registered produce & lot allocation</h3>${subs.length ? batchTable(subs, true) : empty("fa-seedling", "No batches yet")}</div>
      <div class="card"><h3><i class="fa-solid fa-receipt"></i>Payment receipts</h3>${e.receipts.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Receipt</th><th>PO / Lot</th><th>Gross</th><th>Deductions</th><th>Net</th><th>Status</th><th>Reference</th></tr></thead><tbody>${e.receipts.map(r => `<tr><td><b>${r.payment_code}</b></td><td>${r.po_number}<br><span class="small muted">${r.lot_code} · ${esc(r.crop)}</span></td><td>${inr(r.gross_amount, 2)}</td><td>${r.deductions ? inr(r.deductions, 2) + `<br><span class="small muted">${esc(r.deduction_reason)}</span>` : "—"}</td><td class="money">${inr(r.net_payable, 2)}</td><td>${statusPill(r.status)}</td><td class="small">${r.payment_reference ? `${esc(r.payment_method)} ${esc(r.payment_reference)}<br>${fmtDate(r.paid_at)}` : "Awaiting FPO transfer"}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-receipt", "Receipts appear after a buyer confirms delivery")}</div>`;
  },
  gallery: galleryView, mandi: mandiView, centres: centresView,
};
function scanResult(r) {
  const probs = p => p ? Object.entries(p.probabilities).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) => `<div class="prob"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${v * 100}%"></div></div><span>${(v * 100).toFixed(1)}%</span></div>`).join("") : '<span class="small muted">model not installed</span>';
  return `<div class="result ${r.grade}"><div class="small muted">${esc(r.grade_basis)}</div><h2 style="margin:4px 0">${esc(r.grade_label)}</h2>
    ${r.rejection_alert ? `<div class="alert alert-err"><i class="fa-solid fa-ban"></i><div><b>Instant rejection alert:</b> this sample does not meet pooling quality.</div></div>` : ""}
    <p>${esc(r.advisory)}</p><ul class="small">${r.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    <p class="small"><b>Moisture:</b> ${r.moisture.value != null ? r.moisture.value + "% — " : ""}${esc(r.moisture.source)}</p></div>
    <div class="grid g2" style="margin-top:14px"><div><b class="small">Crop identification</b>${probs(r.crop)}</div><div><b class="small">Quality class${r.quality?.model ? " · " + esc(MODEL_LABEL[r.quality.model] || "") : ""}</b>${probs(r.quality)}</div></div>
    ${r.variety ? `<div style="margin-top:10px"><b class="small">Rice variety identification</b>${probs(r.variety)}</div>` : ""}
    ${r.defect ? `<div style="margin-top:10px"><b class="small">Maize defect detection</b>${probs(r.defect)}</div>` : ""}
    <a class="btn btn-outline btn-sm" href="#gallery" style="margin:6px 0"><i class="fa-solid fa-layer-group"></i>See it in the segregation gallery</a>
    <p class="small muted">Models: ${esc(Object.values(r.model_version).join(", ") || "—")} · ${esc(r.timestamp)} UTC</p>
    <button class="btn btn-primary" id="useScan" style="width:100%"><i class="fa-solid fa-arrow-right"></i>Use this assessment to register produce</button>`;
}
function batchTable(subs, detail) {
  const step = s => ({ Pending: 1, "In Yard": 2, Rejected: 2, "Allocated to Lot": 3, Sold: 4 }[s]);
  return `<div class="table-wrap"><table class="t"><thead><tr><th>Batch</th><th>Crop</th><th>Qty (Qtl)</th><th>Grade</th><th>Location</th><th>Harvest</th><th>Status</th>${detail ? "<th>Lot allocation</th>" : ""}</tr></thead><tbody>${subs.map(s => `<tr><td><b>${s.batch_code}</b></td><td>${esc(s.crop)} ${s.variety ? `<span class="small muted">(${esc(s.variety)})</span>` : ""}</td><td>${s.quantity_qtl}<br><span class="small muted">${s.available_quantity} unallocated</span></td><td>${gp(s.grade)}</td><td class="small">${esc(s.location)}</td><td>${fmtDate(s.harvest_date)}</td><td>${statusPill(s.status)}${detail ? `<div class="steps">${["Registered", "In yard", "In lot", "Sold"].map((x, i) => `<div class="${step(s.status) > i ? "on" : ""}">${x}</div>`).join("")}</div>` : ""}</td>${detail ? `<td class="small">${s.allocations?.length ? s.allocations.map(a => `${a.lot_code}: ${a.allocated_quantity} Qtl · ${inr(a.gross_farmer_payment)} <span class="muted">(${a.lot_status})</span>`).join("<br>") : "—"}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- AGGREGATOR
const aggregator = {
  async dashboard() {
    const [e, inflow, reqs] = await Promise.all([api("/api/earnings/aggregator"), api("/api/aggregator/inflow"), api("/api/buyer/requests")]);
    view.innerHTML = `<div class="grid g4">${[["Batches awaiting inspection", inflow.filter(s => s.status === "Pending").length], ["Yard stock (unallocated)", inflow.reduce((a, s) => a + (s.status !== "Pending" ? s.available_quantity : 0), 0).toLocaleString("en-IN") + " Qtl"], ["Open buyer requests", reqs.length], ["Net earnings", inr(e.net_earnings)]].map(([l, v]) => `<div class="stat"><small>${l}</small><b>${v}</b></div>`).join("")}</div><br>${FLOW}`;
  },
  async inflow() {
    state.selected.clear();
    view.innerHTML = title("fa-inbox", "Regional Farmer Inflow Queue", "Inspect new batches, then select compatible ones (same crop and grade) to combine into a standardized commercial lot") +
      `<div class="filters"><select id="fC">${opts(CROPS, "", "All crops")}</select><select id="fG"><option value="">All grades</option><option value="A">Grade A</option><option value="B">Grade B</option><option value="C">Grade C</option><option value="U">Awaiting inspection</option></select>
      <select id="fS"><option value="">All statuses</option><option>Pending</option><option>In Yard</option><option>Allocated to Lot</option></select><input id="fL" placeholder="Filter by location"></div>
      <div id="inOut">${loading()}</div><div class="card tray"><span>Selected volume: <b id="selV">0</b> Quintals · Selected crop: <b id="selC">–</b></span><button class="btn btn-primary" id="combine" disabled><i class="fa-solid fa-boxes-packing"></i>Combine Selected into Commercial Lot</button></div>`;
    const load = async () => {
      const p = new URLSearchParams(); [["crop", "#fC"], ["grade", "#fG"], ["status", "#fS"], ["location", "#fL"]].forEach(([k, s]) => $(s).value && p.set(k, $(s).value));
      const subs = await api("/api/aggregator/inflow?" + p);
      $("#inOut").innerHTML = subs.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Select</th><th>Farmer name</th><th>Crop</th><th>Available (Qtl)</th><th>Quality grade</th><th>Ask ₹/Qtl</th><th>Storage location</th><th>Submitted</th><th>Action</th></tr></thead><tbody>${subs.map(s => `<tr data-id="${s.id}" class="${state.selected.has(s.id) ? "sel" : ""}"><td>${s.status !== "Pending" && s.available_quantity > 0 ? `<input type="checkbox" aria-label="Select ${s.batch_code}" ${state.selected.has(s.id) ? "checked" : ""}>` : ""}</td><td><b>${esc(s.farmer_name)}</b><br><span class="small muted">${s.batch_code}</span></td><td>${esc(s.crop)}</td><td>${s.available_quantity} <span class="small muted">/ ${s.quantity_qtl}</span></td><td>${gp(s.grade)}${s.quality_score ? `<br><span class="small muted">AI conf. ${(s.quality_score * 100).toFixed(0)}%</span>` : ""}</td><td>${inr(s.expected_price_per_qtl)}</td><td class="small">${esc(s.location)}</td><td>${fmtDate(s.created_at)}</td><td>${s.status === "Pending" ? `<button class="btn btn-outline btn-sm" data-review="${s.id}">Inspect</button>` : statusPill(s.status)}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-inbox", "No farmer batches match these filters");
      $("#inOut").querySelectorAll("input[type=checkbox]").forEach(cb => cb.onchange = () => { const s = subs.find(x => x.id == cb.closest("tr").dataset.id); cb.checked ? state.selected.set(s.id, s) : state.selected.delete(s.id); cb.closest("tr").classList.toggle("sel", cb.checked); tray(); });
      $("#inOut").querySelectorAll("[data-review]").forEach(b => b.onclick = () => reviewModal(subs.find(s => s.id == b.dataset.review), load));
    };
    const tray = () => { const sel = [...state.selected.values()]; const crops = new Set(sel.map(s => s.crop + " / Grade " + s.grade)); $("#selV").textContent = sel.reduce((a, s) => a + s.available_quantity, 0); $("#selC").textContent = crops.size === 1 ? [...crops][0] : crops.size ? "Mixed — pick one crop & grade" : "–"; $("#combine").disabled = crops.size !== 1; };
    let tm; ["#fC", "#fG", "#fS", "#fL"].forEach(s => $(s).oninput = () => { clearTimeout(tm); tm = setTimeout(load, 250); });
    $("#combine").onclick = () => lotModal([...state.selected.values()], () => { state.selected.clear(); location.hash = "lots"; });
    await load();
  },
  async demand() {
    const reqs = await api("/api/buyer/requests");
    view.innerHTML = title("fa-clipboard-list", "Buyer Demand Orders", "Accept requests, then match existing lots or auto-assemble a lot from yard stock") +
      (reqs.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Request</th><th>Buyer</th><th>Crop</th><th>Need (Qtl)</th><th>Min grade</th><th>Target ₹/Qtl</th><th>Deliver to / by</th><th>Status</th><th>Actions</th></tr></thead><tbody>${reqs.map(r => `<tr><td><b>${r.request_code}</b></td><td>${esc(r.buyer_name)}</td><td>${esc(r.crop)} ${r.variety ? `<span class="small muted">${esc(r.variety)}</span>` : ""}</td><td>${r.remaining_qty} <span class="small muted">/ ${r.quantity_qtl}</span></td><td>${gp(r.min_grade)}</td><td>${inr(r.target_price)}</td><td class="small">${esc(r.delivery_location)}<br>${fmtDate(r.delivery_date)}</td><td>${statusPill(r.status)}</td><td style="white-space:nowrap">${r.status === "Open" ? `<button class="btn btn-primary btn-sm" data-acc="${r.id}">Accept</button> <button class="btn btn-danger btn-sm" data-rej="${r.id}">Reject</button>` : `<button class="btn btn-outline btn-sm" data-match="${r.id}">Matches</button> <button class="btn btn-primary btn-sm" data-auto="${r.id}">Auto-assemble</button>`}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-clipboard", "No open buyer requests")) + '<div id="dOut"></div>';
    const set = (id, status) => api(`/api/buyer/requests/${id}/status`, { method: "PUT", body: { status } }).then(() => { toast("Request " + status.toLowerCase()); aggregator.demand(); }).catch(e => toast(e.message, true));
    view.querySelectorAll("[data-acc]").forEach(b => b.onclick = () => set(b.dataset.acc, "Accepted"));
    view.querySelectorAll("[data-rej]").forEach(b => b.onclick = () => set(b.dataset.rej, "Rejected"));
    view.querySelectorAll("[data-match]").forEach(b => b.onclick = () => withBusy(b, async () => { const m = await api("/api/matchmaking/suggestions/" + b.dataset.match); $("#dOut").innerHTML = `<br><div class="card"><h3>Lots matching ${m.request.request_code}</h3>${m.matches.length ? `<div class="grid g3">${m.matches.map(l => lotCard(l)).join("")}</div>` : empty("fa-magnifying-glass", "No available lot meets every requirement yet. Try Auto-assemble.")}</div>`; }));
    view.querySelectorAll("[data-auto]").forEach(b => b.onclick = () => { const r = reqs.find(x => x.id == b.dataset.auto); modal(`<h3>Auto-assemble lot for ${r.request_code}</h3><p class="small muted">Pools yard batches of ${esc(r.crop)} at Grade ${r.min_grade} or better (single grade, oldest first) up to ${r.remaining_qty} Qtl. Any shortfall is reported.</p><label class="f">Lot sale price to buyer (₹/Qtl)<input id="aP" type="number" min="1" value="${r.target_price}"></label><br><div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="aGo">Assemble lot</button></div>`, (m, close) => { m.querySelector("#aGo").onclick = e => withBusy(e.currentTarget, async () => { const l = await api("/api/matchmaking/auto-assemble", { method: "POST", body: { request_id: r.id, base_price_per_qtl: +m.querySelector("#aP").value } }); close(); toast(`${l.lot_code} created: ${l.total_quantity} Qtl${l.shortfall_qtl ? `, shortfall ${l.shortfall_qtl} Qtl` : ""}`); location.hash = "lots"; }); }); });
  },
  async lots() {
    const lots = await api("/api/lots");
    view.innerHTML = title("fa-boxes-stacked", "Active Commercial Lots in Marketplace", "Standardized aggregated lots available for buyer procurement matching") +
      (lots.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Lot ID</th><th>Crop commodity</th><th>Total quantity</th><th>Certified grade</th><th>Contributing farmers</th><th>Marketplace status</th><th>Farmer cost</th><th>Gross lot value</th><th></th></tr></thead><tbody>${lots.map(l => `<tr><td><b>${l.lot_code}</b></td><td>${esc(l.crop)}${l.variety ? ` (${esc(l.variety)})` : ""}</td><td>${l.total_quantity} Qtl</td><td>${gp(l.grade)}</td><td class="small">${esc(l.contributing_farmers.join(", "))}</td><td>${statusPill(l.status)}</td><td>${inr(l.farmer_cost)}</td><td class="money">${inr(l.gross_value)}</td><td>${l.status === "Available" ? `<button class="btn btn-outline btn-sm" data-add="${l.id}">Add batch</button> <button class="btn btn-danger btn-sm" data-cancel="${l.id}">Cancel</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-box-open", "No lots yet — combine batches from the Farmer Inflow queue"));
    view.querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => modal(`<h3>Cancel lot?</h3><p>All allocated farmer quantities will be released back to the yard.</p><div class="tray"><button class="btn btn-outline" data-close>Keep</button><button class="btn btn-danger" id="cGo">Cancel lot</button></div>`, (m, close) => m.querySelector("#cGo").onclick = e => withBusy(e.currentTarget, async () => { await api(`/api/lots/${b.dataset.cancel}/status`, { method: "PUT", body: { status: "Cancelled" } }); close(); aggregator.lots(); })));
    view.querySelectorAll("[data-add]").forEach(b => b.onclick = async () => {
      const lot = lots.find(l => l.id == b.dataset.add);
      const subs = (await api(`/api/aggregator/inflow?crop=${encodeURIComponent(lot.crop)}&grade=${lot.grade}`)).filter(s => s.status !== "Pending" && s.available_quantity > 0);
      modal(`<h3>Add batch to ${lot.lot_code}</h3>${subs.length ? `<label class="f">Batch<select id="bS">${subs.map(s => `<option value="${s.id}">${s.batch_code} · ${esc(s.farmer_name)} · ${s.available_quantity} Qtl</option>`).join("")}</select></label><br><label class="f">Quantity (Qtl)<input id="bQ" type="number" step="0.01" min="0.01" value="${subs[0].available_quantity}"></label><br><div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="bGo">Allocate</button></div>` : `<p class="muted">No ${esc(lot.crop)} Grade ${lot.grade} stock available in the yard.</p><button class="btn btn-outline" data-close>Close</button>`}`,
        (m, close) => m.querySelector("#bGo")?.addEventListener("click", e => withBusy(e.currentTarget, async () => { await api("/api/lots/allocate", { method: "POST", body: { lot_id: lot.id, submission_id: +m.querySelector("#bS").value, quantity: +m.querySelector("#bQ").value } }); close(); toast("Allocated"); aggregator.lots(); })));
    });
  },
  async orders() {
    const txns = await api("/api/transactions");
    view.innerHTML = title("fa-truck", "Dispatch, Delivery & Settlement", "Dispatch procured lots, then record farmer payouts and buyer receipts with bank references") +
      (txns.length ? txns.map(x => txnCard(x)).join("") : empty("fa-truck", "No purchase orders yet"));
    bindTxnActions();
  },
  async ledger() {
    const [e, txns] = await Promise.all([api("/api/earnings/aggregator"), api("/api/transactions")]);
    view.innerHTML = title("fa-book", "Financial Ledger & Commission", "All figures are computed from stored transactions") +
      `<div class="grid g4">${[["Crop sales revenue", inr(e.revenue)], ["Farmer procurement cost", inr(e.farmer_cost)], ["Gross margin (commission)", inr(e.gross_margin) + (e.commission_pct != null ? ` <span class="small muted">${e.commission_pct}%</span>` : "")], ["Logistics expenses", inr(e.logistics)], ["Net aggregator earnings", inr(e.net_earnings)], ["Farmer payouts pending", inr(e.farmer_payouts_pending)], ["Buyer receivables", inr(e.buyer_receivables)], ["Orders", e.orders]].map(([l, v]) => `<div class="stat"><small>${l}</small><b>${v}</b></div>`).join("")}</div><br>
      <div class="card"><h3><i class="fa-solid fa-list"></i>Transaction history</h3>${txns.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Txn / PO</th><th>Lot</th><th>Qty</th><th>Revenue</th><th>Farmer cost</th><th>Logistics</th><th>Net</th><th>Status</th><th>Created</th></tr></thead><tbody>${txns.map(x => `<tr><td><b>${x.transaction_code}</b><br><span class="small muted">${x.po_number}</span></td><td>${x.lot.lot_code}</td><td>${x.quantity_qtl}</td><td>${inr(x.crop_sales_revenue)}</td><td>${inr(x.farmer_procurement_cost)}</td><td>${inr(x.transport_charges)}</td><td class="money">${inr(x.aggregator_net_earnings)}</td><td>${statusPill(x.status)}</td><td>${fmtDate(x.created_at)}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-book", "No transactions")}</div>`;
  },
  weather: () => weatherView(false), gallery: galleryView, mandi: mandiView, centres: centresView,
};
function reviewModal(s, reload) {
  modal(`<h3>Yard inspection · ${s.batch_code}</h3><p class="small">${esc(s.farmer_name)} · ${esc(s.crop)} · ${s.quantity_qtl} Qtl · AI: ${s.grade === "U" ? "no grade" : "Grade " + s.grade}</p>
  <div class="form-grid"><label class="f">Inspected grade<select id="rG">${["A", "B", "C"].map(g => `<option ${g === s.grade ? "selected" : ""}>${g}</option>`).join("")}</select></label><label class="f">Moisture-meter reading (%)<input id="rM" type="number" step="0.1" min="1" max="39" value="${s.moisture ?? ""}"></label>
  <label class="f span2">Note to farmer<input id="rN" maxlength="300"></label></div><br>
  <div class="tray"><button class="btn btn-danger" id="rRej">Reject batch</button><button class="btn btn-primary" id="rAcc">Accept into yard</button></div>`, (m, close) => {
    const go = (decision, btn) => withBusy(btn, async () => { await api(`/api/aggregator/inflow/${s.id}/review`, { method: "POST", body: { decision, grade: m.querySelector("#rG").value, moisture: m.querySelector("#rM").value ? +m.querySelector("#rM").value : null, note: m.querySelector("#rN").value || null } }); close(); toast("Batch " + (decision === "accept" ? "accepted" : "rejected")); reload(); });
    m.querySelector("#rAcc").onclick = e => go("accept", e.currentTarget); m.querySelector("#rRej").onclick = e => go("reject", e.currentTarget);
  });
}
function lotModal(sel, after) {
  const s0 = sel[0], m = mspFor(s0.crop), maxAsk = Math.max(...sel.map(s => s.expected_price_per_qtl));
  modal(`<h3>Create commercial lot</h3><p class="small muted">${esc(s0.crop)} · Grade ${s0.grade} · ${sel.length} batch(es)</p>
  <div class="table-wrap"><table class="t"><thead><tr><th>Batch</th><th>Farmer</th><th>Ask ₹/Qtl</th><th>Allocate Qtl</th></tr></thead><tbody>${sel.map(s => `<tr><td>${s.batch_code}</td><td>${esc(s.farmer_name)}</td><td>${inr(s.expected_price_per_qtl)}</td><td><input type="number" data-sid="${s.id}" step="0.01" min="0.01" max="${s.available_quantity}" value="${s.available_quantity}" style="width:90px;border:1px solid #cbd5e1;border-radius:8px;padding:6px"></td></tr>`).join("")}</tbody></table></div><br>
  <div class="form-grid"><label class="f">Variety<input id="lV" value="${esc(s0.variety || "")}"></label><label class="f">Sale price to buyer (₹/Qtl)<input id="lP" type="number" min="1" value="${maxAsk}"><span class="hint">${m ? `MSP ${inr(m.msp_per_qtl)} · ` : ""}highest farmer ask ${inr(maxAsk)}</span></label>
  <label class="f">Lot moisture (meter %)<input id="lM" type="number" step="0.1" min="1" max="39"></label><label class="f">Lot location<input id="lL" value="${esc(s0.location)}"></label></div><br>
  <div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="lGo"><i class="fa-solid fa-boxes-packing"></i>Create lot</button></div>`, (md, close) => {
    md.querySelector("#lGo").onclick = e => withBusy(e.currentTarget, async () => {
      const allocations = [...md.querySelectorAll("[data-sid]")].map(i => ({ submission_id: +i.dataset.sid, quantity: +i.value })).filter(a => a.quantity > 0);
      const l = await api("/api/lots/create", { method: "POST", body: { crop: s0.crop, grade: s0.grade, variety: md.querySelector("#lV").value || null, base_price_per_qtl: +md.querySelector("#lP").value, moisture: md.querySelector("#lM").value ? +md.querySelector("#lM").value : null, location: md.querySelector("#lL").value || null, allocations } });
      close(); toast(`${l.lot_code} created with ${l.total_quantity} Qtl`); after();
    });
  });
}

// ---------------------------------------------------------------- BUYER
function lotCard(l, procure) {
  return `<div class="lot-card"><div class="top"><span class="lot-id">${l.lot_code}</span>${gp(l.grade)}</div><h4>${esc(l.crop)}</h4>
  <div class="small muted">${esc(l.variety || "Standard")}${l.moisture ? ` · Moisture ${l.moisture}% (meter)` : ""}</div>
  <div class="price">${inr(l.base_price_per_qtl)} <small>/ Qtl</small></div>
  <div class="kv"><b>Volume:</b> ${l.total_quantity} Qtl</div><div class="kv"><b>Sourced from:</b> ${esc(l.aggregator || "—")}</div><div class="kv"><b>Location:</b> ${esc(l.location || "—")}</div>
  <div class="kv"><b>Farmers:</b> ${esc(l.contributing_farmers.join(", "))}</div>
  ${l.reasons ? `<div class="kv"><b>Match score ${l.score}</b></div><ul class="reasons">${l.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
  ${procure ? `<button class="btn btn-soft" data-procure="${l.id}" style="margin-top:8px;color:#1d4ed8"><i class="fa-solid fa-cart-shopping"></i>Procure Entire Lot (${inr(l.gross_value)})</button>` : ""}</div>`;
}
function procureModal(l, requestId, after) {
  const key = uuid();
  modal(`<h3>Confirm purchase · ${l.lot_code}</h3><p>${l.total_quantity} Qtl ${esc(l.crop)} Grade ${l.grade} at ${inr(l.base_price_per_qtl)}/Qtl = <b class="money">${inr(l.gross_value)}</b></p>
  <h4>Farm-gate traceability</h4><div class="table-wrap"><table class="t"><thead><tr><th>Batch</th><th>Farmer</th><th>Location</th><th>Harvest</th><th>Qtl</th><th>Farmer rate</th><th>Quality evidence</th></tr></thead><tbody>${l.allocations.map(a => `<tr><td>${a.batch_code}</td><td>${esc(a.farmer_name)}</td><td class="small">${esc(a.location)}</td><td>${fmtDate(a.harvest_date)}</td><td>${a.allocated_quantity}</td><td>${inr(a.farmer_price_per_qtl)}</td><td class="small">${a.quality_class ? `AI ${esc(a.quality_class)} ${(a.quality_confidence * 100).toFixed(0)}%` : "FPO inspection"}</td></tr>`).join("")}</tbody></table></div>
  <p class="small">Farmers receive <b>${inr(l.farmer_cost)}</b> (${((l.farmer_cost / l.gross_value) * 100).toFixed(1)}% of the price); the FPO margin is ${inr(l.gross_value - l.farmer_cost)} before logistics.</p>
  <div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="pGo"><i class="fa-solid fa-file-contract"></i>Place purchase order</button></div>`, (m, close) => {
    m.querySelector("#pGo").onclick = e => withBusy(e.currentTarget, async () => { const t = await api("/api/procure", { method: "POST", body: { lot_id: l.id, request_id: requestId || null, idempotency_key: key } }); close(); after(t); });
  });
}
const buyer = {
  async dashboard() {
    const [e, reqs] = await Promise.all([api("/api/earnings/buyer"), api("/api/buyer/requests")]);
    view.innerHTML = `<div class="grid g4">${[["Open requirements", reqs.filter(r => /Open|Accepted|Partially/.test(r.status)).length], ["Purchase orders", e.orders], ["Volume procured", e.volume_qtl + " Qtl"], ["Total spend", inr(e.total_spend)]].map(([l, v]) => `<div class="stat"><small>${l}</small><b>${v}</b></div>`).join("")}</div><br>${FLOW}
    <div class="card"><h3><i class="fa-solid fa-clock-rotate-left"></i>Procurement history</h3>${reqs.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Request</th><th>Crop</th><th>Qty</th><th>Remaining</th><th>Min grade</th><th>By</th><th>Status</th></tr></thead><tbody>${reqs.map(r => `<tr><td><b>${r.request_code}</b></td><td>${esc(r.crop)}</td><td>${r.quantity_qtl}</td><td>${r.remaining_qty}</td><td>${gp(r.min_grade)}</td><td>${fmtDate(r.delivery_date)}</td><td>${statusPill(r.status)}</td></tr>`).join("")}</tbody></table></div>` : empty("fa-file", "No requirements posted yet")}</div>`;
  },
  async post_req() {
    view.innerHTML = title("fa-file-signature", "Post a Bulk Procurement Requirement", "FPOs are notified immediately and can match or assemble lots for you") +
      `<div class="card narrow"><form id="qf" novalidate><div class="form-grid"><label class="f">Required commodity<select name="crop">${opts(CROPS, "Wheat")}</select></label><label class="f">Required variety<input name="variety" placeholder="Any"></label>
      <label class="f">Required quantity (Quintals)<input name="quantity_qtl" type="number" min="1" step="0.01" required></label><label class="f">Minimum acceptable grade<select name="min_grade"><option value="A">Grade A (Premium)</option><option value="B" selected>Grade B or higher</option><option value="C">Grade C or higher</option></select></label>
      <label class="f">Target price (₹/Qtl)<input name="target_price" type="number" min="1" required><span class="hint" id="qMsp"></span></label><label class="f">Required delivery date<input name="delivery_date" type="date" min="${today()}" required></label>
      <label class="f span2">Delivery location<input name="delivery_location" required value="${esc(ME.location || "")}"></label>
      <label class="f span2" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" name="partial_ok" checked style="min-height:auto"> Accept partial fulfilment from multiple lots</label></div><br>
      <button class="btn btn-primary" style="width:100%"><i class="fa-solid fa-paper-plane"></i>Submit requirement</button></form></div>`;
    const f = $("#qf"), key = uuid(); const h = () => { const m = mspFor(f.crop.value); $("#qMsp").textContent = m ? `MSP ${inr(m.msp_per_qtl)}/Qtl` : ""; }; f.crop.onchange = h; h();
    f.onsubmit = e => { e.preventDefault(); withBusy(f.querySelector("button"), async () => {
      const d = Object.fromEntries(new FormData(f)); d.partial_ok = !!d.partial_ok; d.quantity_qtl = +d.quantity_qtl; d.target_price = +d.target_price; d.variety ||= null; d.idempotency_key = key;
      if (!d.quantity_qtl || !d.target_price || !d.delivery_date || !d.delivery_location) throw new Error("Please complete all required fields");
      const r = await api("/api/buyer/requests", { method: "POST", body: d }); toast(`${r.request_code} posted`); location.hash = "matching?r=" + r.id;
    }); };
  },
  async marketplace() {
    const lots = await api("/api/lots");
    view.innerHTML = title("fa-basket-shopping", "Certified Aggregated Lots Marketplace", "Bulk lots ready for procurement with farm-gate traceability") +
      `<div class="filters"><select id="mkC">${opts(CROPS, "", "All crops")}</select><select id="mkG"><option value="">Any grade</option><option value="A">Grade A</option><option value="B">Grade B+</option></select></div><div id="mkOut"></div>`;
    const draw = () => { const c = $("#mkC").value, g = $("#mkG").value; const ls = lots.filter(l => (!c || l.crop === c) && (!g || (g === "A" ? l.grade === "A" : l.grade !== "C")));
      $("#mkOut").innerHTML = ls.length ? `<div class="grid g3">${ls.map(l => lotCard(l, true)).join("")}</div>` : empty("fa-box-open", "No certified lots available right now");
      $("#mkOut").querySelectorAll("[data-procure]").forEach(b => b.onclick = () => procureModal(lots.find(l => l.id == b.dataset.procure), null, t => { toast(`${t.po_number} placed`); location.hash = "orders"; })); };
    $("#mkC").onchange = $("#mkG").onchange = draw; draw();
  },
  async matching() {
    const reqs = (await api("/api/buyer/requests")).filter(r => /Open|Accepted|Partially/.test(r.status));
    const pre = new URLSearchParams(location.hash.split("?")[1] || "").get("r");
    view.innerHTML = title("fa-gears", "Automated Procurement Matching Engine", "Strict checks first (crop, variety, grade, price, quantity), then an explainable score") +
      `<div class="card narrow">${reqs.length ? `<label class="f">Your requirement<select id="mR">${reqs.map(r => `<option value="${r.id}" ${r.id == pre ? "selected" : ""}>${r.request_code} · ${esc(r.crop)} · ${r.remaining_qty} Qtl · Grade ${r.min_grade}+ · ≤ ${inr(r.target_price)}</option>`).join("")}</select></label><br>
      <button class="btn btn-soft" id="mGo" style="color:#1d4ed8"><i class="fa-solid fa-magnifying-glass"></i>Execute Matching Engine</button>` : `${empty("fa-file-signature", "Post a requirement first")}<a class="btn btn-primary" href="#post_req">Post requirement</a>`}</div><div id="mOut"></div>`;
    if (!reqs.length) return;
    const run = async () => { const m = await api("/api/matchmaking/suggestions/" + $("#mR").value);
      $("#mOut").innerHTML = `<br>${m.matches.length ? `<div class="grid g3">${m.matches.map(l => lotCard(l, true)).join("")}</div>` : `<div class="card narrow">${empty("fa-hourglass-half", "No lot meets every requirement yet. FPOs have been notified of your request and can assemble a lot.")}</div>`}`;
      $("#mOut").querySelectorAll("[data-procure]").forEach(b => b.onclick = () => procureModal(m.matches.find(l => l.id == b.dataset.procure), m.request.id, t => {
        $("#mOut").innerHTML = `<br><div class="card narrow alert-ok"><h3 style="color:var(--g800)"><i class="fa-solid fa-circle-check"></i>Procurement Match Confirmed: ${t.po_number}</h3><p><b>Lot:</b> ${b.closest(".lot-card").querySelector(".lot-id").textContent} · <b>Quantity:</b> ${t.quantity_qtl} Qtl<br><b>Gross contract value:</b> ${inr(t.crop_sales_revenue)}<br><b>Paid to farmers:</b> ${inr(t.farmer_procurement_cost)}</p><a class="btn btn-outline" href="#orders"><i class="fa-solid fa-truck"></i>Track order</a></div>`;
      })); };
    $("#mGo").onclick = e => withBusy(e.currentTarget, run); if (pre) await run();
  },
  async orders() {
    const txns = await api("/api/transactions");
    view.innerHTML = title("fa-truck", "Orders, Dispatch & Delivery", "Track each purchase order and confirm delivery to release farmer payments") + (txns.length ? txns.map(x => txnCard(x)).join("") : empty("fa-truck", "No orders yet"));
    bindTxnActions();
  },
  async expenditure() {
    const [e, txns] = await Promise.all([api("/api/earnings/buyer"), api("/api/transactions")]);
    const rows = txns.map(x => { const m = mspFor(x.lot.crop); return { x, m, diff: m ? (x.price_per_qtl - m.msp_per_qtl) * x.quantity_qtl : null }; });
    view.innerHTML = title("fa-indian-rupee-sign", "Procurement Expenditure Dashboard", "Where every rupee of your spend went") +
      `<div class="grid g4">${[["Total spend", e.total_spend], ["Reached farmers", e.to_farmers], ["FPO margin", e.aggregator_margin], ["Outstanding invoices", e.outstanding]].map(([l, v]) => `<div class="stat"><small>${l}</small><b class="money">${inr(v)}</b></div>`).join("")}</div>
      <p class="small muted">Logistics borne by FPOs on your orders: ${inr(e.logistics_borne_by_fpo)}. Farmer share of spend: ${e.total_spend ? ((e.to_farmers / e.total_spend) * 100).toFixed(1) + "%" : "—"}.</p>
      <div class="card"><h3><i class="fa-solid fa-scale-balanced"></i>Price vs MSP benchmark</h3>${rows.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>PO</th><th>Crop</th><th>Qty</th><th>Paid ₹/Qtl</th><th>MSP ₹/Qtl</th><th>Premium over MSP</th><th>Farmer share</th><th>Logistics</th></tr></thead><tbody>${rows.map(({ x, m, diff }) => `<tr><td>${x.po_number}</td><td>${esc(x.lot.crop)}</td><td>${x.quantity_qtl}</td><td>${inr(x.price_per_qtl)}</td><td>${m ? inr(m.msp_per_qtl) : "—"}</td><td>${diff == null ? "—" : inr(diff)}</td><td>${inr(x.farmer_procurement_cost)}</td><td>${inr(x.transport_charges)}</td></tr>`).join("")}</tbody></table></div><p class="small muted">For savings against live mandi modal prices, compare on the <a href="#mandi">Mandi MSP Rates</a> page (Agmarknet).</p>` : empty("fa-chart-pie", "No purchases yet")}</div>`;
  },
  gallery: galleryView, mandi: mandiView, centres: centresView,
};

// ---------------------------------------------------------------- shared transaction card
function txnCard(x) {
  const steps = ["Ordered", "Dispatched", "Delivered", "Settled"], at = steps.indexOf(x.status);
  const role = ME.role;
  return `<div class="card" data-txn="${x.id}"><div class="tray" style="margin:0"><h3 style="margin:0">${x.po_number} · ${x.lot.lot_code} · ${esc(x.lot.crop)} Grade ${x.lot.grade}</h3>${statusPill(x.status)}</div>
  <div class="steps">${steps.map((s, i) => `<div class="${i <= at ? "on" : ""}">${s}</div>`).join("")}</div>
  <div class="grid g4">${[["Quantity", x.quantity_qtl + " Qtl"], ["Buyer pays", inr(x.total_buyer_cost)], ["To farmers", inr(x.farmer_procurement_cost)], ["Logistics", inr(x.transport_charges)]].map(([l, v]) => `<div class="stat"><small>${l}</small><b>${v}</b></div>`).join("")}</div>
  <p class="small muted">Buyer: ${esc(x.buyer.organisation || x.buyer.name)} · FPO: ${esc(x.lot.aggregator)} ${x.carrier ? `· Carrier ${esc(x.carrier)} (${esc(x.vehicle_number)}) dispatched ${fmtDate(x.dispatched_at)}` : ""} ${x.delivered_at ? `· Delivered ${fmtDate(x.delivered_at)}` : ""}</p>
  ${x.farmer_payments.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Farmer payment</th><th>Farmer</th><th>Gross</th><th>Deductions</th><th>Net</th><th>Status</th>${role === "aggregator" ? "<th></th>" : ""}</tr></thead><tbody>${x.farmer_payments.map(p => `<tr><td>${p.payment_code}</td><td>${esc(p.farmer_name)}</td><td>${inr(p.gross_amount, 2)}</td><td>${p.deductions ? inr(p.deductions, 2) + " · " + esc(p.deduction_reason) : "—"}</td><td>${inr(p.net_payable, 2)}</td><td>${statusPill(p.status)}${p.payment_reference ? `<br><span class="small muted">${esc(p.payment_method)} ${esc(p.payment_reference)}</span>` : ""}</td>${role === "aggregator" ? `<td>${p.status === "Pending" ? `<button class="btn btn-primary btn-sm" data-fpay='${JSON.stringify({ id: p.id, gross: p.gross_amount, name: p.farmer_name })}'>Record payout</button>` : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div><br>` : ""}
  <div class="hero-actions">
  ${role === "aggregator" && x.status === "Ordered" ? `<button class="btn btn-primary" data-dispatch="${x.id}"><i class="fa-solid fa-truck-fast"></i>Dispatch</button>` : ""}
  ${role === "buyer" && x.status === "Dispatched" ? `<button class="btn btn-primary" data-deliver="${x.id}"><i class="fa-solid fa-circle-check"></i>Confirm delivery</button>` : ""}
  ${role === "aggregator" && x.buyer_payment?.status === "Due" ? `<button class="btn btn-outline" data-bpay="${x.id}"><i class="fa-solid fa-building-columns"></i>Confirm buyer payment received</button>` : ""}
  ${role === "buyer" && x.buyer_payment?.status === "Due" ? (HEALTH.integrations.razorpay ? `<button class="btn btn-outline" data-online="${x.id}"><i class="fa-solid fa-credit-card"></i>Pay ${inr(x.buyer_payment.outstanding_balance)} online</button>` : `<span class="small muted">Pay by bank transfer quoting ${x.po_number}; the FPO confirms receipt. (Online payment activates once Razorpay keys are configured.)</span>`) : ""}
  ${/Delivered|Settled/.test(x.status) && role !== "farmer" ? `<button class="btn btn-outline" data-invoice="${x.id}"><i class="fa-solid fa-file-invoice"></i>View settlement invoice</button>` : ""}</div></div>`;
}
function bindTxnActions() {
  const reload = () => VIEWS[ME.role].orders();
  view.querySelectorAll("[data-invoice]").forEach(b => b.onclick = () => openInvoice(b.dataset.invoice));
  view.querySelectorAll("[data-deliver]").forEach(b => b.onclick = () => modal(`<h3>Confirm delivery received?</h3><p>Confirm only after inspecting the consignment. This releases payables to every farmer in the lot and issues your invoice.</p><div class="tray"><button class="btn btn-outline" data-close>Not yet</button><button class="btn btn-primary" id="dGo">Confirm delivery</button></div>`, (m, close) => m.querySelector("#dGo").onclick = e => withBusy(e.currentTarget, async () => { await api("/api/transactions/complete", { method: "POST", body: { transaction_id: +b.dataset.deliver } }); close(); toast("Delivery confirmed"); reload(); })));
  view.querySelectorAll("[data-dispatch]").forEach(b => b.onclick = () => modal(`<h3>Dispatch order</h3><div class="form-grid"><label class="f">Carrier<input id="dC" required></label><label class="f">Vehicle number<input id="dV" required placeholder="TN23AB1234"></label><label class="f span2">Logistics expense (₹)<input id="dT" type="number" min="0" step="1" required><span class="hint">Freight paid by the FPO; deducted from FPO margin, not from farmers</span></label></div><br><div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="dGo">Dispatch</button></div>`, (m, close) => m.querySelector("#dGo").onclick = e => withBusy(e.currentTarget, async () => { await api(`/api/transactions/${b.dataset.dispatch}/dispatch`, { method: "POST", body: { carrier: m.querySelector("#dC").value, vehicle_number: m.querySelector("#dV").value, transport_charges: +m.querySelector("#dT").value } }); close(); toast("Dispatched — buyer notified"); reload(); })));
  view.querySelectorAll("[data-fpay]").forEach(b => { const p = JSON.parse(b.dataset.fpay); b.onclick = () => modal(`<h3>Record payout to ${esc(p.name)}</h3><p class="small muted">Enter the reference of the bank/UPI transfer you have made. Gross ${inr(p.gross, 2)}.</p><div class="form-grid"><label class="f">Method<select id="pM">${opts(["UPI", "NEFT", "IMPS", "RTGS", "Cheque"], "UPI")}</select></label><label class="f">Transfer reference (UTR)<input id="pR" required minlength="4"></label><label class="f">Deductions (₹)<input id="pD" type="number" min="0" value="0"></label><label class="f">Deduction reason<input id="pW" placeholder="Required if deducting"></label></div><br><div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="pGo">Record payment</button></div>`, (m, close) => m.querySelector("#pGo").onclick = e => withBusy(e.currentTarget, async () => { await api("/api/payments/farmer/record", { method: "POST", body: { payment_id: p.id, payment_method: m.querySelector("#pM").value, payment_reference: m.querySelector("#pR").value, deductions: +m.querySelector("#pD").value || 0, deduction_reason: m.querySelector("#pW").value || null } }); close(); toast("Payment recorded — farmer notified"); reload(); })); });
  view.querySelectorAll("[data-bpay]").forEach(b => b.onclick = () => modal(`<h3>Confirm buyer payment received</h3><div class="form-grid"><label class="f">Method<select id="bM">${opts(["NEFT", "RTGS", "IMPS", "UPI", "Cheque"], "RTGS")}</select></label><label class="f">Bank reference<input id="bR" minlength="4"></label></div><br><div class="tray"><button class="btn btn-outline" data-close>Cancel</button><button class="btn btn-primary" id="bGo">Confirm</button></div>`, (m, close) => m.querySelector("#bGo").onclick = e => withBusy(e.currentTarget, async () => { await api("/api/payments/buyer/record", { method: "POST", body: { transaction_id: +b.dataset.bpay, payment_method: m.querySelector("#bM").value, payment_reference: m.querySelector("#bR").value } }); close(); toast("Receipt recorded"); reload(); })));
  view.querySelectorAll("[data-online]").forEach(b => b.onclick = () => withBusy(b, async () => {
    const o = await api("/api/payments/razorpay/order", { method: "POST", body: { transaction_id: +b.dataset.online } });
    await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://checkout.razorpay.com/v1/checkout.js"; s.onload = res; s.onerror = () => rej(new Error("Could not load Razorpay")); document.head.append(s); });
    new window.Razorpay({ key: o.key_id, order_id: o.order.id, amount: o.order.amount, currency: "INR", name: "Agri-Link", description: "Lot payment",
      handler: () => toast("Payment submitted. It will show as Paid once Razorpay confirms it to our server.") }).open();
  }));
}

const VIEWS = { farmer, aggregator, buyer };

// ---------------------------------------------------------------- boot
(async function boot() {
  if (!token()) return location.replace("/login.html");
  try { ME = await api("/api/auth/me"); } catch { return location.replace("/login.html"); }
  if (ME.language && ME.language !== getLang()) setLang(ME.language);
  try { MSP = await api("/api/msp"); } catch {}
  try { HEALTH = await api("/api/health"); } catch {}
  renderChrome(); await route();
  lastUnread = -1; await pollNotes(); lastUnread = Number($("#unread").textContent) || 0;
  setInterval(pollNotes, 15000);
})();
