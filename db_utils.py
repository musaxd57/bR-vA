# db_utils.py

from models import *
from extensions import db
from sqlalchemy import func, or_
from datetime import date, timedelta, datetime
import random
import string
from flask import session
from sqlalchemy.orm.attributes import flag_modified
import uuid
import enum
import json
import pandas as pd
import re
from copy import deepcopy
from briva_numbers import legacy_cart, money as validated_money, quantity as validated_quantity


def consume_legacy_shelf_stock(product, sold_sizes):
    """Keep legacy shelf snapshots consistent in the same outer transaction.

    Stock without any shelf allocation is unassigned legacy stock. Once shelf
    allocations exist they must cover the sold variants; inconsistencies are
    rejected rather than silently fabricating a shelf movement.
    """
    if not product.shelves:
        return
    shelves = deepcopy(product.shelves)
    for sold in sold_sizes:
        size = sold['value']
        remaining = validated_quantity(sold['quantity'], 'Satılan adet')
        for shelf in shelves:
            amounts = shelf.get('sizes', {})
            available = int(amounts.get(size, 0))
            taken = min(remaining, max(available, 0))
            if taken:
                amounts[size] = available - taken
                shelf['sizes'] = amounts
                shelf['quantity'] = sum(max(int(v), 0) for v in amounts.values())
                remaining -= taken
            if remaining == 0:
                break
        if remaining:
            raise ValueError(f'{size} için raf stoku eksik; önce stok/raf mutabakatı gerekli.')
    product.shelves = shelves
    flag_modified(product, 'shelves')


