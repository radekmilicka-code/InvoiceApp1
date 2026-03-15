"""
database.py — SQLite database layer for the invoice app.
Replaces JSON flat-file storage (clients.json, invoices.json, products.json, settings.json).

Usage in app.py:
    from database import db
    db.init_app(app)

All functions return plain dicts/lists — same shape as the old JSON data,
so the rest of app.py and the Jinja templates don't need to change.
"""

import sqlite3
import os
import json
from datetime import date, datetime
from contextlib import contextmanager

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'database.db'))
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # access columns by name
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_migrations(conn):
    """
    Safe schema migrations — run on every startup.
    Each ALTER TABLE is tried individually; if the column already exists SQLite
    raises an error which we silently ignore, so this is always safe to run.
    Add new lines here whenever you change the schema in the future.
    """
    migrations = [
        # Format: plain SQL strings, one per schema change
        # Example: "ALTER TABLE clients ADD COLUMN ico TEXT",
        # ── v1.1 ──────────────────────────────────────────
        # (no migrations yet — add future ones here)
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column/index already exists — safe to ignore


def init_db():
    """Create tables + run migrations. Safe to call on every startup."""
    if not os.path.exists(SCHEMA_PATH):
        raise FileNotFoundError(f'schema.sql not found at {SCHEMA_PATH}')
    with get_db() as conn:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        _run_migrations(conn)


def backup_db():
    """
    Returns the raw bytes of the database file for download.
    Uses SQLite online backup API — safe even while the DB is in use.
    """
    import io
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(':memory:')
    src.backup(dst)
    src.close()
    buf = io.BytesIO()
    for line in dst.iterdump():
        buf.write((line + "\n").encode("utf-8"))
    dst.close()
    buf.seek(0)
    return buf.read()


def init_app(app):
    """Flask integration — call db.init_app(app) in app.py."""
    with app.app_context():
        init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row):
    return dict(row) if row else None

def _rows_to_list(rows):
    return [dict(r) for r in rows]


# ── Clients ───────────────────────────────────────────────────────────────────

def get_all_clients():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM clients ORDER BY name').fetchall()
    return _rows_to_list(rows)


def get_client(client_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM clients WHERE id = ?', (client_id,)).fetchone()
    return _row_to_dict(row)


def create_client(name, company=None, email=None, phone=None, address=None):
    # Store empty strings as None so UNIQUE constraint allows multiple clients without email
    email = email.strip() or None if email else None
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO clients (name, company, email, phone, address) VALUES (?,?,?,?,?)',
            (name, company or None, email, phone or None, address or None)
        )
        return get_client(cur.lastrowid)


def update_client(client_id, name, company=None, email=None, phone=None, address=None):
    with get_db() as conn:
        conn.execute(
            '''UPDATE clients SET name=?, company=?, email=?, phone=?, address=?
               WHERE id=?''',
            (name, company, email, phone, address, client_id)
        )
    return get_client(client_id)


def delete_client(client_id):
    with get_db() as conn:
        conn.execute('DELETE FROM clients WHERE id = ?', (client_id,))


def import_clients_csv(rows):
    """
    Bulk import clients from a list of dicts.
    Skips duplicates by email. Returns (imported, skipped) counts.
    """
    imported = skipped = 0
    with get_db() as conn:
        for row in rows:
            email = (row.get('email') or row.get('E-mail') or '').strip().lower() or None
            name  = row.get('name') or row.get('Jméno') or ''
            if not name:
                continue
            if email:
                exists = conn.execute('SELECT id FROM clients WHERE lower(email)=?', (email,)).fetchone()
                if exists:
                    skipped += 1
                    continue
            conn.execute(
                'INSERT INTO clients (name, company, email, phone, address) VALUES (?,?,?,?,?)',
                (
                    name,
                    row.get('company') or row.get('Firma') or None,
                    email,
                    row.get('phone') or row.get('Telefon') or None,
                    row.get('address') or row.get('Adresa') or None,
                )
            )
            imported += 1
    return imported, skipped


# ── Products ──────────────────────────────────────────────────────────────────

def get_all_products(active_only=False):
    with get_db() as conn:
        q = 'SELECT * FROM products'
        if active_only:
            q += ' WHERE active = 1'
        q += ' ORDER BY category, name'
        rows = conn.execute(q).fetchall()
    return _rows_to_list(rows)


