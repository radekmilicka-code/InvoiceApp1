from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, session
import json
import os
import csv
from datetime import datetime, date
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
import io

app = Flask(__name__)

# ── Přihlášení ────────────────────────────────────────────────────────────────

from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        app_password = os.environ.get('APP_PASSWORD', 'admin')
        if password == app_password:
            session['logged_in'] = True
            next_url = request.form.get('next') or url_for('index')
            return redirect(next_url)
        error = 'Špatné heslo.'
    return render_template('login.html', error=error, next=request.args.get('next', ''))

@app.route('/logout', methods=['POST'])
@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

# Railway/Docker: use /app/data if running in container, otherwise local data/
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
CLIENTS_FILE  = os.path.join(DATA_DIR, 'clients.json')
INVOICES_FILE = os.path.join(DATA_DIR, 'invoices.json')
PRODUCTS_FILE = os.path.join(DATA_DIR, 'products.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

DEFAULT_SETTINGS = {
    'company_name': 'Šefl s.r.o.',
    'company_subtitle': 'Pekárna & Cukrárna',
    'ico': '',
    'dic': '',
    'address': '',
    'email': '',
    'phone': '',
    'bank_account': '',
    'iban': '',
    'invoice_prefix': 'INV',
    'default_due_days': 14,
    'default_tax_rate': 21,
    # Email / SMTP
    'smtp_host': 'smtp.seznam.cz',
    'smtp_port': 465,
    'smtp_user': '',
    'smtp_password': '',
    'reminder_days': 3,
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
        # merge with defaults for any missing keys
        return {**DEFAULT_SETTINGS, **s}
    return DEFAULT_SETTINGS.copy()

def save_settings(data):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def get_next_id(items):
    return max((i.get('id', 0) for i in items), default=0) + 1

def check_overdue(invoices):
    today = date.today().isoformat()
    for inv in invoices:
        if inv['status'] == 'unpaid' and inv.get('due_date') and inv['due_date'] < today:
            inv['status'] = 'overdue'
    return invoices
# ── Email ─────────────────────────────────────────────────────────────────────

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

def send_invoice_email(invoice, client, pdf_bytes, subject=None, body=None):
    """Send invoice PDF to client via SMTP. Returns (ok, error_message)."""
    s = load_settings()
    if not s.get('smtp_user') or not s.get('smtp_password'):
        return False, 'SMTP není nakonfigurováno. Nastavte email v Nastavení.'
    if not client.get('email'):
        return False, 'Klient nemá vyplněný e-mail.'

    from_addr = s['smtp_user']
    to_addr   = client['email']
    company   = s.get('company_name', 'Fakturace')

    if not subject:
        subject = f"Faktura {invoice['invoice_number']} — {company}"
    if not body:
        body = f"""Dobrý den,

v příloze zasíláme fakturu č. {invoice['invoice_number']} na částku {invoice['total']:.2f} Kč.
Datum splatnosti: {invoice['due_date']}.
"""
        if s.get('bank_account'):
            body += 'Cislo uctu: ' + s['bank_account'] + chr(10)
        if s.get('iban'):
            body += 'IBAN: ' + s['iban'] + chr(10)
        body += f"""
Děkujeme za Vaši důvěru.

S pozdravem,
{company}"""
        if s.get('phone'):
            body += f"\nTel: {s['phone']}"
        if s.get('email'):
            body += f"\nEmail: {s['email']}"

    msg = MIMEMultipart()
    msg['From']    = f"{company} <{from_addr}>"
    msg['To']      = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Attach PDF
    part = MIMEBase('application', 'octet-stream')
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition',
                    f'attachment; filename="{invoice["invoice_number"]}.pdf"')
    msg.attach(part)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(s['smtp_host'], int(s['smtp_port']), context=ctx) as server:
            server.login(s['smtp_user'], s['smtp_password'])
            server.sendmail(from_addr, to_addr, msg.as_bytes())
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, 'Chybné přihlašovací údaje k e-mailu.'
    except smtplib.SMTPException as e:
        return False, f'Chyba při odesílání: {str(e)}'
    except Exception as e:
        return False, f'Neočekávaná chyba: {str(e)}'


def send_reminders():
    """
    Send overdue reminders for invoices that:
    - are overdue
    - have not been reminded yet (or last reminder was > 7 days ago)
    Returns list of (invoice_number, client_email, ok, error).
    """
    s = load_settings()
    if not s.get('smtp_user') or not s.get('smtp_password'):
        return []

    invoices = check_overdue(load_json(INVOICES_FILE))
    save_json(INVOICES_FILE, invoices)
    clients  = load_json(CLIENTS_FILE)
    client_map = {c['id']: c for c in clients}
    results = []

    reminder_days = int(s.get('reminder_days', 3))
    today = date.today()

    for inv in invoices:
        if inv['status'] != 'overdue':
            continue
        due = date.fromisoformat(inv['due_date'])
        days_late = (today - due).days
        if days_late < reminder_days:
            continue

        # Check if already reminded recently (within 7 days)
        last_reminded = inv.get('last_reminded')
        if last_reminded:
            last_dt = date.fromisoformat(last_reminded)
            if (today - last_dt).days < 7:
                continue

        client = client_map.get(inv['client_id'], {})
        if not client.get('email'):
            continue

        # Generate PDF
        try:
            pdf_bytes = _generate_pdf_bytes(inv, client, s)
        except Exception:
            results.append((inv['invoice_number'], client.get('email',''), False, 'Chyba při generování PDF'))
            continue

        company = s.get('company_name', 'Fakturace')
        days_str = 'den' if days_late == 1 else ('dny' if days_late < 5 else 'dní')
        subject = f"Upomínka — faktura {inv['invoice_number']} je {days_late} {days_str} po splatnosti"
        body = f"""Dobrý den,

dovolujeme si Vás upozornit, že faktura č. {inv['invoice_number']} na částku {inv['total']:.2f} Kč
je již {days_late} {days_str} po datu splatnosti ({inv['due_date']}).

Prosíme o neprodlené uhrazení.
"""
        if s.get('bank_account'):
            body += f"Číslo účtu: {s['bank_account']}\n"
        if s.get('iban'):
            body += f"IBAN: {s['iban']}\n"
        body += f"""
S pozdravem,
{company}"""

        ok, err = send_invoice_email(inv, client, pdf_bytes, subject=subject, body=body)
        results.append((inv['invoice_number'], client['email'], ok, err))

        if ok:
            inv['last_reminded'] = today.isoformat()

    save_json(INVOICES_FILE, invoices)
    return results



@app.route('/')
@login_required
def index():
    invoices = check_overdue(load_json(INVOICES_FILE))
    save_json(INVOICES_FILE, invoices)
    clients = load_json(CLIENTS_FILE)
    client_map = {c['id']: c['name'] for c in clients}

    stats = {
        'total': len(invoices),
        'paid': sum(1 for i in invoices if i['status'] == 'paid'),
        'unpaid': sum(1 for i in invoices if i['status'] == 'unpaid'),
        'overdue': sum(1 for i in invoices if i['status'] == 'overdue'),
        'revenue': sum(i['total'] for i in invoices if i['status'] == 'paid'),
        'outstanding': sum(i['total'] for i in invoices if i['status'] in ('unpaid', 'overdue')),
    }
    return render_template('index.html', invoices=invoices, client_map=client_map, stats=stats)

# ── Settings ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        s = {
            'company_name':     request.form.get('company_name', ''),
            'company_subtitle': request.form.get('company_subtitle', ''),
            'ico':              request.form.get('ico', ''),
            'dic':              request.form.get('dic', ''),
            'address':          request.form.get('address', ''),
            'email':            request.form.get('email', ''),
            'phone':            request.form.get('phone', ''),
            'bank_account':     request.form.get('bank_account', ''),
            'iban':             request.form.get('iban', '').replace(' ', '').upper(),
            'invoice_prefix':   request.form.get('invoice_prefix', 'INV'),
            'default_due_days': int(request.form.get('default_due_days', 14)),
            'default_tax_rate': float(request.form.get('default_tax_rate', 21)),
            'smtp_host':        request.form.get('smtp_host', 'smtp.seznam.cz'),
            'smtp_port':        int(request.form.get('smtp_port', 465)),
            'smtp_user':        request.form.get('smtp_user', ''),
            'smtp_password':    request.form.get('smtp_password', ''),
            'reminder_days':    int(request.form.get('reminder_days', 3)),
        }
        save_settings(s)
        flash('Nastavení bylo uloženo.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', s=load_settings())

