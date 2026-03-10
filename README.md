# 🥐 Fakturace — Invoice Management System

A full-stack web invoicing application built with **Python + Flask**, designed for small Czech businesses. Features PDF generation with QR payment codes, client and product management, CSV import/export, and a clean black-and-white print-optimized PDF layout.

> Built as a real-world tool for Šefl s.r.o. (a Czech bakery), this project demonstrates end-to-end web application development without a framework like Django — routing, data persistence, file generation, and a polished UI all from scratch.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask)
![ReportLab](https://img.shields.io/badge/PDF-ReportLab-red)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Features

### Invoice Management
- Create, edit, delete and view invoices
- Automatic invoice numbering with configurable prefix (e.g. `FAK-0001`)
- Status tracking: **Nezaplaceno → Zaplaceno → Po splatnosti**
- Automatic overdue detection on every page load
- Mark invoices as paid/unpaid with one click

### PDF Generation
- Black-and-white, print-optimized PDF output
- Full **Czech character support** via embedded DejaVu Sans font (ě, š, č, ř, ž, ý, á, í, é, ů, ú)
- **QR payment code** (Czech SPD standard) generated from IBAN — scannable by any Czech banking app
- Supplier details auto-filled from company settings (IČO, DIČ, address, bank account)
- Overdue warning block with days-past-due count

### Client & Product Catalog
- Full CRUD for clients and products
- Product catalog with categories and pre-filled prices
- **CSV import** for bulk client upload (with duplicate detection by email)
- **CSV export** for invoices and clients (UTF-8 with BOM for Excel compatibility)

### Dashboard & Filtering
- Summary stats: total invoices, paid/unpaid/overdue counts, revenue, outstanding balance
- Clickable stat cards filter the invoice table by status
- Combined filters: date range + client name
- Live result counter ("Zobrazeno: X z Y")

### Company Settings
- One-time setup: company name, IČO, DIČ, address, contact details
- Bank account + IBAN stored once, used in every PDF and QR code
- Configurable default due days and default VAT rate
- Invoice number prefix customizable (INV, FAK, 2024, etc.)

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask |
| Templating | Jinja2 |
| PDF Generation | ReportLab |
| QR Code | Custom pure-Python implementation (no external deps) |
| Data Storage | JSON flat files |
| Frontend | Bootstrap 5, vanilla JS |
| Fonts | DejaVu Sans (embedded TTF for PDF) |

> **No database required.** All data is stored in JSON files inside the `data/` directory — making the app fully portable and easy to back up.

---

## 📁 Project Structure

```
fakturace/
├── app.py                  # Flask application — all routes and business logic
├── qr_generator.py         # Pure-Python QR code generator (SPD payment format)
├── DejaVuSans.ttf          # Embedded font for PDF (Czech character support)
├── DejaVuSans-Bold.ttf
├── DejaVuSans-Oblique.ttf
├── data/
│   ├── clients.json        # Client records
│   ├── invoices.json       # Invoice records
│   ├── products.json       # Product catalog
│   └── settings.json       # Company settings
└── templates/
    ├── base.html           # Layout, navigation, CSS variables
    ├── index.html          # Dashboard with stats and filterable invoice table
    ├── invoice_form.html   # Create/edit invoice with product catalog picker
    ├── invoice_view.html   # Invoice detail view
    ├── clients.html        # Client list with CSV import
    ├── client_form.html    # Add/edit client
    ├── products.html       # Product catalog
    ├── product_form.html   # Add/edit product
    └── settings.html       # Company settings
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/fakturace.git
cd fakturace

# Install dependencies
pip install flask reportlab pillow

# Run the app
python app.py
```

Open your browser at **http://localhost:5000**

### First-time Setup

1. Navigate to **⚙️ Nastavení** (Settings) in the top menu
2. Fill in your company details: name, IČO, DIČ, address, email
3. Enter your **IBAN** to enable automatic QR payment codes on every PDF
4. Set your preferred invoice number prefix and default due days
5. Start creating invoices

---

## 📄 PDF Output

Every generated PDF includes:
- Company header with full supplier details from Settings
- Client billing address
- Itemized table with quantities, unit prices and line totals
- VAT breakdown and total due in CZK
- **Payment section** with bank account number and scannable QR code
- Optional notes
- Overdue warning (if applicable)

The PDF is black-and-white only — optimized for printing on standard office printers.

---

## 📦 CSV Import / Export

### Export
- `/export/invoices/csv` — all invoices with status and totals
- `/export/clients/csv` — full client list

All exports use **semicolon delimiter and UTF-8 BOM** for seamless opening in Microsoft Excel.

### Client Import
Upload a CSV file from the Clients page. Expected columns:

```
Jméno;Firma;E-mail;Telefon;Adresa
```

Duplicate emails are skipped automatically.

---

## 🧩 QR Payment Code

The QR code is generated in the **Czech SPD (Short Payment Descriptor)** format, compatible with all major Czech banking apps (Česká spořitelna, Fio, ČSOB, mBank, etc.).

Format:
```
SPD*1.0*ACC:<IBAN>*AM:<amount>*CC:CZK*MSG:<invoice_number>*
```

The QR generator (`qr_generator.py`) is a **zero-dependency pure Python** implementation of QR code generation, including Reed-Solomon error correction — no `qrcode` or `segno` library required.

---

## 🔧 Configuration

All settings are stored in `data/settings.json` and editable via the UI. Key fields:

| Field | Description |
|---|---|
| `company_name` | Displayed on all invoices and PDFs |
| `ico` / `dic` | Czech company identifiers |
| `iban` | Enables QR payment code generation |
| `invoice_prefix` | e.g. `FAK` → `FAK-0001` |
| `default_due_days` | Auto-calculated due date on new invoices |
| `default_tax_rate` | Pre-selected VAT rate (0, 10, 12, 21 %) |

---

## 🗺️ Roadmap

- [ ] Multiple VAT rates per invoice (10% / 21%)
- [ ] Email sending — send PDF directly to client from the app
- [ ] Recurring invoices — monthly templates auto-generated
- [ ] Overdue reminders — automatic email after X days
- [ ] Annual revenue report / accounting export
- [ ] SQLite migration for larger datasets

---

## 📝 License

MIT License — free to use, modify and distribute.

---

*Developed for Šefl s.r.o., Pekárna & Cukrárna*
