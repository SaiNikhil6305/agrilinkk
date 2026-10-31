"""SQLite persistence layer for Agri-Link (agri_link.db)."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("AGRI_DB_PATH", os.path.join(os.path.dirname(__file__), "agri_link.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT UNIQUE,
  phone TEXT UNIQUE,
  role TEXT NOT NULL CHECK (role IN ('farmer','aggregator','buyer','admin')),
  password_hash TEXT,
  salt TEXT,
  firebase_uid TEXT UNIQUE,
  organisation TEXT,
  location TEXT,
  state TEXT,
  district TEXT,
  latitude REAL,
  longitude REAL,
  language TEXT DEFAULT 'en',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON user_sessions(user_id);

CREATE TABLE IF NOT EXISTS crop_predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id),
  image_sha256 TEXT NOT NULL,
  image_path TEXT,
  crop TEXT, crop_confidence REAL,
  defect TEXT, defect_confidence REAL,
  quality_class TEXT, quality_confidence REAL,
  grade TEXT NOT NULL,
  measured_moisture REAL,
  moisture_source TEXT,
  advisory TEXT,
  model_version TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS farmer_submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_code TEXT UNIQUE NOT NULL,
  farmer_id INTEGER NOT NULL REFERENCES users(id),
  farmer_name TEXT NOT NULL,
  phone TEXT,
  crop TEXT NOT NULL,
  variety TEXT,
  grade TEXT NOT NULL CHECK (grade IN ('A','B','C','U')),  -- U = awaiting FPO inspection
  prediction_id INTEGER REFERENCES crop_predictions(id),
  quality_score REAL,
  moisture REAL,
  quantity_qtl REAL NOT NULL CHECK (quantity_qtl > 0),
  available_quantity REAL NOT NULL CHECK (available_quantity >= 0),
  expected_price_per_qtl REAL NOT NULL CHECK (expected_price_per_qtl > 0),
  location TEXT NOT NULL,
  state TEXT, district TEXT,
  latitude REAL, longitude REAL,
  harvest_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','In Yard','Rejected','Allocated to Lot','Sold')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_sub_farmer ON farmer_submissions(farmer_id);
CREATE INDEX IF NOT EXISTS ix_sub_crop_grade ON farmer_submissions(crop, grade);

CREATE TABLE IF NOT EXISTS commercial_lots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_code TEXT UNIQUE NOT NULL,
  aggregator_id INTEGER NOT NULL REFERENCES users(id),
  crop TEXT NOT NULL,
  variety TEXT,
  grade TEXT NOT NULL CHECK (grade IN ('A','B','C')),
  moisture REAL,
  base_price_per_qtl REAL NOT NULL CHECK (base_price_per_qtl > 0),
  total_quantity REAL NOT NULL DEFAULT 0 CHECK (total_quantity >= 0),
  allocated_quantity REAL NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0),
  remaining_quantity REAL NOT NULL DEFAULT 0 CHECK (remaining_quantity >= 0),
  location TEXT,
  status TEXT NOT NULL DEFAULT 'Available' CHECK (status IN ('Draft','Available','Reserved','Dispatched','Sold','Cancelled')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lot_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_id INTEGER NOT NULL REFERENCES commercial_lots(id),
  submission_id INTEGER NOT NULL REFERENCES farmer_submissions(id),
  farmer_id INTEGER NOT NULL REFERENCES users(id),
  allocated_quantity REAL NOT NULL CHECK (allocated_quantity > 0),
  farmer_price_per_qtl REAL NOT NULL,
  gross_farmer_payment REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (lot_id, submission_id)
);

CREATE TABLE IF NOT EXISTS buyer_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_code TEXT UNIQUE NOT NULL,
  buyer_id INTEGER NOT NULL REFERENCES users(id),
  buyer_name TEXT NOT NULL,
  crop TEXT NOT NULL,
  variety TEXT,
  quantity_qtl REAL NOT NULL CHECK (quantity_qtl > 0),
  remaining_qty REAL NOT NULL CHECK (remaining_qty >= 0),
  min_grade TEXT NOT NULL CHECK (min_grade IN ('A','B','C')),
  target_price REAL NOT NULL CHECK (target_price > 0),
  delivery_location TEXT NOT NULL,
  delivery_date TEXT NOT NULL,
  partial_ok INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','Accepted','Rejected','Partially Fulfilled','Fulfilled','Cancelled')),
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transaction_code TEXT UNIQUE NOT NULL,
  po_number TEXT UNIQUE NOT NULL,
  buyer_id INTEGER NOT NULL REFERENCES users(id),
  aggregator_id INTEGER NOT NULL REFERENCES users(id),
  lot_id INTEGER NOT NULL REFERENCES commercial_lots(id),
  request_id INTEGER REFERENCES buyer_requests(id),
  quantity_qtl REAL NOT NULL CHECK (quantity_qtl > 0),
  price_per_qtl REAL NOT NULL,
  crop_sales_revenue REAL NOT NULL,
  farmer_procurement_cost REAL NOT NULL,
  transport_charges REAL NOT NULL DEFAULT 0 CHECK (transport_charges >= 0),
  aggregator_gross_margin REAL NOT NULL,
  aggregator_net_earnings REAL NOT NULL,
  total_buyer_cost REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'Ordered' CHECK (status IN ('Ordered','Dispatched','Delivered','Settled','Cancelled')),
  vehicle_number TEXT, carrier TEXT,
  dispatched_at TEXT, delivered_at TEXT,
  payment_status TEXT NOT NULL DEFAULT 'Unpaid',
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS farmer_payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_code TEXT UNIQUE NOT NULL,
  transaction_id INTEGER NOT NULL REFERENCES transactions(id),
  allocation_id INTEGER NOT NULL UNIQUE REFERENCES lot_allocations(id),
  farmer_id INTEGER NOT NULL REFERENCES users(id),
  gross_amount REAL NOT NULL,
  deductions REAL NOT NULL DEFAULT 0 CHECK (deductions >= 0),
  deduction_reason TEXT,
  net_payable REAL NOT NULL,
  amount_paid REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Paid')),
  payment_reference TEXT,
  payment_method TEXT,
  paid_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS buyer_payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_code TEXT UNIQUE NOT NULL,
  transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
  buyer_id INTEGER NOT NULL REFERENCES users(id),
  amount_due REAL NOT NULL,
  amount_paid REAL NOT NULL DEFAULT 0,
  outstanding_balance REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'Due' CHECK (status IN ('Due','Paid')),
  payment_reference TEXT,
  payment_method TEXT,
  gateway_payment_id TEXT UNIQUE,
  paid_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  link TEXT,
  is_read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id TEXT,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS procurement_centers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  facility_type TEXT NOT NULL,
  state TEXT NOT NULL,
  district TEXT NOT NULL,
  crops TEXT NOT NULL,
  source TEXT NOT NULL,
  UNIQUE (name, state)
);

CREATE TABLE IF NOT EXISTS msp_rates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  commodity TEXT NOT NULL,
  variety TEXT NOT NULL DEFAULT '',
  season TEXT NOT NULL,
  marketing_year TEXT NOT NULL,
  msp_per_qtl REAL NOT NULL,
  source TEXT NOT NULL,
  UNIQUE (commodity, variety, marketing_year)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)  # autocommit; explicit BEGIN for txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so concurrent allocations serialise."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db():
    from procurement_data import CENTERS, MSP_RATES
    with get_conn() as c:
        c.executescript(SCHEMA)
        c.executemany(
            "INSERT OR IGNORE INTO procurement_centers(name,facility_type,state,district,crops,source) VALUES (?,?,?,?,?,?)",
            CENTERS)
        c.executemany(
            "INSERT OR IGNORE INTO msp_rates(commodity,variety,season,marketing_year,msp_per_qtl,source) VALUES (?,?,?,?,?,?)",
            MSP_RATES)


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