# ── Products ─────────────────────────────────────────────────────────────────

@app.route('/products')
@login_required
def products():
    return render_template('products.html', products=load_json(PRODUCTS_FILE))

@app.route('/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        products = load_json(PRODUCTS_FILE)
        product = {
            'id': get_next_id(products),
            'name': request.form['name'],
            'description': request.form.get('description', ''),
            'price': float(request.form.get('price', 0)),
            'unit': request.form.get('unit', ''),
            'category': request.form.get('category', ''),
        }
        products.append(product)
        save_json(PRODUCTS_FILE, products)
        return redirect(url_for('products'))
    return render_template('product_form.html', product=None)

@app.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    products = load_json(PRODUCTS_FILE)
    product = next((p for p in products if p['id'] == product_id), None)
    if not product:
        return redirect(url_for('products'))
    if request.method == 'POST':
        product.update({
            'name': request.form['name'],
            'description': request.form.get('description', ''),
            'price': float(request.form.get('price', 0)),
            'unit': request.form.get('unit', ''),
            'category': request.form.get('category', ''),
        })
        save_json(PRODUCTS_FILE, products)
        return redirect(url_for('products'))
    return render_template('product_form.html', product=product)

@app.route('/products/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    products = [p for p in load_json(PRODUCTS_FILE) if p['id'] != product_id]
    save_json(PRODUCTS_FILE, products)
    return redirect(url_for('products'))

@app.route('/api/products')
@login_required
def api_products():
    return jsonify(load_json(PRODUCTS_FILE))

# ── Clients ──────────────────────────────────────────────────────────────────

@app.route('/clients')
@login_required
def clients():
    return render_template('clients.html', clients=load_json(CLIENTS_FILE))

@app.route('/clients/add', methods=['GET', 'POST'])
@login_required
def add_client():
    if request.method == 'POST':
        clients = load_json(CLIENTS_FILE)
        client = {
            'id': get_next_id(clients),
            'name': request.form['name'],
            'email': request.form['email'],
            'phone': request.form.get('phone', ''),
            'address': request.form.get('address', ''),
            'company': request.form.get('company', ''),
        }
        clients.append(client)
        save_json(CLIENTS_FILE, clients)
        return redirect(url_for('clients'))
    return render_template('client_form.html', client=None)

@app.route('/clients/edit/<int:client_id>', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    clients = load_json(CLIENTS_FILE)
    client = next((c for c in clients if c['id'] == client_id), None)
    if not client:
        return redirect(url_for('clients'))
    if request.method == 'POST':
        client.update({
            'name': request.form['name'],
            'email': request.form['email'],
            'phone': request.form.get('phone', ''),
            'address': request.form.get('address', ''),
            'company': request.form.get('company', ''),
        })
        save_json(CLIENTS_FILE, clients)
        return redirect(url_for('clients'))
    return render_template('client_form.html', client=client)

@app.route('/clients/delete/<int:client_id>', methods=['POST'])
@login_required
def delete_client(client_id):
    clients = [c for c in load_json(CLIENTS_FILE) if c['id'] != client_id]
    save_json(CLIENTS_FILE, clients)
    return redirect(url_for('clients'))

# ── Invoices ─────────────────────────────────────────────────────────────────

@app.route('/invoices/new', methods=['GET', 'POST'])
@login_required
def new_invoice():
    clients = load_json(CLIENTS_FILE)
    if request.method == 'POST':
        invoices = load_json(INVOICES_FILE)
        items = []
        names = request.form.getlist('item_name')
        qtys = request.form.getlist('item_qty')
        prices = request.form.getlist('item_price')
        for n, q, p in zip(names, qtys, prices):
            if n.strip():
                qty = float(q or 0)
                price = float(p or 0)
                items.append({'name': n, 'qty': qty, 'price': price, 'subtotal': qty * price})

        subtotal = sum(i['subtotal'] for i in items)
        tax_rate = float(request.form.get('tax_rate', 0))
        tax_amount = subtotal * tax_rate / 100
        total = subtotal + tax_amount

        inv = {
            'id': get_next_id(invoices),
            'invoice_number': f"{load_settings()['invoice_prefix']}-{get_next_id(invoices):04d}",
            'client_id': int(request.form['client_id']),
            'issue_date': request.form['issue_date'],
            'due_date': request.form['due_date'],
            'items': items,
            'subtotal': subtotal,
            'tax_rate': tax_rate,
            'tax_amount': tax_amount,
            'total': total,
            'notes': request.form.get('notes', ''),
            'status': 'unpaid',
            'created_at': datetime.now().isoformat(),
        }
        invoices.append(inv)
        save_json(INVOICES_FILE, invoices)
        return redirect(url_for('view_invoice', invoice_id=inv['id']))
    s = load_settings()
    from datetime import timedelta
    default_due = (date.today() + timedelta(days=s['default_due_days'])).isoformat()
    return render_template('invoice_form.html', clients=clients, invoice=None,
                           today=date.today().isoformat(), products=load_json(PRODUCTS_FILE),
                           settings=s, default_due=default_due)

@app.route('/invoices/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    invoices = check_overdue(load_json(INVOICES_FILE))
    save_json(INVOICES_FILE, invoices)
    invoice = next((i for i in invoices if i['id'] == invoice_id), None)
    if not invoice:
        return redirect(url_for('index'))
    clients = load_json(CLIENTS_FILE)
    client = next((c for c in clients if c['id'] == invoice['client_id']), {})
    overdue_days = 0
    if invoice['status'] == 'overdue' and invoice.get('due_date'):
        delta = date.today() - date.fromisoformat(invoice['due_date'])
        overdue_days = delta.days
    return render_template('invoice_view.html', invoice=invoice, client=client, overdue_days=overdue_days)

@app.route('/invoices/edit/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
def edit_invoice(invoice_id):
    invoices = load_json(INVOICES_FILE)
    invoice = next((i for i in invoices if i['id'] == invoice_id), None)
    clients = load_json(CLIENTS_FILE)
    if not invoice:
        return redirect(url_for('index'))
    if request.method == 'POST':
        items = []
        names = request.form.getlist('item_name')
        qtys = request.form.getlist('item_qty')
        prices = request.form.getlist('item_price')
        for n, q, p in zip(names, qtys, prices):
            if n.strip():
                qty = float(q or 0)
                price = float(p or 0)
                items.append({'name': n, 'qty': qty, 'price': price, 'subtotal': qty * price})
        subtotal = sum(i['subtotal'] for i in items)
        tax_rate = float(request.form.get('tax_rate', 0))
        tax_amount = subtotal * tax_rate / 100
        invoice.update({
            'client_id': int(request.form['client_id']),
            'issue_date': request.form['issue_date'],
            'due_date': request.form['due_date'],
            'items': items,
            'subtotal': subtotal,
            'tax_rate': tax_rate,
            'tax_amount': tax_amount,
            'total': subtotal + tax_amount,
            'notes': request.form.get('notes', ''),
        })
        save_json(INVOICES_FILE, invoices)
        return redirect(url_for('view_invoice', invoice_id=invoice_id))
    return render_template('invoice_form.html', clients=clients, invoice=invoice,
                           today=date.today().isoformat(), products=load_json(PRODUCTS_FILE),
                           settings=load_settings(), default_due=invoice.get('due_date',''))

@app.route('/invoices/<int:invoice_id>/status/<status>', methods=['POST'])
@login_required
def update_status(invoice_id, status):
    invoices = load_json(INVOICES_FILE)
    for inv in invoices:
        if inv['id'] == invoice_id:
            inv['status'] = status
            if status == 'paid':
                inv['paid_at'] = datetime.now().isoformat()
    save_json(INVOICES_FILE, invoices)
    return redirect(url_for('view_invoice', invoice_id=invoice_id))

@app.route('/invoices/delete/<int:invoice_id>', methods=['POST'])
@login_required
def delete_invoice(invoice_id):
    invoices = [i for i in load_json(INVOICES_FILE) if i['id'] != invoice_id]
    save_json(INVOICES_FILE, invoices)
    return redirect(url_for('index'))

def _build_pdf(invoice, client, s=None):
    if s is None:
        s = load_settings()

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.platypus import Image as RLImage

    # Register DejaVu fonts for full Czech character support
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.platypus import Image as RLImage
    FONT_DIR = os.path.dirname(os.path.abspath(__file__))
    try:
        pdfmetrics.registerFont(TTFont('DejaVu',         os.path.join(FONT_DIR, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold',    os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf')))
        pdfmetrics.registerFont(TTFont('DejaVu-Oblique', os.path.join(FONT_DIR, 'DejaVuSans-Oblique.ttf')))
        registerFontFamily('DejaVu', normal='DejaVu', bold='DejaVu-Bold', italic='DejaVu-Oblique')
        FONT      = 'DejaVu'
        FONT_BOLD = 'DejaVu-Bold'
    except Exception:
        FONT      = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'

    # Generate QR code for payment if IBAN is set
    qr_image = None
    if s.get('iban'):
        try:
            from qr_generator import generate_qr_png
            spd = (f"SPD*1.0*ACC:{s['iban']}*"
                   f"AM:{invoice['total']:.2f}*CC:CZK*"
                   f"MSG:{invoice['invoice_number']}*")
            qr_png = generate_qr_png(spd, box_size=4, border=2)
            qr_buf = io.BytesIO(qr_png)
            qr_image = RLImage(qr_buf, width=1.2*inch, height=1.2*inch)
        except Exception:
            qr_image = None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            rightMargin=0.8*inch, leftMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)

    BLACK      = colors.black
    WHITE      = colors.white
    DARK_GREY  = colors.HexColor('#1A1A1A')
    MID_GREY   = colors.HexColor('#555555')
    LIGHT_GREY = colors.HexColor('#EEEEEE')
    RULE_GREY  = colors.HexColor('#BBBBBB')

    story = []

    # ── HEADER ────────────────────────────────────────────────────────────────
    company_s = ParagraphStyle('co', fontSize=20, textColor=BLACK,
                                fontName=FONT_BOLD, leading=24)
    tag_s     = ParagraphStyle('tag', fontSize=9, textColor=MID_GREY,
                                fontName=FONT, leading=13)
    inv_s     = ParagraphStyle('inv', fontSize=9, textColor=MID_GREY,
                                fontName=FONT, alignment=TA_RIGHT)
    inv_b     = ParagraphStyle('invb', fontSize=12, textColor=BLACK,
                                fontName=FONT_BOLD, alignment=TA_RIGHT)

    header_table = Table([
        [Paragraph(s.get('company_name', 'Šefl s.r.o.'), company_s),
         Paragraph('FAKTURA', ParagraphStyle('ft', fontSize=9, textColor=MID_GREY, alignment=TA_RIGHT, fontName=FONT_BOLD))],
        [Paragraph(s.get('company_subtitle', ''), tag_s),
         Paragraph(f"<b>{invoice['invoice_number']}</b>", inv_b)],
        ['', Paragraph(f"Vystaveno: {invoice['issue_date']}", inv_s)],
        ['', Paragraph(f"Splatnost: {invoice['due_date']}", inv_s)],
    ], colWidths=[4*inch, 3*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.1*inch))
    story.append(HRFlowable(width="100%", thickness=2, color=BLACK))
    story.append(Spacer(1, 0.05*inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_GREY))
    story.append(Spacer(1, 0.2*inch))

    # ── STATUS ────────────────────────────────────────────────────────────────
    status_labels = {
        'paid':    'ZAPLACENO',
        'unpaid':  'NEZAPLACENO',
        'overdue': 'PO SPLATNOSTI',
    }
    st_label = status_labels.get(invoice['status'], invoice['status'].upper())
    st_style = ParagraphStyle('st', fontSize=9, textColor=BLACK,
                               fontName=FONT_BOLD, alignment=TA_RIGHT)
    story.append(Paragraph(f"Stav: {st_label}", st_style))
    story.append(Spacer(1, 0.2*inch))

    # ── BILL TO / SUPPLIER ────────────────────────────────────────────────────
    lbl_s = ParagraphStyle('lbl', fontSize=7.5, textColor=MID_GREY,
                            fontName=FONT_BOLD, leading=12, spaceAfter=3)
    val_s = ParagraphStyle('val', fontSize=10, textColor=BLACK,
                            fontName=FONT_BOLD, leading=14)
    sub_s = ParagraphStyle('sub', fontSize=9, textColor=DARK_GREY, fontName=FONT, leading=13)

    left_col = [Paragraph('ODBĚRATEL', lbl_s),
                Paragraph(client.get('name', ''), val_s)]
    if client.get('company'):
        left_col.append(Paragraph(client['company'], sub_s))
    if client.get('email'):
        left_col.append(Paragraph(client['email'], sub_s))
    if client.get('phone'):
        left_col.append(Paragraph(client['phone'], sub_s))
    if client.get('address'):
        left_col.append(Paragraph(client['address'], sub_s))

    right_col = [Paragraph('DODAVATEL', lbl_s),
                 Paragraph(s.get('company_name', ''), val_s)]
    if s.get('company_subtitle'):
        right_col.append(Paragraph(s['company_subtitle'], sub_s))
    if s.get('address'):
        right_col.append(Paragraph(s['address'], sub_s))
    if s.get('ico'):
        right_col.append(Paragraph(f"IČO: {s['ico']}", sub_s))
    if s.get('dic'):
        right_col.append(Paragraph(f"DIČ: {s['dic']}", sub_s))
    if s.get('email'):
        right_col.append(Paragraph(s['email'], sub_s))
    if s.get('phone'):
        right_col.append(Paragraph(s['phone'], sub_s))

    info_table = Table(
        [[left_col, right_col]],
        colWidths=[3.75*inch, 3.25*inch]
    )
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOX', (0,0), (0,0), 0.5, RULE_GREY),
        ('BOX', (1,0), (1,0), 0.5, RULE_GREY),
        ('BACKGROUND', (0,0), (0,0), LIGHT_GREY),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.3*inch))

    # ── ITEMS TABLE ───────────────────────────────────────────────────────────
    th_l = ParagraphStyle('thl', fontSize=8.5, textColor=WHITE, fontName=FONT_BOLD)
    th_r = ParagraphStyle('thr', fontSize=8.5, textColor=WHITE, fontName=FONT_BOLD, alignment=TA_RIGHT)
    td_l = ParagraphStyle('tdl', fontSize=9,   textColor=DARK_GREY, fontName=FONT)
    td_r = ParagraphStyle('tdr', fontSize=9,   textColor=DARK_GREY, fontName=FONT, alignment=TA_RIGHT)
    sm_r = ParagraphStyle('smr', fontSize=9,   textColor=MID_GREY,  fontName=FONT, alignment=TA_RIGHT)
    tot_l = ParagraphStyle('totl', fontSize=10, textColor=BLACK, fontName=FONT_BOLD, alignment=TA_RIGHT)
    tot_r = ParagraphStyle('totr', fontSize=10, textColor=BLACK, fontName=FONT_BOLD, alignment=TA_RIGHT)

    item_data = [[
        Paragraph('Popis', th_l),
        Paragraph('Množství', th_r),
        Paragraph('Jedn. cena', th_r),
        Paragraph('Celkem', th_r),
    ]]
    for it in invoice['items']:
        item_data.append([
            Paragraph(it['name'], td_l),
            Paragraph(str(it['qty']), td_r),
            Paragraph(f"{it['price']:.2f} Kč", td_r),
            Paragraph(f"{it['subtotal']:.2f} Kč", td_r),
        ])

    n = len(invoice['items'])
    item_data.append(['', '',
        Paragraph('Mezisoučet', sm_r),
        Paragraph(f"{invoice['subtotal']:.2f} Kč", td_r)])
    item_data.append(['', '',
        Paragraph(f"DPH ({invoice['tax_rate']}%)", sm_r),
        Paragraph(f"{invoice['tax_amount']:.2f} Kč", td_r)])
    item_data.append(['', '',
        Paragraph('CELKEM K ÚHRADĚ', tot_l),
        Paragraph(f"{invoice['total']:.2f} Kč", tot_r)])

    row_styles = []
    for i in range(1, n+1):
        if i % 2 == 0:
            row_styles.append(('BACKGROUND', (0,i), (-1,i), LIGHT_GREY))

    it_table = Table(item_data, colWidths=[3.3*inch, 0.9*inch, 1.3*inch, 1.25*inch])
    it_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLACK),
        ('LINEBELOW', (0,0), (-1,0), 1, BLACK),
        ('LINEBELOW', (0, n), (-1, n), 0.5, RULE_GREY),
        ('LINEABOVE', (0, n+3), (-1, n+3), 1.5, BLACK),
        ('LINEBELOW', (0, n+3), (-1, n+3), 1.5, BLACK),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOX', (0,0), (-1, n), 0.5, RULE_GREY),
        *row_styles,
    ]))
    story.append(it_table)

    # ── OVERDUE WARNING ───────────────────────────────────────────────────────
    if invoice['status'] == 'overdue':
        story.append(Spacer(1, 0.2*inch))
        delta = (date.today() - date.fromisoformat(invoice['due_date'])).days
        days_str = 'den' if delta == 1 else ('dny' if delta < 5 else 'dní')
        warn_s = ParagraphStyle('warn', fontSize=9.5, textColor=BLACK,
                                fontName=FONT_BOLD, borderColor=BLACK,
                                borderWidth=1, borderPadding=10,
                                backColor=LIGHT_GREY)
        story.append(Paragraph(
            f"! UPOZORNĚNÍ: Tato faktura je {delta} {days_str} po splatnosti. "
            f"Žádáme o neprodlené uhrazení.", warn_s))

    # ── PLATEBNÍ ÚDAJE + QR ───────────────────────────────────────────────────
    if s.get('bank_account') or s.get('iban') or qr_image:
        story.append(Spacer(1, 0.25*inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_GREY))
        story.append(Spacer(1, 0.12*inch))

        pay_lbl = ParagraphStyle('plbl', fontSize=7.5, textColor=MID_GREY,
                                  fontName=FONT_BOLD, leading=12, spaceAfter=4)
        pay_val = ParagraphStyle('pval', fontSize=9, textColor=BLACK,
                                  fontName=FONT_BOLD, leading=13)
        pay_sub = ParagraphStyle('psub', fontSize=8.5, textColor=DARK_GREY,
                                  fontName=FONT, leading=13)

        pay_left = [Paragraph('PLATEBNÍ ÚDAJE', pay_lbl)]
        if s.get('bank_account'):
            pay_left.append(Paragraph(f"Číslo účtu: {s['bank_account']}", pay_val))
        if s.get('iban'):
            pay_left.append(Paragraph(f"IBAN: {s['iban']}", pay_sub))
        pay_left.append(Paragraph(f"Částka: {invoice['total']:.2f} Kč", pay_val))
        pay_left.append(Paragraph(f"VS: {invoice['invoice_number']}", pay_sub))

        if qr_image:
            qr_label = ParagraphStyle('qrl', fontSize=7, textColor=MID_GREY,
                                       fontName=FONT, alignment=TA_CENTER)
            pay_right = [[qr_image], [Paragraph('Naskenujte pro platbu', qr_label)]]
            pay_right_cell = pay_right
        else:
            pay_right_cell = [[Paragraph('', pay_sub)]]

        pay_table = Table(
            [[pay_left, pay_right_cell]],
            colWidths=[4.8*inch, 2.2*inch]
        )
        pay_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('ALIGN', (1,0), (1,0), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ]))
        story.append(pay_table)

    # ── NOTES ─────────────────────────────────────────────────────────────────
    if invoice.get('notes'):
        story.append(Spacer(1, 0.2*inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_GREY))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph('Poznámky', ParagraphStyle('nl', fontSize=8,
            textColor=MID_GREY, fontName=FONT_BOLD, spaceAfter=4)))
        story.append(Paragraph(invoice['notes'], ParagraphStyle('nb', fontSize=9, textColor=DARK_GREY, fontName=FONT)))

    # ── FOOTER ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.4*inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_GREY))
    story.append(Spacer(1, 0.08*inch))
    story.append(HRFlowable(width="100%", thickness=1.5, color=BLACK))
    story.append(Spacer(1, 0.1*inch))
    company_line = s.get('company_name', '')
    if s.get('company_subtitle'):
        company_line += f"  ·  {s['company_subtitle']}"
    if s.get('ico'):
        company_line += f"  ·  IČO: {s['ico']}"
    story.append(Paragraph(
        f"{company_line}  ·  Děkujeme za Vaši důvěru",
        ParagraphStyle('footer', fontSize=8, textColor=MID_GREY, fontName=FONT, alignment=TA_CENTER)))

    doc.build(story)
    buf.seek(0)
    filename = f"{invoice['invoice_number']}.pdf"
    buf.seek(0)
    return buf.getvalue()


