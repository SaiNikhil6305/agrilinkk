"""Agri-Link FastAPI server: auth, AI inference, procurement engine, ledger, reference data."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import requests
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import crop_segregation
import inference
from db import get_conn, init_db, rows, write_txn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "crop_quality_results")
SAMPLES_DIR = os.path.join(ROOT, "crop_samples")
DIST_DIR = os.path.join(ROOT, "dist")
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
DATA_GOV_IN_API_KEY = os.environ.get("DATA_GOV_IN_API_KEY", "")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"  # data.gov.in: daily mandi prices
CATEGORY_FOR_GRADE = {"A": "Premium", "B": "Good", "C": "Bad"}
GRADE_RANK = {"A": 3, "B": 2, "C": 1, "U": 0}

app = FastAPI(title="Agri-Link API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.on_event("startup")
def _startup():
    for c in CATEGORY_FOR_GRADE.values():
        os.makedirs(os.path.join(RESULTS_DIR, c), exist_ok=True)
    init_db()


# ---------------------------------------------------------------- helpers
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: int = 60):
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests — please wait a minute and try again.")
    q.append(now)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def notify(conn, user_id: int, title: str, message: str, link: str | None = None):
    conn.execute("INSERT INTO notifications(user_id,title,message,link) VALUES (?,?,?,?)", (user_id, title, message, link))


def notify_role(conn, role: str, title: str, message: str, link: str | None = None):
    for r in conn.execute("SELECT id FROM users WHERE role=?", (role,)).fetchall():
        notify(conn, r["id"], title, message, link)


def audit(conn, user_id, action, entity=None, entity_id=None, detail=None):
    conn.execute("INSERT INTO audit_logs(user_id,action,entity,entity_id,detail) VALUES (?,?,?,?,?)",
                 (user_id, action, entity, str(entity_id) if entity_id is not None else None,
                  json.dumps(detail) if detail is not None else None))


def code(conn, table: str, col: str, prefix: str, row_id: int, width: int = 5) -> str:
    c = f"{prefix}-{row_id:0{width}d}"
    conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (c, row_id))
    return c


def money(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def public_user(u) -> dict:
    d = dict(u)
    for k in ("password_hash", "salt"):
        d.pop(k, None)
    return d


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in")
    token = authorization[7:]
    with get_conn() as c:
        u = c.execute(
            "SELECT u.* FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at > ?",
            (token, now_iso())).fetchone()
    if not u:
        raise HTTPException(401, "Session expired — please sign in again")
    return dict(u)


def require(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(403, f"This action is only available to: {', '.join(roles)}")
        return user
    return dep


def new_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO user_sessions(token,user_id,expires_at) VALUES (?,?,?)", (token, user_id, exp))
    conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now_iso(),))
    return token


# ---------------------------------------------------------------- system
@app.get("/api/health")
def health():
    with get_conn() as c:
        c.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": "ok", "models": {k: v["installed"] for k, v in inference.model_status().items()},
            "integrations": {"firebase": bool(FIREBASE_PROJECT_ID), "mandi_prices_api": bool(DATA_GOV_IN_API_KEY),
                             "razorpay": bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)},
            "time": now_iso()}


@app.get("/api/models")
def models():
    return inference.model_status()


@app.get("/api/samples")
def samples():
    out = {}
    if os.path.isdir(SAMPLES_DIR):
        for crop in sorted(os.listdir(SAMPLES_DIR)):
            d = os.path.join(SAMPLES_DIR, crop)
            if os.path.isdir(d):
                out[crop] = [f"/crop_samples/{crop}/{f}" for f in sorted(os.listdir(d))
                             if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    return out


# ---------------------------------------------------------------- auth
ROLES = ("farmer", "aggregator", "buyer")


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    role: str
    organisation: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    state: str | None = None
    district: str | None = None

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v not in ROLES:
            raise ValueError("role must be farmer, aggregator or buyer")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email")
        return v.lower() if v else v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v:
            v = re.sub(r"[\s-]", "", v)
            if not re.fullmatch(r"\+?\d{10,13}", v):
                raise ValueError("invalid phone number")
        return v


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request):
    rate_limit("reg:" + request.client.host, 10)
    if not body.email and not body.phone:
        raise HTTPException(422, "Email or phone is required")
    salt = secrets.token_hex(16)
    with get_conn() as c, write_txn(c):
        dup = c.execute("SELECT 1 FROM users WHERE (email IS NOT NULL AND email=?) OR (phone IS NOT NULL AND phone=?)",
                        (body.email, body.phone)).fetchone()
        if dup:
            raise HTTPException(409, "An account with this email or phone already exists")
        cur = c.execute(
            "INSERT INTO users(name,email,phone,role,password_hash,salt,organisation,location,state,district) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (body.name, body.email, body.phone, body.role, hash_password(body.password, salt), salt,
             body.organisation, body.location, body.state, body.district))
        uid = cur.lastrowid
        token = new_session(c, uid)
        audit(c, uid, "register", "user", uid, {"role": body.role})
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return {"token": token, "user": public_user(u)}


class LoginIn(BaseModel):
    identifier: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    rate_limit("login:" + request.client.host, 10)
    ident = body.identifier.strip().lower()
    with get_conn() as c:
        u = c.execute("SELECT * FROM users WHERE email=? OR phone=?", (ident, re.sub(r"[\s-]", "", ident))).fetchone()
        if not u or not u["salt"] or not hmac.compare_digest(hash_password(body.password, u["salt"]), u["password_hash"]):
            raise HTTPException(401, "Incorrect email/phone or password")
        with write_txn(c):
            token = new_session(c, u["id"])
            audit(c, u["id"], "login")
    return {"token": token, "user": public_user(u)}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return public_user(user)


@app.get("/api/auth/config")
def auth_config():
    return {"firebase_enabled": bool(FIREBASE_PROJECT_ID)}


class FirebaseLoginIn(BaseModel):
    id_token: str
    role: str | None = None
    name: str | None = None
    location: str | None = None


@app.post("/api/auth/firebase-login")
def firebase_login(body: FirebaseLoginIn, request: Request):
    rate_limit("fb:" + request.client.host, 20)
    if not FIREBASE_PROJECT_ID:
        raise HTTPException(503, "Google / phone sign-in needs FIREBASE_PROJECT_ID set on the server")
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_firebase_token(body.id_token, greq.Request(), audience=FIREBASE_PROJECT_ID)
    except Exception:
        raise HTTPException(401, "Firebase token could not be verified")
    uid, email, phone = claims["sub"], claims.get("email"), claims.get("phone_number")
    if claims.get("firebase", {}).get("sign_in_provider") == "password" and not claims.get("email_verified"):
        raise HTTPException(403, "Please verify your email address first")
    with get_conn() as c, write_txn(c):
        u = c.execute("SELECT * FROM users WHERE firebase_uid=?", (uid,)).fetchone()
        if not u and email:
            u = c.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        if not u and phone:
            u = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if u and not u["firebase_uid"]:
            c.execute("UPDATE users SET firebase_uid=? WHERE id=?", (uid, u["id"]))
        if not u:
            if body.role not in ROLES:
                return JSONResponse({"needs_role": True}, status_code=200)
            name = body.name or claims.get("name") or email or phone or "Agri-Link user"
            cur = c.execute("INSERT INTO users(name,email,phone,role,firebase_uid,location) VALUES (?,?,?,?,?,?)",
                            (name, email.lower() if email else None, phone, body.role, uid, body.location))
            u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
            audit(c, u["id"], "register_firebase", "user", u["id"], {"role": body.role})
        token = new_session(c, u["id"])  # role always comes from the DB, never from the request
        u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return {"token": token, "user": public_user(u)}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default="")):
    with get_conn() as c:
        c.execute("DELETE FROM user_sessions WHERE token=?", (authorization[7:],))
    return {"ok": True}


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    organisation: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    state: str | None = None
    district: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    language: str | None = Field(default=None, max_length=5)


@app.put("/api/auth/profile")
def update_profile(body: ProfileIn, user=Depends(current_user)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}  # role is not updatable
    if fields:
        with get_conn() as c:
            c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), user["id"]))
            u = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        return public_user(u)
    return public_user(user)


# ---------------------------------------------------------------- AI crop scanner
@app.post("/api/predict")
@app.post("/api/assess-crop")
async def predict(image: UploadFile = File(...), crop: str | None = Form(default=None),
                  moisture: float | None = Form(default=None), user=Depends(current_user)):
    rate_limit(f"predict:{user['id']}", 20)
    data = await image.read(inference.MAX_IMAGE_BYTES + 1)
    try:
        img = inference.load_image(data)
        if moisture is not None and not (0 < moisture < 40):
            raise inference.InvalidImage("Moisture reading must be between 0 and 40 %")
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(inference.predict, img, crop or None, moisture)  # keep the event loop free
    except inference.InvalidImage as e:
        raise HTTPException(400, str(e))
    except inference.ModelUnavailable as e:
        raise HTTPException(503, str(e))

    sha = hashlib.sha256(data).hexdigest()
    rel_path = None
    if result["grade"] in CATEGORY_FOR_GRADE and result["quality"]:
        q = result["quality"]
        rel_path = crop_segregation.save_image(data, result["grade"], {
            "crop": result["effective_crop"], "confidence": round(q["confidence"], 4), "probabilities": q["probabilities"],
            "source": f"farmer scan (user {user['id']})", "true_grade": None})
    with get_conn() as c:
        q, d, cr = result["quality"], result["defect"], result["crop"]
        cur = c.execute(
            "INSERT INTO crop_predictions(user_id,image_sha256,image_path,crop,crop_confidence,defect,defect_confidence,"
            "quality_class,quality_confidence,grade,measured_moisture,moisture_source,advisory,model_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], sha, rel_path, result["effective_crop"], cr and cr["confidence"], d and d["label"],
             d and d["confidence"], q and q["label"], q and q["confidence"], result["grade"],
             moisture, result["moisture"]["source"], result["advisory"], json.dumps(result["model_version"])))
        result["prediction_id"] = cur.lastrowid
    result["timestamp"] = now_iso()
    return result


@app.get("/api/crop-quality/categories")
def cq_categories():
    return {"categories": [{"folder": "Premium", "grade": "A"}, {"folder": "Good", "grade": "B"}, {"folder": "Bad", "grade": "C"}]}


@app.get("/api/crop-quality/folders")
def cq_folders(user=Depends(current_user)):
    """Segregated Premium / Good / Bad image folders with each image's model output (and true grade for demo images)."""
    return crop_segregation.summary()


