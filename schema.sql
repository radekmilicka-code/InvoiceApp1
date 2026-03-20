-- ============================================================
-- Invoice App — SQLite Schema
-- Migrated from JSON flat files
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Clients ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    company     TEXT,
    email       TEXT    UNIQUE,
    phone       TEXT,
    address     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    ico         TEXT
);

CREATE INDEX IF NOT EXISTS idx_clients_email   ON clients(email);
CREATE INDEX IF NOT EXISTS idx_clients_company ON clients(company);

-- ── Products ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT,
    category    TEXT,
    unit        TEXT    DEFAULT 'ks',
    price       REAL    NOT NULL DEFAULT 0,
    tax_rate    REAL    NOT NULL DEFAULT 21,
    stock       INTEGER DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,   -- 1=active, 0=archived
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_active   ON products(active);

-- ── Pricing (client-specific overrides) ──────────────────────
CREATE TABLE IF NOT EXISTS pricing (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    custom_price    REAL,                -- fixed override price (NULL = use product price)
    discount_pct    REAL    DEFAULT 0,  -- % discount on top of product price
    tier            TEXT    DEFAULT 'standard', -- standard / wholesale / vip
    valid_from      TEXT,               -- NULL = always valid
    valid_until     TEXT,               -- NULL = no expiry
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (client_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_pricing_client  ON pricing(client_id);
CREATE INDEX IF NOT EXISTS idx_pricing_product ON pricing(product_id);

-- ── Invoices ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number  TEXT    NOT NULL UNIQUE,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    issue_date      TEXT    NOT NULL,
    due_date        TEXT    NOT NULL,
    subtotal        REAL    NOT NULL DEFAULT 0,
    tax_rate        REAL    NOT NULL DEFAULT 21,
    tax_amount      REAL    NOT NULL DEFAULT 0,
    total           REAL    NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'unpaid'
                            CHECK(status IN ('unpaid','paid','overdue')),
    notes           TEXT,
    last_sent       TEXT,
    last_reminded   TEXT,
    paid_at         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_due    ON invoices(due_date);

-- ── Invoice Items ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoice_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id  INTEGER REFERENCES products(id),  -- NULL = custom line item
    name        TEXT    NOT NULL,
    qty         REAL    NOT NULL DEFAULT 1,
    price       REAL    NOT NULL DEFAULT 0,
    subtotal    REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_invoice ON invoice_items(invoice_id);

-- ── Settings (key-value store) ───────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Trigger: auto-update updated_at ──────────────────────────
CREATE TRIGGER IF NOT EXISTS trg_clients_updated
    AFTER UPDATE ON clients
    BEGIN UPDATE clients SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_products_updated
    AFTER UPDATE ON products
    BEGIN UPDATE products SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_updated
    AFTER UPDATE ON invoices
    BEGIN UPDATE invoices SET updated_at = datetime('now') WHERE id = NEW.id; END;