# KULLANICI İŞLEMLERİ
def add_user(username, password, phone=None, company=None, role="user"):
    if User.query.filter_by(username=username).first():
        return
    
    if role in ["admin", "manager"]:
        user = User(
            username=username,
            phone=phone,
            company=company,
            role=role,
            can_make_sale=True,
            can_view_sales_history=True,
            can_view_statistics=True,
            can_generate_reports=True,
            can_add_product=True,
            can_edit_product=True,
            can_delete_product=True,
            can_manage_employees=True,
            can_manage_attributes=True,
            can_manage_customers=True,
            can_manage_debts=True,
            can_orders=True
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

    else:

        user = User(username=username, phone=phone, company=company, role=role)
        # GÜVENLİK GÜNCELLEMESİ: Şifre artık set_password metodu ile hash'lenerek kaydediliyor.
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()


def get_company_sales_stats(company_id):
    # 1. Genel Toplamlar (currency bazlı)
    revenues = db.session.query(
        func.upper(Sale.currency).label('currency'),
        func.sum(Sale.total_price).label('total_revenue')
    ).filter(Sale.company_id == company_id)\
    .group_by(func.upper(Sale.currency)).all()

    stats = db.session.query(
        func.count(Sale.id).label('total_sales_count'),
        func.sum(Sale.quantity).label('total_products_sold')
    ).filter(Sale.company_id == company_id).first()

    # 2. En Çok Satan Ürünler (Miktara Göre Top 5)
    top_products = db.session.query(
        Product.name,
        func.sum(Sale.quantity).label('total_quantity')
    ).join(Product, Sale.product_id == Product.id)\
     .filter(Sale.company_id == company_id)\
     .group_by(Product.name)\
     .order_by(func.sum(Sale.quantity).desc())\
     .limit(5).all()

    # 3. En İyi Çalışanlar
    top_employees = db.session.query(
        User.username,
        func.count(Sale.id).label('sales_count')
    ).join(User, Sale.user_id == User.id)\
     .filter(Sale.company_id == company_id)\
     .group_by(User.username)\
     .order_by(func.count(Sale.id).desc())\
     .limit(5).all()

    # 4. Son 10 Satış
    recent_sales = db.session.query(
        Product.name.label('product_name'),
        Sale.quantity,
        Sale.total_price,
        Sale.currency,
        Sale.sale_date
    ).join(Product, Sale.product_id == Product.id)\
     .filter(Sale.company_id == company_id)\
     .order_by(Sale.sale_date.desc())\
     .limit(10).all()

    result = {
        'revenues': [{'currency': r.currency, 'total': r.total_revenue} for r in revenues],
        'total_sales_count': stats.total_sales_count or 0,
        'total_products_sold': stats.total_products_sold or 0,
        'top_products': [{'name': p.name, 'quantity': p.total_quantity} for p in top_products],
        'top_employees': [{'username': e.username, 'sales_count': e.sales_count} for e in top_employees],
        'recent_sales': [{
            'product_name': s.product_name,
            'quantity': s.quantity,
            'total_price': s.total_price,
            'currency': s.currency,
            'sale_date': s.sale_date.strftime('%d.%m.%Y %H:%M')
        } for s in recent_sales]
    }
    return result

def get_product_sales_trend(company_id, product_code):
    today = date.today()
    start_date = today - timedelta(days=29)
    date_labels = [(today - timedelta(days=i)).strftime('%d.%m') for i in range(29, -1, -1)]
    
    date_map = { (start_date + timedelta(days=i)) : 0 for i in range(30) }

    sales_data = db.session.query(
        func.date(Sale.sale_date).label('sale_day'),
        func.sum(Sale.quantity).label('daily_quantity')
    ).filter(
        Sale.company_id == company_id,
        Sale.product_code == product_code,
        Sale.sale_date >= start_date
    ).group_by('sale_day').all()
    
    for row in sales_data:
        sale_day_date = datetime.strptime(row.sale_day, '%Y-%m-%d').date()
        if sale_day_date in date_map:
            date_map[sale_day_date] = int(row.daily_quantity)
            
    data_values = [date_map[day] for day in sorted(date_map)]
    
    product = Product.query.filter_by(code=product_code, company_id=company_id).first()
    product_name = product.name if product else product_code
    
    return {
        'labels': date_labels, 
        'data': data_values,
        'product_name': product_name
    }

def get_daily_sales_trend(company_id, filter_type="monthly"):
    today = date.today()
    
    # === SENARYO 1: GÜNLÜK (SAATLİK) GÖRÜNÜM ===
    if filter_type == "daily":
        # 00:00'dan 23:00'a kadar etiketler (24 saat)
        labels = [f"{h:02d}:00" for h in range(24)]
        
        # Sonuç iskeleti
        results = {
            "TL": {'labels': labels, 'data': [0.0] * 24},
            "USD": {'labels': labels, 'data': [0.0] * 24},
            "EURO": {'labels': labels, 'data': [0.0] * 24}
        }

        # Sadece bugüne ait satışları çek (Tam tarih kontrolü)
        # Not: Veritabanı saati UTC olabilir, yerel saat farkına dikkat edilmeli. 
        # Basitlik için veritabanındaki kayıt tarihinin gününe bakıyoruz.
        todays_sales = db.session.query(Sale).filter(
            Sale.company_id == company_id,
            func.date(Sale.sale_date) == today
        ).all()

        for sale in todays_sales:
            # Satışın saatini al (0, 1, ..., 23)
            # sale.sale_date bir datetime objesidir.
            sale_hour = sale.sale_date.hour
            
            currency_key = sale.currency.strip().upper()
            
            if currency_key in results:
                # İlgili saatin cirosuna ekle
                results[currency_key]['data'][sale_hour] += sale.total_price

        return results

    # === SENARYO 2: DİĞER (GÜNLER BAZINDA) GÖRÜNÜMLER ===
    else:
        # Filtreye göre gün sayısı belirle
        days_to_subtract = 29 # Varsayılan (Aylık)
        
        if filter_type == "weekly":
            days_to_subtract = 6  # Son 7 gün
        elif filter_type == "3months":
            days_to_subtract = 89
        elif filter_type == "6months":
            days_to_subtract = 179
        elif filter_type == "yearly":
            days_to_subtract = 364
        
        start_date = today - timedelta(days=days_to_subtract)
        
        # Etiketleri oluştur (Gün.Ay formatında)
        date_labels = [(today - timedelta(days=i)).strftime('%d.%m') for i in range(days_to_subtract, -1, -1)]

        sales_data = db.session.query(
            func.date(Sale.sale_date).label('sale_day'),
            Sale.currency.label('currency'),
            func.sum(Sale.total_price).label('daily_total')
        ).filter(
            Sale.company_id == company_id,
            Sale.sale_date >= start_date
        ).group_by('sale_day', 'currency').all()

        # Boş veri yapısı oluştur
        total_days = days_to_subtract + 1
        
        results = {
            "TL": {'labels': date_labels, 'data': [0.0] * total_days},
            "USD": {'labels': date_labels, 'data': [0.0] * total_days},
            "EURO": {'labels': date_labels, 'data': [0.0] * total_days}
        }
        
        date_map = { (start_date + timedelta(days=i)): i for i in range(total_days) }

        for row in sales_data:
            if isinstance(row.sale_day, str):
                sale_day_date = datetime.strptime(row.sale_day, '%Y-%m-%d').date()
            else:
                sale_day_date = row.sale_day

            currency_key = row.currency.strip().upper()
            
            if sale_day_date in date_map and currency_key in results:
                index = date_map[sale_day_date]
                results[currency_key]['data'][index] = float(row.daily_total)

        return results

def feedback_db(company_id, user_id, username, message):
    feedback = Feedback(company_id=company_id, user_id=user_id, username=username, message=message)
    db.session.add(feedback)
    db.session.commit()
    return True

def is_valid_user(username, password):
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        return True
    return False

def user_exists(username):
    return User.query.filter_by(username=username).first() is not None

def current_user():
    if "user" not in session:
        return None
    return User.query.get(session["user"])

def get_user_data(username, field='role'):
    user = User.query.filter_by(username=username).first()
    if not user:
        return None
    return getattr(user, field, None)

def delete_user(user_id):
    # LegacyAPIWarning düzeltmesi: User.query.get yerine db.session.get kullanımı
    user = db.session.get(User, user_id)
    
    if user:
        try:
            # HATA ÇÖZÜMÜ: Kullanıcı silinmeden önce ona ait bildirimler silinmeli
            # Çünkü Notification tablosunda user_id alanı boş (NULL) olamaz.
            Notification.query.filter_by(user_id=user.id).delete()
            
            # Varsa kullanıcıya ait diğer bağımlı verileri de burada silebilirsiniz
            # Örn: Sale, Feedback vb. (Eğer cascade ayarı yoksa)
            
            # Artık kullanıcıyı silebiliriz
            db.session.delete(user)
            db.session.commit()
            return True
        except Exception as e:
            # Hata durumunda işlemi geri al
            db.session.rollback()
            print(f"Kullanıcı silme hatası: {e}")
            return False
            
    return False

def get_product_color_id(code, company_id):
    product = Product.query.filter_by(code=code, company_id=company_id).first()
    if not product:
        return False #renk bulunamadı
    return product.color_id

def get_product_name_by_id(product_id, company_id):
    product = Product.query.filter_by(code=product_id, company_id=company_id).first()
    if product:
        print("Product found:", product.name)
        return product.name
    print("Product not found for ID:", product_id)
    return None


def get_all_sales_for_report(company_id, search_term=None, start_date_str=None, end_date_str=None):
    # Sorgu artık doğrudan tam Sale nesnesini hedefliyor.
    query = db.session.query(Sale).filter(Sale.company_id == company_id)

    # Filtreleme için ilgili tablolara join yapıyoruz.
    if search_term:
        search_like = f"%{search_term}%"
        query = query.join(User, User.id == Sale.user_id).join(Product, (Product.code == Sale.product_code) & (Product.company_id == company_id))
        query = query.filter(
            or_(
                Product.name.ilike(search_like),
                User.username.ilike(search_like),
                Sale.customer_name.ilike(search_like)
            )
        )

    # Tarih filtreleri Sale tablosu üzerinde doğrudan çalışır.
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(Sale.sale_date >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            query = query.filter(Sale.sale_date < (end_date + timedelta(days=1)))
        except ValueError:
            pass
    
    # Sayfalama olmadan, filtrelenmiş tüm Sale nesnelerini döndür.
    return query.order_by(Sale.sale_date.desc()).all()

def get_paginated_sales(company_id, page=1, per_page=15, search_term=None, start_date_str=None, end_date_str=None):
    query = (
        db.session.query(
            Sale.id.label("id"),
            Sale.product_id,
            Sale.product_code,
            Product.name.label("product_name"),
            Sale.user_id,
            User.username.label("sold_by"),
            Sale.company_id,
            Sale.quantity,
            Sale.price_at_sale,
            Sale.discount,
            Sale.total_price,
            Sale.currency,
            Sale.customer_name,
            Sale.sale_notes,
            Sale.sale_date
        )
        .outerjoin(Product, Product.id == Sale.product_id)
        .outerjoin(User, User.id == Sale.user_id)
        .filter(Sale.company_id == company_id)
    )

    # Arama filtresi
    if search_term:
        search_like = f"%{search_term}%"
        query = query.filter(
            or_(
                Product.name.ilike(search_like),
                User.username.ilike(search_like),
                Sale.customer_name.ilike(search_like)
            )
        )

    # Tarih filtresi
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(Sale.sale_date >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            query = query.filter(Sale.sale_date < (end_date + timedelta(days=1)))
        except ValueError:
            pass

    return query.order_by(Sale.sale_date.desc()).paginate(page=page, per_page=per_page, error_out=False)



# ŞİRKET İŞLEMLERİ
def get_all_companies():
    companies = (
        db.session.query(User.company)
        .distinct()
        .filter(User.company.isnot(None))
        .all()
    )
    return [{"name": c[0]} for c in companies]


def delete_company_db(company_id):
    company = Company.query.get(company_id)
    if company:
        db.session.delete(company)
        db.session.commit()
        return True
    return False

def update_attribute_definition(attr_id, company_id, new_name, new_options):
    """Mevcut bir niteliği günceller. (Sözlük yapısını JSON olarak kaydeder)"""
    attr = AttributeDefinition.query.filter_by(id=attr_id, company_id=company_id).first()
    if not attr:
        return False, "Nitelik bulunamadı."
    
    attr.name = new_name
    attr.options = new_options if new_options is not None else []
    
    # JSON alanındaki değişikliği veritabanına bildir (Bu olmazsa kaydetmez!)
    flag_modified(attr, "options")
    db.session.commit()
    return True, "Nitelik başarıyla güncellendi."

def delete_attribute_definition(attr_id, company_id):
    """Bir niteliği siler."""
    attr = AttributeDefinition.query.filter_by(id=attr_id, company_id=company_id).first()
    if not attr:
        return False
    
    db.session.delete(attr)
    db.session.commit()
    return True

def get_company_barcode_rules(company_id):
    try:
        settings = CompanySettings.query.filter_by(company_id=company_id).first()
        return settings.barcode_rules if settings and settings.barcode_rules else []
    except Exception as e:
        # HATA OLURSA VERİTABANI KİLİDİNİ AÇ (Rollback)
        # Bu satır çok önemli, yoksa 500 hatası devam eder.
        db.session.rollback()
        print(f"Tablo bulunamadı, eski sistem devam ediyor: {e}")
        return []
    
def save_company_barcode_rules(company_id, rules):
    settings = CompanySettings.query.filter_by(company_id=company_id).first()
    if not settings:
        settings = CompanySettings(company_id=company_id)
        db.session.add(settings)
    
    settings.barcode_rules = rules
    flag_modified(settings, "barcode_rules")
    db.session.commit()
    return True

def generate_custom_codes(company_id, color_id, custom_attributes, base_random_number, category=""):
    """Kullanıcı kurallarına ve Nitelik kodlarına göre Barkod üretir."""
    rules = get_company_barcode_rules(company_id)
    
    if not rules:
        group_code = str(base_random_number)
        final_code = f"{group_code}{color_id}"
        return group_code, final_code

    group_parts = []
    final_parts =[]

    for rule in rules:
        rtype = rule.get('type')
        is_group = rule.get('is_group', False)
        part_val = ""

        if rtype == 'random':
            part_val = str(base_random_number)
        elif rtype == 'color':
            part_val = str(color_id).upper() if color_id else ""
            
        elif rtype == 'static': # YENİ: SABİT YAZI
            part_val = str(rule.get('value', '')).replace(" ", "").upper()
            
        elif rtype == 'category':
            if category:
                cat_obj = Category.query.filter_by(name=category, company_id=company_id).first()
                # Kategoriye özel kod atanmışsa onu kullan, yoksa adının ilk 3 harfini al
                if cat_obj and cat_obj.code:
                    part_val = str(cat_obj.code).replace(" ", "").upper()
                else:
                    part_val = str(category).replace(" ", "").upper()[:3]
            
        elif rtype == 'custom_attr':
            attr_name = rule.get('attr_name')
            val = custom_attributes.get(attr_name, "")
            
            if val:
                attr_def = AttributeDefinition.query.filter_by(company_id=company_id, name=attr_name).first()
                if attr_def and attr_def.options:
                    opts = attr_def.options
                    if isinstance(opts, str):
                        try: opts = json.loads(opts)
                        except: opts =[]

                    for opt in opts:
                        if isinstance(opt, dict) and opt.get('label') == val:
                            custom_code = opt.get('code', '')
                            if custom_code:
                                part_val = str(custom_code).replace(" ", "").upper()
                            break

        if part_val:
            if is_group:
                group_parts.append(part_val)
            final_parts.append(part_val)

    group_code = "".join(group_parts)
    final_code = "".join(final_parts)

    if not group_code:
        group_code = str(base_random_number)
    if not final_code:
        final_code = f"{group_code}{color_id}"

    return group_code, final_code

def regenerate_all_company_barcodes(company_id):
    """
    Şirketteki TÜM ürünlerin kodlarını yeni barkod kurallarına göre yeniden oluşturur.
    Geçmiş satış ve sipariş kayıtlarındaki eski kodları da yeni kodlarla değiştirerek
    veri bütünlüğünü (geçmişi) korur.
    """
    try:
        products = Product.query.filter_by(company_id=company_id).all()
        if not products:
            return True, "Sistemde güncellenecek ürün bulunamadı."
            
        # Ürünleri aynı ailede tutmak (varyasyonları koparmamak) için eski stock_id'ye göre grupla
        from collections import defaultdict
        groups = defaultdict(list)
        for p in products:
            groups[p.stock_id].append(p)
            
        updated_count = 0
        
        for old_stock_id, variants in groups.items():
            # O grup (aile) için yeni bir rastgele sayı (çekirdek) üret
            base_random = generate_unique_stock_id(company_id)
            
            for p in variants:
                old_code = p.code
                    
                # YENİ KODU OLUŞTUR (shelf parametresi silindi)
                new_group_code, new_final_code = generate_custom_codes(
                    company_id=company_id,
                    color_id=p.color_id,
                    custom_attributes=p.custom_attributes or {},
                    base_random_number=base_random,
                    category=p.category
                )
                
                # Çakışma Önleyici (Aynı kod varsa sonuna rakam ekle)
                original_code = new_final_code
                counter = 1
                while Product.query.filter(Product.code == new_final_code, Product.id != p.id, Product.company_id == company_id).first():
                    new_final_code = f"{original_code}{counter}"
                    counter += 1
                    
                # 1. Ürün Tablosunu Güncelle
                p.code = new_final_code
                p.stock_id = new_group_code
                updated_count += 1
                
                # 2. Satış Geçmişi (Sales) Tablosunu Güncelle
                Sale.query.filter_by(product_id=p.id).update({"product_code": new_final_code})
                
                # 3. Sipariş (Orders) Tablosundaki JSON Sepetleri Güncelle
                orders = Order.query.filter_by(company_id=company_id).all()
                for order in orders:
                    order_updated = False
                    items = order.items
                    
                    # Veri string geldiyse JSON'a çevir
                    if isinstance(items, str):
                        import json
                        try:
                            items = json.loads(items)
                        except:
                            pass
                    
                    # Sepetteki ürün kodunu bul ve değiştir
                    if isinstance(items, list):
                        for item in items:
                            if item.get('code') == old_code:
                                item['code'] = new_final_code
                                order_updated = True
                                
                    if order_updated:
                        order.items = items
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(order, "items")
                        
        db.session.commit()
        return True, f"Toplam {updated_count} varyasyonun barkodu başarıyla güncellendi!"
        
    except Exception as e:
        db.session.rollback()
        print(f"Toplu Barkod Yenileme Hatası: {e}")
        return False, f"Hata oluştu: {str(e)}"

# --- ÖZEL NİTELİK YÖNETİMİ ---

def add_attribute_definition(company_id, name, options=None): # data_type yerine options geldi
    if options is None: options = []
    exists = AttributeDefinition.query.filter_by(company_id=company_id, name=name).first()
    if exists:
        return False, "Bu nitelik zaten tanımlı."
    
    new_attr = AttributeDefinition(company_id=company_id, name=name, options=options)
    db.session.add(new_attr)
    db.session.commit()
    return True, "Nitelik başarıyla eklendi."

def get_company_attributes(company_id):
    """Şirkete ait tanımlı nitelikleri listeler."""
    attrs = AttributeDefinition.query.filter_by(company_id=company_id).all()
    # type yerine options döndürüyoruz
    return [{"id": a.id, "name": a.name, "options": a.options or []} for a in attrs]

def get_products_filtered_by_attribute(company_id, attr_name, attr_value):
    """
    Belirli bir özel niteliğe göre ürünleri filtreler.
    Örn: Materyal = Pamuk olan ürünleri getir.
    """
    # SQLAlchemy JSON sorgusu (SQLite ve PostgreSQL uyumlu yapı)
    # custom_attributes sütunundaki JSON içinde arama yapar
    
    # Not: SQLite kullanıyorsanız bazı eski sürümler JSON sorgularını desteklemeyebilir.
    # Ancak modern SQLAlchemy sürümleri bunu genellikle cast ile halleder.
    
    products = Product.query.filter(
        Product.company_id == company_id,
        Product.custom_attributes[attr_name].astext == str(attr_value)  # PostgreSQL için astext, genel kullanım
        # Eğer SQLite kullanıyorsanız ve hata alırsanız:
        # func.json_extract(Product.custom_attributes, f'$.{attr_name}') == attr_value
    ).all()
    
    return products

# ÜRÜN İŞLEMLERİ
def generate_unique_stock_id(company_id):
    while True:
        stock_id = random.randint(1000, 9999)  # 4 haneli sayı
        if not Product.query.filter_by(stock_id=stock_id, company_id=company_id).first():
            return stock_id

def add_shelf_db(code, company_id):
    if Shelf.query.filter_by(code=code, company_id=company_id).first():
        return False  # Aynı isimde raf zaten var

    shelf = Shelf(company_id=company_id, code=code)
    db.session.add(shelf)
    db.session.commit()
    return True

def add_product(code, name, company_id, category, description=None, stock_id=None, color_id=None, sizes=None, currency=None, image_path=None, shelves=None, custom_attributes=None):
    # DÜZELTME BURADA: custom_attributes sorgudan çıkarıldı
    category_obj = Category.query.filter_by(name=category, company_id=company_id).first()
    
    # Eğer kategori bulunamazsa işlemi durdur (veya hata döndür)
    if not category_obj: 
        print(f"Kategori bulunamadı: {category}")
        return None

    # Toplam stok hesaplama (sizes üzerinden)
    seri_stock = 0
    if sizes:
        seri_components = [s for s in sizes if s.get('number', 0) > 0]
        if seri_components:
            possible_seris = [s.get('stock', 0) // s.get('number', 0) for s in seri_components]
            seri_stock = min(possible_seris) if possible_seris else 0

    if currency is None: currency = {"TL": 0, "USD": 0, "EURO": 0}
    if shelves is None: shelves = [] 

    new_product = Product(
        code=code,
        name=name,
        stock=seri_stock,
        company_id=company_id,
        category=category_obj.name,
        description=description,
        stock_id=stock_id,
        color_id=color_id,
        sizes=sizes, 
        currency=currency,
        image_path=image_path,
        shelves=shelves,
        custom_attributes=custom_attributes # Ürüne ekleniyor, kategoriye değil
    )
    db.session.add(new_product)
    db.session.commit()
    return new_product

# KRİTİK HATA DÜZELTİLDİ: Sorgu, User yerine Product modeline yapılmalıydı.
def is_valid_product(code, company_id):
    product = Product.query.filter_by(code=code, company_id=company_id).first()
    return product is not None

def add_category_db(name, sizes=None, company_id=None, code=None): # code parametresi eklendi
    if Category.query.filter_by(name=name, company_id=company_id).first():
        return False  # aynı isim ve şirket zaten var

    if sizes is None:
        sizes =[]

    if company_id is None:
        raise ValueError("company_id boş olamaz!")

    category = Category(
        name=name,
        code=code, # Kodu kaydediyoruz
        sizes=sizes,
        company_id=company_id
    )
    db.session.add(category)
    db.session.commit()
    return True

def add_stock(product_code, company_id, number):
    try:
        product = Product.query.filter_by(code=product_code, company_id=company_id).first()
        if not product:
            return False
        
        # 1. Ana seri stoğunu artır
        product.stock += int(number)
        
        # 2. Her bir bedenin stoğunu serideki adedine göre artır
        sizes_data = product.sizes or []
        for size_info in sizes_data:
            # Eklenecek seri adedi * serideki o bedenin adedi
            items_to_add = int(number) * size_info.get('number', 0)
            current_stock = size_info.get('stock', 0)
            size_info['stock'] = current_stock + items_to_add
            
        product.sizes = sizes_data
        flag_modified(product, "sizes") # JSON alanındaki değişikliği bildir

        db.session.commit()
        return True
    except Exception as e:
        print(f"add_stock HATA: {e}")
        db.session.rollback()
        return False

def update_product(product_id, company_id, name=None, currency=None, description=None, sizes=None, min_stock=None, image_path=None, custom_attributes=None):
    try:
        product = Product.query.filter_by(id=product_id, company_id=company_id).first()
        if not product: return False

        if name: product.name = name
        if description is not None: product.description = description
        if image_path is not None: product.image_path = image_path if image_path != "" else None
        if min_stock is not None:
             try: product.min_stock = int(min_stock)
             except: pass

        if currency is not None:
            updated_currency = product.currency or {}
            updated_currency.update(currency)
            product.currency = updated_currency
            flag_modified(product, "currency")

        if sizes is not None:
            product.sizes = sizes
            flag_modified(product, "sizes")
            # Stok hesaplama (sizes üzerinden)
            new_seri_stock = 0
            seri_components =[s for s in product.sizes if s.get('number', 0) > 0]
            if seri_components:
                possible_seris =[s.get('stock', 0) // s.get('number', 0) for s in seri_components]
                new_seri_stock = min(possible_seris) if possible_seris else 0
            product.stock = new_seri_stock

        if custom_attributes is not None:
            product.custom_attributes = custom_attributes
            flag_modified(product, "custom_attributes")

        # Printed identity and historical sale snapshots are immutable during
        # ordinary editing. Descriptive fields must never reallocate a barcode
        # or rewrite past sales/orders.

        db.session.commit()
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"Update Error: {e}")
        import traceback
        traceback.print_exc() 
        return False
    
def get_company_products(company_id):
    """Belirtilen şirkete ait tüm ürünleri getirir (nesne listesi olarak)"""
    # Bu fonksiyon, veritabanından aldığı Product nesnelerini doğrudan döndürmelidir.
    # Sözlüğe çevirme işlemi app.py'de yapılacaktır.
    return Product.query.filter_by(company_id=company_id).all()


def get_company_employees(username):
    # Önce kullanıcının şirketini al
    user = User.query.filter_by(username=username).first()
    if not user or not user.company:
        return []

    company = user.company

    # Aynı şirketteki calisan ve kasiyerleri al
    employees = User.query.filter(
        User.company == company,
        User.role.in_(["cashier"])
    ).all()

    # JSON formatında döndür
    return [{"id": e.id, "username": e.username, "role": e.role} for e in employees]

# YENİ: Ürün silme fonksiyonu
def delete_product(product_id, company_id):
    """ Belirtilen ürünü ID ve şirket ID'si ile güvenli bir şekilde siler. """
    product = Product.query.filter_by(id=product_id, company_id=company_id).first()
    if product:
        db.session.delete(product)
        db.session.commit()
        return True
    return False

def get_sales_analysis_data_for_ai(company_id):
    """AI için daha zengin ve detaylı satış analizi verisi hazırlar."""
    base_stats = get_company_sales_stats(company_id)
    
    potential_products = db.session.query(
        Product.name, Product.code, Product.stock,
        func.sum(Sale.quantity).label('total_sold')
    ).join(Sale, Sale.product_id == Product.id)\
     .filter(Product.company_id == company_id)\
     .group_by(Product.name, Product.code, Product.stock)\
     .order_by(func.sum(Sale.quantity).desc())\
     .limit(5).all()

    base_stats['potential_risks_and_opportunities'] = [
        {'name': p.name, 'code': p.code, 'current_stock': p.stock, 'total_sold_recently': int(p.total_sold)} 
        for p in potential_products
    ]
    return base_stats

def ai_query_product_by_code(query_input, company_id):
    """AI için ürün detaylarını getirir (KOD veya İSİM ile arar)."""
    
    # Hem koda hem isme bakıyoruz (Case insensitive - büyük/küçük harf duyarsız)
    product = Product.query.filter(
        Product.company_id == company_id,
        or_(
            Product.code == query_input,
            Product.name.ilike(f"%{query_input}%")
        )
    ).first()

    if not product:
        return None
    
    # Fiyatı çek
    price_tl = 0
    if product.currency and isinstance(product.currency, dict):
        price_tl = product.currency.get('TL', 0)

    # Raf Dağılımı
    shelf_details = []
    if product.shelves:
        for s in product.shelves:
            shelf_code = s.get('code', '-')
            total_qty = s.get('quantity', 0)
            sizes_info = s.get('sizes', {})
            size_str = ", ".join([f"{k}: {v}" for k, v in sizes_info.items()]) if sizes_info else "Beden detayı yok"
            shelf_details.append(f"Raf {shelf_code}: Toplam {total_qty} adet ({size_str})")
    
    shelves_text = "\n".join(shelf_details) if shelf_details else "Ürün henüz bir rafa atanmamış."

    # Geçerli Bedenleri Listele (Kategori veya Ürün tanımından)
    valid_sizes_str = "Standart (Bedensiz)"
    if product.sizes:
        valid_sizes_str = ", ".join([s.get('value') for s in product.sizes])
    elif product.category:
        cat = Category.query.filter_by(name=product.category, company_id=company_id).first()
        if cat and cat.sizes:
            valid_sizes_str = ", ".join(cat.sizes)

    return { 
        "id": product.id, 
        "code": product.code, 
        "name": product.name, 
        "category": product.category,
        "price": price_tl,
        "stock": product.stock, 
        "description": product.description,
        "shelf_status": shelves_text,
        "valid_sizes": valid_sizes_str # AI artık geçerli bedenleri de bilecek
    }

def ai_update_product_by_code(code, company_id, data_to_update):
    """AI için ürün koduna göre güncelleme yapar."""
    product = Product.query.filter_by(code=code, company_id=company_id).first()
    if not product:
        return False
    # Orijinal update_product fonksiyonunun ID ile çalıştığını unutma, burada ID'yi kullanıyoruz.
    return update_product(product.id, company_id, **data_to_update)

def ai_delete_product_by_code(code, company_id):
    """AI için ürün koduna göre silme yapar."""
    product = Product.query.filter_by(code=code, company_id=company_id).first()
    if not product:
        return False
    # Orijinal delete_product fonksiyonunu ID ile çağırıyoruz.
    return delete_product(product.id, company_id)

def ai_create_sale(product_code, quantity, company_id, user_id, customer_name=None, discount=0.0):
    """AI komutları için satış işlemi oluşturur."""
    product = Product.query.filter_by(code=product_code, company_id=company_id).first()
    
    if not product:
        return {"status": "error", "message": "Ürün bulunamadı."}
    
    # Güvenli tamsayı dönüşümü (AI bazen float gönderebilir)
    try:
        quantity = int(quantity)
    except:
        return {"status": "error", "message": "Adet geçersiz."}

    if product.stock < quantity:
        return {"status": "error", "message": f"Yetersiz stok! Mevcut stok: {product.stock}"}
    
    # DÜZELTME: Fiyatı currency JSON içinden çekiyoruz
    unit_price = 0
    if product.currency and isinstance(product.currency, dict):
        unit_price = product.currency.get('TL', 0)
    
    product.stock -= quantity
    total_price = (unit_price * quantity) - discount
    
    # Sale modeline 'currency' parametresini de ekliyoruz ('TL' varsayılan)
    new_sale = Sale(
        sale_group_id=str(uuid.uuid4()), # Sale group id eklenmeli
        product_id=product.id,
        product_code=product.code, # Modelde varsa ekleyin
        user_id=user_id, 
        company_id=company_id,
        quantity=quantity, 
        price_at_sale=unit_price, 
        discount=discount,
        total_price=total_price, 
        currency="TL", # Para birimini belirtiyoruz
        customer_name=customer_name
    )
    db.session.add(new_sale)
    db.session.commit()
    
    return {"status": "ok", "message": f"Satış başarılı! '{product.name}' ürününün yeni stoğu: {product.stock}"}

def ai_get_all_sales(company_id, limit=20):
    """AI için belirli bir şirkete ait son satışları listeler."""
    sales = db.session.query(
        Product.name.label('product_name'),
        Sale.quantity,
        Sale.total_price,
        Sale.sale_date,
        User.username.label('sold_by')
    ).join(Product, Sale.product_id == Product.id)\
     .join(User, Sale.user_id == User.id)\
     .filter(Sale.company_id == company_id)\
     .order_by(Sale.sale_date.desc())\
     .limit(limit).all()

    if not sales:
        return []

    return [
        {
            'product_name': s.product_name,
            'quantity': s.quantity,
            'total_price': float(s.total_price),
            'sale_date': s.sale_date.strftime('%d.%m.%Y %H:%M'),
            'sold_by': s.sold_by
        } for s in sales
    ]

def change_user_data_db(user_id, username=None, password=None, company=None, role=None, phone=None):
    """
    Verilen ID'ye sahip kullanıcının bilgilerini günceller.
    Sadece dolu gelen alanları günceller ve şifreyi hash'ler.
    """
    user = User.query.get(user_id)
    if not user:
        return False  # Kullanıcı bulunamadı

    try:
        if username:
            user.username = username
        if role:
            user.role = role
        if phone:
            user.phone = phone
        if company is not None: # Boş string olarak da gelebilir
            user.company = company
        
        # Şifre alanı doluysa ve boş bir string değilse hash'leyerek güncelle
        if password:
            user.set_password(password)

        db.session.commit()
        return True  # Başarılı
    except Exception as e:
        db.session.rollback() # Hata durumunda işlemi geri al
        return False # Başarısız

def add_color_db(name, company_id, code): # code parametresi eklendi
    # Aynı isimde veya kodda renk var mı?
    if Color.query.filter(Color.company_id == company_id, 
                          or_(Color.name == name, Color.code == code)).first():
        return False
        
    color = Color(code=code.upper(), name=name, company_id=company_id)
    db.session.add(color)
    db.session.commit()
    return True

def get_color_name(code, company_id):
    color = Color.query.filter_by(code=code, company_id=company_id).first()
    if not color:
        return False #renk bulunamadı
    return color.name

def generate_unique_code():
    while True:
        code = str(random.randint(10, 99))  # 10 ile 99 arasında sayı
        exists = Color.query.filter_by(code=code).first()
        if not exists:
            return code

def delete_color_db(code, company_id):
    color = Color.query.filter_by(code=code, company_id=company_id).first()
    if not color:
        return False  # renk yok veya yetkin yok
    try:
        db.session.delete(color)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print("Silme hatası:", e)

def delete_shelf_db(code, company_id):
    shelf = Shelf.query.filter_by(code=code, company_id=company_id).first()
    if not shelf:
        return False  # renk yok veya yetkin yok
    try:
        db.session.delete(shelf)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print("Silme hatası:", e)

def add_customer_db(name, company_id, description=None, address=None, phone=None):
    if Customer.query.filter_by(name=name, company_id=company_id).first():
        return False  # Aynı isimde müşteri zaten var

    customer = Customer(
        name=name,
        company_id=company_id,
        description=description,
        address=address,
        phone=phone
    )
    db.session.add(customer)
    db.session.commit()
    return True

def get_paginated_sales_groups(company_id, page=1, per_page=15, search_term=None, start_date_str=None, end_date_str=None, customer_name=None):
    """Satışları sale_group_id'ye göre gruplayarak ve sayfalandırarak getirir."""
    
    # 1. Ana sorgu: Distinct sale_group_id'leri temel alarak bir alt sorgu oluştur.
    subquery = db.session.query(
        Sale.sale_group_id,
        func.max(Sale.sale_date).label('sale_date'),
        func.max(Sale.customer_name).label('customer_name'),
        func.max(User.username).label('sold_by')
    ).join(User, User.id == Sale.user_id).filter(Sale.company_id == company_id).group_by(Sale.sale_group_id)

    # 2. Filtreleri uygula
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        subquery = subquery.filter(func.date(Sale.sale_date) >= start_date)
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        subquery = subquery.filter(func.date(Sale.sale_date) <= end_date)
    if customer_name:
        # Tam eşleşme arıyoruz
        subquery = subquery.filter(Sale.customer_name == customer_name)
    if search_term:
        subquery = subquery.join(Product, (Product.code == Sale.product_code) & (Product.company_id == company_id)).filter(
            (Sale.customer_name.ilike(f'%{search_term}%')) |
            (User.username.ilike(f'%{search_term}%')) |
            (Product.name.ilike(f'%{search_term}%'))
        )
        
    subquery = subquery.order_by(db.desc('sale_date')).subquery()

    # 3. Sayfalandırma uygula
    paginated_groups = db.session.query(subquery.c.sale_group_id).paginate(page=page, per_page=per_page, error_out=False)
    
    group_ids_on_page = [g.sale_group_id for g in paginated_groups.items]

    # 4. Sayfadaki grupların tüm detaylarını getir
    sales_in_groups = Sale.query.filter(Sale.company_id == company_id, Sale.sale_group_id.in_(group_ids_on_page)).order_by(Sale.sale_date.desc()).all()
    
    # 5. Sonuçları yapılandır
    results = []
    for group_id in group_ids_on_page:
        items_in_group = [s for s in sales_in_groups if s.sale_group_id == group_id]
        if not items_in_group:
            continue
        
        totals = {}
        for item in items_in_group:
            totals[item.currency] = totals.get(item.currency, 0) + item.total_price
        
        sold_by_user = User.query.get(items_in_group[0].user_id)
        
        results.append({
            "sale_group_id": group_id,
            "sale_date": items_in_group[0].sale_date.strftime('%d.%m.%Y %H:%M'),
            "customer_name": items_in_group[0].customer_name or "-",
            "sold_by": sold_by_user.username if sold_by_user else "-",
            "totals": totals,
            "items": [{
                "product_code": item.product_code,
                "product_name": get_product_name_by_id(item.product_code, company_id) or "-",
                "color_name": get_color_name(get_product_color_id(item.product_code, company_id), company_id) or "-",
                "quantity": item.quantity,
                "price_at_sale": item.price_at_sale,
                "discount": item.discount,
                "total_price": item.total_price,
                "currency": item.currency,
                "sizes_breakdown": item.sizes # <-- YENİ EKLENEN VE SORUNU ÇÖZEN SATIR
            } for item in items_in_group]
        })
        
    return paginated_groups, results

# ////////---------Bildirim sistemi---------////////

def create_notification(user_id, company_id, title, message, notif_type='info'):
    """
    Belirli bir kullanıcıya bildirim gönderir.
    notif_type seçenekleri: 'info', 'warning', 'success', 'error'
    """
    try:
        new_notif = Notification(
            user_id=user_id,
            company_id=company_id,
            title=title,
            message=message,
            type=notif_type
        )
        db.session.add(new_notif)
        db.session.commit()
        return True
    except Exception as e:
        print(f"Bildirim oluşturma hatası: {e}")
        db.session.rollback()
        return False

def notify_role_in_company(company_id, role, title, message, notif_type='info'):
    """
    Şirketteki belirli bir role sahip herkese (örn: tüm 'manager'lara) bildirim atar.
    """
    users = User.query.filter_by(company=company_id, role=role).all()
    count = 0
    for user in users:
        create_notification(user.id, company_id, title, message, notif_type)
        count += 1
    return count

def get_user_notifications(user_id, limit=50):
    """Kullanıcının bildirimlerini tarihe göre (yeni en üstte) getirir."""
    return Notification.query.filter_by(user_id=user_id)\
        .order_by(Notification.created_at.desc())\
        .limit(limit).all()

def mark_notification_read(notification_id, user_id):
    """Tek bir bildirimi okundu yapar."""
    notif = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if notif:
        notif.is_read = True
        db.session.commit()
        return True
    return False

def mark_all_read(user_id):
    """Kullanıcının tüm bildirimlerini okundu yapar."""
    try:
        Notification.query.filter_by(user_id=user_id, is_read=False).update({Notification.is_read: True})
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        return False

def send_bulk_notification(user_ids, title, raw_message, notif_type):
    """
    Seçili kullanıcılara, şablon değişkenlerini ({username} vb.) işleyerek bildirim gönderir.
    """
    count = 0
    # user_ids listesindeki ID'lere sahip kullanıcıları çek
    users = User.query.filter(User.id.in_(user_ids)).all()
    
    for user in users:
        # Şablon değişkenlerini değiştir (Template Engine Mantığı)
        personalized_message = raw_message\
            .replace('{username}', user.username)\
            .replace('{company}', user.company or 'Belirtilmemiş')\
            .replace('{phone}', user.phone or 'Belirtilmemiş')\
            .replace('{role}', user.role)

        # Bildirimi oluştur
        if create_notification(user.id, user.company or 'Sistem', title, personalized_message, notif_type):
            count += 1
            
    return count

def create_order_db(company_id, user_id, items, customer_name, note, is_debt, amount_paid):
    """
    Yeni bir sipariş oluşturur.
    Fiyatları doğru hesaplar ve sadece yetkili (permission sahibi) kişilere bildirim atar.
    """
    
    # 1. Toplam Tutar Hesaplama
    total_amount = 0
    currency = "TL"
    
    if items and len(items) > 0:
        currency = items[0].get('currency', 'TL')
        
        for item in items:
            try:
                # Güvenli tip dönüşümleri
                item_price = float(item.get('price', 0))
                discount = float(item.get('discount', 0))
                quantity = int(item.get('quantity', 1))
                
                # Tekli Satış mı Seri Satış mı?
                if item.get('is_single', False):
                    # TEKLİ SATIŞ:
                    # Frontend'de hesaplanan 'price' zaten o satırın toplamıdır.
                    # Formül: Satır Fiyatı - İndirim
                    row_total = item_price - discount
                else:
                    # SERİ SATIŞ:
                    # Frontend'de 'price' bir serinin birim fiyatıdır.
                    # Formül: (Birim Seri Fiyatı * Adet) - İndirim
                    row_total = (item_price * quantity) - discount
                
                total_amount += row_total
            except (ValueError, TypeError):
                # Veri hatası varsa o satırı atla, işlemi durdurma
                continue

    # 2. Veritabanı Kaydı (Order)
    new_order = Order(
        company_id=company_id,
        user_id=user_id,
        items=items,
        customer_name=customer_name,
        note=note,
        total_amount=total_amount,
        currency=currency,
        is_debt=is_debt,
        amount_paid=amount_paid,
        status=OrderStatus.PENDING
    )
    
    db.session.add(new_order)
    db.session.commit()

    # 3. Gelişmiş Bildirim Gönderimi (Rol ve İzin Bazlı)
    try:
        # Şirketteki şu kriterlere uyan kullanıcıları bul:
        # - Rolü 'admin' veya 'manager' olanlar
        # - VEYA 'can_view_sales_history' yetkisi olanlar
        # - VEYA 'can_manage_debts' yetkisi olanlar
        
        authorized_users = User.query.filter(
            User.company == company_id,
            or_(
                User.role.in_(['admin', 'manager']),
                User.can_view_sales_history == True,
                User.can_manage_debts == True
            )
        ).all()

        msg_title = "📦 Yeni Sipariş Bekliyor"
        msg_body = f"{customer_name or 'Misafir'} müşterisi için {total_amount:.2f} {currency} tutarında yeni sipariş oluşturuldu."

        count = 0
        for target_user in authorized_users:
            # Siparişi oluşturan kişinin kendisine bildirim gitmesin
            if target_user.id != user_id:
                create_notification(
                    user_id=target_user.id,
                    company_id=company_id,
                    title=msg_title,
                    message=msg_body,
                    notif_type="info"
                )
                count += 1
        
        print(f"Sipariş ID: {new_order.id} için {count} yetkiliye bildirim gönderildi.")

    except Exception as e:
        print(f"Bildirim gönderme hatası: {e}")

    return new_order.id

def get_order_history_db(company_id, limit=50):
    """Onaylanmış veya Reddedilmiş son siparişleri getirir."""
    return Order.query.filter(
        Order.company_id == company_id, 
        Order.status != OrderStatus.PENDING
    ).order_by(Order.created_at.desc()).limit(limit).all()

def revert_order_status(order_id, company_id):
    """
    Siparişi tekrar PENDING durumuna getirir.
    Eğer sipariş daha önce ONAYLANMIŞSA:
      1. İlgili satış kayıtlarını bulur.
      2. Düşülen stokları ürünlere geri ekler.
      3. Satış kayıtlarını siler.
      4. Varsa borç işlemini siler.
    """
    order = Order.query.filter_by(id=order_id, company_id=company_id).first()
    if not order:
        return False

    try:
        # Eğer sipariş daha önce ONAYLANMIŞ ise stokları geri yükle ve satışları sil
        if order.status == OrderStatus.APPROVED and order.related_sale_group_id:
            sales = Sale.query.filter_by(sale_group_id=order.related_sale_group_id).all()
            
            for sale in sales:
                product = Product.query.filter_by(code=sale.product_code, company_id=company_id).first()
                if product:
                    # 1. Seri Stoğunu Geri Yükle (Eğer seri satışsa)
                    # Not: Parça satışta quantity 0 olabiliyordu, o yüzden Sale modeline kaydettiğimiz quantity'i kullanıyoruz.
                    # Ancak asıl önemli olan 'sizes' JSON içindeki detaylardır.
                    
                    # 2. Beden Stoklarını Geri Yükle
                    if sale.sizes:
                        product_sizes = product.sizes or []
                        for sold_size in sale.sizes:
                            # sold_size: {'value': 'S', 'quantity': 5}
                            for p_size in product_sizes:
                                if p_size['value'] == sold_size['value']:
                                    p_size['stock'] += sold_size['quantity']
                        
                        product.sizes = product_sizes
                        flag_modified(product, "sizes")

                    # 3. Ana Seri Stoğunu Yeniden Hesapla (Standardizasyon için)
                    new_seri_stock = 0
                    if product.sizes:
                        seri_components = [s for s in product.sizes if s.get('number', 0) > 0]
                        if seri_components:
                            possible_seris = [s.get('stock', 0) // s.get('number', 0) for s in seri_components]
                            new_seri_stock = min(possible_seris) if possible_seris else 0
                    product.stock = new_seri_stock

                # 4. Satış Kaydını Sil
                db.session.delete(sale)

            # 5. Borç İşlemini Sil (Varsa)
            # İlgili satış grubuna ait borç kaydını bul ve sil
            debt_transaction = DebtTransaction.query.filter_by(sale_group_id=order.related_sale_group_id).first()
            if debt_transaction:
                # Müşterinin bakiyesinden de düşmek gerekir
                customer = Customer.query.get(debt_transaction.customer_id)
                if customer and customer.debt:
                    curr = debt_transaction.currency
                    amt = debt_transaction.amount
                    if curr in customer.debt:
                        customer.debt[curr] -= amt
                        if customer.debt[curr] <= 0:
                            del customer.debt[curr]
                        flag_modified(customer, "debt")
                
                db.session.delete(debt_transaction)

        # Sipariş Durumunu Sıfırla
        order.status = OrderStatus.PENDING
        order.related_sale_group_id = None # İlişkiyi kopar
        order.processed_at = None
        
        db.session.commit()
        return True

    except Exception as e:
        db.session.rollback()
        print(f"Revert Error: {e}")
        return False

def get_pending_orders(company_id):
    """Bekleyen siparişleri getirir."""
    return Order.query.filter_by(company_id=company_id, status=OrderStatus.PENDING).order_by(Order.created_at.desc()).all()

def process_order_approval(order_id, user_id, action):
    """Siparişi onaylar veya reddeder."""
    order = Order.query.get(order_id)
    if not order:
        return False, "Sipariş bulunamadı."
    
    if order.status != OrderStatus.PENDING:
        return False, "Bu sipariş zaten işlenmiş."

    now = datetime.utcnow()

    if action == 'reject':
        order.status = OrderStatus.REJECTED
        order.processed_at = now
        order.picked_at = now
        db.session.commit()
        return True, "Sipariş reddedildi."

    if action == 'approve':
        try:
            sale_group_id = str(uuid.uuid4())
            total_sale_price_by_currency = {}
            
            # --- JSON PARSE KONTROLÜ ---
            items_data = order.items
            if isinstance(items_data, str):
                import json
                items_data = json.loads(items_data)
            
            # Siparişteki ürünleri döngüye al
            for item in items_data:
                product_code = item.get('code')
                product = Product.query.filter_by(code=product_code, company_id=order.company_id).first()
                
                if not product:
                    raise ValueError(f"Ürün bulunamadı: {product_code}")

                is_single_sale = item.get('is_single', False)
                quantity = int(item.get('quantity', 0))
                price_val = float(item.get('price', 0)) 
                discount = float(item.get('discount', 0))
                currency = item.get('currency', 'TL')
                
                # --- Stok ve Fiyat Ayarlamaları ---
                sale_quantity_record = 0
                sale_unit_price = 0
                current_item_total_price = 0
                
                sizes_breakdown_for_record = []
                sizes_data = product.sizes or []

                if is_single_sale:
                    # Tekli satışta price_val zaten toplam fiyattır
                    current_item_total_price = price_val - discount
                    single_sizes_sold = item.get('single_sizes', {})
                    
                    # Birim fiyat hesabı (Raporlama için)
                    total_pieces = sum(int(v) for v in single_sizes_sold.values())
                    sale_unit_price = (current_item_total_price + discount) / total_pieces if total_pieces > 0 else 0
                    
                    # Stok düşümü
                    for size_info in sizes_data:
                        sz_val = size_info.get('value')
                        if sz_val in single_sizes_sold:
                            qty_sold = int(single_sizes_sold[sz_val])
                            if size_info['stock'] < qty_sold:
                                raise ValueError(f"Yetersiz stok: {product.name} ({sz_val})")
                            size_info['stock'] -= qty_sold
                            sizes_breakdown_for_record.append({"value": sz_val, "quantity": qty_sold})
                
                else:
                    # Seri Satış
                    sale_unit_price = price_val
                    current_item_total_price = (price_val * quantity) - discount
                    sale_quantity_record = quantity
                    
                    if product.stock < quantity:
                         raise ValueError(f"Yetersiz seri stoğu: {product.name}")
                    
                    # Stok düşümü
                    for size_info in sizes_data:
                        pieces_in_one_series = size_info.get('number', 0)
                        sold_this_size = quantity * pieces_in_one_series
                        if size_info['stock'] < sold_this_size:
                             raise ValueError(f"Yetersiz beden stoğu: {product.name}")
                        size_info['stock'] -= sold_this_size
                        sizes_breakdown_for_record.append({"value": size_info.get('value'), "quantity": sold_this_size})

                # Ürünü güncelle
                product.sizes = sizes_data
                flag_modified(product, "sizes")
                
                # Ana seri stoğunu tekrar hesapla
                new_seri_stock = 0
                if product.sizes:
                    seri_components = [s for s in product.sizes if s.get('number', 0) > 0]
                    if seri_components:
                        possible_seris = [s.get('stock', 0) // s.get('number', 0) for s in seri_components]
                        new_seri_stock = min(possible_seris) if possible_seris else 0
                product.stock = new_seri_stock

                # Satış Kaydı
                new_sale = Sale(
                    sale_group_id=sale_group_id,
                    product_id=product.id,
                    product_code=product.code,
                    user_id=order.user_id,
                    company_id=order.company_id,
                    quantity=sale_quantity_record,
                    price_at_sale=sale_unit_price,
                    discount=discount,
                    total_price=current_item_total_price,
                    currency=currency,
                    customer_name=order.customer_name,
                    sale_notes=f"{order.note or ''} (Sipariş Onayı)",
                    sizes=sizes_breakdown_for_record,
                    sale_date=datetime.utcnow()
                )
                db.session.add(new_sale)
                total_sale_price_by_currency[currency] = total_sale_price_by_currency.get(currency, 0) + current_item_total_price

            # Borç İşlemi
            if order.is_debt and order.customer_name:
                customer = Customer.query.filter_by(name=order.customer_name, company_id=order.company_id).first()
                if customer:
                    if len(total_sale_price_by_currency) == 1:
                        currency, total_price = next(iter(total_sale_price_by_currency.items()))
                        debt_to_add = total_price - (order.amount_paid or 0)
                        if debt_to_add > 0:
                            if customer.debt is None: customer.debt = {}
                            current_debt = customer.debt.get(currency, 0)
                            customer.debt[currency] = current_debt + debt_to_add
                            flag_modified(customer, "debt")
                            
                            debt_record = DebtTransaction(
                                customer_id=customer.id,
                                user_id=order.user_id,
                                company_id=order.company_id,
                                sale_group_id=sale_group_id,
                                transaction_type=TransactionType.DEBIT,
                                amount=debt_to_add,
                                currency=currency,
                                note=f"Veresiye (Sipariş) - ID: {sale_group_id[:8]}"
                            )
                            db.session.add(debt_record)
  
            order.status = OrderStatus.APPROVED
            order.related_sale_group_id = sale_group_id
            order.processed_at = now
            db.session.commit()
            return True, "Sipariş onaylandı."

        except Exception as e:
            db.session.rollback()
            print(f"Approval Error: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Hata: {str(e)}"
        
def get_orders_unified_db(company_id, page=1, per_page=15, status_filter='ALL', customer_filter=None, date_filter='ALL'):
    """
    Siparişleri birleştirilmiş, filtrelenmiş ve sayfalanmış şekilde getirir.
    """
    query = Order.query.filter_by(company_id=company_id)

    # 1. Statü Filtresi
    if status_filter and status_filter != 'ALL':
        if status_filter == 'PENDING':
            query = query.filter(Order.status == OrderStatus.PENDING)
        elif status_filter == 'APPROVED':
            query = query.filter(Order.status == OrderStatus.APPROVED)
        elif status_filter == 'REJECTED':
            query = query.filter(Order.status == OrderStatus.REJECTED)

    # 2. Müşteri Filtresi
    if customer_filter and customer_filter != 'ALL':
        query = query.filter(Order.customer_name == customer_filter)

    # 3. Tarih Filtresi
    now = datetime.utcnow()
    if date_filter == 'TODAY':
        # Bugünün başlangıcı
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif date_filter == 'THIS_WEEK':
        # Bu haftanın başı (Pazartesi)
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif date_filter == 'THIS_MONTH':
        # Bu ayın başı
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    
    # Sıralama: En yeni en üstte
    query = query.order_by(Order.created_at.desc())

    # Sayfalama
    return query.paginate(page=page, per_page=per_page, error_out=False)

def parse_excel_prices(price_str):
    """ '100 TL, 10 USD' stringini JSON'a çevirir. """
    prices = {"TL": 0, "USD": 0, "EURO": 0}
    if pd.isna(price_str): return prices
    
    raw = str(price_str).replace(';', ',') 
    parts = raw.split(',')
    
    for part in parts:
        p = part.strip().split(' ')
        try:
            val = float(p[0])
            curr = p[1].upper() if len(p) > 1 else "TL"
            if curr in prices: prices[curr] = val
        except: pass
    return prices

def process_excel_import_db(file_path, mapping, company_id):
    try:
        df = pd.read_excel(file_path)
        df = df.where(pd.notnull(df), None)
        
        # Geçerli Verileri Çek (Doğrulama için)
        valid_categories = {c.name.strip().lower(): c.name for c in Category.query.filter_by(company_id=company_id).all()}
        valid_shelves = {s.code.strip().lower(): s.code for s in Shelf.query.filter_by(company_id=company_id).all()}
        valid_colors = {c.name.strip().lower(): c for c in Color.query.filter_by(company_id=company_id).all()}

        added = 0
        errors = []
        
        for index, row in df.iterrows():
            row_num = index + 2
            
            p_name = row.get(mapping.get('db_name'))
            if not p_name:
                errors.append(f"Satır {row_num}: Ürün adı boş.")
                continue

            # Kategori Kontrol
            excel_cat = str(row.get(mapping.get('db_category'), '')).strip()
            if excel_cat.lower() not in valid_categories:
                errors.append(f"Satır {row_num}: '{excel_cat}' kategorisi bulunamadı.")
                continue
            final_category = valid_categories[excel_cat.lower()]

            # Raf Kontrol
            excel_shelf = str(row.get(mapping.get('db_shelf'), '')).strip()
            if excel_shelf.lower() not in valid_shelves:
                errors.append(f"Satır {row_num}: '{excel_shelf}' rafı bulunamadı.")
                continue
            final_shelf = valid_shelves[excel_shelf.lower()]

            # Renk Kontrol
            excel_color = str(row.get(mapping.get('db_color'), '')).strip()
            if excel_color.lower() not in valid_colors:
                errors.append(f"Satır {row_num}: '{excel_color}' rengi bulunamadı.")
                continue
            color_obj = valid_colors[excel_color.lower()]
            final_color_id = color_obj.code 

            # Otomatik Kod Üretimi
            stock_id = str(generate_unique_stock_id(company_id))
            generated_code = f"{stock_id}{final_shelf}{final_color_id}"

            # Diğer Alanlar
            p_desc = row.get(mapping.get('db_desc'))
            p_currency = {"TL": 0, "USD": 0, "EURO": 0}
            if mapping.get('db_price'):
                p_currency = parse_excel_prices(row.get(mapping['db_price']))

            p_sizes = []
            p_stock = 0
            if mapping.get('db_stock') or mapping.get('db_series'):
                p_sizes = parse_excel_sizes(row.get(mapping.get('db_stock')), row.get(mapping.get('db_series')))
                seri_parts = [s for s in p_sizes if s['number'] > 0]
                if seri_parts:
                    possible = [s['stock'] // s['number'] for s in seri_parts]
                    p_stock = min(possible)

            new_product = Product(
                code=generated_code,
                name=p_name,
                company_id=company_id,
                stock_id=stock_id,
                category=final_category,
                shelf=final_shelf,
                color_id=final_color_id,
                description=p_desc,
                currency=p_currency,
                sizes=p_sizes,
                stock=p_stock
            )
            
            db.session.add(new_product)
            added += 1

        if added > 0:
            db.session.commit()
            
        status = "ok" if not errors else "partial_error"
        if added == 0 and errors: status = "error"
        
        return {
            "status": status, 
            "added": added, 
            "updated": 0, 
            "errors": errors
        }
        
    except Exception as e:
        db.session.rollback()
        print(f"Excel Import Error: {e}")
        return {"status": "error", "message": str(e)}

def parse_excel_sizes(stock_str, series_str, size_mapping=None):
    """
    Regex kullanarak bozuk formatlı (XLL15, S 10 vb.) stok verilerini de okur.
    """
    if size_mapping is None: size_mapping = {}
    size_map = {}
    
    # Yardımcı: Regex ile ayırma
    # Mantık: (Herhangi bir metin) + (Opsiyonel ayırıcılar) + (Sayı)
    # Örn: "XLL15" -> "XLL", "15"
    # Örn: "S: 10" -> "S", "10"
    regex_pattern = r"(.+?)\s*[:\-\s]*\s*(\d+)$"

    def get_clean_key(raw_key):
        k = raw_key.strip()
        # Mapping varsa çevir, yoksa olduğu gibi bırak
        return size_mapping.get(k, k)

    # 1. Stokları işle
    if pd.notna(stock_str):
        # Virgülle ayrılmış parçaları al
        for item in str(stock_str).split(','):
            item = item.strip()
            if not item: continue
            
            # Regex ile parçala
            match = re.search(regex_pattern, item)
            if match:
                raw_size = match.group(1).strip() # Örn: XLL
                qty = int(match.group(2))         # Örn: 15
                
                clean_k = get_clean_key(raw_size)
                size_map[clean_k] = {"stock": qty, "number": 0}
            elif ':' in item:
                # Yedek mantık (eski usül)
                try:
                    k, v = item.split(':')
                    clean_k = get_clean_key(k)
                    size_map[clean_k] = {"stock": int(v), "number": 0}
                except: pass

    # 2. Seri içeriğini işle
    if pd.notna(series_str):
        for item in str(series_str).split(','):
            item = item.strip()
            if not item: continue

            match = re.search(regex_pattern, item)
            if match:
                raw_size = match.group(1).strip()
                qty = int(match.group(2))
                
                clean_k = get_clean_key(raw_size)
                if clean_k in size_map:
                    size_map[clean_k]["number"] = qty
                else:
                    size_map[clean_k] = {"stock": 0, "number": qty}

    return [{"value": k, "stock": v['stock'], "number": v['number']} for k, v in size_map.items()]


def analyze_excel_data(file_path, mapping, company_id):
    df = pd.read_excel(file_path)
    df = df.where(pd.notnull(df), None)

    # DB Verileri
    db_cats = {c.name.strip().lower(): c.name for c in Category.query.filter_by(company_id=company_id).all()}
    db_shelves = {s.code.strip().lower(): s.code for s in Shelf.query.filter_by(company_id=company_id).all()}
    db_colors = {c.name.strip().lower(): c.name for c in Color.query.filter_by(company_id=company_id).all()}
    
    # Geçerli Bedenler
    valid_sizes_set = set()
    all_categories = Category.query.filter_by(company_id=company_id).all()
    for cat in all_categories:
        if cat.sizes:
            for s in cat.sizes:
                valid_sizes_set.add(str(s).strip())

    missing_values = {
        "categories": set(),
        "shelves": set(),
        "colors": set(),
        "sizes": set()
    }

    # Beden ayrıştırma Regex'i
    regex_pattern = r"(.+?)\s*[:\-\s]*\s*(\d+)$"

    for _, row in df.iterrows():
        # ... (Diğer kontroller aynı) ...
        if mapping.get('db_category'):
            val = str(row.get(mapping['db_category'], '')).strip()
            if val and val.lower() not in db_cats: missing_values["categories"].add(val)
        
        if mapping.get('db_shelf'):
            val = str(row.get(mapping['db_shelf'], '')).strip()
            if val and val.lower() not in db_shelves: missing_values["shelves"].add(val)

        if mapping.get('db_color'):
            val = str(row.get(mapping['db_color'], '')).strip()
            if val and val.lower() not in db_colors: missing_values["colors"].add(val)

        # --- GÜNCELLENMİŞ BEDEN KONTROLÜ ---
        raw_size_strings = []
        if mapping.get('db_stock'): raw_size_strings.append(str(row.get(mapping['db_stock'], '')))
        if mapping.get('db_series'): raw_size_strings.append(str(row.get(mapping['db_series'], '')))
        
        for raw_str in raw_size_strings:
            if raw_str and raw_str.lower() != 'nan':
                # Virgülle ayır: "X:10, XLL15" -> ["X:10", " XLL15"]
                for item in raw_str.split(','):
                    item = item.strip()
                    if not item: continue

                    # Regex ile bedeni (Harf kısmını) çek
                    # XLL15 -> match.group(1) = "XLL"
                    match = re.search(regex_pattern, item)
                    
                    if match:
                        size_key = match.group(1).strip()
                        # Eğer bu beden veritabanında yoksa hataya ekle
                        if size_key not in valid_sizes_set:
                            missing_values["sizes"].add(size_key)
                    elif ':' in item:
                        # Regex yakalayamazsa eski yöntemle dene
                        size_key = item.split(':')[0].strip()
                        if size_key not in valid_sizes_set:
                            missing_values["sizes"].add(size_key)

    return {
        "missing_categories": list(missing_values["categories"]),
        "missing_shelves": list(missing_values["shelves"]),
        "missing_colors": list(missing_values["colors"]),
        "missing_sizes": list(missing_values["sizes"]),
        
        "valid_categories": list(db_cats.values()),
        "valid_shelves": list(db_shelves.values()),
        "valid_colors": list(db_colors.values()),
        "valid_sizes": sorted(list(valid_sizes_set))
    }

def add_stock_to_shelf_db(product_code, shelf_code, quantities, company_id):
    """
    Rafa ürün eklerken KATEGORİ TANIMINA göre beden doğrulaması yapar.
    1. Raf var mı?
    2. Ürün var mı?
    3. Girilen bedenler, ürünün KATEGORİSİNDE tanımlı mı? (Kategori kuralı)
    4. Rafa ekle -> Ürünün beden listesini güncelle (Yoksa oluştur).
    """
    try:
        # 1. RAF KONTROLÜ
        shelf_exists = Shelf.query.filter_by(code=shelf_code, company_id=company_id).first()
        if not shelf_exists:
            return {
                "status": "error", 
                "message": f"HATA: '{shelf_code}' kodlu raf sistemde tanımlı değil! Lütfen önce rafı tanımlayın."
            }

        # 2. ÜRÜN KONTROLÜ
        product = Product.query.filter_by(code=product_code, company_id=company_id).first()
        if not product:
            return {"status": "error", "message": "Ürün bulunamadı!"}

        # --- 3. KATEGORİ BAZLI BEDEN DOĞRULAMA (YENİ MANTIK) ---
        # Ürünün kategorisini bul
        category_name = product.category
        category_obj = Category.query.filter_by(name=category_name, company_id=company_id).first()

        # Kategoride izin verilen bedenler
        allowed_sizes = []
        if category_obj and category_obj.sizes:
            allowed_sizes = category_obj.sizes # Örn: ['S', 'M', 'L', 'XL']
        
        # Eğer kategori bulunamazsa veya bedensiz bir kategoriyse, "STD" dışında girişe izin verme veya hata dön.
        # Esneklik için: Eğer kategoride hiç beden yoksa (boş liste), sadece "STD" kabul edelim veya ürün bedensizdir diyelim.
        
        if allowed_sizes:
            for input_size in quantities.keys():
                if input_size not in allowed_sizes:
                    return {
                        "status": "error",
                        "message": f"HATA: '{input_size}' bedeni '{category_name}' kategorisinde TANIMLI DEĞİL. İzin verilenler: {', '.join(allowed_sizes)}"
                    }
        else:
            # Kategoride beden yok ama kullanıcı beden girmeye çalışıyor (STD harici)
            for input_size in quantities.keys():
                if input_size != "STD" and input_size != "Standart":
                     return {
                        "status": "error",
                        "message": f"HATA: '{category_name}' kategorisi bedensiz (standart) olarak tanımlanmış. '{input_size}' eklenemez."
                    }
        # -------------------------------------------------------

        # Eklenecek toplam adedi hesapla
        total_added_qty = sum(int(v) for v in quantities.values())
        if total_added_qty <= 0:
             return {"status": "error", "message": "Lütfen en az bir adet giriniz."}

        # 4. RAF VERİSİNİ GÜNCELLE (shelves)
        current_shelves = list(product.shelves) if product.shelves else []
        found_shelf = False
        
        for s in current_shelves:
            if s['code'] == shelf_code:
                s['quantity'] = int(s.get('quantity', 0)) + total_added_qty
                
                existing_sizes = s.get('sizes', {})
                for size, qty in quantities.items():
                    qty = int(qty)
                    if qty > 0:
                        existing_sizes[size] = existing_sizes.get(size, 0) + qty
                
                s['sizes'] = existing_sizes
                found_shelf = True
                break
        
        if not found_shelf:
            clean_sizes = {k: int(v) for k, v in quantities.items() if int(v) > 0}
            current_shelves.append({
                "code": shelf_code, 
                "quantity": total_added_qty,
                "sizes": clean_sizes
            })
            
        product.shelves = current_shelves
        flag_modified(product, "shelves")

        # 5. ÜRÜN BEDEN STOKLARINI GÜNCELLE (Product.sizes)
        # Burası önemli: Eğer ürünün sizes listesinde o beden henüz yoksa (ama kategoride varsa),
        # buraya yeni bir kayıt olarak eklemeliyiz.
        
        current_product_sizes = list(product.sizes) if product.sizes else []
        
        # Kolay erişim için mevcut bedenleri haritala: {'S': {obj}, 'M': {obj}}
        size_map = {item['value']: item for item in current_product_sizes}

        for q_size, q_qty in quantities.items():
            q_qty = int(q_qty)
            if q_qty > 0:
                if q_size in size_map:
                    # Varsa stoğu artır
                    size_map[q_size]['stock'] = int(size_map[q_size].get('stock', 0)) + q_qty
                else:
                    # Yoksa yeni oluştur (Çünkü kategori kontrolünden geçti, ekleyebiliriz)
                    new_size_obj = {
                        "value": q_size,
                        "stock": q_qty,
                        "number": 0 # Seri içinde kaç tane olduğu varsayılan 0
                    }
                    size_map[q_size] = new_size_obj
                    current_product_sizes.append(new_size_obj)

        product.sizes = current_product_sizes
        flag_modified(product, "sizes")

        # 6. ANA SERİ STOĞUNU HESAPLA
        # (Sadece seri içeriği tanımlanmış bedenlere göre hesaplanır)
        if product.sizes:
            seri_components = [s for s in product.sizes if int(s.get('number', 0)) > 0]
            if seri_components:
                possible_seris = [int(s.get('stock', 0)) // int(s.get('number', 0)) for s in seri_components]
                product.stock = min(possible_seris)
            else:
                # Seri tanımı yoksa toplam parça sayısını yaz
                product.stock = sum(int(s.get('stock', 0)) for s in product.sizes)
        else:
            product.stock += total_added_qty

        db.session.commit()
        return {"status": "ok", "message": f"{total_added_qty} adet ürün {shelf_code} rafına eklendi."}

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    
def calculate_picking_list(order_id, company_id):
    """
    Sipariş için raf toplama listesi oluşturur.
    Hangi ürünün, hangi bedenden, hangi raftan alınacağını hesaplar.
    """
    order = Order.query.get(order_id)
    if not order or order.company_id != company_id:
        return {"status": "error", "message": "Sipariş bulunamadı"}

    picking_list = [] # Sonuç listesi
    
    # Sipariş kalemlerini gez
    for item in order.items:
        product_code = item.get('code')
        product = Product.query.filter_by(code=product_code, company_id=company_id).first()
        
        if not product: continue
        
        # 1. İhtiyaç Duyulan Bedenleri ve Adetleri Belirle
        needed_sizes = {} # {"S": 5, "M": 2}
        
        is_single = item.get('is_single', False)
        order_qty = int(item.get('quantity', 0))

        if is_single:
            # Tekli satışsa direkt bedenleri al
            # item['single_sizes'] formatı: {"S": 2, "M": 1}
            needed_sizes = item.get('single_sizes', {})
            # String gelen değerleri integer'a çevir
            needed_sizes = {k: int(v) for k, v in needed_sizes.items()}
        else:
            # Seri satışsa, ürünün seri tanımını (sizes) kullanarak toplamı bul
            # product.sizes formatı: [{"value": "S", "number": 1}, ...]
            if product.sizes:
                for s_def in product.sizes:
                    size_label = s_def.get('value')
                    per_series_count = int(s_def.get('number', 0))
                    total_needed = per_series_count * order_qty
                    if total_needed > 0:
                        needed_sizes[size_label] = total_needed
        
        # 2. Rafları Tara ve Eşleştir (Greedy Algoritması)
        # Ürünün raflardaki dağılımı: product.shelves -> [{"code": "A1", "sizes": {"S":10, "M":5}}]
        
        available_shelves = product.shelves or []
        
        # İhtiyaç duyulan her beden için rafları gez
        for size, qty_needed in needed_sizes.items():
            qty_remaining = qty_needed
            
            for shelf in available_shelves:
                if qty_remaining <= 0: break
                
                shelf_code = shelf.get('code')
                shelf_sizes = shelf.get('sizes', {}) # Bu raftaki bedenler
                
                # Bu rafta aradığımız bedenden var mı?
                available_in_shelf = int(shelf_sizes.get(size, 0))
                
                if available_in_shelf > 0:
                    # Ne kadar alabiliriz?
                    take_amount = min(qty_remaining, available_in_shelf)
                    
                    picking_list.append({
                        "shelf": shelf_code,
                        "product_code": product.code,
                        "product_name": product.name,
                        "color": get_color_name(product.color_id, company_id),
                        "size": size,
                        "quantity": take_amount,
                        "status": "ok"
                    })
                    
                    qty_remaining -= take_amount
            
            # Eğer raflar bitti ama hala ihtiyaç varsa
            if qty_remaining > 0:
                picking_list.append({
                    "shelf": "YETERSİZ STOK",
                    "product_code": product.code,
                    "product_name": product.name,
                    "color": get_color_name(product.color_id, company_id),
                    "size": size,
                    "quantity": qty_remaining,
                    "status": "missing"
                })

    # Listeyi Raf Koduna Göre Sırala (Depocu aynı raftaki işleri bitirsin)
    picking_list.sort(key=lambda x: x['shelf'])
    
    return {"status": "ok", "picking_list": picking_list}

def process_excel_import_final(file_path, col_mapping, val_mapping, company_id):
    try:
        df = pd.read_excel(file_path)
        df = df.where(pd.notnull(df), None)
        
        # ... (Kategori ve Renk DB çekme işlemleri aynı) ...
        db_categories = {c.name.strip().lower(): c.name for c in Category.query.filter_by(company_id=company_id).all()}
        db_shelves = {s.code.strip().lower(): s.code for s in Shelf.query.filter_by(company_id=company_id).all()}
        db_colors_obj = {c.name.strip().lower(): c for c in Color.query.filter_by(company_id=company_id).all()}

        added = 0
        added_names = []
        errors = []

        for index, row in df.iterrows():
            row_num = index + 2
            
            # ... (Kategori ve Renk eşleştirme kodları aynı) ...
            raw_cat = str(row.get(col_mapping.get('db_category'), '')).strip()
            mapped_cat = val_mapping.get('categories', {}).get(raw_cat, raw_cat)
            if mapped_cat.lower() not in db_categories:
                errors.append(f"Satır {row_num}: Kategori hatası ({raw_cat})")
                continue
            final_cat = db_categories[mapped_cat.lower()]

            raw_color = str(row.get(col_mapping.get('db_color'), '')).strip()
            mapped_color = val_mapping.get('colors', {}).get(raw_color, raw_color)
            if mapped_color.lower() not in db_colors_obj:
                errors.append(f"Satır {row_num}: Renk hatası ({raw_color})")
                continue
            final_color_code = db_colors_obj[mapped_color.lower()].code

            # --- RAF İŞLEMİ ---
            # Excel'den gelen "A1" bilgisini alıp shelves JSON yapısına çeviriyoruz
            # Ancak kod üretmek için "Ana Raf" olarak kullanacağız.
            raw_shelf = str(row.get(col_mapping.get('db_shelf'), '')).strip()
            mapped_shelf = val_mapping.get('shelves', {}).get(raw_shelf, raw_shelf)
            
            if mapped_shelf.lower() not in db_shelves:
                errors.append(f"Satır {row_num}: Raf hatası ({raw_shelf})")
                continue
            final_shelf_code = db_shelves[mapped_shelf.lower()]

            # --- DİĞER VERİLER (İsim, Fiyat, Beden) ---
            p_name = row.get(col_mapping.get('db_name'))
            p_desc = row.get(col_mapping.get('db_desc'))
            
            p_currency = {"TL": 0, "USD": 0, "EURO": 0}
            if col_mapping.get('db_price'): p_currency = parse_excel_prices(row.get(col_mapping['db_price']))

            p_sizes = []
            p_stock = 0 # Toplam Stok
            if col_mapping.get('db_stock') or col_mapping.get('db_series'):
                size_map_dict = val_mapping.get('sizes', {})
                p_sizes = parse_excel_sizes(row.get(col_mapping.get('db_stock')), row.get(col_mapping.get('db_series')), size_map_dict)
                seri_parts = [s for s in p_sizes if s['number'] > 0]
                if seri_parts:
                    p_stock = min([s['stock'] // s['number'] for s in seri_parts])

            # --- STOK HESABI VE RAF JSON OLUŞTURMA ---
            # Excel'den gelen stoğu, o satırdaki rafa atıyoruz.
            # NOT: Bu basit bir varsayımdır. Excel'de 100 stok varsa, hepsi A1 rafındadır diyoruz.
            current_shelves_json = [{"code": final_shelf_code, "quantity": p_stock}]

            # --- KAYIT ---
            stock_id = str(generate_unique_stock_id(company_id))
            # Kod üretimi: stock_id + Raf + Renk (Benzersizlik için Raf hala kodun bir parçası olabilir veya çıkarılabilir. 
            # Raf değişince kod değişmesin istiyorsanız rafı koddan çıkarmalısınız. Şimdilik yapıyı koruyoruz)
            generated_code = f"{stock_id}{final_shelf_code}{final_color_code}"
            
            # Çakışma kontrolü
            if Product.query.filter_by(code=generated_code, company_id=company_id).first():
                 stock_id = str(generate_unique_stock_id(company_id))
                 generated_code = f"{stock_id}{final_shelf_code}{final_color_code}"

            new_product = Product(
                code=generated_code, name=p_name, company_id=company_id, stock_id=stock_id,
                category=final_cat, 
                color_id=final_color_code, description=p_desc, currency=p_currency, 
                sizes=p_sizes, stock=p_stock,
                shelves=current_shelves_json 
            )
            db.session.add(new_product)
            added += 1
            added_names.append(p_name)

        if added > 0: db.session.commit()
        
        status = "ok" if not errors else "partial_error"
        if added == 0 and errors: status = "error"
        return {"status": status, "added": added, "added_names": added_names, "errors": errors}

    except Exception as e:
        db.session.rollback()
        return {"status": "error", "message": str(e)}

def delete_product_by_code_helper(code, company_id):
    """
    AI tarafından ürün kodu (barkod) ile silme işlemi yapılması için yardımcı fonksiyon.
    """
    # Önce ürünü bul
    product = Product.query.filter_by(code=code, company_id=company_id).first()
    
    if product:
        try:
            db.session.delete(product)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Silme hatası: {e}")
            return False
    return False

def ai_add_customer(name, phone, address, description, company_id):
    """AI için müşteri ekleme."""
    if Customer.query.filter_by(name=name, company_id=company_id).first():
        return {"status": "error", "message": f"{name} isimli müşteri zaten var."}
    
    add_customer_db(name, company_id, description, address, phone)
    return {"status": "ok", "message": f"Müşteri {name} başarıyla eklendi."}

def ai_get_customer_debt(name, company_id):
    """AI için müşteri borç sorgulama."""
    customer = Customer.query.filter(Customer.name.ilike(f"%{name}%"), Customer.company_id == company_id).first()
    if not customer:
        return "Müşteri bulunamadı."
    
    if not customer.debt:
        return f"{customer.name} müşterisinin borcu yok."
    
    debt_str = ", ".join([f"{amt} {curr}" for curr, amt in customer.debt.items()])
    return f"{customer.name} müşterisinin güncel borcu: {debt_str}"

def ai_add_expenditure(amount, currency, description, company_id, user_id):
    """AI için gider ekleme."""
    try:
        currency_data = {currency: float(amount)}
        exp = Expenditure(company_id=company_id, user_id=user_id, currency=currency_data, description=description)
        db.session.add(exp)
        db.session.commit()
        return f"{amount} {currency} tutarındaki gider ('{description}') kaydedildi."
    except Exception as e:
        return f"Gider eklenirken hata: {str(e)}"

def ai_manage_order(order_id, action, company_id, user_id):
    """AI için sipariş onaylama/reddetme."""
    success, msg = process_order_approval(order_id, user_id, action) # Mevcut fonksiyonu kullanıyoruz
    return msg

def ai_list_pending_orders(company_id):
    """AI için bekleyen siparişleri listeler."""
    orders = get_pending_orders(company_id)
    if not orders:
        return "Bekleyen sipariş yok."
    
    result = []
    for o in orders:
        result.append(f"Sipariş #{o.id} - Müşteri: {o.customer_name} - Tutar: {o.total_amount} {o.currency}")
    return "\n".join(result)

def ai_add_definition(type, value, company_id):
    """AI için Renk, Raf veya Kategori ekleme."""
    try:
        if type == "color":
            add_color_db(value, company_id)
            return f"Renk '{value}' eklendi."
        elif type == "shelf":
            add_shelf_db(value, company_id)
            return f"Raf '{value}' eklendi."
        elif type == "category":
            add_category_db(value, [], company_id)
            return f"Kategori '{value}' eklendi."
        return "Geçersiz tanımlama türü."
    except Exception as e:
        return f"Hata oluştu: {str(e)}"

def ai_get_low_stock_products(company_id, limit=10):
    """AI için kritik stoktaki ürünleri getirir."""
    # Min stok tanımlı ve stoğu bunun altında olanları bul
    products = Product.query.filter(
        Product.company_id == company_id,
        Product.min_stock > 0,
        Product.stock <= Product.min_stock
    ).limit(limit).all()
    
    if not products:
        return "Kritik seviyede ürün bulunmuyor."
        
    result = ["⚠️ KRİTİK STOK LİSTESİ:"]
    for p in products:
        result.append(f"- {p.name} ({p.code}): Mevcut {p.stock} / Min {p.min_stock}")
    return "\n".join(result)

def ai_add_stock_to_shelf_helper(product_code, shelf_code, quantities, company_id):
    """
    AI için rafa stok ekleme sarmalayıcısı.
    quantities formatı: {"S": 5, "M": 10}
    """
    # Fonksiyon mevcut mu kontrol edelim, yoksa import hatası almayalım
    # (add_stock_to_shelf_db fonksiyonunuzun db_utils içinde tanımlı olduğundan emin olun)
    try:
        result = add_stock_to_shelf_db(product_code, shelf_code, quantities, company_id)
        if result.get("status") == "ok":
            return f"Başarılı! {shelf_code} rafına ekleme yapıldı. {result.get('message')}"
        else:
            return f"Hata: {result.get('message')}"
    except Exception as e:
        return f"İşlem sırasında hata oluştu: {str(e)}"
    
def ai_list_shelves(company_id):
    """AI için şirkete ait tüm rafları listeler."""
    shelves = Shelf.query.filter_by(company_id=company_id).all()
    if not shelves:
        return "Sistemde henüz tanımlı bir raf bulunmuyor."
    
    # Raf kodlarını virgülle ayrılmış string yap
    shelf_list = ", ".join([s.code for s in shelves])
    return f"Sistemde Tanımlı Raflar: {shelf_list}"

def ai_add_shelf(code, company_id):
    """AI için yeni raf ekler."""
    # Zaten var mı kontrol et
    if Shelf.query.filter_by(code=code, company_id=company_id).first():
        return f"Hata: '{code}' kodlu raf zaten mevcut."
    
    try:
        add_shelf_db(code, company_id)
        return f"Başarılı! '{code}' rafı sisteme eklendi."
    except Exception as e:
        return f"Raf eklenirken hata oluştu: {str(e)}"