def _safe_folder(folder: str) -> str:
    if folder not in CATEGORY_FOR_GRADE.values():
        raise HTTPException(400, "Unknown category")
    return os.path.join(RESULTS_DIR, folder)


@app.get("/api/crop-quality/folder/{folder}")
def cq_folder(folder: str, user=Depends(current_user)):
    return sorted(os.listdir(_safe_folder(folder)))


@app.get("/api/crop-quality/image/{folder}/{filename}")
def cq_image(folder: str, filename: str, user=Depends(current_user)):
    base = _safe_folder(folder)
    if not re.fullmatch(r"[0-9a-f]{16}\.jpg", filename):
        raise HTTPException(400, "Invalid file name")
    path = os.path.realpath(os.path.join(base, filename))
    if os.path.commonpath([path, os.path.realpath(base)]) != os.path.realpath(base) or not os.path.exists(path):
        raise HTTPException(404, "Not found")
    return FileResponse(path)


# ---------------------------------------------------------------- farmer produce
class SubmissionIn(BaseModel):
    crop: str = Field(min_length=2, max_length=40)
    variety: str | None = Field(default=None, max_length=60)
    quantity_qtl: float = Field(gt=0, le=10000)
    expected_price_per_qtl: float = Field(gt=0, le=100000)
    location: str = Field(min_length=2, max_length=160)
    state: str | None = None
    district: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    harvest_date: str
    prediction_id: int | None = None
    moisture: float | None = Field(default=None, gt=0, lt=40)

    @field_validator("harvest_date")
    @classmethod
    def _date(cls, v):
        d = datetime.strptime(v, "%Y-%m-%d").date()
        if d > datetime.now().date():
            raise ValueError("harvest date cannot be in the future")
        return v


