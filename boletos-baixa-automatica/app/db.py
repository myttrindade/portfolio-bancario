import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS charges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cnpj TEXT NOT NULL, payer TEXT NOT NULL, description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'REGISTERED',   -- REGISTERED | PARTIAL | PAID
  paid_cents INTEGER NOT NULL DEFAULT 0,
  credit_cents INTEGER NOT NULL DEFAULT 0,     -- pago a maior (a devolver ou abater)
  our_number TEXT UNIQUE, txid TEXT UNIQUE, linha TEXT, pix_payload TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,                   -- PRIMARY KEY: o mesmo aviso do banco nunca vale duas vezes
  charge_id INTEGER,
  kind TEXT NOT NULL,                          -- PAID | PARTIAL | OVERPAID | DUPLICATE | UNMATCHED
  channel TEXT, reference TEXT,
  amount_cents INTEGER NOT NULL, applied_cents INTEGER NOT NULL DEFAULT 0,
  paid_at TEXT, late INTEGER NOT NULL DEFAULT 0,
  erp_status TEXT NOT NULL DEFAULT 'NA',       -- NA | PENDING | DONE
  note TEXT NOT NULL DEFAULT ''
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
