from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, session
import sqlite3
from functools import wraps
import os
import csv
import io
import urllib.request
import base64
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

# ── SQLite database layer (replaces all JSON file functions) ──────────────────
from database import (
    init_db, backup_db,
    get_all_clients, get_client, create_client, update_client, delete_client,
    get_all_products, get_product, create_product, update_product, delete_product,
    get_all_invoices, get_invoice, create_invoice, update_invoice, delete_invoice,
    update_invoice_status, get_invoice_stats, get_next_invoice_number,
    mark_invoice_sent, get_overdue_for_reminders, mark_invoice_reminded,
    load_settings, save_settings,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'invoice-app-secret-change-in-production')

# Initialise DB tables on startup
with app.app_context():
    init_db()

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == os.environ.get('APP_PASSWORD', 'admin'):
            session['logged_in'] = True
            return redirect(request.form.get('next') or url_for('index'))
        error = 'Špatné heslo.'
    return render_template('login.html', error=error, next=request.args.get('next', ''))

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Email (Resend API) ────────────────────────────────────────────────────────

def _build_email_body(invoice, client, s):
    company = s.get('company_name', 'Fakturace')
    body = f"Dobry den,\n\nv priloze zasilame fakturu c. {invoice['invoice_number']} na castku {invoice['total']:.2f} Kc.\nDatum splatnosti: {invoice['due_date'][8:10]}/{invoice['due_date'][5:7]}/{invoice['due_date'][0:4]}.\n"
    if s.get('bank_account'):
        body += f"Cislo uctu: {s['bank_account']}\n"
    if s.get('iban'):
        body += f"IBAN: {s['iban']}\n"
    body += f"\nDekujeme za Vasi duveru.\n\nS pozdravem,\n{company}"
    if s.get('phone'):
        body += f"\nTel: {s['phone']}"
    if s.get('email'):
        body += f"\nEmail: {s['email']}"
    return body