@app.post("/api/farmers/register")
def register_produce(body: SubmissionIn, user=Depends(require("farmer"))):
    grade, score = "U", None
    with get_conn() as c, write_txn(c):
        if body.prediction_id:
            p = c.execute("SELECT * FROM crop_predictions WHERE id=? AND user_id=?", (body.prediction_id, user["id"])).fetchone()
            if not p:
                raise HTTPException(404, "Scan result not found")
            if p["grade"] in ("A", "B", "C"):
                grade, score = p["grade"], p["quality_confidence"]
        cur = c.execute(
            "INSERT INTO farmer_submissions(batch_code,farmer_id,farmer_name,phone,crop,variety,grade,prediction_id,"
            "quality_score,moisture,quantity_qtl,available_quantity,expected_price_per_qtl,location,state,district,"
            "latitude,longitude,harvest_date) VALUES ('tmp-'||hex(randomblob(8)),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], user["name"], user["phone"], body.crop, body.variety, grade, body.prediction_id, score,
             body.moisture, body.quantity_qtl, body.quantity_qtl, body.expected_price_per_qtl, body.location,
             body.state, body.district, body.latitude, body.longitude, body.harvest_date))
        bc = code(c, "farmer_submissions", "batch_code", "BAT", cur.lastrowid)
        notify(c, user["id"], "Produce submitted", f"{bc}: {body.quantity_qtl:g} Qtl {body.crop} registered for pooling.")
        notify_role(c, "aggregator", "New farmer submission",
                    f"{user['name']} submitted {body.quantity_qtl:g} Qtl {body.crop} ({'Grade ' + grade if grade != 'U' else 'needs inspection'}) at {body.location}.",
                    "inflow")
        audit(c, user["id"], "submit_produce", "farmer_submission", cur.lastrowid, body.model_dump())
        s = c.execute("SELECT * FROM farmer_submissions WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(s)


@app.get("/api/farmers/submissions")
def list_submissions(status: str | None = None, user=Depends(require("farmer", "aggregator"))):
    q, args = "SELECT * FROM farmer_submissions WHERE 1=1", []
    if user["role"] == "farmer":
        q += " AND farmer_id=?"; args.append(user["id"])
    if status:
        q += " AND status=?"; args.append(status)
    with get_conn() as c:
        subs = rows(c.execute(q + " ORDER BY id DESC", args))
        for s in subs:
            s["allocations"] = rows(c.execute(
                "SELECT a.allocated_quantity, a.gross_farmer_payment, l.lot_code, l.status AS lot_status "
                "FROM lot_allocations a JOIN commercial_lots l ON l.id=a.lot_id WHERE a.submission_id=?", (s["id"],)))
    return subs


# ---------------------------------------------------------------- aggregator inflow
@app.get("/api/aggregator/inflow")
def inflow(crop: str | None = None, grade: str | None = None, location: str | None = None,
           status: str | None = None, user=Depends(require("aggregator"))):
    q = "SELECT * FROM farmer_submissions WHERE status IN ('Pending','In Yard','Allocated to Lot')"
    args = []
    if crop:
        q += " AND crop=?"; args.append(crop)
    if grade:
        q += " AND grade=?"; args.append(grade)
    if location:
        q += " AND (location LIKE ? OR state LIKE ? OR district LIKE ?)"; args += [f"%{location}%"] * 3
    if status:
        q += " AND status=?"; args.append(status)
    with get_conn() as c:
        return rows(c.execute(q + " ORDER BY created_at DESC", args))


class ReviewIn(BaseModel):
    decision: str  # accept | reject
    grade: str | None = None
    moisture: float | None = Field(default=None, gt=0, lt=40)
    note: str | None = Field(default=None, max_length=300)


@app.post("/api/aggregator/inflow/{sub_id}/review")
def review_submission(sub_id: int, body: ReviewIn, user=Depends(require("aggregator"))):
    with get_conn() as c, write_txn(c):
        s = c.execute("SELECT * FROM farmer_submissions WHERE id=?", (sub_id,)).fetchone()
        if not s or s["status"] != "Pending":
            raise HTTPException(409, "Only pending batches can be reviewed")
        if body.decision == "reject":
            c.execute("UPDATE farmer_submissions SET status='Rejected', available_quantity=0 WHERE id=?", (sub_id,))
            msg = f"{s['batch_code']} was rejected at yard inspection. {body.note or ''}"
        elif body.decision == "accept":
            grade = body.grade or s["grade"]
            if grade not in ("A", "B", "C"):
                raise HTTPException(422, "Set an inspected grade (A, B or C) before accepting")
            c.execute("UPDATE farmer_submissions SET status='In Yard', grade=?, moisture=COALESCE(?,moisture) WHERE id=?",
                      (grade, body.moisture, sub_id))
            msg = f"{s['batch_code']} accepted into the yard as Grade {grade}."
        else:
            raise HTTPException(422, "decision must be accept or reject")
        notify(c, s["farmer_id"], "Batch reviewed", msg)
        audit(c, user["id"], "review_batch", "farmer_submission", sub_id, body.model_dump())
    return {"ok": True}


# ---------------------------------------------------------------- lots
class AllocIn(BaseModel):
    submission_id: int
    quantity: float = Field(gt=0)


class LotIn(BaseModel):
    crop: str
    variety: str | None = None
    grade: str
    base_price_per_qtl: float = Field(gt=0, le=100000)
    moisture: float | None = Field(default=None, gt=0, lt=40)
    location: str | None = Field(default=None, max_length=160)
    allocations: list[AllocIn] = Field(min_length=1)


def _allocate(c, lot, sub_id: int, qty: float, user) -> dict:
    s = c.execute("SELECT * FROM farmer_submissions WHERE id=?", (sub_id,)).fetchone()
    if not s:
        raise HTTPException(404, f"Batch {sub_id} not found")
    if s["status"] not in ("In Yard", "Allocated to Lot"):
        raise HTTPException(409, f"{s['batch_code']} has not been accepted into the yard")
    if s["crop"] != lot["crop"] or s["grade"] != lot["grade"]:
        raise HTTPException(409, f"{s['batch_code']} is {s['crop']} Grade {s['grade']}; lot needs {lot['crop']} Grade {lot['grade']}")
    if c.execute("SELECT 1 FROM lot_allocations WHERE lot_id=? AND submission_id=?", (lot["id"], sub_id)).fetchone():
        raise HTTPException(409, f"{s['batch_code']} is already in this lot")
    # Atomic guarded decrement: succeeds only if enough stock is still unallocated.
    cur = c.execute("UPDATE farmer_submissions SET available_quantity = available_quantity - ?, status='Allocated to Lot' "
                    "WHERE id=? AND available_quantity >= ?", (qty, sub_id, qty))
    if cur.rowcount != 1:
        raise HTTPException(409, f"{s['batch_code']} only has {s['available_quantity']:g} Qtl available")
    gross = money(qty * s["expected_price_per_qtl"])
    c.execute("INSERT INTO lot_allocations(lot_id,submission_id,farmer_id,allocated_quantity,farmer_price_per_qtl,gross_farmer_payment) "
              "VALUES (?,?,?,?,?,?)", (lot["id"], sub_id, s["farmer_id"], qty, s["expected_price_per_qtl"], gross))
    c.execute("UPDATE commercial_lots SET total_quantity=total_quantity+?, remaining_quantity=remaining_quantity+?, "
              "allocated_quantity=allocated_quantity+? WHERE id=?", (qty, qty, qty, lot["id"]))
    notify(c, s["farmer_id"], "Batch allocated",
           f"{qty:g} Qtl of {s['batch_code']} added to commercial lot {lot['lot_code']} (₹{gross:,.0f} gross).")
    audit(c, user["id"], "allocate", "lot", lot["id"], {"submission_id": sub_id, "qty": qty})
    return {"submission": s["batch_code"], "quantity": qty}


@app.post("/api/lots/create")
def create_lot(body: LotIn, user=Depends(require("aggregator"))):
    if body.grade not in ("A", "B", "C"):
        raise HTTPException(422, "grade must be A, B or C")
    with get_conn() as c, write_txn(c):
        cur = c.execute("INSERT INTO commercial_lots(lot_code,aggregator_id,crop,variety,grade,moisture,base_price_per_qtl,location) "
                        "VALUES ('tmp-'||hex(randomblob(8)),?,?,?,?,?,?,?)",
                        (user["id"], body.crop, body.variety, body.grade, body.moisture, body.base_price_per_qtl, body.location))
        lc = code(c, "commercial_lots", "lot_code", "LOT", cur.lastrowid, 4)
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=?", (cur.lastrowid,)).fetchone()
        for a in body.allocations:
            _allocate(c, lot, a.submission_id, a.quantity, user)
        farmers = {r["farmer_id"] for r in c.execute("SELECT farmer_id FROM lot_allocations WHERE lot_id=?", (lot["id"],))}
        for f in farmers:
            notify(c, f, "Lot created", f"Commercial lot {lc} ({body.crop}, Grade {body.grade}) now lists your produce.")
        notify_role(c, "buyer", "New certified lot", f"{lc}: {body.crop} Grade {body.grade} at ₹{body.base_price_per_qtl:,.0f}/Qtl.", "marketplace")
        audit(c, user["id"], "create_lot", "lot", lot["id"], body.model_dump())
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=?", (lot["id"],)).fetchone()
    return dict(lot)


class AllocateIn(BaseModel):
    lot_id: int
    submission_id: int
    quantity: float = Field(gt=0)


@app.post("/api/lots/allocate")
def allocate(body: AllocateIn, user=Depends(require("aggregator"))):
    with get_conn() as c, write_txn(c):
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=? AND aggregator_id=?", (body.lot_id, user["id"])).fetchone()
        if not lot:
            raise HTTPException(404, "Lot not found")
        if lot["status"] != "Available":
            raise HTTPException(409, "Batches can only be added while the lot is Available")
        res = _allocate(c, lot, body.submission_id, body.quantity, user)
    return res


def _lot_detail(c, lot) -> dict:
    d = dict(lot)
    d["allocations"] = rows(c.execute(
        "SELECT a.id, a.allocated_quantity, a.farmer_price_per_qtl, a.gross_farmer_payment, s.batch_code, s.farmer_name, "
        "s.location, s.harvest_date, s.grade, s.moisture, p.model_version, p.quality_class, p.quality_confidence "
        "FROM lot_allocations a JOIN farmer_submissions s ON s.id=a.submission_id "
        "LEFT JOIN crop_predictions p ON p.id=s.prediction_id WHERE a.lot_id=?", (lot["id"],)))
    d["contributing_farmers"] = sorted({a["farmer_name"] for a in d["allocations"]})
    d["farmer_cost"] = money(sum(a["gross_farmer_payment"] for a in d["allocations"]))
    d["gross_value"] = money(d["total_quantity"] * d["base_price_per_qtl"])
    agg = c.execute("SELECT name, organisation FROM users WHERE id=?", (lot["aggregator_id"],)).fetchone()
    d["aggregator"] = (agg["organisation"] or agg["name"]) if agg else None
    return d


@app.get("/api/lots")
def list_lots(crop: str | None = None, min_grade: str | None = None, user=Depends(current_user)):
    q, args = "SELECT * FROM commercial_lots WHERE 1=1", []
    if user["role"] == "aggregator":
        q += " AND aggregator_id=?"; args.append(user["id"])
    elif user["role"] == "buyer":
        q += " AND status='Available' AND total_quantity > 0"
    else:
        q += " AND id IN (SELECT lot_id FROM lot_allocations WHERE farmer_id=?)"; args.append(user["id"])
    if crop:
        q += " AND crop=?"; args.append(crop)
    with get_conn() as c:
        lots = [_lot_detail(c, l) for l in c.execute(q + " ORDER BY id DESC", args).fetchall()]
    if min_grade:
        lots = [l for l in lots if GRADE_RANK[l["grade"]] >= GRADE_RANK[min_grade]]
    if user["role"] == "farmer":  # farmers see only their own share of a lot
        for l in lots:
            l["allocations"] = [a for a in l["allocations"] if a["farmer_name"] == user["name"]]
    return lots


@app.get("/api/lots/{lot_id}/allocations")
def lot_allocations(lot_id: int, user=Depends(require("aggregator", "buyer"))):
    with get_conn() as c:
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=?", (lot_id,)).fetchone()
        if not lot:
            raise HTTPException(404, "Lot not found")
        if user["role"] == "aggregator" and lot["aggregator_id"] != user["id"]:
            raise HTTPException(403, "Not your lot")
        return _lot_detail(c, lot)


class LotStatusIn(BaseModel):
    status: str


@app.put("/api/lots/{lot_id}/status")
def lot_status(lot_id: int, body: LotStatusIn, user=Depends(require("aggregator"))):
    if body.status not in ("Available", "Cancelled"):
        raise HTTPException(422, "Aggregators can set Available or Cancelled; other states follow orders")
    with get_conn() as c, write_txn(c):
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=? AND aggregator_id=?", (lot_id, user["id"])).fetchone()
        if not lot:
            raise HTTPException(404, "Lot not found")
        if lot["status"] not in ("Available", "Draft"):
            raise HTTPException(409, f"Lot is {lot['status']} and cannot be changed")
        if body.status == "Cancelled":  # release stock back to farmers
            for a in c.execute("SELECT * FROM lot_allocations WHERE lot_id=?", (lot_id,)).fetchall():
                c.execute("UPDATE farmer_submissions SET available_quantity=available_quantity+?, status='In Yard' WHERE id=?",
                          (a["allocated_quantity"], a["submission_id"]))
                notify(c, a["farmer_id"], "Lot cancelled", f"{lot['lot_code']} was cancelled; your produce is available again.")
            c.execute("DELETE FROM lot_allocations WHERE lot_id=?", (lot_id,))
            c.execute("UPDATE commercial_lots SET total_quantity=0, allocated_quantity=0, remaining_quantity=0 WHERE id=?", (lot_id,))
        c.execute("UPDATE commercial_lots SET status=? WHERE id=?", (body.status, lot_id))
        audit(c, user["id"], "lot_status", "lot", lot_id, body.model_dump())
    return {"ok": True}


# ---------------------------------------------------------------- buyer requests & matching
class BuyerRequestIn(BaseModel):
    crop: str = Field(min_length=2, max_length=40)
    variety: str | None = Field(default=None, max_length=60)
    quantity_qtl: float = Field(gt=0, le=100000)
    min_grade: str
    target_price: float = Field(gt=0, le=100000)
    delivery_location: str = Field(min_length=2, max_length=160)
    delivery_date: str
    partial_ok: bool = True
    idempotency_key: str | None = Field(default=None, max_length=64)

    @field_validator("min_grade")
    @classmethod
    def _g(cls, v):
        if v not in ("A", "B", "C"):
            raise ValueError("min_grade must be A, B or C")
        return v

    @field_validator("delivery_date")
    @classmethod
    def _d(cls, v):
        if datetime.strptime(v, "%Y-%m-%d").date() < datetime.now().date():
            raise ValueError("delivery date is in the past")
        return v


@app.post("/api/buyer/requests")
def create_request(body: BuyerRequestIn, user=Depends(require("buyer"))):
    with get_conn() as c, write_txn(c):
        if body.idempotency_key:
            ex = c.execute("SELECT * FROM buyer_requests WHERE idempotency_key=? AND buyer_id=?", (body.idempotency_key, user["id"])).fetchone()
            if ex:
                return dict(ex)
        cur = c.execute(
            "INSERT INTO buyer_requests(request_code,buyer_id,buyer_name,crop,variety,quantity_qtl,remaining_qty,min_grade,"
            "target_price,delivery_location,delivery_date,partial_ok,idempotency_key) VALUES ('tmp-'||hex(randomblob(8)),?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], user["organisation"] or user["name"], body.crop, body.variety, body.quantity_qtl, body.quantity_qtl,
             body.min_grade, body.target_price, body.delivery_location, body.delivery_date, int(body.partial_ok), body.idempotency_key))
        rc = code(c, "buyer_requests", "request_code", "REQ", cur.lastrowid)
        notify_role(c, "aggregator", "New buyer request",
                    f"{rc}: {body.quantity_qtl:g} Qtl {body.crop}, Grade {body.min_grade}+ by {body.delivery_date}, ≤ ₹{body.target_price:,.0f}/Qtl.", "demand")
        audit(c, user["id"], "create_request", "buyer_request", cur.lastrowid, body.model_dump())
        r = c.execute("SELECT * FROM buyer_requests WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(r)


@app.get("/api/buyer/requests")
def list_requests(user=Depends(require("buyer", "aggregator"))):
    with get_conn() as c:
        if user["role"] == "buyer":
            return rows(c.execute("SELECT * FROM buyer_requests WHERE buyer_id=? ORDER BY id DESC", (user["id"],)))
        return rows(c.execute("SELECT * FROM buyer_requests WHERE status IN ('Open','Accepted','Partially Fulfilled') ORDER BY delivery_date"))


@app.get("/api/buyer/requests/{rid}")
def get_request(rid: int, user=Depends(require("buyer", "aggregator"))):
    with get_conn() as c:
        r = c.execute("SELECT * FROM buyer_requests WHERE id=?", (rid,)).fetchone()
    if not r or (user["role"] == "buyer" and r["buyer_id"] != user["id"]):
        raise HTTPException(404, "Request not found")
    return dict(r)


class ReqStatusIn(BaseModel):
    status: str


@app.put("/api/buyer/requests/{rid}/status")
def request_status(rid: int, body: ReqStatusIn, user=Depends(require("buyer", "aggregator"))):
    allowed = {"aggregator": ("Accepted", "Rejected"), "buyer": ("Cancelled",)}[user["role"] if user["role"] != "admin" else "aggregator"]
    if body.status not in allowed:
        raise HTTPException(422, f"Allowed: {', '.join(allowed)}")
    with get_conn() as c, write_txn(c):
        r = c.execute("SELECT * FROM buyer_requests WHERE id=?", (rid,)).fetchone()
        if not r or (user["role"] == "buyer" and r["buyer_id"] != user["id"]):
            raise HTTPException(404, "Request not found")
        if r["status"] not in ("Open", "Accepted"):
            raise HTTPException(409, f"Request is already {r['status']}")
        c.execute("UPDATE buyer_requests SET status=? WHERE id=?", (body.status, rid))
        if user["role"] == "aggregator":
            org = user["organisation"] or user["name"]
            notify(c, r["buyer_id"], f"Request {body.status.lower()}", f"{org} {body.status.lower()} {r['request_code']}.")
        audit(c, user["id"], "request_status", "buyer_request", rid, body.model_dump())
    return {"ok": True}


def _match(c, r) -> list[dict]:
    """Strict checks first; only eligible lots are scored, and every result carries its reasons."""
    out = []
    for lot in c.execute("SELECT * FROM commercial_lots WHERE status='Available' AND crop=? AND total_quantity>0", (r["crop"],)).fetchall():
        checks = []
        if r["variety"] and lot["variety"] and r["variety"].lower() not in lot["variety"].lower():
            continue
        if GRADE_RANK[lot["grade"]] < GRADE_RANK[r["min_grade"]]:
            continue
        if lot["base_price_per_qtl"] > r["target_price"]:
            continue
        if lot["total_quantity"] > r["remaining_qty"] and not r["partial_ok"]:
            pass  # lot larger than need is fine; buyer gets the whole lot
        if lot["total_quantity"] < r["remaining_qty"] and not r["partial_ok"]:
            continue
        fill = min(1.0, lot["total_quantity"] / r["remaining_qty"])
        price = 1 - lot["base_price_per_qtl"] / r["target_price"]
        grade_bonus = (GRADE_RANK[lot["grade"]] - GRADE_RANK[r["min_grade"]]) / 2
        score = round(100 * (0.5 * fill + 0.3 * min(1, price * 5) + 0.2 * grade_bonus), 1)
        checks.append(f"Grade {lot['grade']} meets minimum {r['min_grade']}")
        checks.append(f"₹{lot['base_price_per_qtl']:,.0f}/Qtl within ₹{r['target_price']:,.0f} target")
        checks.append(f"{lot['total_quantity']:g} Qtl covers {fill:.0%} of the {r['remaining_qty']:g} Qtl still needed")
        d = _lot_detail(c, lot)
        d.update(score=score, reasons=checks)
        out.append(d)
    return sorted(out, key=lambda x: -x["score"])


@app.get("/api/matchmaking/suggestions/{rid}")
def suggestions(rid: int, user=Depends(require("buyer", "aggregator"))):
    with get_conn() as c:
        r = c.execute("SELECT * FROM buyer_requests WHERE id=?", (rid,)).fetchone()
        if not r or (user["role"] == "buyer" and r["buyer_id"] != user["id"]):
            raise HTTPException(404, "Request not found")
        return {"request": dict(r), "matches": _match(c, r)}


class AutoAssembleIn(BaseModel):
    request_id: int
    base_price_per_qtl: float = Field(gt=0)


@app.post("/api/matchmaking/auto-assemble")
def auto_assemble(body: AutoAssembleIn, user=Depends(require("aggregator"))):
    """Pool yard batches (grade >= requested, oldest first) into a lot sized to the buyer's remaining need."""
    with get_conn() as c:
        r = c.execute("SELECT * FROM buyer_requests WHERE id=?", (body.request_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Request not found")
        grades = [g for g in ("A", "B", "C") if GRADE_RANK[g] >= GRADE_RANK[r["min_grade"]]]
        # a lot must be single-grade; use the lowest acceptable grade with enough stock, else the best available
        best = None
        for g in reversed(grades):
            subs = c.execute("SELECT * FROM farmer_submissions WHERE crop=? AND grade=? AND available_quantity>0 "
                             "AND status IN ('In Yard','Allocated to Lot') ORDER BY created_at", (r["crop"], g)).fetchall()
            total = sum(s["available_quantity"] for s in subs)
            if total >= r["remaining_qty"] or (best is None and total > 0):
                best = (g, subs)
                if total >= r["remaining_qty"]:
                    break
    if not best:
        raise HTTPException(409, f"No yard stock of {r['crop']} at Grade {r['min_grade']} or better")
    need, allocs = r["remaining_qty"], []
    for s in best[1]:
        if need <= 0:
            break
        q = min(need, s["available_quantity"])
        allocs.append(AllocIn(submission_id=s["id"], quantity=q))
        need -= q
    lot = create_lot(LotIn(crop=r["crop"], variety=r["variety"], grade=best[0], base_price_per_qtl=body.base_price_per_qtl,
                           location=r["delivery_location"], allocations=allocs), user)
    lot["shortfall_qtl"] = max(0, need)
    return lot


# ---------------------------------------------------------------- procurement / transactions
class ProcureIn(BaseModel):
    lot_id: int
    request_id: int | None = None
    idempotency_key: str = Field(min_length=8, max_length=64)


@app.post("/api/procure")
def procure(body: ProcureIn, user=Depends(require("buyer"))):
    with get_conn() as c, write_txn(c):
        ex = c.execute("SELECT * FROM transactions WHERE idempotency_key=?", (body.idempotency_key,)).fetchone()
        if ex:
            if ex["buyer_id"] != user["id"]:
                raise HTTPException(409, "Duplicate key")
            return dict(ex)
        lot = c.execute("SELECT * FROM commercial_lots WHERE id=?", (body.lot_id,)).fetchone()
        if not lot:
            raise HTTPException(404, "Lot not found")
        # Reserve the whole lot atomically: only one buyer can flip Available -> Reserved.
        cur = c.execute("UPDATE commercial_lots SET status='Reserved', remaining_quantity=0 WHERE id=? AND status='Available' AND total_quantity>0", (lot["id"],))
        if cur.rowcount != 1:
            raise HTTPException(409, f"{lot['lot_code']} is no longer available")
        req = None
        if body.request_id:
            req = c.execute("SELECT * FROM buyer_requests WHERE id=? AND buyer_id=?", (body.request_id, user["id"])).fetchone()
            if not req:
                raise HTTPException(404, "Request not found")
        qty = lot["total_quantity"]
        revenue = money(qty * lot["base_price_per_qtl"])
        fcost = money(c.execute("SELECT COALESCE(SUM(gross_farmer_payment),0) FROM lot_allocations WHERE lot_id=?", (lot["id"],)).fetchone()[0])
        margin = money(revenue - fcost)
        cur = c.execute(
            "INSERT INTO transactions(transaction_code,po_number,buyer_id,aggregator_id,lot_id,request_id,quantity_qtl,price_per_qtl,"
            "crop_sales_revenue,farmer_procurement_cost,aggregator_gross_margin,aggregator_net_earnings,total_buyer_cost,idempotency_key) "
            "VALUES ('tmp-'||hex(randomblob(8)),'tmp-'||hex(randomblob(8)),?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], lot["aggregator_id"], lot["id"], body.request_id, qty, lot["base_price_per_qtl"], revenue, fcost,
             margin, margin, revenue, body.idempotency_key))
        tid = cur.lastrowid
        tc = code(c, "transactions", "transaction_code", "TXN", tid, 6)
        po = code(c, "transactions", "po_number", "PO", tid, 6)
        if req:
            rem = max(0, req["remaining_qty"] - qty)
            c.execute("UPDATE buyer_requests SET remaining_qty=?, status=? WHERE id=?",
                      (rem, "Fulfilled" if rem == 0 else "Partially Fulfilled", req["id"]))
        org = user["organisation"] or user["name"]
        notify(c, lot["aggregator_id"], "Procurement confirmation", f"{org} placed {po} for {lot['lot_code']} ({qty:g} Qtl, ₹{revenue:,.0f}).", "orders")
        for a in c.execute("SELECT DISTINCT farmer_id FROM lot_allocations WHERE lot_id=?", (lot["id"],)):
            notify(c, a["farmer_id"], "Buyer procurement completed", f"Lot {lot['lot_code']} containing your produce was purchased ({po}).")
        notify(c, user["id"], "Lot matched", f"{po} created for {lot['lot_code']}. Awaiting dispatch.")
        audit(c, user["id"], "procure", "transaction", tid, {"lot": lot["lot_code"], "revenue": revenue})
        t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    return dict(t)


def _txn_for(c, tid: int, user):
    t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404, "Transaction not found")
    if user["role"] == "buyer" and t["buyer_id"] != user["id"]:
        raise HTTPException(404, "Transaction not found")
    if user["role"] == "aggregator" and t["aggregator_id"] != user["id"]:
        raise HTTPException(404, "Transaction not found")
    if user["role"] == "farmer" and not c.execute("SELECT 1 FROM lot_allocations WHERE lot_id=? AND farmer_id=?", (t["lot_id"], user["id"])).fetchone():
        raise HTTPException(404, "Transaction not found")
    return t


def _txn_detail(c, t, user) -> dict:
    d = dict(t)
    lot = c.execute("SELECT * FROM commercial_lots WHERE id=?", (t["lot_id"],)).fetchone()
    d["lot"] = _lot_detail(c, lot)
    if user["role"] == "farmer":
        d["lot"]["allocations"] = [a for a in d["lot"]["allocations"] if a["farmer_name"] == user["name"]]
    d["buyer"] = dict(c.execute("SELECT name, organisation, location FROM users WHERE id=?", (t["buyer_id"],)).fetchone())
    d["farmer_payments"] = rows(c.execute(
        "SELECT fp.*, u.name AS farmer_name FROM farmer_payments fp JOIN users u ON u.id=fp.farmer_id WHERE fp.transaction_id=?"
        + (" AND fp.farmer_id=?" if user["role"] == "farmer" else ""),
        (t["id"], user["id"]) if user["role"] == "farmer" else (t["id"],)))
    bp = c.execute("SELECT * FROM buyer_payments WHERE transaction_id=?", (t["id"],)).fetchone()
    d["buyer_payment"] = dict(bp) if bp and user["role"] != "farmer" else None
    return d


@app.get("/api/transactions")
def list_txns(user=Depends(current_user)):
    with get_conn() as c:
        if user["role"] == "buyer":
            ts = c.execute("SELECT * FROM transactions WHERE buyer_id=? ORDER BY id DESC", (user["id"],)).fetchall()
        elif user["role"] == "aggregator":
            ts = c.execute("SELECT * FROM transactions WHERE aggregator_id=? ORDER BY id DESC", (user["id"],)).fetchall()
        else:
            ts = c.execute("SELECT * FROM transactions WHERE lot_id IN (SELECT lot_id FROM lot_allocations WHERE farmer_id=?) ORDER BY id DESC", (user["id"],)).fetchall()
        return [_txn_detail(c, t, user) for t in ts]


@app.get("/api/transactions/{tid}")
def get_txn(tid: int, user=Depends(current_user)):
    with get_conn() as c:
        return _txn_detail(c, _txn_for(c, tid, user), user)


class DispatchIn(BaseModel):
    transport_charges: float = Field(ge=0, le=10_000_000)
    vehicle_number: str = Field(min_length=4, max_length=20)
    carrier: str = Field(min_length=2, max_length=80)


@app.post("/api/transactions/{tid}/dispatch")
def dispatch(tid: int, body: DispatchIn, user=Depends(require("aggregator"))):
    with get_conn() as c, write_txn(c):
        t = _txn_for(c, tid, user)
        if t["status"] != "Ordered":
            raise HTTPException(409, f"Order is {t['status']}")
        net = money(t["aggregator_gross_margin"] - body.transport_charges)
        c.execute("UPDATE transactions SET status='Dispatched', transport_charges=?, aggregator_net_earnings=?, vehicle_number=?, "
                  "carrier=?, dispatched_at=? WHERE id=?", (body.transport_charges, net, body.vehicle_number, body.carrier, now_iso(), tid))
        c.execute("UPDATE commercial_lots SET status='Dispatched' WHERE id=?", (t["lot_id"],))
        notify(c, t["buyer_id"], "Order dispatched", f"{t['po_number']} dispatched via {body.carrier} ({body.vehicle_number}).", "orders")
        audit(c, user["id"], "dispatch", "transaction", tid, body.model_dump())
    return {"ok": True}


class CompleteIn(BaseModel):
    transaction_id: int


@app.post("/api/transactions/complete")
def complete(body: CompleteIn, user=Depends(require("buyer"))):
    """Buyer confirms delivery: lot sold, farmer payables and buyer invoice are created exactly once."""
    with get_conn() as c, write_txn(c):
        t = _txn_for(c, body.transaction_id, user)
        cur = c.execute("UPDATE transactions SET status='Delivered', delivered_at=? WHERE id=? AND status='Dispatched'", (now_iso(), t["id"]))
        if cur.rowcount != 1:
            raise HTTPException(409, "Only dispatched orders can be confirmed as delivered")
        c.execute("UPDATE commercial_lots SET status='Sold' WHERE id=?", (t["lot_id"],))
        for a in c.execute("SELECT * FROM lot_allocations WHERE lot_id=?", (t["lot_id"],)).fetchall():
            cur = c.execute("INSERT INTO farmer_payments(payment_code,transaction_id,allocation_id,farmer_id,gross_amount,net_payable) "
                            "VALUES ('tmp-'||hex(randomblob(8)),?,?,?,?,?)",
                            (t["id"], a["id"], a["farmer_id"], a["gross_farmer_payment"], a["gross_farmer_payment"]))
            code(c, "farmer_payments", "payment_code", "FPAY", cur.lastrowid, 6)
            sub = c.execute("SELECT * FROM farmer_submissions WHERE id=?", (a["submission_id"],)).fetchone()
            if sub["available_quantity"] == 0:
                c.execute("UPDATE farmer_submissions SET status='Sold' WHERE id=?", (sub["id"],))
            notify(c, a["farmer_id"], "Delivery completed", f"Your produce in {t['po_number']} was delivered. ₹{a['gross_farmer_payment']:,.0f} is now payable.")
        cur = c.execute("INSERT INTO buyer_payments(payment_code,transaction_id,buyer_id,amount_due,outstanding_balance) VALUES ('tmp-'||hex(randomblob(8)),?,?,?,?)",
                        (t["id"], t["buyer_id"], t["total_buyer_cost"], t["total_buyer_cost"]))
        code(c, "buyer_payments", "payment_code", "BPAY", cur.lastrowid, 6)
        notify(c, t["aggregator_id"], "Delivery completed", f"Buyer confirmed delivery of {t['po_number']}.", "orders")
        notify(c, t["buyer_id"], "Delivery completed", f"{t['po_number']} marked delivered. Invoice is ready.", "orders")
        audit(c, user["id"], "confirm_delivery", "transaction", t["id"])
    return {"ok": True}


def _maybe_settle(c, tid: int):
    t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    pending_f = c.execute("SELECT COUNT(*) FROM farmer_payments WHERE transaction_id=? AND status!='Paid'", (tid,)).fetchone()[0]
    bp = c.execute("SELECT status FROM buyer_payments WHERE transaction_id=?", (tid,)).fetchone()
    buyer_paid = bp and bp["status"] == "Paid"
    c.execute("UPDATE transactions SET payment_status=? WHERE id=?",
              ("Paid" if buyer_paid else "Unpaid", tid))
    if t["status"] == "Delivered" and pending_f == 0 and buyer_paid:
        c.execute("UPDATE transactions SET status='Settled' WHERE id=?", (tid,))


class FarmerPayIn(BaseModel):
    payment_id: int
    deductions: float = Field(default=0, ge=0)
    deduction_reason: str | None = Field(default=None, max_length=200)
    payment_reference: str = Field(min_length=4, max_length=60)
    payment_method: str = Field(pattern="^(UPI|NEFT|RTGS|IMPS|Cheque)$")


@app.post("/api/payments/farmer/record")
def record_farmer_payment(body: FarmerPayIn, user=Depends(require("aggregator"))):
    """Records a bank/UPI transfer the FPO made outside the app, with its reference number."""
    with get_conn() as c, write_txn(c):
        p = c.execute("SELECT fp.*, t.aggregator_id, t.po_number FROM farmer_payments fp JOIN transactions t ON t.id=fp.transaction_id WHERE fp.id=?",
                      (body.payment_id,)).fetchone()
        if not p or p["aggregator_id"] != user["id"]:
            raise HTTPException(404, "Payment not found")
        if p["status"] == "Paid":
            raise HTTPException(409, "Already recorded as paid")
        if body.deductions > 0 and not body.deduction_reason:
            raise HTTPException(422, "A reason is required for any deduction")
        if body.deductions > p["gross_amount"]:
            raise HTTPException(422, "Deductions exceed gross amount")
        net = money(p["gross_amount"] - body.deductions)
        c.execute("UPDATE farmer_payments SET deductions=?, deduction_reason=?, net_payable=?, amount_paid=?, status='Paid', "
                  "payment_reference=?, payment_method=?, paid_at=? WHERE id=? AND status='Pending'",
                  (body.deductions, body.deduction_reason, net, net, body.payment_reference, body.payment_method, now_iso(), p["id"]))
        notify(c, p["farmer_id"], "Payment received",
               f"₹{net:,.2f} for {p['po_number']} sent by {body.payment_method} (ref {body.payment_reference})."
               + (f" Deduction ₹{body.deductions:,.2f}: {body.deduction_reason}" if body.deductions else ""))
        _maybe_settle(c, p["transaction_id"])
        audit(c, user["id"], "farmer_payment", "farmer_payment", p["id"], body.model_dump())
    return {"ok": True, "net_paid": net}


class BuyerPayIn(BaseModel):
    transaction_id: int
    payment_reference: str = Field(min_length=4, max_length=60)
    payment_method: str = Field(pattern="^(UPI|NEFT|RTGS|IMPS|Cheque)$")


@app.post("/api/payments/buyer/record")
def record_buyer_payment(body: BuyerPayIn, user=Depends(require("aggregator"))):
    """The receiving FPO confirms the buyer's transfer arrived — a buyer cannot mark their own invoice paid."""
    with get_conn() as c, write_txn(c):
        t = _txn_for(c, body.transaction_id, user)
        cur = c.execute("UPDATE buyer_payments SET amount_paid=amount_due, outstanding_balance=0, status='Paid', payment_reference=?, "
                        "payment_method=?, paid_at=? WHERE transaction_id=? AND status='Due'",
                        (body.payment_reference, body.payment_method, now_iso(), t["id"]))
        if cur.rowcount != 1:
            raise HTTPException(409, "No outstanding invoice for this order")
        notify(c, t["buyer_id"], "Payment confirmed", f"FPO confirmed receipt for {t['po_number']} (ref {body.payment_reference}).")
        _maybe_settle(c, t["id"])
        audit(c, user["id"], "buyer_payment", "transaction", t["id"], body.model_dump())
    return {"ok": True}


# Razorpay: active only when keys are configured. Ledger changes only on a verified webhook.
@app.post("/api/payments/razorpay/order")
def razorpay_order(body: CompleteIn, user=Depends(require("buyer"))):
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        raise HTTPException(503, "Online payment needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET on the server")
    with get_conn() as c:
        t = _txn_for(c, body.transaction_id, user)
        bp = c.execute("SELECT * FROM buyer_payments WHERE transaction_id=? AND status='Due'", (t["id"],)).fetchone()
    if not bp:
        raise HTTPException(409, "No outstanding invoice")
    r = requests.post("https://api.razorpay.com/v1/orders", auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), timeout=15,
                      json={"amount": int(round(bp["outstanding_balance"] * 100)), "currency": "INR", "receipt": bp["payment_code"],
                            "notes": {"transaction_id": str(t["id"])}})
    if r.status_code != 200:
        raise HTTPException(502, "Razorpay order creation failed")
    return {"order": r.json(), "key_id": RAZORPAY_KEY_ID}


