import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cnpj TEXT NOT NULL,
  barcode TEXT NOT NULL UNIQUE,          -- UNIQUE: o mesmo boleto nunca entra duas vezes
  beneficiary TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | IN_BATCH | PAID
  batch_id INTEGER
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL,
  approved_by TEXT,
  status TEXT NOT NULL,                  -- PENDING_APPROVAL | APPROVING | PAID | PARTIAL | FAILED | REJECTED
  total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  bill_id INTEGER NOT NULL,
  status TEXT NOT NULL,                  -- PAID | REJECTED
  bank_ref TEXT, receipt TEXT, reason TEXT,
  erp_status TEXT NOT NULL DEFAULT 'NA'  -- NA | PENDING | DONE
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