@app.route("/invoices/<int:invoice_id>/pdf")
@login_required
def download_pdf(invoice_id):
    invoices = load_json(INVOICES_FILE)
    invoice = next((i for i in invoices if i["id"] == invoice_id), None)
    if not invoice:
        return redirect(url_for("index"))
    clients = load_json(CLIENTS_FILE)
    client = next((c for c in clients if c["id"] == invoice["client_id"]), {})
    pdf = _build_pdf(invoice=invoice, client=client, s=load_settings())
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=f"{invoice['invoice_number']}.pdf",
                     mimetype="application/pdf")


@app.route("/invoices/<int:invoice_id>/send", methods=["POST"])
@login_required
def send_invoice(invoice_id):
    invoices = load_json(INVOICES_FILE)
    invoice = next((i for i in invoices if i["id"] == invoice_id), None)
    if not invoice:
        return redirect(url_for("index"))
    clients = load_json(CLIENTS_FILE)
    client = next((c for c in clients if c["id"] == invoice["client_id"]), {})
    s = load_settings()
    try:
        pdf = _build_pdf(invoice=invoice, client=client, s=s)
    except Exception as e:
        flash(f"Chyba pri generovani PDF: {e}", "error")
        return redirect(url_for("view_invoice", invoice_id=invoice_id))
    ok, err = send_invoice_email(invoice, client, pdf)
    if ok:
        for inv in invoices:
            if inv["id"] == invoice_id:
                inv["last_sent"] = datetime.now().isoformat()
        save_json(INVOICES_FILE, invoices)
        flash(f"Faktura odeslana na {client.get('email', '')}", "success")
    else:
        flash(f"Nepodarilo se odeslat: {err}", "error")
    return redirect(url_for("view_invoice", invoice_id=invoice_id))