@app.post("/api/payments/razorpay/webhook")
async def razorpay_webhook(request: Request):
    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook secret not configured")
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature", "")
    if not hmac.compare_digest(hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest(), sig):
        raise HTTPException(400, "Invalid signature")
    ev = json.loads(raw)
    if ev.get("event") != "payment.captured":
        return {"ignored": True}
    pay = ev["payload"]["payment"]["entity"]
    tid = int(pay.get("notes", {}).get("transaction_id", 0))
    with get_conn() as c, write_txn(c):
        if c.execute("SELECT 1 FROM buyer_payments WHERE gateway_payment_id=?", (pay["id"],)).fetchone():
            return {"duplicate": True}
        bp = c.execute("SELECT * FROM buyer_payments WHERE transaction_id=? AND status='Due'", (tid,)).fetchone()
        if not bp or int(round(bp["outstanding_balance"] * 100)) != pay["amount"]:
            raise HTTPException(409, "Amount or invoice mismatch")
        c.execute("UPDATE buyer_payments SET amount_paid=amount_due, outstanding_balance=0, status='Paid', payment_method='Razorpay', "
                  "gateway_payment_id=?, payment_reference=?, paid_at=? WHERE id=?", (pay["id"], pay["id"], now_iso(), bp["id"]))
        _maybe_settle(c, tid)
        audit(c, None, "razorpay_captured", "transaction", tid, {"payment_id": pay["id"]})
    return {"ok": True}