def _send_via_resend(invoice, client, pdf_bytes, subject, body, s):
    """Send via Resend.com API."""
    api_key   = s.get('resend_api_key', '')
    company   = s.get('company_name', 'Fakturace')
    from_addr = f"{company} <onboarding@resend.dev>"

    import json as _json
    payload = _json.dumps({
        'from': from_addr,
        'to':   [client['email']],
        'subject': subject,
        'text': body,
        'attachments': [{'filename': f"{invoice['invoice_number']}.pdf",
                         'content': base64.b64encode(pdf_bytes).decode('utf-8')}],
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.resend.com/emails', data=payload,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, None
    except urllib.error.HTTPError as e:
        return False, f'Resend chyba {e.code}: {e.read().decode("utf-8", errors="ignore")[:200]}'
    except Exception as e:
        return False, f'Chyba: {str(e)}'


def _send_via_brevo(invoice, client, pdf_bytes, subject, body, s):
    """Send via Brevo (sendinblue) API v3."""
    api_key     = s.get('brevo_api_key', '')
    company     = s.get('company_name', 'Fakturace')
    sender_name = company
    sender_email = s.get('email') or 'noreply@example.com'

    import json as _json
    payload = _json.dumps({
        'sender':  {'name': sender_name, 'email': sender_email},
        'to':      [{'email': client['email'], 'name': client.get('name', '')}],
        'subject': subject,
        'textContent': body,
        'attachment': [{
            'name':    f"{invoice['invoice_number']}.pdf",
            'content': base64.b64encode(pdf_bytes).decode('utf-8'),
        }],
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.brevo.com/v3/smtp/email', data=payload,
        headers={
            'api-key':      api_key,
            'Content-Type': 'application/json',
            'Accept':       'application/json',
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, None
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8', errors='ignore')[:200]
        return False, f'Brevo chyba {e.code}: {err}'
    except Exception as e:
        return False, f'Chyba: {str(e)}'


def send_invoice_email(invoice, client, pdf_bytes, subject=None, body=None):
    s        = load_settings()
    provider = s.get('email_provider', 'resend')

    if not client.get('email'):
        return False, 'Klient nema vyplneny e-mail.'

    company = s.get('company_name', 'Fakturace')
    subject = subject or f"Faktura {invoice['invoice_number']} od {company}"
    body    = body    or _build_email_body(invoice, client, s)

    if provider == 'brevo':
        if not s.get('brevo_api_key'):
            return False, 'Brevo API klic neni nastaven. Vyplnte ho v Nastaveni.'
        return _send_via_brevo(invoice, client, pdf_bytes, subject, body, s)
    else:
        if not s.get('resend_api_key'):
            return False, 'Resend API klic neni nastaven. Vyplnte ho v Nastaveni.'
        return _send_via_resend(invoice, client, pdf_bytes, subject, body, s)



def send_reminders():
    s = load_settings()
    if not s.get('resend_api_key'):
        return []

    reminder_days = int(s.get('reminder_days', 3))
    eligible = get_overdue_for_reminders(reminder_days)
    results = []
    today = date.today()

    for inv in eligible:
        # get full invoice with items for PDF
        full_inv = get_invoice(inv['id'])
        client = get_client(inv['client_id'])
        try:
            pdf_bytes = _build_pdf(full_inv, client, s)
        except Exception:
            results.append((inv['invoice_number'], inv.get('client_email', ''), False, 'Chyba pri generovani PDF'))
            continue

        due = date.fromisoformat(inv['due_date'])
        days_late = (today - due).days
        days_str  = 'den' if days_late == 1 else ('dny' if days_late < 5 else 'dni')
        company   = s.get('company_name', 'Fakturace')
        subject   = f"Upominka: faktura {inv['invoice_number']} je {days_late} {days_str} po splatnosti"
        body      = f"Dobry den,\n\nfaktura c. {inv['invoice_number']} na castku {inv['total']:.2f} Kc je {days_late} {days_str} po splatnosti ({inv['due_date']}).\n\nProsite o neprodlene uhrazeni.\n"
        if s.get('bank_account'):
            body += f"Cislo uctu: {s['bank_account']}\n"
        if s.get('iban'):
            body += f"IBAN: {s['iban']}\n"
        body += f"\nS pozdravem,\n{company}"

        ok, err = send_invoice_email(full_inv, client, pdf_bytes, subject=subject, body=body)
        results.append((inv['invoice_number'], inv.get('client_email', ''), ok, err))
        if ok:
            mark_invoice_reminded(inv['id'])

    return results

# ── PDF ───────────────────────────────────────────────────────────────────────

def _build_pdf(invoice, client, s=None):
    if s is None:
        s = load_settings()

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
        FONT = 'DejaVu'; FONT_BOLD = 'DejaVu-Bold'
    except Exception:
        FONT = 'Helvetica'; FONT_BOLD = 'Helvetica-Bold'

    qr_image = None
    if s.get('iban'):
        try:
            from qr_generator import generate_qr_png
            spd = f"SPD*1.0*ACC:{s['iban']}*AM:{invoice['total']:.2f}*CC:CZK*MSG:{invoice['invoice_number']}*"
            qr_image = RLImage(io.BytesIO(generate_qr_png(spd, box_size=4, border=2)), width=1.2*inch, height=1.2*inch)
        except Exception:
            qr_image = None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            rightMargin=0.8*inch, leftMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)

    BLACK = colors.black; WHITE = colors.white
    DARK_GREY = colors.HexColor('#1A1A1A'); MID_GREY = colors.HexColor('#555555')
    LIGHT_GREY = colors.HexColor('#EEEEEE'); RULE_GREY = colors.HexColor('#BBBBBB')

    story = []

    company_s = ParagraphStyle('co', fontSize=20, textColor=BLACK, fontName=FONT_BOLD, leading=24)
    tag_s     = ParagraphStyle('tag', fontSize=9, textColor=MID_GREY, fontName=FONT, leading=13)
    inv_s     = ParagraphStyle('inv', fontSize=9, textColor=MID_GREY, fontName=FONT, alignment=TA_RIGHT)
    inv_b     = ParagraphStyle('invb', fontSize=12, textColor=BLACK, fontName=FONT_BOLD, alignment=TA_RIGHT)

    header_table = Table([
        [Paragraph(s.get('company_name', 'Šefl s.r.o.'), company_s),
         Paragraph('FAKTURA', ParagraphStyle('ft', fontSize=9, textColor=MID_GREY, alignment=TA_RIGHT, fontName=FONT_BOLD))],
        [Paragraph(s.get('company_subtitle', ''), tag_s),
         Paragraph(f"<b>{invoice['invoice_number']}</b>", inv_b)],
        ['', Paragraph(f"Vystaveno: {invoice['issue_date'][8:10]}/{invoice['issue_date'][5:7]}/{invoice['issue_date'][0:4]}", inv_s)],
        ['', Paragraph(f"Splatnost: {invoice['due_date'][8:10]}/{invoice['due_date'][5:7]}/{invoice['due_date'][0:4]}", inv_s)],
    ], colWidths=[4*inch, 3*inch])
    header_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    story += [header_table, Spacer(1,0.1*inch), HRFlowable(width="100%",thickness=2,color=BLACK),
              Spacer(1,0.05*inch), HRFlowable(width="100%",thickness=0.5,color=RULE_GREY), Spacer(1,0.2*inch)]

    st_label = {'paid':'ZAPLACENO','unpaid':'NEZAPLACENO','overdue':'PO SPLATNOSTI'}.get(invoice['status'], invoice['status'].upper())
    story.append(Paragraph(f"Stav: {st_label}", ParagraphStyle('st',fontSize=9,textColor=BLACK,fontName=FONT_BOLD,alignment=TA_RIGHT)))
    story.append(Spacer(1,0.2*inch))

    lbl_s = ParagraphStyle('lbl', fontSize=7.5, textColor=MID_GREY, fontName=FONT_BOLD, leading=12, spaceAfter=3)
    val_s = ParagraphStyle('val', fontSize=10, textColor=BLACK, fontName=FONT_BOLD, leading=14)
    sub_s = ParagraphStyle('sub', fontSize=9, textColor=DARK_GREY, fontName=FONT, leading=13)

    left_col = [Paragraph('ODBĚRATEL', lbl_s), Paragraph(client.get('name',''), val_s)]
    for field in ['company','email','phone','address']:
        if client.get(field): left_col.append(Paragraph(client[field], sub_s))

    right_col = [Paragraph('DODAVATEL', lbl_s), Paragraph(s.get('company_name',''), val_s)]
    if s.get('company_subtitle'): right_col.append(Paragraph(s['company_subtitle'], sub_s))
    if s.get('address'):          right_col.append(Paragraph(s['address'], sub_s))
    if s.get('ico'):              right_col.append(Paragraph(f"IČO: {s['ico']}", sub_s))
    if s.get('dic'):              right_col.append(Paragraph(f"DIČ: {s['dic']}", sub_s))
    if s.get('email'):            right_col.append(Paragraph(s['email'], sub_s))
    if s.get('phone'):            right_col.append(Paragraph(s['phone'], sub_s))

    info_table = Table([[left_col, right_col]], colWidths=[3.75*inch, 3.25*inch])
    info_table.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(0,0),0.5,RULE_GREY),('BOX',(1,0),(1,0),0.5,RULE_GREY),
        ('BACKGROUND',(0,0),(0,0),LIGHT_GREY),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
    ]))
    story += [info_table, Spacer(1,0.3*inch)]

    th_l = ParagraphStyle('thl',fontSize=8.5,textColor=WHITE,fontName=FONT_BOLD)
    th_r = ParagraphStyle('thr',fontSize=8.5,textColor=WHITE,fontName=FONT_BOLD,alignment=TA_RIGHT)
    td_l = ParagraphStyle('tdl',fontSize=9,textColor=DARK_GREY,fontName=FONT)
    td_r = ParagraphStyle('tdr',fontSize=9,textColor=DARK_GREY,fontName=FONT,alignment=TA_RIGHT)
    sm_r = ParagraphStyle('smr',fontSize=9,textColor=MID_GREY,fontName=FONT,alignment=TA_RIGHT)
    tot_l = ParagraphStyle('totl',fontSize=10,textColor=BLACK,fontName=FONT_BOLD,alignment=TA_RIGHT)
    tot_r = ParagraphStyle('totr',fontSize=10,textColor=BLACK,fontName=FONT_BOLD,alignment=TA_RIGHT)

    item_data = [[Paragraph('Popis',th_l),Paragraph('Množství',th_r),Paragraph('Jedn. cena',th_r),Paragraph('Celkem',th_r)]]
    for it in invoice['items']:
        item_data.append([Paragraph(it['name'],td_l),Paragraph(str(it['qty']),td_r),
                          Paragraph(f"{it['price']:.2f} Kč",td_r),Paragraph(f"{it['subtotal']:.2f} Kč",td_r)])
    n = len(invoice['items'])
    item_data += [
        ['','',Paragraph('Mezisoučet',sm_r),Paragraph(f"{invoice['subtotal']:.2f} Kč",td_r)],
        ['','',Paragraph(f"DPH ({invoice['tax_rate']}%)",sm_r),Paragraph(f"{invoice['tax_amount']:.2f} Kč",td_r)],
        ['','',Paragraph('CELKEM K ÚHRADĚ',tot_l),Paragraph(f"{invoice['total']:.2f} Kč",tot_r)],
    ]
    row_styles = [('BACKGROUND',(0,i),(-1,i),LIGHT_GREY) for i in range(1,n+1) if i%2==0]
    it_table = Table(item_data, colWidths=[3.3*inch,0.9*inch,1.3*inch,1.25*inch])
    it_table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),BLACK),('LINEBELOW',(0,0),(-1,0),1,BLACK),
        ('LINEBELOW',(0,n),(-1,n),0.5,RULE_GREY),('LINEABOVE',(0,n+3),(-1,n+3),1.5,BLACK),
        ('LINEBELOW',(0,n+3),(-1,n+3),1.5,BLACK),('TOPPADDING',(0,0),(-1,-1),7),
        ('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),8),
        ('RIGHTPADDING',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('BOX',(0,0),(-1,n),0.5,RULE_GREY), *row_styles,
    ]))
    story.append(it_table)

    if invoice['status'] == 'overdue':
        story.append(Spacer(1,0.2*inch))
        delta = (date.today() - date.fromisoformat(invoice['due_date'])).days
        days_str = 'den' if delta==1 else ('dny' if delta<5 else 'dní')
        story.append(Paragraph(
            f"! UPOZORNĚNÍ: Tato faktura je {delta} {days_str} po splatnosti. Žádáme o neprodlené uhrazení.",
            ParagraphStyle('warn',fontSize=9.5,textColor=BLACK,fontName=FONT_BOLD,
                           borderColor=BLACK,borderWidth=1,borderPadding=10,backColor=LIGHT_GREY)))

    if s.get('bank_account') or s.get('iban') or qr_image:
        story += [Spacer(1,0.25*inch), HRFlowable(width="100%",thickness=0.5,color=RULE_GREY), Spacer(1,0.12*inch)]
        pay_lbl = ParagraphStyle('plbl',fontSize=7.5,textColor=MID_GREY,fontName=FONT_BOLD,leading=12,spaceAfter=4)
        pay_val = ParagraphStyle('pval',fontSize=9,textColor=BLACK,fontName=FONT_BOLD,leading=13)
        pay_sub = ParagraphStyle('psub',fontSize=8.5,textColor=DARK_GREY,fontName=FONT,leading=13)
        pay_left = [Paragraph('PLATEBNÍ ÚDAJE', pay_lbl)]
        if s.get('bank_account'): pay_left.append(Paragraph(f"Číslo účtu: {s['bank_account']}", pay_val))
        if s.get('iban'):         pay_left.append(Paragraph(f"IBAN: {s['iban']}", pay_sub))
        pay_left += [Paragraph(f"Částka: {invoice['total']:.2f} Kč", pay_val),
                     Paragraph(f"VS: {invoice['invoice_number']}", pay_sub)]
        if qr_image:
            qr_label = ParagraphStyle('qrl',fontSize=7,textColor=MID_GREY,fontName=FONT,alignment=TA_CENTER)
            pay_right_cell = [[qr_image],[Paragraph('Naskenujte pro platbu',qr_label)]]
        else:
            pay_right_cell = [[Paragraph('',pay_sub)]]
        pay_table = Table([[pay_left, pay_right_cell]], colWidths=[4.8*inch,2.2*inch])
        pay_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(1,0),(1,0),'CENTER'),
            ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
        story.append(pay_table)

    if invoice.get('notes'):
        story += [Spacer(1,0.2*inch), HRFlowable(width="100%",thickness=0.5,color=RULE_GREY), Spacer(1,0.1*inch),
                  Paragraph('Poznámky', ParagraphStyle('nl',fontSize=8,textColor=MID_GREY,fontName=FONT_BOLD,spaceAfter=4)),
                  Paragraph(invoice['notes'], ParagraphStyle('nb',fontSize=9,textColor=DARK_GREY,fontName=FONT))]

    story += [Spacer(1,0.4*inch), HRFlowable(width="100%",thickness=0.5,color=RULE_GREY),
              Spacer(1,0.08*inch), HRFlowable(width="100%",thickness=1.5,color=BLACK), Spacer(1,0.1*inch)]
    company_line = s.get('company_name','')
    if s.get('company_subtitle'): company_line += f"  ·  {s['company_subtitle']}"
    if s.get('ico'):              company_line += f"  ·  IČO: {s['ico']}"
    story.append(Paragraph(f"{company_line}  ·  Děkujeme za Vaši důvěru",
        ParagraphStyle('footer',fontSize=8,textColor=MID_GREY,fontName=FONT,alignment=TA_CENTER)))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    stats      = get_invoice_stats()
    invoices   = get_all_invoices()
    clients    = get_all_clients()
    client_map = {c['id']: c['name'] for c in clients}
    return render_template('index.html', invoices=invoices, client_map=client_map, stats=stats)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        save_settings({
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
            'default_due_days': request.form.get('default_due_days', 14),
            'default_tax_rate': request.form.get('default_tax_rate', 21),
            'email_provider':   request.form.get('email_provider', 'resend'),
            'resend_api_key':   request.form.get('resend_api_key', ''),
            'brevo_api_key':    request.form.get('brevo_api_key', ''),
            'reminder_days':    request.form.get('reminder_days', 3),
        })
        flash('Nastavení bylo uloženo.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', s=load_settings())


