# app.py
from flask_migrate import Migrate
from flask import Flask, request, redirect, render_template, session, url_for, flash, jsonify, send_file
from models import *
from db_utils import *
from extensions import db
import os
from waitress import serve
from datetime import date, timedelta, datetime
from dotenv import load_dotenv
import logging
from fpdf import FPDF, XPos, YPos
import traceback
import io
import sys
import uuid
from collections import deque
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename
import json
from PIL import Image
import pandas as pd
from briva_numbers import legacy_cart, money as validated_money, quantity as validated_quantity
from briva_core import register_core
from briva_totp import (
    init_auth, begin_login_challenge, revoke_current_session,
    has_authenticated_factor, revoke_user_sessions, is_user_enabled, deactivate_user,
)
from briva_security import (
    init_security, establish_session, csrf_token,
    current_user as secure_current_user, validate_employee_payload,
    validate_permissions_payload, validate_password, SecurityValidationError,
)
from briva_media import register_media
from briva_documents import register_documents

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
session_secret = os.environ.get("SECRET_KEY", "").strip()
if len(session_secret) < 32:
    raise RuntimeError(
        "SECRET_KEY en az 32 karakter olmalı. İlk kurulum: python manage.py init"
    )

INSTANCE_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("BRIVA_DATA_DIR") or os.path.join(BASE_DIR, "instance")
))
DB_PATH = os.path.join(INSTANCE_DIR, "data.db")
FONT_PATH_REGULAR = os.path.join(BASE_DIR, 'fonts', 'DejaVuSans.ttf')
FONT_PATH_BOLD = os.path.join(BASE_DIR, 'fonts', 'DejaVuSans-Bold.ttf')
os.makedirs(INSTANCE_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(INSTANCE_DIR, "app.log"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

from ai_utils import get_groq_response

app = Flask(__name__)
# fotoğraf yükleme ayarları

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
# fotoğraf yükleme ayarları sonu

def compress_and_save(file, save_path):
    try:
        # Resmi aç
        img = Image.open(file)
        
        # Eğer resim PNG ise ve saydamlık varsa, JPEG yaparken siyah olmasın diye beyaza boya
        # Veya direkt RGB'ye çevir (JPEG uyumluluğu için)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Maksimum boyut (Genişlik, Yükseklik) - Oran korunur
        max_size = (400, 400) 
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # Resmi kaydet (optimize=True dosya boyutunu daha da düşürür)
        # Kalite 85 genelde gözle görülür kayıp olmadan iyi sıkıştırma sağlar
        img.save(save_path, optimize=True, quality=85)
        
        return True
    except Exception as e:
        print(f"Resim sıkıştırma hatası: {e}")
        return False

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"hide_parameters": True}
app.secret_key = session_secret
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('BRIVA_HTTPS') == '1'
app.config['BRIVA_ALLOW_LEGACY_WRITES'] = os.environ.get('BRIVA_ALLOW_LEGACY_WRITES') == '1'
app.config['TRUSTED_HOSTS'] = [
    value.strip() for value in os.environ.get(
        'BRIVA_TRUSTED_HOSTS', 'localhost,127.0.0.1,[::1]'
    ).split(',') if value.strip()
]

# The new variant/location ledger is the only active stock and account source.
# Old routes remain available for historical reads, but cannot silently write a
# separate set of inventory or customer balances. Compatibility is opt-in for
# isolated regression tests and controlled migration work only.
LEGACY_WRITE_ENDPOINTS = frozenset({
    'add_expenditure', 'add_stock_route', 'collect_payment', 'create_sale',
    'create_order_route', 'manage_order_route', 'revert_order_route',
    'add_product_route', 'update_product_route', 'delete_product_route',
    'add_stock_to_shelf_route', 'add_customer', 'delete_customer',
    'add_shelf', 'add_color', 'add_category', 'delete_color',
    'delete_category', 'delete_shelf', 'add_attribute_def_route',
    'update_attribute_def_route', 'delete_attribute_def_route',
    'manage_barcode_rules',
})
LEGACY_PAGE_DESTINATIONS = {
    'customer_debts': 'customers', 'add_customer_route': 'customers',
    'definitions': 'catalog', 'addcolor_route': 'catalog',
    'addcategory_route': 'catalog', 'addshelf_route': 'inventory',
}


def protect_legacy_ledger():
    compatibility = app.testing and app.config['BRIVA_ALLOW_LEGACY_WRITES']
    if request.method in {'GET', 'HEAD'} and not compatibility and request.endpoint in LEGACY_PAGE_DESTINATIONS:
        return redirect(url_for('workbench') + '#' + LEGACY_PAGE_DESTINATIONS[request.endpoint])
    if (request.method not in {'GET', 'HEAD', 'OPTIONS'}
            and request.endpoint in LEGACY_WRITE_ENDPOINTS
            and not compatibility):
        return jsonify(status='error', error={
            'code': 'legacy_read_only',
            'message': 'Bu eski ekran yalnızca arşiv içindir. İşlemi yeni çalışma ekranında yapın.',
            'workbench': '/workbench',
        }), 410


@app.after_request
def protect_response_details(response):
    # The token can rotate after a credential change. Same-origin clients keep
    # their next mutation valid without putting the token in a URL or log.
    if request.endpoint != 'static':
        response.headers['X-CSRF-Token'] = csrf_token()
        response.headers.setdefault('Cache-Control', 'no-store')
    if response.status_code >= 500 and request.endpoint and not request.endpoint.startswith(('briva_core.', 'briva_auth.', 'briva_media.')):
        # Several archived handlers return str(exception); never expose SQL,
        # local paths, or internal exception details to the browser.
        response.set_data(json.dumps({'status': 'error', 'message': 'İşlem tamamlanamadı. Lütfen tekrar deneyin.'}, ensure_ascii=False))
        response.content_type = 'application/json; charset=utf-8'
    return response

app.permanent_session_lifetime = timedelta(hours=24)

db.init_app(app)
migrate = Migrate(app, db)


try:
    with open(os.path.join(BASE_DIR, 'knowledge_base.md'), 'r', encoding='utf-8') as f:
        KNOWLEDGE_BASE = f.read()
except FileNotFoundError:
    KNOWLEDGE_BASE = "Bilgi bankası dosyası bulunamadı."

logs = deque(maxlen=2000)

class StdoutRedirector:
    def write(self, text):
        if text.strip():  # boş satırları atlama
            logs.append(text.strip())
        sys.__stdout__.write(text)  # terminale de yaz

    def flush(self):
        sys.__stdout__.flush()

sys.stdout = StdoutRedirector()

# logging çıktısını da yakalamak
class ListHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        logs.append(log_entry)

class IgnoreLogsJSONFilter(logging.Filter):
    def filter(self, record):
        # Eğer log '/logs.json' ile ilgiliyse false döndür (yani loglanmasın)
        return '/logs.json' not in record.getMessage()

# logger ayarları
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(ListHandler())
logger.addFilter(IgnoreLogsJSONFilter())
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

def logf(log_text):
    # Forwarded headers are untrusted unless a deployment explicitly installs
    # a correctly scoped proxy adapter. Local clients cannot spoof audit peers.
    ip = request.remote_addr

    full_log = f"{log_text} - IP: {ip}"
    logging.info(full_log)  # hem log dosyasına hem de logs listesine yazılır

@app.route('/logs')
def show_logs():
    # Process diagnostics contain events from multiple tenants. Only the new
    # company-scoped audit ledger belongs in the browser.
    return redirect(url_for('workbench') + '#history')

@app.route('/add_expenditure', methods=['POST'])
def add_expenditure():
    user = current_user()
    # İzin kontrolü: Sadece 'can_manage_debts' veya yeni bir izin tanımlayabilirsiniz.
    # Şimdilik 'can_manage_debts' (Borç/Finans yönetimi) iznini kullanalım.
    if not user or not user.can_manage_debts: 
        return jsonify({"status": "error", "message": "Gider ekleme yetkiniz yok."}), 403

    data = request.get_json()
    amount = float(data.get('amount', 0))
    currency_type = data.get('currency', 'TL')
    description = data.get('description', '')
    company_id = session.get('company')

    if amount <= 0 or not description:
        return jsonify({"status": "error", "message": "Geçersiz tutar veya açıklama."}), 400

    # Currency alanını JSON olarak kaydediyoruz: {"TL": 150} gibi
    currency_data = {currency_type: amount}

    new_expenditure = Expenditure(
        company_id=company_id,
        user_id=user.id,
        currency=currency_data,
        description=description
    )

    db.session.add(new_expenditure)
    db.session.commit()
    
    logf(f"{user.username} yeni gider ekledi: {amount} {currency_type} - {description}")

    return jsonify({"status": "ok", "message": "Gider başarıyla kaydedildi."}), 201

# 2. Giderleri Listeleme Rotası
@app.route('/get_expenditures', methods=['GET'])
def get_expenditures():
    user = current_user()
    if not user:
        return jsonify({"status": "error"}), 403
        
    company_id = session.get('company')
    
    # Son 20 gideri getir
    expenditures = Expenditure.query.filter_by(company_id=company_id)\
        .order_by(Expenditure.date.desc())\
        .limit(20).all()
    
    result = []
    for exp in expenditures:
        # Currency JSON'dan tutarı ve birimi çıkar
        # Örn: {"TL": 100} -> amount=100, currency="TL"
        curr_key = list(exp.currency.keys())[0]
        curr_val = exp.currency[curr_key]
        
        # Ekleyen kullanıcı adını bul
        added_by = db.session.get(User, exp.user_id)
        username = added_by.username if added_by else "Bilinmiyor"

        result.append({
            "date": exp.date.strftime('%d.%m.%Y %H:%M'),
            "amount": curr_val,
            "currency": curr_key,
            "description": exp.description,
            "user": username
        })
        
    return jsonify(result), 200

@app.route("/generate_transaction_pdf/<string:sale_group_id>", methods=["GET"])
def generate_transaction_pdf(sale_group_id):
    try:
        user = current_user()
        if not user or not user.can_generate_reports:
            return "Yetkiniz yok.", 403

        company_id = session.get('company')
        
        # Satış grubunu çek
        sales_in_group = Sale.query.filter_by(sale_group_id=sale_group_id, company_id=company_id).order_by(Sale.id).all()
        
        if not sales_in_group:
            return "İşlem bulunamadı.", 404
        
        # --- PDF SINIFI ---
        class ModernPDF(FPDF):
            def header(self):
                self.add_font('DejaVu', '', FONT_PATH_REGULAR, uni=True)
                self.add_font('DejaVu', 'B', FONT_PATH_BOLD, uni=True)
                
                self.set_font('DejaVu', 'B', 16)
                self.set_text_color(30, 30, 30) 
                self.cell(0, 10, f'{company_id.upper()}', ln=1, align='L')
                
                self.set_font('DejaVu', '', 8)
                self.set_text_color(100, 100, 100) 
                self.cell(0, 5, f'İşlem ID: #{sale_group_id[-8:].upper()}', ln=1, align='L')
                
                self.set_draw_color(200, 200, 200)
                self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('DejaVu', '', 8)
                self.set_text_color(150, 150, 150)
                self.cell(0, 10, f'Sayfa {self.page_no()}', align='R')

        pdf = ModernPDF(orientation='P', unit='mm', format='A4')
        pdf.add_page()
        
        # --- MÜŞTERİ VE TARİH BİLGİSİ ---
        first_sale = sales_in_group[0]
        sold_by_user = User.query.get(first_sale.user_id)
        seller_name = sold_by_user.username if sold_by_user else "-"
        customer_name = first_sale.customer_name or "Misafir"
        sale_date = first_sale.sale_date.strftime('%d.%m.%Y %H:%M')

        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(50, 50, 50)
        
        pdf.cell(95, 6, f"Müşteri: {customer_name}", ln=0)
        pdf.cell(95, 6, f"Tarih: {sale_date}", ln=1, align='R')
        pdf.cell(95, 6, f"Satış Temsilcisi: {seller_name}", ln=1)
        pdf.ln(5)

        # --- TABLO BAŞLIKLARI ---
        # Renk kalktığı için ürün adına (w_name) ek alan verildi
        w_code = 30
        w_name = 70  
        w_shelf = 20
        w_detail = 45 
        w_price = 25  
        
        pdf.set_fill_color(245, 245, 245) 
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('DejaVu', 'B', 8)
        
        pdf.cell(w_code, 10, "ÜRÜN KODU", 0, 0, 'L', True)
        pdf.cell(w_name, 10, "ÜRÜN ADI", 0, 0, 'L', True)
        pdf.cell(w_shelf, 10, "RAF", 0, 0, 'C', True)
        pdf.cell(w_detail, 10, "BEDEN / SERİ İÇERİĞİ", 0, 0, 'L', True)
        pdf.cell(w_price, 10, "TUTAR", 0, 1, 'R', True)
        
        # --- TABLO İÇERİĞİ ---
        pdf.set_font('DejaVu', '', 8)
        pdf.set_text_color(50, 50, 50)
        
        grand_total = 0
        currency_symbol = first_sale.currency

        for item in sales_in_group:
            product = Product.query.filter_by(code=item.product_code, company_id=company_id).first()
            product_name = product.name if product else "Bilinmiyor"
            
            shelf_code = "-"
            if product and product.shelves:
                codes = [s.get('code', '') for s in product.shelves if s.get('code')]
                shelf_code = ", ".join(codes)
            
            detail_str = ""
            if item.sizes:
                parts = [f"{s['value']}:{s['quantity']}" for s in item.sizes if s.get('quantity', 0) > 0]
                detail_str = " | ".join(parts)
            
            final_detail = f"[{item.quantity} ADET] {detail_str}"

            # Satır Yazdırma
            h = 8
            pdf.cell(w_code, h, item.product_code, "B", 0, 'L')
            pdf.cell(w_name, h, product_name, "B", 0, 'L')
            pdf.cell(w_shelf, h, shelf_code, "B", 0, 'C')
            
            pdf.set_font_size(7)
            pdf.cell(w_detail, h, final_detail, "B", 0, 'L')
            pdf.set_font_size(8)
            
            pdf.cell(w_price, h, f"{item.total_price:.2f} {item.currency}", "B", 1, 'R')
            
            grand_total += item.total_price

        pdf.ln(5)

        # --- TOPLAM ALANI ---
        pdf.set_font('DejaVu', 'B', 10)
        offset = w_code + w_name + w_shelf + w_detail
        pdf.cell(offset, 10, "GENEL TOPLAM:", 0, 0, 'R')
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(w_price, 10, f"{grand_total:.2f} {currency_symbol}", 0, 1, 'R', True)
        
        # --- NOT KUTUSU ---
        sale_note_content = first_sale.sale_notes if first_sale.sale_notes else "Ek açıklama girilmemiştir."
        pdf.ln(10)
        pdf.set_fill_color(255, 255, 255)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_font('DejaVu', 'B', 9)
        pdf.cell(190, 8, "  NOT", "LTR", 1, 'L', True) 
        pdf.set_font('DejaVu', '', 9)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(190, 8, f"  {sale_note_content}\n ", border='LBR', align='L', fill=True)

        buffer = io.BytesIO(pdf.output())
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"Satis_Raporu_{sale_group_id[:8]}.pdf"
        )

    except Exception as e:
        print(f"PDF Hatası: {e}")
        return f"PDF oluşturulamadı: {e}", 500