@app.get("/api/transactions/{tid}/invoice", response_class=HTMLResponse)
def invoice(tid: int, user=Depends(require("buyer", "aggregator"))):
    with get_conn() as c:
        d = _txn_detail(c, _txn_for(c, tid, user), user)
    if d["status"] not in ("Delivered", "Settled"):
        raise HTTPException(409, "Invoice is issued after delivery is confirmed")
    lot = d["lot"]
    rows_html = "".join(f"<tr><td>{a['batch_code']}</td><td>{a['farmer_name']}</td><td>{a['location']}</td>"
                        f"<td>{a['allocated_quantity']:g}</td><td>₹{a['farmer_price_per_qtl']:,.2f}</td><td>₹{a['gross_farmer_payment']:,.2f}</td></tr>"
                        for a in lot["allocations"])
    bp = d["buyer_payment"] or {}
    return f"""<!doctype html><meta charset=utf-8><title>Invoice {d['po_number']}</title>
<style>body{{font-family:system-ui;margin:32px;color:#1f2937}}table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #d1d5db;padding:6px 8px;text-align:left;font-size:13px}}h1{{color:#166534}}.r{{text-align:right}}</style>
<h1>Agri-Link Settlement Invoice</h1>
<p><b>Invoice:</b> {bp.get('payment_code','')} &nbsp; <b>PO:</b> {d['po_number']} &nbsp; <b>Txn:</b> {d['transaction_code']}<br>
<b>Buyer:</b> {d['buyer']['organisation'] or d['buyer']['name']} &nbsp; <b>Seller (FPO):</b> {lot['aggregator']}<br>
<b>Lot:</b> {lot['lot_code']} — {lot['crop']} {lot['variety'] or ''}, Grade {lot['grade']} &nbsp; <b>Delivered:</b> {d['delivered_at']}</p>
<table><tr><th>Description</th><th>Qty (Qtl)</th><th>Rate</th><th class=r>Amount</th></tr>
<tr><td>{lot['crop']} Grade {lot['grade']}</td><td>{d['quantity_qtl']:g}</td><td>₹{d['price_per_qtl']:,.2f}</td><td class=r>₹{d['crop_sales_revenue']:,.2f}</td></tr>
<tr><th colspan=3>Total payable</th><th class=r>₹{d['total_buyer_cost']:,.2f}</th></tr></table>
<h3>Transparency breakdown</h3>
<table><tr><td>Paid to farmers (procurement cost)</td><td class=r>₹{d['farmer_procurement_cost']:,.2f}</td></tr>
<tr><td>Logistics (borne by FPO)</td><td class=r>₹{d['transport_charges']:,.2f}</td></tr>
<tr><td>FPO net margin</td><td class=r>₹{d['aggregator_net_earnings']:,.2f}</td></tr></table>
<h3>Farm-gate traceability</h3><table><tr><th>Batch</th><th>Farmer</th><th>Location</th><th>Qtl</th><th>Farmer rate</th><th>Farmer gross</th></tr>{rows_html}</table>
<p><b>Payment status:</b> {bp.get('status','—')} {('· ref ' + bp['payment_reference']) if bp.get('payment_reference') else ''}</p>
<button onclick="print()">Print / Save PDF</button>"""