@app.route("/reminders/send", methods=["POST"])
@login_required
def send_reminders_route():
    results = send_reminders()
    if not results:
        flash("Zadne faktury k upominani nebo SMTP neni nastaveno.", "warning")
    else:
        ok_count = sum(1 for _, _, ok, _ in results if ok)
        err_count = len(results) - ok_count
        if ok_count:
            flash(f"Odeslano {ok_count} upominek.", "success")
        if err_count:
            errs = "; ".join(f"{inv}: {e}" for inv, _, ok, e in results if not ok)
            flash(f"Chyby ({err_count}): {errs}", "error")
    return redirect(url_for("index"))


app.secret_key = os.environ.get('SECRET_KEY', 'invoice-app-secret-change-in-production')

# ── Export / Import ───────────────────────────────────────────────────────────

@app.route('/export/invoices/csv')
@login_required
def export_invoices_csv():
    invoices = load_json(INVOICES_FILE)
    clients = load_json(CLIENTS_FILE)
    client_map = {c['id']: c['name'] for c in clients}

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow([
        'Číslo faktury', 'Klient', 'Datum vystavení', 'Datum splatnosti',
        'Stav', 'Mezisoučet (Kč)', 'DPH (%)', 'DPH (Kč)', 'Celkem (Kč)', 'Poznámky'
    ])
    for inv in sorted(invoices, key=lambda x: x.get('issue_date', ''), reverse=True):
        status_map = {'paid': 'zaplaceno', 'unpaid': 'nezaplaceno', 'overdue': 'po splatnosti'}
        writer.writerow([
            inv.get('invoice_number', ''),
            client_map.get(inv.get('client_id'), '—'),
            inv.get('issue_date', ''),
            inv.get('due_date', ''),
            status_map.get(inv.get('status', ''), inv.get('status', '')),
            f"{inv.get('subtotal', 0):.2f}".replace('.', ','),
            f"{inv.get('tax_rate', 0):.2f}".replace('.', ','),
            f"{inv.get('tax_amount', 0):.2f}".replace('.', ','),
            f"{inv.get('total', 0):.2f}".replace('.', ','),
            inv.get('notes', ''),
        ])

    buf.seek(0)
    # Add BOM for Excel UTF-8 detection
    output = io.BytesIO()
    output.write(b'\xef\xbb\xbf')
    output.write(buf.getvalue().encode('utf-8'))
    output.seek(0)
    filename = f"faktury_export_{date.today().isoformat()}.csv"
    return send_file(output, as_attachment=True, download_name=filename, mimetype='text/csv; charset=utf-8')