@app.route('/logs.json')
def logs_json():
    return jsonify(error={
        'code': 'process_logs_private',
        'message': 'İşlem kayıtları yeni çalışma ekranındaki şirketinize ait denetim defterindedir.',
        'workbench': '/workbench#history',
    }), 410

@app.route("/get_product_sales_trend", methods=["GET"])
def get_product_sales_trend_route():
    user = current_user()
    if not user or not user.can_view_statistics:
        return jsonify({"status": "error", "message": "Satış trendini görme yetkiniz yok."}), 403

    company_id = session.get('company')
    product_code = request.args.get('code')

    if not company_id or not product_code:
        return jsonify({"error": "Eksik bilgi: Şirket ve Ürün Kodu zorunludur"}), 400

    trend_data = get_product_sales_trend(company_id, product_code)
    return jsonify(trend_data), 200

@app.route("/generate_lod_pdf", methods=["GET"])
def generate_lod_pdf():
    try:
        user = current_user()
        if not user or not user.can_generate_reports:
            return jsonify({"status": "error", "message": "LOD indirme yetkiniz yok."}), 403

        company_id = session.get('company')
        if not company_id:
            return jsonify({"error": "Şirket bilgisi bulunamadı"}), 400

        search_term = request.args.get('search', None)
        start_date_str = request.args.get('start_date', None)
        end_date_str = request.args.get('end_date', None)
        
        sales_results = get_all_sales_for_report(
            company_id,
            search_term=search_term,
            start_date_str=start_date_str,
            end_date_str=end_date_str
        )

        if not sales_results:
            return jsonify({"error": "Rapor oluşturulacak satış kaydı bulunamadı."}), 404

        # Toplamları hesaplama
        totals_by_currency = {}
        total_series_sold = 0
        total_items_sold = 0
        for s in sales_results:
            totals_by_currency[s.currency] = totals_by_currency.get(s.currency, 0) + s.total_price
            total_series_sold += s.quantity
            if s.sizes:
                total_items_sold += sum(item['quantity'] for item in s.sizes)

        class PDF(FPDF):
            def header(self):
                self.add_font('DejaVu', '', FONT_PATH_REGULAR)
                self.add_font('DejaVu', 'B', FONT_PATH_BOLD)
                self.set_font('DejaVu', 'B', 10)
                self.cell(0, 5, f'Mağaza: {company_id}', ln=1, align='L')
                self.set_font('DejaVu', 'B', 14)
                self.cell(0, 10, 'Satış Detay Raporu (LOD)', ln=1, align='C')
                self.set_font('DejaVu', '', 8)
                self.cell(0, 5, f'Rapor Tarihi: {datetime.now().strftime("%d.%m.%Y %H:%M")}', ln=1, align='C')
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('DejaVu', '', 8)
                self.cell(0, 10, f'Sayfa {self.page_no()}', align='C')

        pdf = PDF(orientation='L', unit='mm', format='A4')
        pdf.add_page()
        
        # Özet Bilgileri
        pdf.set_font('DejaVu', 'B', 10)
        pdf.cell(0, 8, 'Rapor Özeti', ln=1, align='L')
        pdf.set_font('DejaVu', '', 9)
        
        filters_applied = []
        if start_date_str: filters_applied.append(f"Başlangıç: {start_date_str}")
        if end_date_str: filters_applied.append(f"Bitiş: {end_date_str}")
        if search_term: filters_applied.append(f"Arama: '{search_term}'")
        filter_text = f"Filtreler: {' | '.join(filters_applied) if filters_applied else 'Yok'}"
        pdf.cell(0, 5, filter_text, ln=1)

        pdf.cell(0, 5, f"Toplam Satılan Seri Adedi: {total_series_sold}", ln=1)
        pdf.cell(0, 5, f"Toplam Satılan Ürün Adedi: {total_items_sold}", ln=1)
        
        for currency, total in totals_by_currency.items():
            pdf.cell(0, 5, f"Toplam Ciro ({currency}): {total:,.2f} {currency}", ln=1)
        
        pdf.ln(8)

        # Detaylı Satış Tablosu (Renk kaldırıldı, genişlikler güncellendi)
        headers = ["Ürün Kodu", "Ürün Adı", "Müşteri", "Seri Adet", "Beden Dökümü", "Birim Fiyat", "Toplam Tutar", "Satan Kişi", "Tarih"]
        col_widths = [30, 55, 30, 15, 45, 25, 25, 25, 25]

        pdf.set_font('DejaVu', 'B', 7)
        for i, header in enumerate(headers):
            pdf.cell(col_widths[i], 8, header, border=1, align='C')
        pdf.ln()

        pdf.set_font('DejaVu', '', 7)
        for s in sales_results:
            product_name = get_product_name_by_id(s.product_code, s.company_id) or "-"
            sold_by_user = User.query.filter_by(id=s.user_id).first()
            sold_by_username = sold_by_user.username if sold_by_user else "-"
            
            sizes_breakdown_str = " / ".join([f"{size['quantity']} {size['value']}" for size in s.sizes]) if s.sizes else "N/A"

            row = [
                str(s.product_code),
                product_name,
                s.customer_name or "-",
                str(s.quantity),
                sizes_breakdown_str,
                f"{s.price_at_sale:.2f} {s.currency}",
                f"{s.total_price:.2f} {s.currency}",
                sold_by_username,
                s.sale_date.strftime('%d.%m.%Y %H:%M')
            ]

            for i, data in enumerate(row):
                pdf.cell(col_widths[i], 7, str(data), border=1, align='C', ln=1 if i == len(row) - 1 else 0)

        buffer = io.BytesIO()
        pdf.output(buffer)
        buffer.seek(0)

        filename = f"LOD_Rapor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)

    except Exception as e:
        print(f"Toplu PDF hatası: {traceback.format_exc()}")
        return f"Toplu PDF oluşturulamadı: {e}", 500

@app.route('/chat_ai', methods=['POST'])
def chat_ai():
    data = request.get_json()
    user_message = data.get('message')
    
    # Oturum bilgilerini al
    company_id = session.get('company')
    user = current_user()
    
    if not all([user_message, company_id, user]):
        return jsonify({"error": "Oturum süresi dolmuş veya yetkisiz erişim."}), 401
        
    logf(f"Kullanıcı ID {user.id} AI isteği gönderdi.")

    # Geçmiş konuşmaları session'da tutabiliriz (Opsiyonel basit hafıza)
    if 'ai_history' not in session:
        session['ai_history'] = []
    
    # Groq Utils'i çağır
    ai_response_text = get_groq_response(
        user_message=user_message,
        company_id=company_id,
        user_id=user.id,
        conversation_history=session['ai_history']
    )
    
    # Hafızayı güncelle
    session['ai_history'].append({"role": "user", "content": user_message})
    session['ai_history'].append({"role": "assistant", "content": ai_response_text})
    
    # Session çok şişmesin ama bağlamı hatırlasın (LİMİTİ ARTIRDIK)
    if len(session['ai_history']) > 30: # Eskiden 10'du, 30 yaptık
        session['ai_history'] = session['ai_history'][-30:]

    return jsonify({"ai_response": ai_response_text})

@app.route('/')
def main():
    if current_user():
        return redirect(url_for('workbench'))
    return render_template('login.html')


@app.route('/workbench')
def workbench():
    if not current_user():
        return redirect(url_for('login'))
    return render_template('workbench.html')


@app.route('/product-photos')
def product_photos():
    if not current_user():
        return redirect(url_for('login'))
    return render_template('product_photos.html')

@app.route('/logout', methods=['POST', 'GET'])
def logout():
    if request.method == 'GET':
        return render_template('logout.html')
    logf(f"Kullanıcı ID {session.get('user')} çıkış yaptı.")
    revoke_current_session()
    db.session.commit()
    session.clear()
    return redirect(url_for('login'))

@app.route(('/storage'))
def storage():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"Kullanıcı ID {session.get('user')} depo arşivini açtı.")
    products = Product.query.filter_by(company_id=company_id).order_by(Product.id.desc()).limit(500).all()
    return render_template('archive_storage.html', products=products)

@app.route('/profile')
def profile():
    # Sadece giriş yapmış kullanıcıların bu sayfayı görebilmesini sağla
    if 'user' not in session:
        return redirect(url_for('login'))
    logf(f"Kullanıcı ID {session.get('user')} profilini görüntüledi.")
    return render_template('profile.html')