# ---------------------------------------------------------------- earnings
@app.get("/api/earnings/farmer")
def earnings_farmer(user=Depends(require("farmer"))):
    with get_conn() as c:
        p = c.execute("SELECT COALESCE(SUM(gross_amount),0) g, COALESCE(SUM(deductions),0) d, "
                      "COALESCE(SUM(CASE WHEN status='Paid' THEN amount_paid END),0) paid, "
                      "COALESCE(SUM(CASE WHEN status='Pending' THEN net_payable END),0) pending FROM farmer_payments WHERE farmer_id=?",
                      (user["id"],)).fetchone()
        allocated_unsold = c.execute("SELECT COALESCE(SUM(a.gross_farmer_payment),0) FROM lot_allocations a JOIN commercial_lots l ON l.id=a.lot_id "
                                     "WHERE a.farmer_id=? AND l.status IN ('Available','Reserved','Dispatched')", (user["id"],)).fetchone()[0]
        receipts = rows(c.execute("SELECT fp.*, t.po_number, l.lot_code, l.crop FROM farmer_payments fp JOIN transactions t ON t.id=fp.transaction_id "
                                  "JOIN commercial_lots l ON l.id=t.lot_id WHERE fp.farmer_id=? ORDER BY fp.id DESC", (user["id"],)))
    return {"gross": money(p["g"]), "deductions": money(p["d"]), "net": money(p["g"] - p["d"]), "paid": money(p["paid"]),
            "pending": money(p["pending"]), "in_unsold_lots": money(allocated_unsold), "receipts": receipts}