# ── Backup & DB info ─────────────────────────────────────────────────────────

@app.route('/backup/download')
@login_required
def backup_download():
    """Download a full SQL dump of the database — safe to run anytime."""
    try:
        sql_bytes = backup_db()
        filename = f"fakturace_backup_{date.today().isoformat()}.sql"
        return send_file(
            io.BytesIO(sql_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype='text/plain'
        )
    except Exception as e:
        flash(f'Chyba při záloze: {str(e)}', 'error')
        return redirect(url_for('settings'))


@app.route('/backup/restore', methods=['POST'])
@login_required
def backup_restore():
    """Restore database from an uploaded .sql dump file."""
    file = request.files.get('backup_file')
    if not file or not file.filename.endswith('.sql'):
        flash('Nahrajte platný .sql soubor ze zálohy.', 'error')
        return redirect(url_for('settings'))
    try:
        import database as db_module
        sql = file.read().decode('utf-8')
        conn = sqlite3.connect(db_module.DB_PATH)
        conn.executescript(sql)
        conn.close()
        flash('Záloha byla úspěšně obnovena.', 'success')
    except Exception as e:
        flash(f'Chyba při obnově zálohy: {str(e)}', 'error')
    return redirect(url_for('settings'))


# ── Products ──────────────────────────────────────────────────────────────────

@app.route('/products')
@login_required
def products():
    return render_template('products.html', products=get_all_products())

@app.route('/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        create_product(
            name=request.form['name'],
            description=request.form.get('description', ''),
            category=request.form.get('category', ''),
            unit=request.form.get('unit', 'ks'),
            price=float(request.form.get('price', 0)),
            tax_rate=float(request.form.get('tax_rate', 21)),
        )
        return redirect(url_for('products'))
    return render_template('product_form.html', product=None)

@app.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = get_product(product_id)
    if not product:
        return redirect(url_for('products'))
    if request.method == 'POST':
        update_product(
            product_id,
            name=request.form['name'],
            description=request.form.get('description', ''),
            category=request.form.get('category', ''),
            unit=request.form.get('unit', 'ks'),
            price=float(request.form.get('price', 0)),
            tax_rate=float(request.form.get('tax_rate', 21)),
        )
        return redirect(url_for('products'))
    return render_template('product_form.html', product=product)

@app.route('/products/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product_route(product_id):
    delete_product(product_id)
    return redirect(url_for('products'))

@app.route('/api/products')
@login_required
def api_products():
    return jsonify(get_all_products(active_only=True))


# ── Clients ───────────────────────────────────────────────────────────────────

@app.route('/clients')
@login_required
def clients():
    return render_template('clients.html', clients=get_all_clients())

@app.route('/clients/add', methods=['GET', 'POST'])
@login_required
def add_client():
    if request.method == 'POST':
        try:
            create_client(
                name=request.form['name'],
                company=request.form.get('company', ''),
                email=request.form.get('email', ''),
                phone=request.form.get('phone', ''),
                address=request.form.get('address', ''),
            )
            return redirect(url_for('clients'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash(f"Klient s e-mailem {request.form.get('email')} již existuje.", 'error')
            else:
                flash(f'Chyba při ukládání: {str(e)}', 'error')
            return render_template('client_form.html', client=request.form)
    return render_template('client_form.html', client=None)

@app.route('/clients/edit/<int:client_id>', methods=['GET', 'POST'])
@login_required
def edit_client(client_id):
    client = get_client(client_id)
    if not client:
        return redirect(url_for('clients'))
    if request.method == 'POST':
        try:
            update_client(
                client_id,
                name=request.form['name'],
                company=request.form.get('company', ''),
                email=request.form.get('email', ''),
                phone=request.form.get('phone', ''),
                address=request.form.get('address', ''),
            )
            return redirect(url_for('clients'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash(f"Jiný klient s e-mailem {request.form.get('email')} již existuje.", 'error')
            else:
                flash(f'Chyba při ukládání: {str(e)}', 'error')
            merged = dict(client); merged.update(request.form)
            return render_template('client_form.html', client=merged)
    return render_template('client_form.html', client=client)

@app.route('/clients/delete/<int:client_id>', methods=['POST'])
@login_required
def delete_client_route(client_id):
    delete_client(client_id)
    return redirect(url_for('clients'))


# ── Invoices ──────────────────────────────────────────────────────────────────

def _parse_items():
    items = []
    for n, q, p in zip(request.form.getlist('item_name'),
                       request.form.getlist('item_qty'),
                       request.form.getlist('item_price')):
        if n.strip():
            items.append({'name': n, 'qty': float(q or 0), 'price': float(p or 0)})
    return items

@app.route('/invoices/new', methods=['GET', 'POST'])
@login_required
def new_invoice():
    all_clients  = get_all_clients()
    all_products = get_all_products(active_only=True)
    if request.method == 'POST':
        s = load_settings()
        inv_num = get_next_invoice_number(s.get('invoice_prefix', 'INV'))
        inv = create_invoice(
            invoice_number=inv_num,
            client_id=int(request.form['client_id']),
            issue_date=request.form['issue_date'],
            due_date=request.form['due_date'],
            items=_parse_items(),
            tax_rate=float(request.form.get('tax_rate', 0)),
            notes=request.form.get('notes', ''),
        )
        return redirect(url_for('view_invoice', invoice_id=inv['id']))
    s = load_settings()
    default_due = (date.today() + timedelta(days=int(s.get('default_due_days', 14)))).isoformat()
    return render_template('invoice_form.html', clients=all_clients, invoice=None,
                           today=date.today().isoformat(), products=all_products,
                           settings=s, default_due=default_due)

@app.route('/invoices/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    invoice = get_invoice(invoice_id)
    if not invoice:
        return redirect(url_for('index'))
    client = get_client(invoice['client_id']) or {}
    overdue_days = 0
    if invoice['status'] == 'overdue' and invoice.get('due_date'):
        overdue_days = (date.today() - date.fromisoformat(invoice['due_date'])).days
    return render_template('invoice_view.html', invoice=invoice, client=client, overdue_days=overdue_days)

@app.route('/invoices/edit/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
def edit_invoice(invoice_id):
    invoice = get_invoice(invoice_id)
    if not invoice:
        return redirect(url_for('index'))
    all_clients  = get_all_clients()
    all_products = get_all_products(active_only=True)
    if request.method == 'POST':
        update_invoice(
            invoice_id,
            client_id=int(request.form['client_id']),
            issue_date=request.form['issue_date'],
            due_date=request.form['due_date'],
            items=_parse_items(),
            tax_rate=float(request.form.get('tax_rate', 0)),
            notes=request.form.get('notes', ''),
        )
        return redirect(url_for('view_invoice', invoice_id=invoice_id))
    return render_template('invoice_form.html', clients=all_clients, invoice=invoice,
                           today=date.today().isoformat(), products=all_products,
                           settings=load_settings(), default_due=invoice.get('due_date',''))

@app.route('/invoices/<int:invoice_id>/status/<status>', methods=['POST'])
@login_required
def update_status(invoice_id, status):
    update_invoice_status(invoice_id, status)
    return redirect(url_for('view_invoice', invoice_id=invoice_id))

@app.route('/invoices/delete/<int:invoice_id>', methods=['POST'])
@login_required
def delete_invoice_route(invoice_id):
    delete_invoice(invoice_id)
    return redirect(url_for('index'))

@app.route('/invoices/<int:invoice_id>/pdf')
@login_required
def download_pdf(invoice_id):
    invoice = get_invoice(invoice_id)
    if not invoice:
        return redirect(url_for('index'))
    client = get_client(invoice['client_id']) or {}
    pdf = _build_pdf(invoice=invoice, client=client, s=load_settings())
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=f"{invoice['invoice_number']}.pdf",
                     mimetype='application/pdf')

@app.route('/invoices/<int:invoice_id>/send', methods=['POST'])
@login_required
def send_invoice(invoice_id):
    invoice = get_invoice(invoice_id)
    if not invoice:
        return redirect(url_for('index'))
    client = get_client(invoice['client_id']) or {}
    try:
        pdf = _build_pdf(invoice=invoice, client=client, s=load_settings())
    except Exception as e:
        flash(f'Chyba pri generovani PDF: {e}', 'error')
        return redirect(url_for('view_invoice', invoice_id=invoice_id))
    ok, err = send_invoice_email(invoice, client, pdf)
    if ok:
        mark_invoice_sent(invoice_id)
        flash(f"Faktura odeslana na {client.get('email', '')}", 'success')
    else:
        flash(f'Nepodarilo se odeslat: {err}', 'error')
    return redirect(url_for('view_invoice', invoice_id=invoice_id))

@app.route('/reminders/send', methods=['POST'])
@login_required
def send_reminders_route():
    results = send_reminders()
    if not results:
        flash('Zadne faktury k upominani nebo API klic neni nastaven.', 'warning')
    else:
        ok_count  = sum(1 for _, _, ok, _ in results if ok)
        err_count = len(results) - ok_count
        if ok_count:  flash(f'Odeslano {ok_count} upominek.', 'success')
        if err_count: flash(f'Chyby ({err_count}): ' + '; '.join(f"{inv}: {e}" for inv,_,ok,e in results if not ok), 'error')
    return redirect(url_for('index'))


# ── Export / Import ───────────────────────────────────────────────────────────

@app.route('/export/invoices/csv')
@login_required
def export_invoices_csv():
    invoices   = get_all_invoices(with_items=False)
    clients    = get_all_clients()
    client_map = {c['id']: c['name'] for c in clients}
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['Číslo faktury','Klient','Datum vystavení','Datum splatnosti','Stav','Mezisoučet (Kč)','DPH (%)','DPH (Kč)','Celkem (Kč)','Poznámky'])
    status_map = {'paid':'zaplaceno','unpaid':'nezaplaceno','overdue':'po splatnosti'}
    for inv in sorted(invoices, key=lambda x: x.get('issue_date',''), reverse=True):
        writer.writerow([
            inv.get('invoice_number',''), client_map.get(inv.get('client_id'),'—'),
            inv.get('issue_date',''), inv.get('due_date',''),
            status_map.get(inv.get('status',''), inv.get('status','')),
            f"{inv.get('subtotal',0):.2f}".replace('.',','),
            f"{inv.get('tax_rate',0):.2f}".replace('.',','),
            f"{inv.get('tax_amount',0):.2f}".replace('.',','),
            f"{inv.get('total',0):.2f}".replace('.',','),
            inv.get('notes',''),
        ])
    buf.seek(0)
    output = io.BytesIO()
    output.write(b'\xef\xbb\xbf')
    output.write(buf.getvalue().encode('utf-8'))
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f"faktury_export_{date.today().isoformat()}.csv",
                     mimetype='text/csv')

@app.route('/export/clients/csv')
@login_required
def export_clients_csv():
    clients = get_all_clients()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['Jméno','Firma','E-mail','Telefon','Adresa'])
    for c in clients:
        writer.writerow([c.get('name',''),c.get('company',''),c.get('email',''),c.get('phone',''),c.get('address','')])
    buf.seek(0)
    output = io.BytesIO()
    output.write(b'\xef\xbb\xbf')
    output.write(buf.getvalue().encode('utf-8'))
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f"klienti_export_{date.today().isoformat()}.csv",
                     mimetype='text/csv')

@app.route('/import/clients/csv', methods=['POST'])
@login_required
def import_clients_csv():
    file = request.files.get('file') or request.files.get('csv_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Prosím nahrajte soubor CSV.', 'error')
        return redirect(url_for('clients'))
    try:
        raw = file.read()
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8')), delimiter=';')
        rows = list(reader)
        from database import import_clients_csv as db_import
        added, skipped = db_import(rows)
        flash(f'Import dokončen: {added} přidáno, {skipped} přeskočeno.', 'success')
    except Exception as e:
        flash(f'Chyba při importu: {str(e)}', 'error')
    return redirect(url_for('clients'))



@app.route('/import/invoices/csv', methods=['POST'])
@login_required
def import_invoices_csv():
    file = request.files.get('file') or request.files.get('csv_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Prosím nahrajte soubor CSV.', 'error')
        return redirect(url_for('index'))

    try:
        raw = file.read()
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]

        # Auto-detect delimiter
        sample = raw[:2000].decode('utf-8', errors='ignore')
        delimiter = ';' if sample.count(';') >= sample.count(',') else ','

        reader = csv.DictReader(
            io.StringIO(raw.decode('utf-8', errors='ignore')),
            delimiter=delimiter
        )

        # Column name aliases — handles export from this app + common Excel variations
        def col(row, *keys):
            for k in keys:
                if k in row and row[k].strip():
                    return row[k].strip()
            return ''

        def parse_amount(val):
            """Parse Czech decimal comma or dot: '1 234,50' or '1234.50'"""
            if not val:
                return 0.0
            return float(val.replace(' ', '').replace(',', '.'))

        status_map = {
            'zaplaceno': 'paid', 'paid': 'paid',
            'nezaplaceno': 'unpaid', 'unpaid': 'unpaid',
            'po splatnosti': 'overdue', 'overdue': 'overdue',
        }

        clients     = get_all_clients()
        client_map  = {c['name'].lower(): c['id'] for c in clients}
        s           = load_settings()
        prefix      = s.get('invoice_prefix', 'INV')

        added = skipped = 0
        errors = []

        for i, row in enumerate(reader, 1):
            try:
                # ── Resolve client ────────────────────────────────────────────
                client_name = col(row, 'Klient', 'klient', 'client', 'Client')
                client_id   = client_map.get(client_name.lower())

                if not client_id:
                    # Create client on the fly if they don't exist yet
                    if client_name:
                        new_c = create_client(name=client_name)
                        client_id = new_c['id']
                        client_map[client_name.lower()] = client_id
                    else:
                        skipped += 1
                        errors.append(f'Řádek {i}: chybí název klienta')
                        continue

                # ── Dates ─────────────────────────────────────────────────────
                issue_date = col(row, 'Datum vystavení', 'datum vystaveni', 'issue_date', 'Issue Date', 'Datum', 'Date')
                due_date   = col(row, 'Datum splatnosti', 'datum splatnosti', 'due_date', 'Due Date', 'Splatnost')

                if not issue_date:
                    issue_date = date.today().isoformat()
                if not due_date:
                    from datetime import timedelta
                    due_date = (date.today() + timedelta(days=int(s.get('default_due_days', 14)))).isoformat()

                # Normalize date format dd.mm.yyyy -> yyyy-mm-dd
                for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
                    try:
                        from datetime import datetime as dt
                        issue_date = dt.strptime(issue_date, fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
                for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
                    try:
                        from datetime import datetime as dt
                        due_date = dt.strptime(due_date, fmt).date().isoformat()
                        break
                    except ValueError:
                        pass

                # ── Amounts ───────────────────────────────────────────────────
                subtotal   = parse_amount(col(row, 'Mezisoučet (Kč)', 'mezisoucet', 'subtotal', 'Subtotal', 'Mezisoučet'))
                tax_rate   = parse_amount(col(row, 'DPH (%)', 'dph_%', 'tax_rate', 'DPH'))
                total      = parse_amount(col(row, 'Celkem (Kč)', 'celkem', 'total', 'Total'))

                # If only total given, work backwards
                if total and not subtotal:
                    subtotal = round(total / (1 + tax_rate / 100), 2) if tax_rate else total

                # ── Status ────────────────────────────────────────────────────
                status_raw = col(row, 'Stav', 'stav', 'status', 'Status').lower()
                status     = status_map.get(status_raw, 'unpaid')

                # ── Invoice number — always generate new to avoid conflicts ──
                inv_num = get_next_invoice_number(prefix)

                # ── Create invoice with one summary line item ─────────────────
                notes = col(row, 'Poznámky', 'poznamky', 'notes', 'Notes')
                orig_num = col(row, 'Číslo faktury', 'cislo faktury', 'invoice_number', 'Invoice Number', 'Invoice')
                if orig_num:
                    notes = f'Původní číslo: {orig_num}' + (f' | {notes}' if notes else '')

                inv = create_invoice(
                    invoice_number=inv_num,
                    client_id=client_id,
                    issue_date=issue_date,
                    due_date=due_date,
                    items=[{
                        'name': f'Import ({orig_num})' if orig_num else 'Importovaná položka',
                        'qty': 1,
                        'price': subtotal or total,
                    }],
                    tax_rate=tax_rate,
                    notes=notes,
                )

                # Update status if paid/overdue
                if status != 'unpaid':
                    update_invoice_status(inv['id'], status)

                added += 1

            except Exception as e:
                skipped += 1
                errors.append(f'Řádek {i}: {str(e)[:80]}')

        msg = f'Import dokončen: {added} přidáno, {skipped} přeskočeno.'
        flash(msg, 'success')
        if errors:
            flash('Chyby: ' + ' | '.join(errors[:5]), 'error')

    except Exception as e:
        flash(f'Chyba při čtení souboru: {str(e)}', 'error')

    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