@app.route('/get_current_user_data', methods=['GET'])
def get_current_user_data():
    user = current_user()
    if not user:
        return jsonify({"error": "Kullanıcı bulunamadı veya oturum açılmamış."}), 401
    
    return jsonify({
        "username": user.username,
        "phone": user.phone
    }), 200

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = current_user()
    if not user:
        return jsonify({"message": "Yetkisiz işlem."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {'username', 'phone'}:
        return jsonify(message='Yalnız kullanıcı adı ve telefon bilgisi gönderin.'), 400
    new_username, new_phone = data.get('username'), data.get('phone', '')
    if (not isinstance(new_username, str) or not 2 <= len(new_username.strip()) <= 100
            or any(ord(char) < 32 or ord(char) == 127 for char in new_username)):
        return jsonify(message='Kullanıcı adı 2–100 karakter olmalı ve kontrol karakteri içermemeli.'), 400
    if not isinstance(new_phone, str) or len(new_phone) > 15 or any(ord(char) < 32 for char in new_phone):
        return jsonify(message='Telefon bilgisi en fazla 15 karakter olmalı.'), 400
    new_username, new_phone = new_username.strip(), new_phone.strip()

    # Yeni kullanıcı adının başka bir kullanıcı tarafından alınıp alınmadığını kontrol et
    existing_user = User.query.filter(User.username == new_username, User.id != user.id).first()
    if existing_user:
        return jsonify({"message": "Bu kullanıcı adı zaten alınmış."}), 409

    # Veritabanında güncelleme yap
    # db_utils'deki mevcut fonksiyonu kullanabiliriz
    success = change_user_data_db(
        user_id=user.id, 
        username=new_username, 
        phone=new_phone
    )

    if success:
        # Session'daki kullanıcı adını da güncelle ki sidebar gibi yerler doğru görünsün
        session['username'] = new_username
        logf(f"Kullanıcı ID {user.id} profilini güncelledi.")
        return jsonify({"message": "Profil güncellendi."}), 200
    else:
        return jsonify({"message": "Güncelleme sırasında bir hata oluştu."}), 500

@app.route('/change_password', methods=['POST'])
def change_password():
    user = current_user()
    if not user:
        return jsonify({"message": "Yetkisiz işlem."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {'current_password', 'new_password'}:
        return jsonify(message='Geçerli parola alanlarını gönderin.'), 400
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not isinstance(current_password, str) or not 1 <= len(current_password) <= 256 or not new_password:
        return jsonify({"message": "Tüm alanlar zorunludur."}), 400
    try:
        validate_password(new_password)
    except SecurityValidationError as error:
        return jsonify({"message": str(error)}), 400

    # Mevcut şifrenin doğru olup olmadığını kontrol et
    if not user.check_password(current_password):
        return jsonify({"message": "Mevcut şifreniz yanlış."}), 403 # 403 Forbidden daha uygun

    # Yeni şifreyi ayarla ve kaydet
    try:
        was_mfa_verified = has_authenticated_factor(user)
        revoke_user_sessions(user)
        user.set_password(new_password)
        db.session.commit()
        establish_session(user, mfa_verified=was_mfa_verified)
        db.session.commit()
        logf(f"Kullanıcı ID {user.id} şifresini değiştirdi.")
        return jsonify({"message": "Şifre başarıyla değiştirildi."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Şifre değiştirme hatası: {e}")
        return jsonify({"message": "Sunucu hatası oluştu."}), 500

@app.route("/add_stock_route", methods=["POST"])
def add_stock_route():
    company_id = session.get("company")
    data = request.get_json()
    product_code = data.get("product_id")  # aslında kod geliyor
    try:
        number = int(data.get("number"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Geçersiz adet"}), 400

    user = current_user()
    if not user or not getattr(user, "can_edit_product", False):
        return jsonify({"status": "error", "message": "Stok güncelleme yetkiniz yok."}), 403

    update_status = add_stock(product_code, company_id, number)

    if update_status:
        return jsonify({"status": "ok", "message": "Stok başarıyla güncellendi."}), 200
    else:
        return jsonify({"status": "error", "message": "Ürün bulunamadı veya stok güncellenemedi."}), 400

@app.route('/get_customers_with_debt', methods=['GET'])
def get_customers_with_debt():
    user = current_user()
    # Bu sayfayı sadece müşteri yönetme yetkisi olanlar görebilir
    if not user or not user.can_manage_debts:
        return jsonify({"success": False, "message": "Yetkisiz işlem"}), 403
    
    company_id = session.get('company')
    if not company_id:
        return jsonify(success=False, message="Şirket bilgisi bulunamadı"), 403

    # Şirkete ait tüm müşterileri çek
    customers = Customer.query.filter_by(company_id=company_id).order_by(Customer.name).all()
    
    result = [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone or "-",
            "address": c.address or "-",
            # Borç bilgisi None değilse ve içi doluysa gönder, değilse boş bir sözlük gönder
            "debt": c.debt if c.debt else {} 
        }
        for c in customers
    ]
    return jsonify(success=True, customers=result)

@app.route('/collect_payment', methods=['POST'])
def collect_payment():
    user = current_user()
    if not user or not user.can_manage_debts:
        return jsonify({"success": False, "message": "Tahsilat yapma yetkiniz yok."}), 403

    data = request.get_json()
    
    try:
        # --- DEĞİŞİKLİK: Tüm veri işleme ve doğrulama mantığı try bloğunun içine taşındı ---
        customer_id_str = data.get('customer_id')
        amount_str = data.get('amount')
        currency = data.get('currency')
        note = data.get('note', '')

        if not all([customer_id_str, amount_str, currency]):
            return jsonify({"success": False, "message": "Eksik bilgi: Müşteri, tutar ve para birimi zorunludur."}), 400

        amount = float(amount_str)
        customer_id = int(customer_id_str)
        
        if amount <= 0:
            return jsonify({"success": False, "message": "Tahsilat tutarı pozitif bir değer olmalıdır."}), 400

        # Müşteriyi veritabanından bul
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({"success": False, "message": "Müşteri bulunamadı."}), 404
            
        # GÜVENLİK: Müşterinin, işlem yapan kullanıcının şirketine ait olduğunu kontrol et
        if customer.company_id != session.get('company'):
            return jsonify({"success": False, "message": "Yetkisiz işlem."}), 403

        if not customer.debt or customer.debt.get(currency, 0) <= 0:
            return jsonify({"success": False, "message": f"Bu müşterinin {currency} cinsinden borcu bulunmamaktadır."}), 400

        # 1. Müşterinin toplam borcunu güncelle
        current_debt = customer.debt.get(currency, 0)
        new_debt = current_debt - amount
        customer.debt[currency] = max(0, new_debt)
        if customer.debt[currency] == 0:
            del customer.debt[currency]
        flag_modified(customer, "debt")

        # 2. Tahsilat işlem kaydı oluştur
        credit_record = DebtTransaction(
            customer_id=customer.id,
            user_id=user.id,
            company_id=session.get('company'),
            transaction_type=TransactionType.CREDIT,
            amount=amount,
            currency=currency,
            note=note
        )
        db.session.add(credit_record)
        db.session.commit()

        logf(f"{session['username']}, {customer.name} isimli müşteriden {amount} {currency} tahsilat yaptı.")
        return jsonify({"success": True, "message": "Tahsilat başarıyla kaydedildi."})

    except (ValueError, TypeError):
        # Eğer float() veya int() çevrimi başarısız olursa bu hata yakalanır
        db.session.rollback()
        return jsonify({"success": False, "message": "Geçersiz tutar veya müşteri ID formatı."}), 400
    except Exception as e:
        db.session.rollback()
        print(f"Tahsilat sırasında beklenmedik bir hata oluştu: {e}")
        traceback.print_exc() # Terminalde detaylı hata görmek için
        return jsonify({"success": False, "message": "İşlem sırasında sunucu hatası oluştu."}), 500

@app.route('/get_customer_debt_history/<int:customer_id>', methods=['GET'])
def get_customer_debt_history(customer_id):
    user = current_user()
    if not user or not user.can_manage_debts:
        return jsonify({"success": False, "message": "Yetkisiz işlem"}), 403

    transactions = DebtTransaction.query.filter_by(
        customer_id=customer_id, 
        company_id=session.get('company')
    ).order_by(DebtTransaction.transaction_date.desc()).all()

    result = [
        {
            "type": t.transaction_type.value, # DEBIT veya CREDIT
            "amount": t.amount,
            "currency": t.currency,
            "date": t.transaction_date.strftime('%d.%m.%Y %H:%M'),
            "note": t.note or "",
            "user": t.user.username if t.user else "Bilinmiyor",
            "sale_id": t.sale_group_id
        } for t in transactions
    ]
    return jsonify(success=True, history=result)


@app.route('/customer_debts')
def customer_debts():
    # Sadece giriş yapmış ve şirketi belli olan kullanıcıların erişimine izin ver
    if 'company' not in session:
        return redirect(url_for('login'))
    
    user = current_user()
    if not user or not user.can_manage_debts:
        flash("Bu sayfayı görme yetkiniz yok.", "error")
        return redirect(url_for('main'))

    logf(f"Kullanıcı ID {session.get('user')} cari arşivini açtı.")
    return render_template('customer_debts.html')

@app.route('/sale', methods=['POST'])
def create_sale():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "Geçerli bir satış isteği gerekli."}), 400
    company_id = session.get('company')
    user = current_user()
    
    if not user or not user.can_make_sale:
        return jsonify({"status": "error", "message": "Satış yapma yetkiniz yok."}), 403

    is_debt = data.get('is_debt', False)
    if not isinstance(is_debt, bool):
        return jsonify({"status": "error", "message": "Veresiye seçimi geçersiz."}), 400
    try:
        amount_paid = float(validated_money(data.get('amount_paid', 0), 'Ödenen tutar'))
        cart_items = legacy_cart(data.get('items'))
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    customer_name = data.get('customerName')
    sale_notes = data.get('notes')

    if not cart_items:
        return jsonify({"status": "error", "message": "Sepet boş olamaz."}), 400
    if len({item['currency'] for item in cart_items}) != 1:
        return jsonify({"status": "error", "message": "Bir satışta tek para birimi kullanılmalı."}), 400
    if is_debt and not customer_name:
        return jsonify({"status": "error", "message": "Veresiye için müşteri gerekli."}), 400

    sale_group_id = str(uuid.uuid4())

    try:
        total_sale_price_by_currency = {}

        for item in cart_items:
            product_code = item.get('code')
            product = Product.query.filter_by(code=product_code, company_id=company_id).first()
            
            if not product:
                raise ValueError(f"Ürün bulunamadı: {product_code}")

            is_single_sale = item.get('is_single', False)
            currency = item.get('currency')
            discount = float(item.get('discount', 0))
            
            # --- DEĞİŞKENLERİ HAZIRLA ---
            sizes_data = product.sizes or []
            sizes_breakdown_for_sale_record = []
            current_item_total_price = 0
            
            # --- SENARYO 1: TEKLİ (PARÇA) SATIŞ ---
            if is_single_sale:
                # Frontend'den gelen toplam fiyatı al (Birim Fiyat * Adet - İndirim)
                # Not: İndirimi zaten frontend'de fiyattan düşmüş olabiliriz veya burada düşeriz.
                # En temizi: Fiyatı (total) alıp indirimi düşmek.
                
                # item['price'] -> Frontend'de hesaplanan (Birim Fiyat * Toplam Adet)
                batch_count = item['quantity']
                current_item_total_price = float(item['price']) * batch_count - discount
                
                # Satılan bedenleri al: {'S': 2, 'M': 1} gibi
                single_sizes_sold = item.get('single_sizes', {}) 
                known_sizes = {size.get('value') for size in sizes_data}
                if not set(single_sizes_sold).issubset(known_sizes):
                    raise ValueError('Satışta ürüne ait olmayan beden var.')
                
                # Stok Düşme İşlemi
                for size_info in sizes_data:
                    sz_val = size_info.get('value')
                    # Bu bedenden satıldıysa
                    if sz_val in single_sizes_sold:
                        qty_sold = int(single_sizes_sold[sz_val]) * batch_count
                        
                        if size_info['stock'] < qty_sold:
                             raise ValueError(f"Yetersiz stok: {product.name} - {sz_val} bedeni. Mevcut: {size_info['stock']}, İstenen: {qty_sold}")
                        
                        size_info['stock'] -= qty_sold
                        
                        # Satış kaydı için detay ekle
                        sizes_breakdown_for_sale_record.append({
                            "value": sz_val, "quantity": qty_sold
                        })
                
                # Seri adedi olarak 0 kaydediyoruz çünkü tam seri satılmadı.
                sale_quantity_record = 0 
                # Birim fiyat olarak (Toplam Tutar / Toplam Parça) kaydedebiliriz veya 0.
                # Raporlarda karışıklık olmaması için price_at_sale'i yaklaşık bir değer yapabiliriz.
                total_pieces = sum(int(v) for v in single_sizes_sold.values()) * batch_count
                sale_unit_price = (current_item_total_price + discount) / total_pieces if total_pieces > 0 else 0

            # --- SENARYO 2: SERİ SATIŞ (ESKİ MANTIK) ---
            else:
                quantity_of_seris_sold = int(item.get('quantity', 0))
                price_per_seri = float(item.get('price', 0)) # Bu birim seri fiyatıdır
                
                # Seri stok kontrolü
                if product.stock < quantity_of_seris_sold:
                    raise ValueError(f"Yetersiz seri stoğu: '{product.name}'. Mevcut: {product.stock}, İstenen: {quantity_of_seris_sold}")

                current_item_total_price = (price_per_seri * quantity_of_seris_sold) - discount
                sale_quantity_record = quantity_of_seris_sold
                sale_unit_price = price_per_seri
                
                # Stok Düşme İşlemi (Seri Mantığı)
                for size_info in sizes_data:
                    pieces_in_one_series = size_info.get('number', 0)
                    sold_this_size = quantity_of_seris_sold * pieces_in_one_series
                    
                    if sold_this_size > 0:
                        if size_info['stock'] < sold_this_size:
                             raise ValueError(f"Yetersiz beden stoğu (Seri içi): {product.name} - {size_info.get('value')}")
                        
                        size_info['stock'] -= sold_this_size
                        sizes_breakdown_for_sale_record.append({
                            "value": size_info.get('value'), "quantity": sold_this_size
                        })

            # --- ORTAK İŞLEMLER (KAYIT VE STOK GÜNCELLEME) ---
            
            # Ciro Toplamı
            total_sale_price_by_currency[currency] = total_sale_price_by_currency.get(currency, 0) + current_item_total_price

            # 1. Beden Stoklarını Kaydet
            product.sizes = sizes_data
            flag_modified(product, "sizes")

            # 2. Ana Seri Stoğunu (product.stock) Yeniden Hesapla
            # (Kalan parça stoklarına göre kaç tam seri çıkabileceğini bul)
            new_seri_stock = 0
            if product.sizes:
                seri_components = [s for s in product.sizes if s.get('number', 0) > 0]
                if seri_components:
                    # Formül: (Mevcut Parça Stoğu) // (Bir Serideki Adet)
                    # En az olan beden, toplam seri sayısını belirler (Darboğaz mantığı)
                    possible_seris = [s.get('stock', 0) // s.get('number', 0) for s in seri_components]
                    new_seri_stock = min(possible_seris) if possible_seris else 0
            
            product.stock = new_seri_stock
            consume_legacy_shelf_stock(product, sizes_breakdown_for_sale_record)
            
            # 3. Kritik Stok Bildirimi (Min Stock Kontrolü)
            if product.min_stock is not None and product.min_stock > 0:
                if product.stock <= product.min_stock:
                    authorized_users = User.query.filter_by(company=company_id, can_edit_product=True).all()
                    notif_title = "⚠️ Kritik Stok Uyarısı"
                    notif_msg = (f"Dikkat! '{product.name}' ({product.code}) ürününün stoğu kritik seviyenin altına düştü.\n"
                                 f"Mevcut Seri Stoğu: {product.stock}\n"
                                 f"Kritik Eşik: {product.min_stock}")
                    
                    for target_user in authorized_users:
                        # Queue inside this sale's transaction. A notification
                        # helper that commits here would make rollback partial.
                        db.session.add(Notification(
                            user_id=target_user.id, company_id=company_id,
                            title=notif_title, message=notif_msg, type='warning',
                        ))

            # 4. Satış Kaydını Oluştur
            note_suffix = " (Parça Satış)" if is_single_sale else ""
            final_note = (sale_notes or "") + note_suffix

            new_sale = Sale(
                sale_group_id=sale_group_id, 
                product_id=product.id, 
                product_code=product.code,
                user_id=user.id, 
                company_id=company_id, 
                quantity=sale_quantity_record, # Seri adedi (Tekli satışta 0)
                price_at_sale=sale_unit_price, 
                discount=discount,
                total_price=current_item_total_price, 
                currency=currency, 
                customer_name=customer_name, 
                sale_notes=final_note.strip(),
                sizes=sizes_breakdown_for_sale_record
            )
            db.session.add(new_sale)

        # --- BORÇ İŞLEMLERİ (DEĞİŞMEDİ) ---
        if is_debt and customer_name:
            customer = Customer.query.filter_by(name=customer_name, company_id=company_id).first()
            if not customer:
                raise ValueError("Borç eklenecek müşteri bulunamadı.")
            
            if len(total_sale_price_by_currency) == 1:
                currency, total_price = next(iter(total_sale_price_by_currency.items()))
                debt_to_add = total_price - amount_paid

                if debt_to_add > 0:
                    if customer.debt is None: customer.debt = {}
                    current_debt = customer.debt.get(currency, 0)
                    customer.debt[currency] = current_debt + debt_to_add
                    flag_modified(customer, "debt")

                    debt_record = DebtTransaction(
                        customer_id=customer.id,
                        user_id=user.id,
                        company_id=company_id,
                        sale_group_id=sale_group_id,
                        transaction_type=TransactionType.DEBIT,
                        amount=debt_to_add,
                        currency=currency,
                        note=f"Veresiye Satış - İşlem ID: {sale_group_id[:8]}"
                    )
                    db.session.add(debt_record)
            else:
                db.session.rollback()
                return jsonify({"status": "error", "message": "Borç işlemi farklı para birimlerini aynı anda desteklemiyor."}), 400

        db.session.commit()

        return jsonify({
            "status": "ok",
            "message": "Satış başarıyla kaydedildi!",
            "sale_group_id": sale_group_id
        }), 201
        
    except ValueError as ve:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(ve)}), 400

    except Exception as e:
        db.session.rollback()
        print(f"Satış sırasında hata: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": "Sunucu hatası, satış kaydedilemedi."}), 500

@app.route('/login', methods=['POST','GET'])
def login():       
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # GÜNCELLEME: is_valid_user artık hash'lenmiş şifreyi kontrol ediyor.
        if username and password and is_valid_user(username, password):
            user = User.query.filter_by(username=username).first()
            if begin_login_challenge(user):
                db.session.commit()
                return redirect(url_for('briva_auth.login_challenge'))
            establish_session(user)
            db.session.commit()
            return redirect(url_for('workbench'))
        else:
            logf(f"Başarısız giriş.")
            flash('Kullanıcı adı veya şifre hatalı.', 'error')
            return render_template('login.html'), 401
    return render_template('login.html')

@app.route('/create_order', methods=['POST'])
def create_order_route():
    data = request.get_json()
    company_id = session.get('company')
    user = current_user()
    
    if not user or not user.can_make_sale:
        return jsonify({"status": "error", "message": "Sipariş oluşturma yetkiniz yok."}), 403

    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Sipariş oluşturma yetkiniz yok."}), 403

    try:
        # Frontend'den gelen veriler
        items = data.get('items')
        customer_name = data.get('customerName')
        notes = data.get('notes')
        is_debt = data.get('is_debt', False)
        
        # Sayısal değerlerin güvenli dönüşümü
        try:
            amount_paid = float(data.get('amount_paid', 0.0))
        except:
            amount_paid = 0.0
        
        if not items:
            return jsonify({"status": "error", "message": "Sepet boş olamaz."}), 400

        # Veritabanına kaydet
        order_id = create_order_db(company_id, user.id, items, customer_name, notes, is_debt, amount_paid)
        
        return jsonify({
            "status": "ok", 
            "message": "Sipariş oluşturuldu ve onaya gönderildi.",
            "order_id": order_id
        }), 201

    except Exception as e:
        # HATAYI TERMİNALE YAZDIR
        import traceback
        traceback.print_exc() 
        print(f"KRİTİK HATA: {e}")
        return jsonify({"status": "error", "message": f"Sunucu hatası: {str(e)}"}), 500

@app.route('/get_orders', methods=['GET'])
def get_orders_route():
    """Bekleyen siparişleri listeler."""
    company_id = session.get('company')
    user = current_user()
    if not company_id: return jsonify({"status": "error"}), 403

    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Siparişleri görüntüleme yetkiniz yok."}), 403
    
    orders = get_pending_orders(company_id)
    
    orders_list = []
    for o in orders:
        creator = db.session.get(User, o.user_id)
        orders_list.append({
            "id": o.id,
            "creator": creator.username if creator else "Bilinmiyor",
            "customer": o.customer_name or "-",
            "total": o.total_amount,
            "currency": o.currency,
            "date": o.created_at.strftime('%d.%m.%Y %H:%M'),
            "items": o.items,
            "note": o.note
        })
    
    return jsonify(orders_list), 200

@app.route('/manage_order', methods=['POST'])
def manage_order_route():
    """Siparişi onaylar veya reddeder."""
    user = current_user()
    # Bu işlemi sadece yetkili (örn: manager veya depo sorumlusu) yapabilir
    # İzin kontrolü ekleyelim, örneğin 'can_manage_employees' veya yeni bir izin.
    if not user:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403
        
    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Sipariş yönetme yetkiniz yok."}), 403

    data = request.get_json()
    order_id = data.get('order_id')
    action = data.get('action') # 'approve' veya 'reject'
    
    success, message = process_order_approval(order_id, user.id, action)
    
    if success:
        return jsonify({"status": "ok", "message": message}), 200
    else:
        return jsonify({"status": "error", "message": message}), 400

@app.route("/generate_order_pdf/<int:order_id>", methods=["GET"])
def generate_order_pdf(order_id):
    try:
        user = current_user()
        if not user:
            return "Yetkisiz işlem.", 403

        company_id = session.get('company')
        order = Order.query.filter_by(id=order_id, company_id=company_id).first()
        
        if not order:
            return "Sipariş bulunamadı.", 404
        
        # --- PDF SINIFI ---
        class ModernPDF(FPDF):
            def header(self):
                self.add_font('DejaVu', '', FONT_PATH_REGULAR, uni=True)
                self.add_font('DejaVu', 'B', FONT_PATH_BOLD, uni=True)
                
                # 1. Üst Bilgi (Logo/İsim ve Fiş No)
                self.set_font('DejaVu', 'B', 16)
                self.set_text_color(30, 30, 30) 
                self.cell(0, 10, f'{company_id.upper()}', ln=1, align='L')
                
                self.set_font('DejaVu', '', 8)
                self.set_text_color(100, 100, 100) 
                # LOD raporunda "İşlem ID", burada "Sipariş No" veya "İşlem ID" yazabiliriz.
                # Ekran görüntüsüne sadık kalmak için İşlem ID formatında yazıyoruz:
                self.cell(0, 5, f'İşlem ID: #{order.id}', ln=1, align='L')
                
                # Ayırıcı Çizgi
                self.set_draw_color(200, 200, 200)
                self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('DejaVu', '', 8)
                self.set_text_color(150, 150, 150)
                self.cell(0, 10, f'Sayfa {self.page_no()}', align='R')

        # PDF Başlat
        pdf = ModernPDF(orientation='P', unit='mm', format='A4')
        pdf.add_page()
        
        # --- MÜŞTERİ VE TARİH BİLGİSİ ---
        creator = db.session.get(User, order.user_id)
        seller_name = creator.username if creator else "-"
        customer_name = order.customer_name or "Misafir"
        order_date = order.created_at.strftime('%d.%m.%Y %H:%M')

        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(50, 50, 50)
        
        # Sol Taraf: Müşteri
        pdf.cell(95, 6, f"Müşteri: {customer_name}", ln=0)
        # Sağ Taraf: Tarih
        pdf.cell(95, 6, f"Tarih: {order_date}", ln=1, align='R')
        
        pdf.cell(95, 6, f"Satış Temsilcisi: {seller_name}", ln=1)
        
        pdf.ln(5)

        # --- TABLO BAŞLIKLARI ---
        # Sütun Genişlikleri (Toplam ~190mm)
        # Ekran görüntüsündeki yapı: Kod | Ad & Renk | Raf | Beden | Tutar
        w_code = 30
        w_name = 60 
        w_shelf = 20
        w_detail = 50 
        w_price = 30 
        
        pdf.set_fill_color(245, 245, 245) 
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('DejaVu', 'B', 8)
        
        # Başlık Satırı
        pdf.cell(w_code, 10, "ÜRÜN KODU", 0, 0, 'L', True)
        pdf.cell(w_name, 10, "ÜRÜN ADI & RENK", 0, 0, 'L', True)
        pdf.cell(w_shelf, 10, "RAF", 0, 0, 'C', True)
        pdf.cell(w_detail, 10, "BEDEN / SERİ İÇERİĞİ", 0, 0, 'L', True)
        pdf.cell(w_price, 10, "TUTAR", 0, 1, 'R', True)
        
        # --- TABLO İÇERİĞİ ---
        pdf.set_font('DejaVu', '', 8)
        pdf.set_text_color(50, 50, 50)
        
        # JSON verisini çöz
        items = order.items
        if isinstance(items, str):
            import json
            items = json.loads(items)

        grand_total = 0
        currency_symbol = order.currency

        for item in items:
            # 1. Ürün Verilerini Veritabanından Çek (Güncel Raf ve Renk için)
            product_code = item.get('code')
            product = Product.query.filter_by(code=product_code, company_id=company_id).first()
            
            # A) İsim ve Renk
            product_name = item.get('name', '-')
            color_name = "-"
            
            if product:
                # Rengi veritabanından bul
                c_obj = Color.query.filter_by(code=product.color_id, company_id=company_id).first()
                if c_obj: color_name = c_obj.name
                elif item.get('color'): color_name = item.get('color')
            else:
                if item.get('color'): color_name = item.get('color')

            # B) Raf Bilgisi (Veritabanından)
            # Ekran görüntüsündeki gibi virgülle ayrılmış: "10, 47"
            shelf_code = "-"
            if product and product.shelves:
                # product.shelves bir liste: [{'code': '10', ...}, {'code': '47', ...}]
                codes = [s.get('code', '') for s in product.shelves if s.get('code')]
                if codes:
                    shelf_code = ", ".join(codes)
            
            # C) Fiyat ve Adet
            unit_price = float(item.get('price', 0))
            quantity = int(item.get('quantity', 0))
            discount = float(item.get('discount', 0))
            
            # Tutar Hesabı
            if item.get('is_single'):
                row_total = unit_price - discount
            else:
                row_total = (unit_price * quantity) - discount
            
            grand_total += row_total

            # D) Beden Detayı Formatı (Ekran görüntüsüne birebir uygun)
            # Örn: "[1 ADET] 1:1 | 2:2 | 3:1"
            final_detail = ""
            if item.get('is_single'):
                # Tekli Satış
                single_sizes = item.get('single_sizes', {})
                
                # Toplam parça sayısı
                total_pieces = sum(int(v) for v in single_sizes.values())
                
                # Bedenleri birleştir: "36:1 | 38:2"
                size_parts = []
                for k, v in single_sizes.items():
                    if int(v) > 0:
                        size_parts.append(f"{k}:{v}")
                size_str = " | ".join(size_parts)
                
                final_detail = f"[{total_pieces} ADET] {size_str}"
            else:
                # Seri Satış
                # Seri içeriğini bulmaya çalışalım (Opsiyonel, veri tabanından)
                series_content = ""
                if product and product.sizes:
                     # Seri içeriği: 36:1 | 38:2 gibi
                     s_parts = []
                     for s in product.sizes:
                         if s.get('number', 0) > 0:
                             s_parts.append(f"{s['value']}:{s['number']}")
                     series_content = " | ".join(s_parts)
                
                final_detail = f"[{quantity} SERİ] {series_content}"

            # --- SATIR YAZDIRMA ---
            h = 8 # Satır yüksekliği
            
            # Ürün Kodu
            pdf.cell(w_code, h, str(product_code), "B", 0, 'L')
            
            # İsim ve Renk (Örn: "Deneme (-)")
            prod_full = f"{product_name} ({color_name})"
            # İsim çok uzunsa sığdırmak için kes
            if len(prod_full) > 35: prod_full = prod_full[:32] + "..."
            pdf.cell(w_name, h, prod_full, "B", 0, 'L')
            
            # Raf
            pdf.cell(w_shelf, h, shelf_code, "B", 0, 'C')
            
            # Detay (Font küçült)
            pdf.set_font_size(7)
            pdf.cell(w_detail, h, final_detail, "B", 0, 'L')
            pdf.set_font_size(8)
            
            # Tutar
            pdf.cell(w_price, h, f"{row_total:.2f} {currency_symbol}", "B", 1, 'R')

        pdf.ln(5)

        # --- TOPLAM ALANI ---
        pdf.set_font('DejaVu', 'B', 10)
        pdf.set_text_color(0, 0, 0)
        
        # Hizalama için offset
        offset = w_code + w_name + w_shelf + w_detail
        
        pdf.cell(offset, 10, "GENEL TOPLAM:", 0, 0, 'R')
        
        # Tutarın arkası gri (Screenshot ile aynı)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(w_price, 10, f"{order.total_amount:.2f} {currency_symbol}", 0, 1, 'R', True)
        
        # --- AÇIKLAMA / NOT KUTUSU (LOD Tasarımıyla Aynı) ---
        note_content = order.note if order.note else "Not girilmemiştir."
        
        pdf.ln(10) # Boşluk

        # Kutunun rengi (İçi Beyaz, Çerçeve Siyah)
        pdf.set_fill_color(255, 255, 255)   
        pdf.set_draw_color(0, 0, 0)         

        # Başlık Fontu (Bold)
        pdf.set_font('DejaVu', 'B', 9)
        pdf.set_text_color(0, 0, 0)         
        
        x_pos = pdf.get_x()
        
        # 1. Başlık Satırı ("NOT" yazısı ve Üst/Yan çizgiler)
        # 'LTR' -> Left, Top, Right border
        pdf.cell(190, 8, "  NOT", "LTR", 1, 'L', True) 
        
        # 2. İçerik Satırı
        pdf.set_font('DejaVu', '', 9)
        pdf.set_text_color(50, 50, 50)
        
        pdf.set_x(x_pos) 
        # MultiCell ile notu yazdır. 'LBR' -> Left, Bottom, Right border (Kutuyu kapatır)
        # Notun başına biraz boşluk bırakıyoruz
        pdf.multi_cell(190, 8, f"  {note_content}\n ", border='LBR', align='L', fill=True)

        # --- ÇIKTI ---
        buffer = io.BytesIO(pdf.output())
        buffer.seek(0)
        filename = f"Siparis_Fisi_{order.id}.pdf"
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"PDF Hatası: {e}")
        print(traceback.format_exc())
        return f"PDF oluşturulamadı: {e}", 500
    
@app.route('/get_orders_unified', methods=['GET'])
def get_orders_unified_route():
    company_id = session.get('company')
    user = current_user()
    
    if not company_id: return jsonify({"status": "error", "message": "Giriş yapmalısınız"}), 403
    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Yetkisiz işlem."}), 403

    # Parametreleri al
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'ALL')
    customer_filter = request.args.get('customer', 'ALL')
    date_filter = request.args.get('date', 'ALL')

    # DB Utils çağır
    paginated_orders = get_orders_unified_db(
        company_id, 
        page=page, 
        per_page=15, 
        status_filter=status_filter, 
        customer_filter=customer_filter, 
        date_filter=date_filter
    )

    orders_list = []
    for o in paginated_orders.items:
        creator = db.session.get(User, o.user_id)
        
        # Enum değerini string olarak al (PENDING, APPROVED vs.)
        status_str = o.status.value if hasattr(o.status, 'value') else str(o.status)
        
        orders_list.append({
            "id": o.id,
            "creator": creator.username if creator else "Bilinmiyor",
            "customer": o.customer_name or "-",
            "total": o.total_amount,
            "currency": o.currency,
            "date": o.created_at.strftime('%d.%m.%Y %H:%M'),
            "status": status_str,
            "note": o.note,
            "items": o.items # Detay görünümü için gerekli
        })

    return jsonify({
        "orders": orders_list,
        "total_pages": paginated_orders.pages,
        "current_page": paginated_orders.page,
        "total_items": paginated_orders.total
    }), 200

@app.route("/all_sales", methods=["GET"])
def get_all_sales_paginated():
    user = current_user()
    if not user or not user.can_view_sales_history:
        return jsonify({"status": "error", "message": "Satış geçmişini görme yetkiniz yok."}), 403

    company_id = session.get('company')
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', None)
    start_date_str = request.args.get('start_date', None)
    end_date_str = request.args.get('end_date', None)
    customer_filter = request.args.get('customer', None)

    # YENİ FONKSİYONU ÇAĞIR
    paginated_groups, sales_groups_data = get_paginated_sales_groups(
        company_id,
        page=page,
        per_page=10, # Sayfa başına 10 işlem gösterelim
        search_term=search_term,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        customer_name=customer_filter
    )

    return jsonify({
        "sales_groups": sales_groups_data, # İsim "sales" yerine "sales_groups" oldu
        "total_pages": paginated_groups.pages,
        "current_page": paginated_groups.page,
        "has_next": paginated_groups.has_next,
        "has_prev": paginated_groups.has_prev
    }), 200



@app.route("/get_sales_statistics", methods=["GET"])
def get_sales_statistics():
    user = current_user()
    if not user or not user.can_view_statistics:
        print(user.can_view_statistics)
        return jsonify({"status": "error", "message": "İstatistikleri görme yetkiniz yok."}), 403


    company_id = session.get('company')
    if not company_id:
        return jsonify({"error": "Şirket bilgisi bulunamadı"}), 400

    stats = get_company_sales_stats(company_id)
    return jsonify(stats), 200

# YENİ: Grafik verilerini sunan API rotası
@app.route("/get_daily_sales_trend", methods=["GET"])
def get_daily_sales_trend_route():
    user = current_user()
    if not user or not user.can_view_statistics:
        return jsonify({"status": "error", "message": "Satış geçmişini görme yetkiniz yok."}), 403

    company_id = session.get('company')
    if not company_id:
        return jsonify({"error": "Şirket bilgisi bulunamadı"}), 400

    # URL'den filtreyi al (Örn: ?filter=weekly), yoksa 'monthly' kullan
    filter_type = request.args.get('filter', 'monthly')

    trend_data = get_daily_sales_trend(company_id, filter_type)
    return jsonify(trend_data), 200

@app.route('/home', methods=['GET', 'POST'])
def home():
    return redirect(url_for('main'))
    if 'username' not in session:
        return redirect(url_for('main'))
    return render_template(
        'main',
        username=session.get('username'),
        telephone=session.get('telephone'),
        company=session.get('company'),
        role=session.get('role')
    )

@app.route('/admin')
def admin():
    # GÜVENLİK: Sadece admin rolündeki kullanıcıların bu sayfaya erişebilmesini sağla
    if session.get('role') != 'admin':
        return redirect(url_for('home'))
    logf(f"Kullanıcı ID {session.get('user')} yönetim panelini açtı.")
    return render_template(
        'admin.html',
        username=session.get('username'),
        telephone=session.get('telephone'),
        company=session.get('company'),
        role=session.get('role')
    )
@app.route('/update_user/<int:user_id>', methods=['POST'])
def update_user_route(user_id):
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Eksik veri"}), 400

    target = User.query.filter_by(id=user_id, company=current_user().company).first()
    if target is None:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı."}), 404
    try:
        clean = validate_employee_payload(data, target=target, creating=False)
    except SecurityValidationError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    username, password, role, phone, company = (
        clean['username'], clean['password'], clean['role'], clean['phone'], clean['company']
    )
    logf(f"Kullanıcı ID {session.get('user')} çalışan ID {user_id} bilgilerini güncelledi.")
    existing_user = User.query.filter(User.username == username, User.id != user_id).first()
    if existing_user:
        return jsonify({"status": "error", "msg": "Bu kullanıcı adı zaten başka bir kullanıcı tarafından kullanılıyor!"}), 409

    # Bu çağrı artık kesin olarak db_utils.py'deki doğru fonksiyonu kullanacak
    if change_user_data_db(user_id=user_id, username=username, password=password, company=company, role=role, phone=phone):
        return jsonify({"status": "ok", "message": "Kullanıcı başarıyla güncellendi."}), 200
    else:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı veya güncelleme sırasında bir sunucu hatası oluştu."}), 500

@app.route("/manager")
def manager():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"Kullanıcı ID {session.get('user')} çalışan panelini açtı.")
    return render_template(
        'manager.html',
        username=session.get('username'),
        telephone=session.get('telephone'),
        company=session.get('company'),
        role=session.get('role')
    )