def get_product(product_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    return _row_to_dict(row)


def create_product(name, description=None, category=None, unit='ks',
                   price=0.0, tax_rate=21.0, stock=0):
    with get_db() as conn:
        cur = conn.execute(
            '''INSERT INTO products (name, description, category, unit, price, tax_rate, stock)
               VALUES (?,?,?,?,?,?,?)''',
            (name, description, category, unit, price, tax_rate, stock)
        )
        return get_product(cur.lastrowid)


def update_product(product_id, name, description=None, category=None, unit='ks',
                   price=0.0, tax_rate=21.0, stock=0, active=1):
    with get_db() as conn:
        conn.execute(
            '''UPDATE products
               SET name=?, description=?, category=?, unit=?, price=?, tax_rate=?, stock=?, active=?
               WHERE id=?''',
            (name, description, category, unit, price, tax_rate, stock, active, product_id)
        )
    return get_product(product_id)


def delete_product(product_id):
    with get_db() as conn:
        conn.execute('DELETE FROM products WHERE id = ?', (product_id,))


# ── Pricing ───────────────────────────────────────────────────────────────────

def get_pricing_for_client(client_id):
    """Returns all pricing rows for a client, joined with product name."""
    with get_db() as conn:
        rows = conn.execute('''
            SELECT p.*, pr.name as product_name, pr.price as base_price
            FROM pricing p
            JOIN products pr ON pr.id = p.product_id
            WHERE p.client_id = ?
            ORDER BY pr.category, pr.name
        ''', (client_id,)).fetchall()
    return _rows_to_list(rows)


def get_effective_price(client_id, product_id):
    """
    Returns the effective price for a client/product combination.
    Priority: custom_price > discount_pct > base price.
    Only considers valid (not expired) pricing rows.
    """
    today = date.today().isoformat()
    with get_db() as conn:
        row = conn.execute('''
            SELECT p.custom_price, p.discount_pct, pr.price as base_price
            FROM pricing p
            JOIN products pr ON pr.id = p.product_id
            WHERE p.client_id = ? AND p.product_id = ?
              AND (p.valid_from IS NULL OR p.valid_from <= ?)
              AND (p.valid_until IS NULL OR p.valid_until >= ?)
            ORDER BY p.id DESC LIMIT 1
        ''', (client_id, product_id, today, today)).fetchone()

        if not row:
            product = conn.execute('SELECT price FROM products WHERE id=?', (product_id,)).fetchone()
            return product['price'] if product else 0.0

        if row['custom_price'] is not None:
            return row['custom_price']
        return round(row['base_price'] * (1 - row['discount_pct'] / 100), 2)


def upsert_pricing(client_id, product_id, custom_price=None, discount_pct=0.0,
                   tier='standard', valid_from=None, valid_until=None, notes=None):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO pricing (client_id, product_id, custom_price, discount_pct,
                                 tier, valid_from, valid_until, notes)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(client_id, product_id) DO UPDATE SET
                custom_price=excluded.custom_price,
                discount_pct=excluded.discount_pct,
                tier=excluded.tier,
                valid_from=excluded.valid_from,
                valid_until=excluded.valid_until,
                notes=excluded.notes
        ''', (client_id, product_id, custom_price, discount_pct,
              tier, valid_from, valid_until, notes))


def delete_pricing(pricing_id):
    with get_db() as conn:
        conn.execute('DELETE FROM pricing WHERE id = ?', (pricing_id,))


# ── Invoices ──────────────────────────────────────────────────────────────────

def _attach_items(conn, invoice_dict):
    """Add 'items' list to an invoice dict."""
    rows = conn.execute(
        'SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY id',
        (invoice_dict['id'],)
    ).fetchall()
    invoice_dict['items'] = _rows_to_list(rows)
    return invoice_dict


def get_all_invoices(with_items=False):
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM invoices ORDER BY issue_date DESC').fetchall()
        invoices = _rows_to_list(rows)
        if with_items:
            for inv in invoices:
                _attach_items(conn, inv)
    return invoices


def get_invoice(invoice_id, with_items=True):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM invoices WHERE id = ?', (invoice_id,)).fetchone()
        if not row:
            return None
        inv = _row_to_dict(row)
        if with_items:
            _attach_items(conn, inv)
    return inv


def check_overdue(conn=None):
    """Mark all unpaid invoices past due_date as overdue. Returns count changed."""
    today = date.today().isoformat()
    close_conn = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE invoices SET status='overdue' WHERE status='unpaid' AND due_date < ?",
            (today,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        if close_conn:
            conn.close()


def get_invoice_stats():
    """Returns dashboard stats dict. Always returns ints/floats, never None."""
    with get_db() as conn:
        check_overdue(conn)
        row = conn.execute('''
            SELECT
                COUNT(*)                                                                   as total,
                COALESCE(SUM(status='paid'), 0)                                           as paid,
                COALESCE(SUM(status='unpaid'), 0)                                         as unpaid,
                COALESCE(SUM(status='overdue'), 0)                                        as overdue,
                COALESCE(SUM(CASE WHEN status='paid' THEN total END), 0)                  as revenue,
                COALESCE(SUM(CASE WHEN status IN ('unpaid','overdue') THEN total END), 0) as outstanding
            FROM invoices
        ''').fetchone()
    d = _row_to_dict(row)
    for k in ('total', 'paid', 'unpaid', 'overdue'):
        d[k] = int(d[k] or 0)
    for k in ('revenue', 'outstanding'):
        d[k] = float(d[k] or 0)
    return d


def get_next_invoice_number(prefix='INV'):
    with get_db() as conn:
        row = conn.execute(
            "SELECT invoice_number FROM invoices ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return f'{prefix}-0001'
    last = row['invoice_number']
    try:
        num = int(last.split('-')[-1]) + 1
    except (ValueError, IndexError):
        num = 1
    return f'{prefix}-{num:04d}'


def create_invoice(invoice_number, client_id, issue_date, due_date,
                   items, tax_rate, notes=''):
    """
    items: list of {'name', 'qty', 'price', 'product_id' (optional)}
    Returns the created invoice dict with items.
    """
    for item in items:
        item['subtotal'] = round(item['qty'] * item['price'], 2)

    subtotal   = round(sum(i['subtotal'] for i in items), 2)
    tax_amount = round(subtotal * tax_rate / 100, 2)
    total      = round(subtotal + tax_amount, 2)

    with get_db() as conn:
        cur = conn.execute('''
            INSERT INTO invoices
              (invoice_number, client_id, issue_date, due_date,
               subtotal, tax_rate, tax_amount, total, status, notes, created_at)
            VALUES (?,?,?,?,?,?,?,?,'unpaid',?,?)
        ''', (invoice_number, client_id, issue_date, due_date,
              subtotal, tax_rate, tax_amount, total, notes,
              datetime.now().isoformat()))
        inv_id = cur.lastrowid

        for item in items:
            conn.execute('''
                INSERT INTO invoice_items (invoice_id, product_id, name, qty, price, subtotal)
                VALUES (?,?,?,?,?,?)
            ''', (inv_id,
                  item.get('product_id'),
                  item['name'],
                  item['qty'],
                  item['price'],
                  item['subtotal']))

    return get_invoice(inv_id)


def update_invoice(invoice_id, client_id, issue_date, due_date,
                   items, tax_rate, notes=''):
    for item in items:
        item['subtotal'] = round(item['qty'] * item['price'], 2)

    subtotal   = round(sum(i['subtotal'] for i in items), 2)
    tax_amount = round(subtotal * tax_rate / 100, 2)
    total      = round(subtotal + tax_amount, 2)

    with get_db() as conn:
        conn.execute('''
            UPDATE invoices SET client_id=?, issue_date=?, due_date=?,
              subtotal=?, tax_rate=?, tax_amount=?, total=?, notes=?
            WHERE id=?
        ''', (client_id, issue_date, due_date,
              subtotal, tax_rate, tax_amount, total, notes, invoice_id))

        conn.execute('DELETE FROM invoice_items WHERE invoice_id=?', (invoice_id,))
        for item in items:
            conn.execute('''
                INSERT INTO invoice_items (invoice_id, product_id, name, qty, price, subtotal)
                VALUES (?,?,?,?,?,?)
            ''', (invoice_id, item.get('product_id'), item['name'],
                  item['qty'], item['price'], item['subtotal']))

    return get_invoice(invoice_id)


def update_invoice_status(invoice_id, status):
    paid_at = datetime.now().isoformat() if status == 'paid' else None
    with get_db() as conn:
        conn.execute(
            'UPDATE invoices SET status=?, paid_at=? WHERE id=?',
            (status, paid_at, invoice_id)
        )


def mark_invoice_sent(invoice_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE invoices SET last_sent=? WHERE id=?",
            (datetime.now().isoformat(), invoice_id)
        )


def mark_invoice_reminded(invoice_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE invoices SET last_reminded=? WHERE id=?",
            (date.today().isoformat(), invoice_id)
        )


def delete_invoice(invoice_id):
    with get_db() as conn:
        conn.execute('DELETE FROM invoices WHERE id=?', (invoice_id,))


def get_overdue_for_reminders(reminder_days=3):
    """Returns invoices eligible for reminder emails."""
    today = date.today().isoformat()
    with get_db() as conn:
        check_overdue(conn)
        rows = conn.execute('''
            SELECT i.*, c.name as client_name, c.email as client_email,
                   c.company as client_company
            FROM invoices i
            JOIN clients c ON c.id = i.client_id
            WHERE i.status = 'overdue'
              AND c.email IS NOT NULL
              AND (
                i.last_reminded IS NULL
                OR julianday('now') - julianday(i.last_reminded) >= 7
              )
              AND julianday('now') - julianday(i.due_date) >= ?
        ''', (reminder_days,)).fetchall()
    return _rows_to_list(rows)


# ── Settings ──────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    'company_name':     'Šefl s.r.o.',
    'company_subtitle': 'Pekárna & Cukrárna',
    'ico':              '',
    'dic':              '',
    'address':          '',
    'email':            '',
    'phone':            '',
    'bank_account':     '',
    'iban':             '',
    'invoice_prefix':   'INV',
    'default_due_days': '14',
    'default_tax_rate': '21',
    'resend_api_key':   '',
    'reminder_days':    '3',
}


def load_settings():
    with get_db() as conn:
        rows = conn.execute('SELECT key, value FROM settings').fetchall()
    stored = {r['key']: r['value'] for r in rows}
    result = {**DEFAULT_SETTINGS, **stored}
    # Cast numeric fields
    result['default_due_days'] = int(result.get('default_due_days') or 14)
    result['default_tax_rate'] = float(result.get('default_tax_rate') or 21)
    result['reminder_days']    = int(result.get('reminder_days') or 3)
    return result


def save_settings(data):
    with get_db() as conn:
        for key, value in data.items():
            conn.execute(
                '''INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
                (key, str(value) if value is not None else '')
            )