@app.route('/export/clients/csv')
@login_required
def export_clients_csv():
    clients = load_json(CLIENTS_FILE)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['Jméno', 'Firma', 'E-mail', 'Telefon', 'Adresa'])
    for c in clients:
        writer.writerow([c.get('name',''), c.get('company',''), c.get('email',''), c.get('phone',''), c.get('address','')])
    buf.seek(0)
    output = io.BytesIO()
    output.write(b'\xef\xbb\xbf')
    output.write(buf.getvalue().encode('utf-8'))
    output.seek(0)
    filename = f"klienti_export_{date.today().isoformat()}.csv"
    return send_file(output, as_attachment=True, download_name=filename, mimetype='text/csv; charset=utf-8')


@app.route('/import/clients/csv', methods=['POST'])
@login_required
def import_clients_csv():
    file = request.files.get('csv_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Prosím nahrajte soubor CSV.', 'error')
        return redirect(url_for('clients'))

    clients = load_json(CLIENTS_FILE)
    existing_emails = {c['email'].lower() for c in clients}
    added = 0
    skipped = 0

    try:
        raw = file.read()
        # Strip BOM if present
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        content = raw.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content), delimiter=';')
        for row in reader:
            email = (row.get('E-mail') or row.get('email') or '').strip().lower()
            name = (row.get('Jméno') or row.get('jmeno') or row.get('name') or '').strip()
            if not name:
                skipped += 1
                continue
            if email and email in existing_emails:
                skipped += 1
                continue
            clients.append({
                'id': get_next_id(clients),
                'name': name,
                'email': email,
                'phone': (row.get('Telefon') or row.get('phone') or '').strip(),
                'company': (row.get('Firma') or row.get('company') or '').strip(),
                'address': (row.get('Adresa') or row.get('address') or '').strip(),
            })
            existing_emails.add(email)
            added += 1
        save_json(CLIENTS_FILE, clients)
        flash(f'Import dokončen: {added} přidáno, {skipped} přeskočeno.', 'success')
    except Exception as e:
        flash(f'Chyba při importu: {str(e)}', 'error')

    return redirect(url_for('clients'))


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    app.run(debug=True, port=5000)