@app.route('/add_manager', methods=['POST'])
def add_manager():
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "msg": "Yetkisiz işlem!"}), 403
        
    data = request.get_json()
    if not data or 'name' not in data or 'password' not in data:
        return jsonify({"status": "error", "msg": "Eksik alan!"}), 400

    try:
        clean = validate_employee_payload(dict(data, role=data.get('role', 'manager')))
    except SecurityValidationError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    username, password, company, phone, role = (
        clean['username'], clean['password'], clean['company'], clean['phone'], clean['role']
    )
    logf(f"Kullanıcı ID {session.get('user')} yeni müdür hesabı oluşturdu.")
    if user_exists(username):
        return jsonify({"status": "error", "msg": "Bu kullanıcı adı zaten mevcut!"}), 409

    add_user(username, password, phone, company, role)
    
    new_user = User.query.filter_by(username=username).first()
    return jsonify({
        "status": "ok", 
        "user": {
            "id": new_user.id,
            "name": username,
            "username": username,
            "role": role,
            "company": company,
            "phone": phone
        }
    }), 201

@app.route('/addcolor_route', methods=['POST', 'GET'])
def addcolor_route():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"renk ekle")
    return render_template('addcolor.html')


@app.route('/add_user_py', methods=['POST'])
def add_user_py():
    user = current_user()
    if not user or not user.can_manage_employees:
        return jsonify({"status": "error", "message": "Çalışan eklme yetkiniz yok."}), 403
        
    data = request.get_json()
    if not data or 'name' not in data or 'password' not in data or 'role' not in data:
        return jsonify({"status": "error", "msg": "Eksik alan!"}), 400
    
    try:
        clean = validate_employee_payload(data)
    except SecurityValidationError as error:
        return jsonify({"status": "error", "message": str(error)}), 400
    username, password, company, role = (
        clean['username'], clean['password'], clean['company'], clean['role']
    )
    logf(f"Kullanıcı ID {session.get('user')} çalışan hesabı oluşturdu.")
    if user_exists(username):
        return jsonify({"status": "error", "msg": "Bu kullanıcı adı zaten mevcut!"}), 409 # 409 Conflict

    add_user(username, password, None, company, role)
    
    # DÜZELTİLDİ: Frontend'in beklediği gibi bir user objesi döndürülüyor.
    new_user = User.query.filter_by(username=username).first()
    return jsonify({
        "status": "ok", 
        "user": {
            "id": new_user.id,
            "name": username, 
            "username": username, # Frontend uyumluluğu için eklendi
            "role": role, 
            "company": company
        }
    }), 201 # 201 Created

