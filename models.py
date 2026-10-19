# models.py

from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import enum

class User(db.Model):
    # DÜZELTİLDİ: __tablename__ standardı kullanıldı.
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    # GÜVENLİK İYİLEŞTİRMESİ: Şifre alanı artık hash'lenmiş veriyi tutacak.
    password_hash = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(15), nullable=True)
    company = db.Column(db.String(100), nullable=True)
    role = db.Column(db.String(50), nullable=False)

    ##################################
    # İzinler
    can_make_sale = db.Column(db.Boolean, default=False, nullable=True)
    can_view_sales_history = db.Column(db.Boolean, default=False, nullable=True)
    can_view_statistics = db.Column(db.Boolean, default=False, nullable=True)
    can_generate_reports = db.Column(db.Boolean, default=False, nullable=True)
    can_add_product = db.Column(db.Boolean, default=False, nullable=True)
    can_edit_product = db.Column(db.Boolean, default=False, nullable=True)
    can_delete_product = db.Column(db.Boolean, default=False, nullable=True)
    can_manage_employees = db.Column(db.Boolean, default=False, nullable=True)
    can_manage_attributes = db.Column(db.Boolean, default=False, nullable=True)
    can_manage_customers = db.Column(db.Boolean, default=False, nullable=True)
    can_manage_debts = db.Column(db.Boolean, default=False, nullable=True)
    can_orders = db.Column(db.Boolean, default=False, nullable=True)
    ##################################


    def __repr__(self):
        return f'<User {self.username}>'
    

    # İYİLEŞTİRME: SQLAlchemy'nin kendi __init__ metodunu kullanmak daha temiz ve güvenlidir.
    # Bu yüzden manuel __init__ kaldırıldı.

    # GÜVENLİK İYİLEŞTİRMESİ: Şifreyi hash'leyen ve kontrol eden metodlar eklendi.
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    # GÜVENLİK İYİLEŞTİRMESİ: Şifrenin doğrudan okunmasını engellemek için.
    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

class Shelf(db.Model):
    __tablename__ = 'shelf'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), nullable=False)

class CompanySettings(db.Model):
    """
    Şirket bazlı ayarları tutar. (Örn: Barkod Kuralları)
    """
    __tablename__ = 'company_settings'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), unique=True, nullable=False)
    # Barkod kurallarını JSON olarak tutacağız
    # Örn: [{"type": "category", "is_group": True}, {"type": "random", "is_group": True}]
    barcode_rules = db.Column(db.JSON, nullable=True, default=lambda: [])

class AttributeDefinition(db.Model):
    """
    Şirketlerin kendi özel alan tanımlarını tutar.
    Örn: "Kumaş Türü", "Sezon", "Garanti"
    """
    __tablename__ = 'attribute_definition'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False) # Nitelik Adı (Örn: Materyal)
    options = db.Column(db.JSON, nullable=True, default=lambda: []) # YENİ: Seçenekleri tutacak alan

    __table_args__ = (db.UniqueConstraint('company_id', 'name', name='_company_attr_uc'),)

class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=True)
    company_id = db.Column(db.String(100), nullable=False)
    stock_id = db.Column(db.String(20), nullable=True)
    color_id = db.Column(db.String(20), nullable=True)
    sizes = db.Column(db.JSON, nullable=True, default=lambda: [])
    currency = db.Column(db.JSON, nullable=True, default=lambda: {
        "TL": 0, "USD": 0, "EURO": 0,
    })
    category = db.Column(db.String(100), nullable=True)
    min_stock = db.Column(db.Integer, default=0, nullable=True) 
    image_path = db.Column(db.String(255), nullable=True) 
    shelves = db.Column(db.JSON, nullable=True, default=lambda: []) 
    custom_attributes = db.Column(db.JSON, nullable=True, default=lambda: {})

    __table_args__ = (db.UniqueConstraint('code', 'company_id', name='_code_company_uc'),)


class Category(db.Model):
    __tablename__ = 'category'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), nullable=True) # YENİ: Kategori Kodu (Örn: AYK)
    sizes = db.Column(db.JSON, nullable=True, default=lambda:[])
    company_id = db.Column(db.String(100), nullable=False)