@app.get("/api/earnings/aggregator")
def earnings_agg(user=Depends(require("aggregator"))):
    with get_conn() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(crop_sales_revenue),0) rev, COALESCE(SUM(farmer_procurement_cost),0) fc, "
                      "COALESCE(SUM(transport_charges),0) tr, COALESCE(SUM(aggregator_net_earnings),0) net "
                      "FROM transactions WHERE aggregator_id=? AND status!='Cancelled'", (user["id"],)).fetchone()
        pend = c.execute("SELECT COALESCE(SUM(fp.net_payable),0) FROM farmer_payments fp JOIN transactions t ON t.id=fp.transaction_id "
                         "WHERE t.aggregator_id=? AND fp.status='Pending'", (user["id"],)).fetchone()[0]
        recv = c.execute("SELECT COALESCE(SUM(bp.outstanding_balance),0) FROM buyer_payments bp JOIN transactions t ON t.id=bp.transaction_id "
                         "WHERE t.aggregator_id=?", (user["id"],)).fetchone()[0]
    return {"orders": r["n"], "revenue": money(r["rev"]), "farmer_cost": money(r["fc"]), "gross_margin": money(r["rev"] - r["fc"]),
            "logistics": money(r["tr"]), "net_earnings": money(r["net"]),
            "commission_pct": round(100 * (r["rev"] - r["fc"]) / r["rev"], 2) if r["rev"] else None,
            "farmer_payouts_pending": money(pend), "buyer_receivables": money(recv)}