# Diğer route'larınızda (add_company, delete_user vs.) bir değişiklik gerekmiyor.
# Onları olduğu gibi bırakabilirsiniz.

@app.route('/add_company', methods=['POST'])
def add_company():
    return jsonify({"status": "error", "message": "Bu kişisel kurulumda işletme oluşturma kapalı."}), 410
    # Archived implementation retained for reference; never reached.
    try:
        data = request.get_json()
        company_name = data.get('name')
        address = data.get('address')

        if not company_name or not address:
            return jsonify({"status": "error", "msg": "Lütfen tüm alanları doldurun!"})

        if Company.query.filter_by(company_name=company_name).first():
            return jsonify({"status": "error", "msg": "Bu şirket zaten kayıtlı!"})

        company = Company(company_name=company_name, company_adress=address)
        db.session.add(company)
        db.session.commit()
        logf(f"Yeni bayi eklendi {company}.")  
        # Geliştirme: Yeni eklenen şirketin ID'sini de döndürmek iyi bir pratik.
        return jsonify({
            "status": "ok",
            "company": {"id": company.id, "name": company_name, "address": address}
        }), 201
    except Exception as e:
        return jsonify({"status": "error", "msg": "Sunucu hatası!"})

@app.route('/get_user_permissions/<int:user_id>', methods=['GET'])
def get_user_permissions(user_id):
    """Bir kullanıcının mevcut izinlerini getirir."""
    user = current_user()
    from briva_security import has_permission
    if not user or not has_permission('can_manage_employees', user):
        return jsonify({"status": "error", "message": "Çalışan izinleri değiştirme yetkiniz yok."}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı"}), 404

    # Kullanıcının kendi şirketindeki birini sorguladığından emin ol
    # Adminler herkesi sorgulayabilir
    if session.get('role') == 'manager' and user.company != session.get('company'):
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    # Modeldeki boolean alanlarından bir sözlük oluştur
    permissions = {
        'can_make_sale': user.can_make_sale,
        'can_view_sales_history': user.can_view_sales_history,
        'can_view_statistics': user.can_view_statistics,
        'can_generate_reports': user.can_generate_reports,
        'can_add_product': user.can_add_product,
        'can_edit_product': user.can_edit_product,
        'can_delete_product': user.can_delete_product,
        'can_manage_employees': user.can_manage_employees,
        'can_manage_attributes': user.can_manage_attributes,
        'can_manage_customers': user.can_manage_customers,
        'can_manage_debts': user.can_manage_debts,
        'can_orders': user.can_orders
    }
    
    return jsonify(permissions), 200

@app.route('/update_user_permissions/<int:user_id>', methods=['POST'])
def update_user_permissions(user_id):
    """Bir kullanıcının izinlerini günceller."""
    user = current_user()
    if not user or not user.can_manage_employees:
        return jsonify({"status": "error", "message": "Çalışan izinleri değiştirme yetkiniz yok."}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı"}), 404
        
    # Kullanıcının kendi şirketindeki birini güncellediğinden emin ol
    if session.get('role') == 'manager' and user.company != session.get('company'):
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    new_permissions = request.get_json()
    if not isinstance(new_permissions, dict):
        return jsonify({"status": "error", "message": "Geçersiz veri formatı"}), 400
    try:
        new_permissions = validate_permissions_payload(new_permissions, target=user)
    except SecurityValidationError as error:
        return jsonify({"status": "error", "message": str(error)}), 400

    try:
        # Gelen sözlükten verileri alıp modeldeki alanları güncelle
        user.can_make_sale = new_permissions.get('can_make_sale', False)
        user.can_view_sales_history = new_permissions.get('can_view_sales_history', False)
        user.can_view_statistics = new_permissions.get('can_view_statistics', False)
        user.can_generate_reports = new_permissions.get('can_generate_reports', False)
        user.can_add_product = new_permissions.get('can_add_product', False)
        user.can_edit_product = new_permissions.get('can_edit_product', False)
        user.can_delete_product = new_permissions.get('can_delete_product', False)
        user.can_manage_employees = new_permissions.get('can_manage_employees', False)
        user.can_manage_attributes = new_permissions.get('can_manage_attributes', False)
        user.can_manage_customers = new_permissions.get('can_manage_customers', False)
        user.can_manage_debts = new_permissions.get('can_manage_debts', False)
        user.can_orders = new_permissions.get('can_orders', False)

        db.session.commit()
        logf(f"Kullanıcı ID {user_id} için izinler güncellendi.")
        return jsonify({"status": "ok", "message": "İzinler başarıyla güncellendi."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"İzin güncelleme hatası: {e}")
        return jsonify({"status": "error", "message": "Sunucu hatası oluştu."}), 500
    
@app.route('/deleteuser/<int:user_id>', methods=['DELETE'])
def delete_user_route(user_id):
    user = current_user()
    from briva_security import has_permission
    if not user or not has_permission('can_manage_employees', user):
        return jsonify({"status": "error", "message": "Çalışan silme yetkiniz yok."}), 403
    target = User.query.filter_by(id=user_id, company=user.company).first()
    if target is None:
        return jsonify(status='error', message='Kullanıcı bulunamadı.'), 404
    deactivate_user(user, target)
    db.session.commit()
    logf(f"Çalışan erişimi kapatıldı user_id: {user_id}.")
    return '', 204

@app.route('/deletecompany/<int:company_id>', methods=['DELETE'])
def delete_company(company_id):
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403
    if delete_company_db(company_id):
        return '', 204
    return jsonify({"status": "error", "message": "Şirket bulunamadı"}), 404

@app.route("/get_all_companys", methods=["GET"])
def get_all_companys():
    if 'username' not in session:
        return jsonify({"status": "error", "message": "Giriş yapmalısınız"}), 401
    user = current_user()
    return jsonify([{"id": user.company, "name": user.company}])


@app.route('/getallusers')
def get_all_users():
    if 'username' not in session:
        return jsonify({"status": "error", "message": "Giriş yapmalısınız"}), 401
    
    users = User.query.filter_by(company=current_user().company).all()
    result = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "company": u.company,
            "phone": u.phone,
            "active": is_user_enabled(u),
        }
        for u in users
    ]
    return jsonify(result)


@app.route("/get_employees", methods=["GET"])
def get_employees():
    # Kullanıcı session’dan alınabilir
    username = session.get("username")  # veya JWT/token kullanabilirsin
    user = current_user()
    if not user or not user.can_manage_employees:
        return jsonify({"status": "error", "message": "Çalışan listeleme yetkiniz yok."}), 403
    if not username:
        return jsonify({"error": "Kullanıcı oturumu bulunamadı"}), 400

    # DB’den kullanıcıları çek
    roles = ('cashier', 'employee', 'warehouse', 'manager') if user.role in {'admin', 'owner'} else ('cashier', 'employee', 'warehouse')
    employees = [{
        'id': employee.id, 'username': employee.username, 'role': employee.role,
        'phone': employee.phone, 'active': is_user_enabled(employee),
    } for employee in User.query.filter(User.company == user.company, User.role.in_(roles)).order_by(User.username).all()]
    
    return jsonify(employees), 200

@app.route('/addshelf_route', methods=['POST', 'GET'])
def addshelf_route():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"raf ekle")
    return render_template('addshelf.html')