'''
class Company(db.Model):
    __tablename__ = 'company'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), nullable=False, unique=True)
    company_adress = db.Column(db.String(100), nullable=False)
    products = db.relationship('Product', backref='company_obj', lazy=True)
'''
class Sale(db.Model):
    __tablename__ = 'sales'
    id = db.Column(db.Integer, primary_key=True)
    sale_group_id = db.Column(db.String(50), nullable=False)  # Satış grubunu tanımlamak için
    product_code = db.Column(db.String(20), nullable=False)  # Ürün kodu
    product_id = db.Column(db.Integer, nullable=False)   # ForeignKey yok
    user_id = db.Column(db.Integer, nullable=False)      # ForeignKey yok
    company_id = db.Column(db.String(100), nullable=False)

    quantity = db.Column(db.Integer, nullable=False)
    price_at_sale = db.Column(db.Float, nullable=False)   # Satış anındaki birim fiyat
    discount = db.Column(db.Float, nullable=False, default=0)  # İndirim (TL cinsinden)
    total_price = db.Column(db.Float, nullable=False)    # İndirim sonrası toplam tutar
    currency = db.Column(db.String(10), nullable=False)
    customer_name = db.Column(db.String(150), nullable=True)
    sale_notes = db.Column(db.String(300), nullable=True)
    sale_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sizes = db.Column(db.JSON, nullable=True, default=lambda: [])



class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    company_id = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String, nullable=True)
    address = db.Column(db.String, nullable=True)
    phone = db.Column(db.String(15), nullable=True)
    debt = db.Column(db.JSON, nullable=True, default=dict)
    debt_description = db.Column(db.Text, nullable=True)


class Color(db.Model):
    __tablename__ = 'color'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    company_id = db.Column(db.String(100), nullable=False)

class Feedback(db.Model):
    __tablename__ = 'feedback'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    username = db.Column(db.Text, nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TransactionType(enum.Enum):
    DEBIT = "DEBIT"   # Borç (Müşterinin borcu arttı)
    CREDIT = "CREDIT" # Alacak/Tahsilat (Müşterinin borcu azaldı)

class DebtTransaction(db.Model):
    __tablename__ = 'debt_transaction'
    id = db.Column(db.Integer, primary_key=True)
    
    # İlişkiler
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    company_id = db.Column(db.String(100), nullable=False)
    
    # Bu işlem bir satıştan dolayı oluştuysa, o satış grubunun ID'sini tutar
    sale_group_id = db.Column(db.String(36), nullable=True) 
    
    # İşlem Detayları
    transaction_type = db.Column(db.Enum(TransactionType), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    note = db.Column(db.Text, nullable=True)
    transaction_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # İlişkileri daha kolay sorgulamak için
    customer = db.relationship('Customer', backref=db.backref('debt_transactions', lazy=True))
    user = db.relationship('User', backref=db.backref('recorded_transactions', lazy=True))

class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    company_id = db.Column(db.String(100), nullable=False)
    
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    
    # Türler: 'info', 'warning', 'success', 'error' (CSS ile eşleşecek)
    type = db.Column(db.String(20), default='info') 
    
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # İlişki (Hangi kullanıcının bildirimi olduğunu görmek için)
    user = db.relationship('User', backref=db.backref('notifications', lazy=True))

class Expenditure(db.Model):
    __tablename__ = 'expenditure'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    currency = db.Column(db.JSON, nullable=True, default=lambda: {
        "TL": 0, "USD": 0, "EURO": 0,
    })
    description = db.Column(db.Text, nullable=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class OrderStatus(enum.Enum):
    PENDING = "PENDING"   # Beklemede
    APPROVED = "APPROVED" # Onaylandı
    REJECTED = "REJECTED" # Reddedildi

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Siparişi oluşturan kişi
    
    customer_name = db.Column(db.String(100), nullable=True)
    items = db.Column(db.JSON, nullable=False) # Sepetteki ürünlerin JSON hali
    total_amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    note = db.Column(db.Text, nullable=True)
    
    is_debt = db.Column(db.Boolean, default=False)
    amount_paid = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.Enum(OrderStatus), default=OrderStatus.PENDING)
    related_sale_group_id = db.Column(db.String(50), nullable=True) 
    created_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(hours=3))
    processed_at = db.Column(db.DateTime, nullable=True)
    
    # İlişki
    creator = db.relationship('User', backref=db.backref('orders', lazy=True))

    picked_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(hours=3))