@app.get("/api/earnings/buyer")
def earnings_buyer(user=Depends(require("buyer"))):
    with get_conn() as c:
        ts = c.execute("SELECT t.*, l.crop FROM transactions t JOIN commercial_lots l ON l.id=t.lot_id WHERE t.buyer_id=? AND t.status!='Cancelled'",
                       (user["id"],)).fetchall()
        out = c.execute("SELECT COALESCE(SUM(outstanding_balance),0) FROM buyer_payments WHERE buyer_id=?", (user["id"],)).fetchone()[0]
    spend = sum(t["total_buyer_cost"] for t in ts)
    return {"orders": len(ts), "total_spend": money(spend), "to_farmers": money(sum(t["farmer_procurement_cost"] for t in ts)),
            "aggregator_margin": money(sum(t["aggregator_gross_margin"] for t in ts)),
            "logistics_borne_by_fpo": money(sum(t["transport_charges"] for t in ts)), "outstanding": money(out),
            "volume_qtl": sum(t["quantity_qtl"] for t in ts),
            "by_crop": {cr: money(sum(t["total_buyer_cost"] for t in ts if t["crop"] == cr)) for cr in {t["crop"] for t in ts}}}


# ---------------------------------------------------------------- notifications
@app.get("/api/notifications")
def notifications(user=Depends(current_user)):
    with get_conn() as c:
        items = rows(c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],)))
        unread = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchone()[0]
    return {"unread": unread, "items": items}


@app.post("/api/notifications/read")
def notifications_read(user=Depends(current_user)):
    with get_conn() as c:
        c.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- reference data
@app.get("/api/procurement-centers")
def centers(state: str | None = None, q: str | None = None, crop: str | None = None, facility_type: str | None = None,
            page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    sql, args = "SELECT * FROM procurement_centers WHERE 1=1", []
    if state:
        sql += " AND state=?"; args.append(state)
    if facility_type:
        sql += " AND facility_type=?"; args.append(facility_type)
    if crop:
        sql += " AND crops LIKE ?"; args.append(f"%{crop}%")
    if q:
        sql += " AND (name LIKE ? OR district LIKE ?)"; args += [f"%{q}%"] * 2
    with get_conn() as c:
        total = c.execute(sql.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
        items = rows(c.execute(sql + " ORDER BY state, district LIMIT ? OFFSET ?", args + [page_size, (page - 1) * page_size]))
    return {"total": total, "items": items}


@app.get("/api/procurement-centers/states")
def center_states():
    with get_conn() as c:
        return rows(c.execute("SELECT state, COUNT(*) AS count FROM procurement_centers GROUP BY state ORDER BY state"))


@app.get("/api/procurement-centers/{cid}")
def center(cid: int):
    with get_conn() as c:
        r = c.execute("SELECT * FROM procurement_centers WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Not found")
    return dict(r)


@app.get("/api/msp")
def msp():
    with get_conn() as c:
        return rows(c.execute("SELECT * FROM msp_rates ORDER BY commodity, variety"))


_mandi_cache: dict = {}


@app.get("/api/mandi-prices")
def mandi_prices(state: str | None = None, commodity: str | None = None, date: str | None = None, limit: int = Query(50, le=500)):
    """Proxy to data.gov.in 'Current daily price of various commodities from various markets (Mandi)' (Agmarknet)."""
    if not DATA_GOV_IN_API_KEY:
        raise HTTPException(503, "Mandi prices need a free data.gov.in API key (DATA_GOV_IN_API_KEY) on the server")
    params = {"api-key": DATA_GOV_IN_API_KEY, "format": "json", "limit": limit}
    if state:
        params["filters[state.keyword]"] = state
    if commodity:
        params["filters[commodity]"] = commodity
    if date:
        params["filters[arrival_date]"] = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
    key = json.dumps(params, sort_keys=True)
    if key in _mandi_cache and time.time() - _mandi_cache[key][0] < 900:
        return _mandi_cache[key][1]
    try:
        r = requests.get(f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception:
        raise HTTPException(502, "data.gov.in did not respond — try again shortly")
    out = {"source": "Agmarknet via data.gov.in (resource " + AGMARKNET_RESOURCE + ")", "fetched_at": now_iso(),
           "records": [{"market": x.get("market"), "district": x.get("district"), "state": x.get("state"),
                        "commodity": x.get("commodity"), "variety": x.get("variety"), "arrival_date": x.get("arrival_date"),
                        "min_price": x.get("min_price"), "max_price": x.get("max_price"), "modal_price": x.get("modal_price")}
                       for x in data.get("records", [])]}
    _mandi_cache[key] = (time.time(), out)
    return out


@app.get("/api/audit")
def audit_log(user=Depends(current_user)):
    with get_conn() as c:
        return rows(c.execute("SELECT * FROM audit_logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)))


# ---------------------------------------------------------------- static frontend (production)
if os.path.isdir(SAMPLES_DIR):
    app.mount("/crop_samples", StaticFiles(directory=SAMPLES_DIR), name="samples")
if os.path.isdir(DIST_DIR):
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "5000")))