@app.route('/add_shelf', methods=['POST'])
def add_shelf():
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Raf ekleme yetkiniz yok."}), 403
    data = request.get_json()
    company_id = session.get('company')
    if not company_id:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    code = data.get('code')

    if not code:
        return jsonify({"status": "error", "message": "Eksik veri: Kod zorunludur."}), 400
        
    if Shelf.query.filter_by(code=code, company_id=company_id).first():
        return jsonify({"status": "error", "message": "Bu isimde başka raf var"}), 409

    add_shelf_db(code, company_id)
    
    new_shelf = Shelf.query.filter_by(code=code, company_id=company_id).first()
    print(data)
    return jsonify({
        "status": "ok", 
        "message": "Raf eklendi", 
        "shelf": {
            "code": new_shelf.code,
        }
    }), 201

@app.route('/product', methods=['POST'])
def add_product_route():
    user = current_user()
    if not user or not user.can_add_product:
        return jsonify({"status": "error", "message": "Ürün ekleme yetkiniz yok."}), 403

    try:
        # FormData'dan verileri alıyoruz
        name = request.form.get('name')
        category_name = request.form.get('category')
        description = request.form.get('description')
        
        sizes_list = json.loads(request.form.get('sizes'))
        currency = json.loads(request.form.get('currency'))
        colors_to_add = json.loads(request.form.get('colors'))
        custom_attributes_str = request.form.get('custom_attributes')
        custom_attributes = json.loads(custom_attributes_str) if custom_attributes_str else {}

        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                compress_and_save(file, full_path)
                image_path = f"uploads/{filename}" 

        if not all([name, category_name, colors_to_add]):
            return jsonify({"status": "error", "message": "Eksik veri."}), 400

        created_products = []
        company_id = session.get('company')
        base_random_number = generate_unique_stock_id(company_id)

        for color_id in colors_to_add:
            # BARKOD MOTORUNA CUSTOM_ATTRIBUTES DEĞERLERİNİ GÖNDERİYORUZ
            stock_group_code, final_product_code = generate_custom_codes(
                company_id=company_id, 
                color_id=color_id, 
                custom_attributes=custom_attributes, 
                base_random_number=base_random_number,
                category=category_name
            )
            
            original_code = final_product_code
            counter = 1
            while Product.query.filter_by(code=final_product_code, company_id=company_id).first():
                final_product_code = f"{original_code}{counter}"
                counter += 1
            
            # ÜRÜNÜ KAYDEDERKEN CUSTOM_ATTRIBUTES DEĞERLERİNİ DE KAYDEDİYORUZ
            new_product = add_product(
                code=final_product_code, name=name, company_id=company_id, category=category_name,
                description=description, stock_id=stock_group_code, color_id=color_id, sizes=sizes_list, 
                currency=currency, image_path=image_path, custom_attributes=custom_attributes
            )
            if new_product:
                created_products.append(new_product.name)

        return jsonify({"status": "ok", "message": "Ürünler ve fotoğraf kaydedildi."}), 201

    except Exception as e:
        print(f"HATA: {e}")
        return jsonify({"status": "error", "message": f"Hata oluştu: {str(e)}"}), 500

@app.route('/api/regenerate_all_barcodes', methods=['POST'])
def api_regenerate_all_barcodes():
    user = current_user()
    # Sadece yetkililer yapabilsin
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Bu işlem için yetkiniz yok!"}), 403
        
    company_id = session.get('company')
    success, msg = regenerate_all_company_barcodes(company_id)
    
    if success:
        logf(f"{user.username} sistemdeki TÜM ürünlerin barkodlarını yeni şablona göre güncelledi.")
        return jsonify({"status": "ok", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 500

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logging.getLogger('briva').error('Unhandled application error class=%s endpoint=%s',
                                   type(error).__name__, request.endpoint)
    return jsonify(status='error', message='İşlem tamamlanamadı. Kayıt durumunu kontrol edip tekrar deneyin.'), 500

@app.route("/delete_customer/<int:customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    user = current_user()
    if not user or not user.can_manage_customers:
        return jsonify({"status": "error", "message": "Müşteri silme yetkiniz yok."}), 403
    try:
        customer = Customer.query.filter_by(id=customer_id).first()
        if not customer:
            return jsonify({"success": False, "message": "Müşteri bulunamadı"}), 404

        db.session.delete(customer)
        db.session.commit()
        return jsonify({"success": True, "message": "Müşteri silindi"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_all_customers', methods=['GET'])
def get_all_customers():
    user = current_user()
    if not user or (not user.can_manage_customers and not user.can_view_sales_history):
        return jsonify({"success": False, "message": "Yetkisiz işlem"}), 403
    if 'username' not in session:
        return jsonify(success=False, message="Giriş yapmalısınız"), 401
    
    company_id = session.get('company')
    if not company_id:
        return jsonify(success=False, message="Yetkisiz işlem"), 403

    customers = Customer.query.filter_by(company_id=company_id).all()
    
    result = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "address": c.address,
            "phone": c.phone,
            **({"debt": c.debt if c.debt else {}} if user.can_manage_debts or user.role in {'admin', 'owner'} else {})
        }
        for c in customers
    ]
    return jsonify(success=True, customers=result)


@app.route('/add_customer', methods=['POST'])
def add_customer():
    data = request.get_json()
    company_id = session.get('company')
    user = current_user()
    if not user or not user.can_manage_customers:
        return jsonify({"status": "error", "message": "Müşteri ekleme yetkiniz yok."}), 403
    if not company_id:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    name = data.get('name')
    description = data.get('description')
    address = data.get('address')
    phone = data.get('phone')
    logf(f"Yeni müşteri eklendi.")  

    if not name:
        return jsonify({"status": "error", "message": "Eksik veri: İsim zorunludur."}), 400

    if Customer.query.filter_by(name=name, company_id=company_id).first():
        return jsonify({"status": "error", "message": "Bu telefon numarasına sahip başka müşteri var"}), 409
    
    add_customer_db(name, company_id, description, address, phone)
    
    new_customer = Customer.query.filter_by(phone=phone, company_id=company_id).first()
    
    return jsonify({
        "status": "ok", 
        "message": "Müşteri eklendi", 
        "customer": {
            "id": new_customer.id,   # <--- BURAYI EKLEYİN (Sorunu çözen satır)
            "name": new_customer.name,
            "phone": new_customer.phone,
            "description": new_customer.description,
            "address": new_customer.address
        }
    }), 201

@app.route('/add_customer_route')
def add_customer_route():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"müşteri ekle")
    return render_template('addcustomer.html')

@app.route('/get_colors', methods=['GET'])
def get_colors():
    company_id = session.get('company')
    if not company_id:
        return jsonify({"success": False, "msg": "Şirket bilgisi bulunamadı"}), 403

    all_colors = Color.query.filter_by(company_id=company_id).all()
    colors_list = [{"id": c.id, "name": c.name, "code": c.code} for c in all_colors]

    return jsonify({"success": True, "colors": colors_list}), 200

@app.route('/get_shelves', methods=['GET'])
def get_shelves():
    company_id = session.get('company')
    if not company_id:
        return jsonify({"success": False, "msg": "Şirket bilgisi bulunamadı"}), 403

    all_shelves = Shelf.query.filter_by(company_id=company_id).all()
    shelf_list = [{"id": c.id, "code": c.code} for c in all_shelves]

    return jsonify({"success": True, "shelves": shelf_list}), 200

@app.route("/delete_color/<string:code>", methods=["DELETE"])
def delete_color(code):
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Renk silme yetkiniz yok."}), 403
    try:
        color = Color.query.filter_by(code=code, company_id=user.company).first()
        if not color:
            return jsonify({"success": False, "message": "Renk bulunamadı"}), 404

        db.session.delete(color)
        db.session.commit()
        return jsonify({"success": True, "message": "Renk silindi"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/delete_category/<int:id>", methods=["DELETE"])
def delete_category(id):
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Kategori silme yetkiniz yok."}), 403
    try:
        category = Category.query.filter_by(id=id, company_id=user.company).first()
        if not category:
            return jsonify({"success": False, "message": "Kategori bulunamadı"}), 404

        db.session.delete(category)
        db.session.commit()
        return jsonify({"success": True, "message": "Kategori silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/delete_shelf/<int:id>", methods=["DELETE"])
def delete_shelf(id):
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Raf silme yetkiniz yok."}), 403
    try:
        shelf = Shelf.query.filter_by(id=id, company_id=user.company).first()
        if not shelf:
            return jsonify({"success": False, "message": "Raf bulunamadı"}), 404

        db.session.delete(shelf)
        db.session.commit()
        return jsonify({"success": True, "message": "Raf silindi"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/add_color', methods=['POST'])
def add_color():
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Renk ekleme yetkiniz yok."}), 403
    
    data = request.get_json()
    name = data.get('color')
    code = data.get('code') # Frontend'den gelen kod

    if not name or not code:
        return jsonify({"status": "error", "message": "Renk adı ve kodu zorunludur"}), 400

    success = add_color_db(name=name, code=code, company_id=session.get('company'))
    
    if success:
        return jsonify({"status": "ok"}), 200
    else:
        return jsonify({"status": "error", "message": "Bu isimde veya kodda bir renk zaten var."}), 409

@app.route('/query_product', methods=['GET'])
def query_product():
    # Bu fonksiyon ürün okutulduğunda çağrılır.
    code = request.args.get('code')
    company_id = session.get('company')
    
    logf(f"Ürün sorgulandı: Ürün kodu: {code}, Bayi: {company_id}.")  
    
    if not code or not company_id:
        return jsonify({"status": "error", "message": "Eksik veya geçersiz istek"}), 400

    product = Product.query.filter_by(code=code, company_id=company_id).first()
    
    if product:
        color = get_color_name(code=product.color_id, company_id=company_id)
        
        sizes_with_composition = []
        if product.sizes:
            for size_info in product.sizes:
                sizes_with_composition.append({
                    "value": size_info.get("value"),
                    "stock": size_info.get("stock", 0),
                    "number": size_info.get("number", 0)
                })

        variants = []
        if product.stock_id:
            siblings = Product.query.filter_by(stock_id=product.stock_id, company_id=company_id).all()
            for p in siblings:
                c_name = get_color_name(p.color_id, company_id)
                variants.append({
                    "code": p.code,
                    "color": c_name or "Bilinmiyor",
                    "stock": p.stock,
                    "is_current": (p.code == product.code)
                })

        # Son Satış Bilgisi
        last_sale_info = None
        user = current_user()
        last_sale = (Sale.query.filter_by(product_code=code, company_id=company_id)
                     .order_by(Sale.sale_date.desc()).first()
                     if user.can_view_sales_history or user.role in {'admin', 'owner'} else None)
        
        if last_sale:
            seller = User.query.get(last_sale.user_id)
            seller_name = seller.username if seller else "Bilinmiyor"
            last_sale_info = {
                "date": last_sale.sale_date.strftime('%d.%m.%Y %H:%M'),
                "customer": last_sale.customer_name or "Misafir Müşteri",
                "quantity": last_sale.quantity,
                "price": last_sale.total_price,
                "currency": last_sale.currency,
                "seller": seller_name
            }

        product_data = {
            "id": product.id,
            "code": product.code,
            "name": product.name,
            "currency": product.currency,
            "sizes": sizes_with_composition,
            "stock": product.stock,
            "color": color,
            "category": product.category,
            "shelves": product.shelves, 
            "description": product.description,
            "image_path": product.image_path,
            "variants": variants,
            "last_sale": last_sale_info
        }
        return jsonify(product_data), 200
    else:
        return jsonify({"status": "error", "message": "Ürün bulunamadı"}), 404

@app.route('/api/upload_excel_step2', methods=['POST'])
def upload_excel_step2():
    """
    ANALİZ ADIMI: Verileri tarar, eksik/eşleşmeyenleri bulur ve döner.
    """
    data = request.json
    filename = data.get('filename')
    col_mapping = data.get('mapping')
    company_id = session.get('company')
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "Dosya bulunamadı."}), 400
        
    # db_utils'deki analiz fonksiyonunu çağır
    analysis_result = analyze_excel_data(file_path, col_mapping, company_id)
    
    # Eğer hiç eksik yoksa frontend'e "direct_save" sinyali gönderebiliriz
    # Ama akış bozulmasın diye boş liste döneriz, frontend anlar.
    
    return jsonify({"status": "ok", "analysis": analysis_result})

@app.route('/api/upload_excel_step3', methods=['POST'])
def upload_excel_step3():
    """
    KAYIT ADIMI: Hem sütun eşleşmesini hem de değer eşleşmesini alır ve kaydeder.
    """
    data = request.json
    filename = data.get('filename')
    col_mapping = data.get('col_mapping')
    val_mapping = data.get('val_mapping') # {'categories': {'Blue':'Mavi'}, ...}
    company_id = session.get('company')
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "Dosya bulunamadı."}), 400
        
    result = process_excel_import_final(file_path, col_mapping, val_mapping, company_id)
    
    # İşlem bitti, dosyayı temizle
    try: os.remove(file_path)
    except: pass
    
    return jsonify(result)

@app.route('/export_products')
def export_products():
    company_id = session.get('company')
    if not company_id: return "Giriş yapmalısınız", 403

    try:
        # 1. Tüm Ürünleri Çek
        products = Product.query.filter_by(company_id=company_id).all()
        
        # Renk İsimlerini Eşleştirmek için Sözlük (Performans için)
        colors_map = {c.code: c.name for c in Color.query.filter_by(company_id=company_id).all()}
        
        data_list = []
        
        for p in products:
            # A) Fiyatları String'e Çevir
            # Örn: {"TL": 100, "USD": 10} -> "100.0 TL, 10.0 USD"
            prices_str = ""
            if p.currency:
                prices_list = [f"{val} {key}" for key, val in p.currency.items() if val > 0]
                prices_str = ", ".join(prices_list)
            
            # B) Bedenleri ve Seriyi String'e Çevir
            # Stok: "S:10, M:20"
            # Seri: "S:1, M:2"
            stock_str_list = []
            series_str_list = []
            
            if p.sizes:
                for s in p.sizes:
                    if s.get('stock', 0) > 0:
                        stock_str_list.append(f"{s['value']}:{s['stock']}")
                    if s.get('number', 0) > 0:
                        series_str_list.append(f"{s['value']}:{s['number']}")
            
            # C) Renk İsmini Bul
            color_name = colors_map.get(p.color_id, p.color_id) # Bulamazsa kodu yaz

            shelves_str = "Raf Yok"
            if p.shelves:
                shelves_list = [f"{s['code']} ({s['quantity']})" for s in p.shelves]
                shelves_str = ", ".join(shelves_list)

            # D) Listeye Ekle
            data_list.append({
                "Urun Kodu": p.code,
                "Urun Adi": p.name,
                "Kategori": p.category,
                "Raflar": shelves_str,
                "Renk": color_name,
                "Aciklama": p.description,
                "Fiyatlar": prices_str,
                "Toplam Stok (Seri)": p.stock,
                "Beden Stoklari": ", ".join(stock_str_list),
                "Seri Icerigi": ", ".join(series_str_list),
                "Min Stok Uyarisi": p.min_stock
            })

        # 2. DataFrame Oluştur
        df = pd.DataFrame(data_list)
        
        # 3. Excel'e Yaz
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Tum Stoklar')
            
            # Sütun Genişliklerini Ayarla
            worksheet = writer.sheets['Tum Stoklar']
            for idx, col in enumerate(df.columns):
                # Başlık uzunluğu ile en uzun veri uzunluğunu kıyasla
                max_len = max(
                    df[col].astype(str).map(len).max() if not df.empty else 0,
                    len(col)
                ) + 2
                # Çok geniş olmasını engelle (Max 50 karakter)
                if max_len > 50: max_len = 50
                worksheet.column_dimensions[chr(65 + idx)].width = max_len

        output.seek(0)
        
        filename = f"Stok_Listesi_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"Export Hatası: {e}")
        import traceback
        traceback.print_exc()
        return f"Hata oluştu: {str(e)}", 500

@app.route('/add_stock_to_shelf', methods=['POST'])
def add_stock_to_shelf_route():
    data = request.get_json()
    company_id = session.get('company')
    user = current_user()
    
    if not user or not user.can_edit_product:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    p_code = data.get('product_code')
    s_code = data.get('shelf_code')
    
    # quantities sözlük olarak geliyor { 'S': 5, 'M': 2 }
    quantities = data.get('quantities') 

    if not all([p_code, s_code, quantities]):
        return jsonify({"status": "error", "message": "Eksik bilgi."}), 400

    result = add_stock_to_shelf_db(p_code, s_code, quantities, company_id)
    
    if result['status'] == 'ok':
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route('/get_picking_list/<int:order_id>', methods=['GET'])
def get_picking_list_route(order_id):
    company_id = session.get('company')
    user = current_user()
    
    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    result = calculate_picking_list(order_id, company_id)
    return jsonify(result)

@app.route('/download_sample_excel')
def download_sample_excel():
    # Sistemdeki verileri çekerek dinamik şablon oluştur
    company_id = session.get('company')
    if not company_id: return "Giriş yapmalısınız", 403

    # Geçerli kategori ve rafları çekelim ki kullanıcı ne yazacağını bilsin
    categories = [c.name for c in Category.query.filter_by(company_id=company_id).all()]
    shelves = [s.code for s in Shelf.query.filter_by(company_id=company_id).all()]
    colors = [c.name for c in Color.query.filter_by(company_id=company_id).all()]

    # Örnek veriyi oluştur
    data = {
        "Urun Adi": ["Ornek Urun 1", "Ornek Urun 2"],
        "Kategori": [categories[0] if categories else "Kategori Yok", categories[0] if categories else ""],
        "Raf": [shelves[0] if shelves else "Raf Yok", shelves[0] if shelves else ""],
        "Renk": [colors[0] if colors else "Renk Yok", colors[0] if colors else ""],
        "Aciklama": ["%100 Pamuk", "Detaylı açıklama"],
        "Fiyatlar": ["100 TL, 10 USD", "250 TL, 20 EURO"],
        "Beden Stoklari": ["S:10, M:20", "36:5, 38:8, 40:10"],
        "Seri Icerigi": ["S:1, M:2", "36:1, 38:2, 40:1"]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Urunler')
        
        # Sütun genişliklerini ayarla
        worksheet = writer.sheets['Urunler']
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = max_len

    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='stok_yukleme_sablonu.xlsx')

@app.route('/api/upload_excel_step1', methods=['POST'])
def upload_excel_step1():
    if 'file' not in request.files:
        return jsonify({"error": "Dosya yok"}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"error": "Sadece Excel dosyaları yüklenebilir."}), 400

    filename = secure_filename(f"import_{uuid.uuid4()}.xlsx")
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)
    
    try:
        # Sadece başlıkları oku
        df = pd.read_excel(path, nrows=0)
        columns = df.columns.tolist()
        
        return jsonify({
            "status": "ok",
            "filename": filename,
            "excel_columns": columns,
            # Veritabanı Alanları (Kullanıcı bunları eşleyecek)
            "db_fields": [
                {"key": "db_name", "label": "Ürün Adı", "required": True},
                {"key": "db_category", "label": "Kategori (Sistemle Eşleşmeli)", "required": True},
                {"key": "db_shelf", "label": "Raf (Sistemle Eşleşmeli)", "required": True},
                {"key": "db_color", "label": "Renk (Sistemle Eşleşmeli)", "required": True},
                {"key": "db_price", "label": "Fiyatlar (Örn: 100 TL)", "required": True},
                {"key": "db_stock", "label": "Beden Stok (Örn: S:10)", "required": True},
                {"key": "db_series", "label": "Seri İçeriği (Örn: S:1)", "required": False},
                {"key": "db_desc", "label": "Açıklama", "required": False},
            ]
        })
    except Exception as e:
        return jsonify({"error": f"Dosya okunamadı: {str(e)}"}), 500

@app.route('/addcategory_route', methods=['POST', 'GET'])
def addcategory_route():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"kategori ekle")
    return render_template('addcategory.html')

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    user = current_user()
    # 1. Kullanıcı giriş yapmamışsa, giriş sayfasına yönlendir.
    if not user:
        flash("Geri bildirim göndermek için lütfen giriş yapın.", "warning")
        return redirect(url_for('login'))

    # 2. Form gönderildiğinde (POST metodu ile)
    if request.method == 'POST':
        company_id = session.get('company')
        message = request.form.get('message', '').strip()

        # 3. Gerekli bilgiler var mı diye kontrol et. (user.id olarak düzeltildi)
        if company_id and user.id and message:
            try:
                # 4. Veritabanına kaydet.
                feedback_db(company_id, user.id, user.username, message)
                logf(f"Kullanıcı ID {user.id} geri bildirim gönderdi.")
                flash("Geri bildiriminiz için teşekkür ederiz!", "success")
                # 5. Başarılı işlemden sonra, tekrar gönderimi önlemek için sayfaya yönlendir.
                # Artık url_for('feedback') doğru şekilde çalışacaktır.
                return redirect(url_for('feedback'))
            except Exception as e:
                logf("Geri bildirim kaydedilemedi.")
                flash("Geri bildirim gönderilirken bir hata oluştu. Lütfen tekrar deneyin.", "error")
        else:
            # Mesaj boş ise hata göster.
            flash("Lütfen bir mesaj yazdığınızdan emin olun.", "error")
    
    # 6. Sayfa ilk açıldığında (GET metodu ile) veya işlem sonrası
    return render_template('feedback.html')

@app.route('/definitions', methods=['POST', 'GET'])
def definitions():
    company_id = session.get('company', None)  # bulunamazsa None döner
    if not company_id:
        return redirect(url_for('login'))
    logf(f"tanımlamalar")
    return render_template('definitions.html')

@app.route('/get_categories', methods=['GET'])
def get_categories():
    company_id = session.get('company')
    if not company_id:
        return jsonify({"success": False, "msg": "Şirket bilgisi bulunamadı"}), 403

    all_categories = Category.query.filter_by(company_id=company_id).all()
    categories_list = [{"id": c.id, "name": c.name, "sizes": c.sizes} for c in all_categories]
    return jsonify({"success": True, "categories": categories_list}), 200

@app.route('/add_category', methods=['POST'])
def add_category():
    data = request.get_json()
    company_id = session.get('company')
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Kategori ekleme yetkiniz yok."}), 403
    if not company_id:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    name = data.get('name')
    sizes = data.get('sizes')
    code = data.get('code')

    if not name:
        return jsonify({"status": "error", "message": "Eksik veri: İsim zorunludur."}), 400
        
    if Category.query.filter_by(name=name, company_id=company_id).first():
        return jsonify({"status": "error", "message": "Bu isimde başka kategori var"}), 409

    add_category_db(name, sizes, company_id, code)
    
    new_category = Category.query.filter_by(name=name, sizes=sizes, company_id=company_id).first()
    print(data)
    return jsonify({
        "status": "ok", 
        "message": "Kategori eklendi", 
        "category": {
            "id": new_category.id,
            "sizes": new_category.sizes,
            "name": new_category.name,
        }
    }), 201

@app.route('/update_product_route', methods=['POST'])
def update_product_route():
    company_id = session.get('company')
    user = current_user()
    if not user or not user.can_edit_product:
        return jsonify({"status": "error", "message": "Ürün düzenleme yetkiniz yok."}), 403

    try:
        product_id = request.form.get('id')
        name = request.form.get('name')
        description = request.form.get('description')
        min_stock = request.form.get('min_stock')
        
        sizes_str = request.form.get('sizes')
        sizes = json.loads(sizes_str) if sizes_str else None
        
        currency_str = request.form.get('currency')
        currency = json.loads(currency_str) if currency_str else None

        image_action = request.form.get('image_action')
        new_image_path = None 

        custom_attributes_str = request.form.get('custom_attributes')
        custom_attributes = json.loads(custom_attributes_str) if custom_attributes_str else None

        if image_action == 'delete':
            new_image_path = ""
        elif image_action == 'update' and 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                compress_and_save(file, full_path)
                new_image_path = f"uploads/{filename}"

        success = update_product(
            product_id=product_id,
            company_id=company_id,
            name=name,
            currency=currency,
            description=description,
            sizes=sizes,
            min_stock=min_stock,
            image_path=new_image_path,
            custom_attributes=custom_attributes
        )

        if success:
            return jsonify({"status": "ok", "message": "Ürün başarıyla güncellendi"})
        else:
            return jsonify({"status": "error", "message": "Ürün bulunamadı veya güncellenemedi"}), 404

    except Exception as e:
        print(f"Update Error: {e}")
        return jsonify({"status": "error", "message": f"Sunucu hatası: {str(e)}"}), 500

@app.route('/products', methods=['GET'])
def get_products_route():
    company_id = session.get('company')
    user = current_user()
    if not company_id:
        return jsonify({"status": "error", "msg": "Şirket bilgisi bulunamadı"}), 403

    if not user or not user.can_add_product:
        return jsonify({"status": "error", "message": "Ürün düzenleme yetkiniz yok."}), 403

    products = get_company_products(company_id) 
    
    products_list = [
        {
            "id": p.id,
            "code": p.code,
            "stock_id": p.stock_id,
            "name": p.name,
            "color_id": get_color_name(code=p.color_id, company_id=company_id),
            "shelves": p.shelves, 
            "category": p.category,
            "sizes": p.sizes,
            "stock": p.stock,
            "currency": p.currency,
            "description": p.description,
            "min_stock": p.min_stock,
            "image_path": p.image_path,
            "custom_attributes": p.custom_attributes or {} 
        } for p in products
    ]
    return jsonify(products_list), 200

@app.route('/get_order_history', methods=['GET'])
def get_order_history_route():
    company_id = session.get('company')
    user = current_user()

    if not company_id: return jsonify({"status": "error"}), 403

    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Sipariş görüntüleme yetkiniz yok."}), 403
    
    orders = get_order_history_db(company_id)
    
    orders_list = []
    for o in orders:
        creator = User.query.get(o.user_id)
        
        status_tr = "Bilinmiyor"
        if o.status == OrderStatus.APPROVED: status_tr = "Onaylandı"
        elif o.status == OrderStatus.REJECTED: status_tr = "Reddedildi"

        processed_date_str = o.processed_at.strftime('%d.%m.%Y %H:%M') if o.processed_at else "-"
        picked_date_str = o.picked_at.strftime('%d.%m.%Y %H:%M') if o.picked_at else "-"
        
        orders_list.append({
            "id": o.id,
            "creator": creator.username if creator else "Bilinmiyor",
            "customer": o.customer_name or "-",
            "total": o.total_amount,
            "currency": o.currency,
            "date": o.created_at.strftime('%d.%m.%Y %H:%M'),
            "processed_date": processed_date_str, 
            "picked_date": picked_date_str,
            "status": status_tr,
            "raw_status": o.status.value,
            "items": o.items,
            "note": o.note
        })
    
    return jsonify(orders_list), 200

@app.route('/revert_order', methods=['POST'])
def revert_order_route():
    user = current_user()
    # Bu işlemi yetkili kişiler yapabilmeli
    if not user:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    if not user or not user.can_orders:
        return jsonify({"status": "error", "message": "Sipariş yönetme yetkiniz yok."}), 403
        
    data = request.get_json()
    order_id = data.get('order_id')
    company_id = session.get('company')
    
    if revert_order_status(order_id, company_id):
        logf(f"{user.username} sipariş ID: {order_id} durumunu 'Bekleyen' olarak değiştirdi.")
        return jsonify({"status": "ok", "message": "Sipariş bekleyenlere taşındı."}), 200
    else:
        return jsonify({"status": "error", "message": "Sipariş bulunamadı."}), 400

#//--bildirimler--//
@app.route('/notifications')
def notifications():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    user = current_user()
    if not user:
        return redirect(url_for('login'))

    # Veritabanından gerçek bildirimleri çekiyoruz
    user_notifs = get_user_notifications(user.id)
    
    # Frontend (Jinja2) tarafında kullanmak için verileri hazırlıyoruz
    formatted_notifications = []
    
    # Zaman farkını hesaplamak için yardımcı fonksiyon (örn: "2 saat önce")
    def time_ago(dt):
        diff = datetime.utcnow() - dt
        if diff.days > 0:
            return f"{diff.days} gün önce"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600} saat önce"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60} dk önce"
        else:
            return "Şimdi"

    for n in user_notifs:
        formatted_notifications.append({
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "message": n.message,
            "date": time_ago(n.created_at),
            "read": n.is_read
        })

    return render_template('notifications.html', notifications=formatted_notifications)

@app.route('/mark_read/<int:notif_id>', methods=['POST'])
def mark_read_route(notif_id):
    user = current_user()
    if not user: return jsonify({"status": "error"}), 403
    
    if mark_notification_read(notif_id, user.id):
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400

@app.route('/mark_all_read', methods=['POST'])
def mark_all_read_route():
    user = current_user()
    if not user: return jsonify({"status": "error"}), 403
    
    if mark_all_read(user.id):
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400

@app.route('/get_unread_count')
def get_unread_count():
    if 'user' not in session:
        return jsonify({'count': 0})
    
    user = current_user()
    # Notification modelinizde 'is_read' alanı olduğunu varsayıyoruz
    count = Notification.query.filter_by(user_id=user.id, is_read=False).count()
    return jsonify({'count': count})

@app.route('/add_attribute_def', methods=['POST'])
def add_attribute_def_route():
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Yetkiniz yok."}), 403
    
    data = request.get_json()
    name = data.get('name')
    options = data.get('options', []) # Frontend'den liste olarak gelecek
    company_id = session.get('company')
    
    if not name:
        return jsonify({"status": "error", "message": "Nitelik adı zorunlu."}), 400
        
    success, msg = add_attribute_definition(company_id, name, options)
    if success:
        return jsonify({"status": "ok", "message": msg}), 201
    else:
        return jsonify({"status": "error", "message": msg}), 409

@app.route('/update_attribute_def/<int:attr_id>', methods=['PUT'])
def update_attribute_def_route(attr_id):
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Yetkiniz yok."}), 403
    
    data = request.get_json()
    new_name = data.get('name')
    new_options = data.get('options', [])
    company_id = session.get('company')
    
    if not new_name:
        return jsonify({"status": "error", "message": "Nitelik adı zorunlu."}), 400
        
    success, msg = update_attribute_definition(attr_id, company_id, new_name, new_options)
    if success:
        logf(f"{user.username} niteliği güncelledi: {new_name}")
        return jsonify({"status": "ok", "message": msg}), 200
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/barcode_rules', methods=['GET', 'POST'])
def manage_barcode_rules():
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Yetkiniz yok"}), 403
        
    company_id = session.get('company')
    
    if request.method == 'GET':
        rules = get_company_barcode_rules(company_id)
        return jsonify({"status": "ok", "rules": rules})
        
    if request.method == 'POST':
        data = request.get_json()
        rules = data.get('rules', [])
        save_company_barcode_rules(company_id, rules)
        return jsonify({"status": "ok", "message": "Barkod kuralları kaydedildi."})

@app.route('/delete_attribute_def/<int:attr_id>', methods=['DELETE'])
def delete_attribute_def_route(attr_id):
    user = current_user()
    if not user or not user.can_manage_attributes:
        return jsonify({"status": "error", "message": "Yetkiniz yok."}), 403
    
    company_id = session.get('company')
    
    if delete_attribute_definition(attr_id, company_id):
        logf(f"{user.username} bir niteliği sildi (ID: {attr_id}).")
        return jsonify({"status": "ok", "message": "Nitelik silindi."}), 200
    else:
        return jsonify({"status": "error", "message": "Nitelik bulunamadı."}), 404

@app.route('/get_attribute_defs', methods=['GET'])
def get_attribute_defs_route():
    company_id = session.get('company')
    if not company_id: return jsonify({"status": "error"}), 403
    
    attrs = get_company_attributes(company_id)
    return jsonify({"status": "ok", "attributes": attrs}), 200

@app.route('/send_employee_message', methods=['POST'])
def send_employee_message():
    user = current_user()
    # 1. Yetki Kontrolü
    if not user or not user.can_manage_employees:
        return jsonify({"status": "error", "message": "Bu işlem için yetkiniz yok."}), 403

    data = request.get_json()
    target_user_id = data.get('user_id')
    title = data.get('title')
    message = data.get('message')
    notif_type = data.get('type', 'info')

    if not all([target_user_id, title, message]):
        return jsonify({"status": "error", "message": "Eksik veri."}), 400

    # 2. Bildirimi Oluştur (db_utils.py içindeki fonksiyonu kullanıyoruz)
    # create_notification(user_id, company_id, title, message, notif_type)
    # Not: Hedef kullanıcının company_id'sini bulmamız gerekebilir veya gönderenin şirketini kullanabiliriz.
    
    company_id = session.get('company')
    success = create_notification(target_user_id, company_id, title, message, notif_type)

    if success:
        # Opsiyonel: Loglama
        logf(f"Kullanıcı ID {user.id} çalışan ID {target_user_id} için bildirim kaydetti.")
        return jsonify({"status": "ok", "message": "Mesaj gönderildi."}), 200
    else:
        return jsonify({"status": "error", "message": "Veritabanı hatası."}), 500

@app.route('/send_bulk_notification', methods=['POST'])
def send_bulk_notification_route():
    user = current_user()
    # Sadece admin yetkisi olanlar
    if not user or user.role != 'admin':
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    data = request.get_json()
    user_ids = data.get('user_ids', [])
    title = data.get('title')
    message = data.get('message')
    notif_type = data.get('type', 'info')

    if not user_ids or not title or not message:
        return jsonify({"status": "error", "message": "Eksik bilgi (Kullanıcı, Başlık veya Mesaj)"}), 400

    sent_count = send_bulk_notification(user_ids, title, message, notif_type)
    
    return jsonify({
        "status": "ok", 
        "message": f"{sent_count} kullanıcıya bildirim başarıyla gönderildi."
    })

@app.route('/product_detail/<int:product_id>')
def product_detail(product_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    current_user_obj = current_user()
    
    product = Product.query.filter_by(id=product_id, company_id=current_user_obj.company).first()
    if not product:
        abort(404)

    sales_history = Sale.query.filter_by(
        product_code=product.code, 
        company_id=current_user_obj.company
    ).order_by(Sale.sale_date.desc()).limit(50).all() if (
        current_user_obj.can_view_sales_history or current_user_obj.role in {'admin', 'owner'}
    ) else []

    # --- YENİ EKLENEN KISIM ---
    # Şirketin tanımladığı tüm özel nitelikleri çekiyoruz
    definitions = AttributeDefinition.query.filter_by(company_id=current_user_obj.company).all()
    # --------------------------

    return render_template(
        'product_detail.html', 
        product=product, 
        sales=sales_history,
        user=current_user_obj,
        definitions=definitions # Template'e gönderiyoruz
    )

#    Ürün silme rotası
@app.route('/product/delete/<int:product_id>', methods=['DELETE'])
def delete_product_route(product_id):
    company_id = session.get('company')
    user = current_user()
    if not user or not user.can_delete_product:
        return jsonify({"status": "error", "message": "Ürün silme yetkiniz yok."}), 403
    if not company_id:
        return jsonify({"status": "error", "message": "Yetkisiz işlem"}), 403

    if delete_product(product_id, company_id):
        logf(f"Ürün silindi {session['username']} tarafından Bayi: {company_id}, Ürün kodu: {product_id}.")  
        return jsonify({"status": "ok", "message": "Ürün silindi"}), 200
    else:
        return jsonify({"status": "error", "message": "Ürün bulunamadı veya silme yetkiniz yok"}), 404

# All request identity, CSRF and policy checks are installed centrally.
current_user = secure_current_user
register_core(app)
register_media(app)
register_documents(app)
init_auth(app, db, User)
init_security(app, db, User, resource_models={
    'Customer': Customer, 'Color': Color, 'Category': Category,
    'Shelf': Shelf, 'Product': Product, 'Order': Order,
})
app.before_request(protect_legacy_ledger)


if __name__ == "__main__":
    if not os.path.isfile(DB_PATH):
        raise SystemExit("Veritabanı bulunamadı. İlk kurulum: python manage.py init")
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.run(host="127.0.0.1", port=5000, debug=False)
