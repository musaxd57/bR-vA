"""Session-authenticated v2 API; one commit boundary per idempotent command."""
from functools import wraps
import hashlib
import json
import logging
import re
from urllib.parse import unquote

from flask import Blueprint, Response, current_app, g, jsonify, request, session
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from extensions import db
from . import service as s
from .errors import DomainError, reject
from .models import (CORE_TABLE_NAMES, AccountMovement, AuditEvent, Barcode, CatalogModel, Customer,
                     Location, Package, Payment, PickingTask, ProductionPlan,
                     Receipt, Recipe, RequestRecord, Sale, StockMovement, Variant)

blueprint = Blueprint("briva_core", __name__, url_prefix="/api/v2")
ALL_PERMISSIONS = frozenset(("read", "catalog", "catalog_create", "catalog_edit", "inventory", "sales", "history", "customers",
                             "payments", "returns", "price_override", "picking", "audit", "barcode_rules"))
logger = logging.getLogger(__name__)


def current_actor():
    """Company and permissions come from the current DB user, never the body."""
    from models import User
    user_id = session.get("user")
    reject(type(user_id) is not int, "Önce giriş yapın.", "unauthenticated", 401)
    if "briva_security" in current_app.extensions:
        from briva_security import current_user
        user = current_user()
    else:
        user = db.session.get(User, user_id)
    reject(user is None or not user.company, "Oturum geçersiz; tekrar giriş yapın.", "unauthenticated", 401)
    if user.role in ("admin", "owner"):
        permissions = ALL_PERMISSIONS
    else:
        permissions = {"read"}
        if getattr(user, "can_make_sale", False):
            permissions.add("sales")
        if getattr(user, "can_add_product", False):
            permissions.update(("catalog", "catalog_create"))
        if getattr(user, "can_edit_product", False):
            permissions.update(("catalog", "catalog_edit", "inventory"))
        if getattr(user, "can_view_sales_history", False):
            permissions.add("history")
        if getattr(user, "can_manage_customers", False):
            permissions.add("customers")
        if getattr(user, "can_manage_debts", False):
            permissions.update(("payments", "returns"))
        if getattr(user, "can_orders", False):
            permissions.add("picking")
        # Price overrides and full audit access remain owner operations until a
        # dedicated, explicit permission is provisioned. Role names do not grant
        # hidden privileges to non-owners.
        permissions = frozenset(permissions)
    return s.Actor(user.company, user.id, permissions, user.username)


def _error(error):
    return jsonify(error={"code": error.code, "message": error.message, "details": error.details}), error.status


@blueprint.errorhandler(DomainError)
def domain_error(error):
    db.session.rollback()
    return _error(error)


@blueprint.errorhandler(BadRequest)
def malformed_json(error):
    db.session.rollback()
    return _error(DomainError("Geçerli bir JSON nesnesi gönderin.", "invalid_json"))


@blueprint.errorhandler(UnsupportedMediaType)
def unsupported_json(error):
    db.session.rollback()
    return _error(DomainError("Content-Type application/json başlığı gerekli.", "json_required", 415))


@blueprint.errorhandler(RequestEntityTooLarge)
def oversized_json(error):
    db.session.rollback()
    return _error(DomainError("İşlem gövdesi çok büyük.", "payload_too_large", 413))


def _replay(record, digest, actor):
    reject(record.actor_id != actor.user_id, "Bu işlem anahtarı başka bir kullanıcıya ait.", "idempotency_owner", 409)
    reject(record.digest != digest, "Aynı işlem anahtarı farklı içerik için kullanılamaz.", "idempotency_conflict", 409)
    reject(record.result is None, "İşlem sürüyor; aynı anahtarla tekrar deneyin.", "request_in_progress", 409)
    return jsonify(data=record.result, replayed=True), 200


def command(permission):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            actor = current_actor()
            actor.require(permission)
            expected_actor = request.headers.get("X-Briva-Actor")
            expected_company = request.headers.get("X-Briva-Company")
            if expected_actor is not None or expected_company is not None:
                reject(expected_actor is None or expected_company is None, "İşlem kullanıcı ve şirket bağlamı birlikte gönderilmeli.", "auth_context_required")
                try:
                    expected_company = unquote(expected_company, encoding="utf-8", errors="strict")
                except UnicodeError:
                    raise DomainError("İşlem şirket bağlamı geçersiz.", "auth_context_required")
                reject(expected_actor != str(actor.user_id) or expected_company != actor.company_id,
                       "Bu taslak başka bir kullanıcı veya şirkete ait. Özgün oturumla açıp işlem sonucunu kontrol edin.", "auth_context_changed", 409)
            reject(request.content_length is not None and request.content_length > 512_000,
                   "İşlem gövdesi çok büyük.", "payload_too_large", 413)
            request.max_content_length = 512_000
            try:
                payload = request.get_json(silent=False)
            except RecursionError:
                raise DomainError("JSON iç içe veri sınırını aşıyor.", "invalid_json")
            reject(not isinstance(payload, dict), "JSON gövdesi nesne olmalı.")
            # Tenant and actor fields are never caller supplied, even if a
            # confused UI happens to send them.
            reject(any(field in payload for field in ("company", "company_id", "actor_id", "user_id")),
                   "Şirket ve kullanıcı işlem gövdesinde belirtilemez.", "tenant_field")
            key = request.headers.get("Idempotency-Key", "")
            reject(not re.fullmatch(r"[A-Za-z0-9_.:-]{8,100}", key),
                   "8–100 karakterlik benzersiz Idempotency-Key başlığı gerekli.", "idempotency_required")
            operation = request.method + " " + request.path
            try:
                canonical = json.dumps({"operation": operation, "payload": payload},
                                       sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise DomainError("JSON sonlu sayılar ve geçerli alanlar içermeli.", "invalid_json")
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            record = db.session.execute(select(RequestRecord).where(
                RequestRecord.company_id == actor.company_id, RequestRecord.key == key)).scalar_one_or_none()
            if record:
                return _replay(record, digest, actor)
            record = RequestRecord(company_id=actor.company_id, key=key, digest=digest,
                                   operation=operation, actor_id=actor.user_id)
            try:
                db.session.add(record)
                db.session.flush()  # Unique key serializes retries before writes.
                result = function(actor, payload, *args, **kwargs)
                record.result = result
                db.session.commit()
                return jsonify(data=result, replayed=False), 201
            except DomainError:
                db.session.rollback()
                raise
            except IntegrityError:
                db.session.rollback()
                existing = db.session.execute(select(RequestRecord).where(
                    RequestRecord.company_id == actor.company_id, RequestRecord.key == key)).scalar_one_or_none()
                if existing:
                    return _replay(existing, digest, actor)
                return _error(DomainError("Bu kod veya referans zaten kullanılıyor; kayıtlar değiştirilmedi.", "unique_conflict", 409))
            except OperationalError as error:
                db.session.rollback()
                logger.warning("briva_command_database_busy actor_id=%s request_id=%s exception_class=%s",
                    actor.user_id, getattr(g, "briva_request_id", "unavailable"), type(error).__name__)
                return _error(DomainError("Veritabanı şu anda meşgul; aynı işlem anahtarıyla tekrar deneyin.", "database_busy", 503))
            except Exception as error:
                db.session.rollback()
                logger.error("briva_command_failed actor_id=%s request_id=%s exception_class=%s",
                    actor.user_id, getattr(g, "briva_request_id", "unavailable"), type(error).__name__)
                return _error(DomainError("İşlem sonucu doğrulanamadı; aynı işlem anahtarıyla kontrol edin.", "operation_failed", 500))
        return wrapped
    return decorate


def reading(permission="read"):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            actor = current_actor()
            actor.require(permission)
            with s.read_cache_scope(actor):
                result = function(actor, *args, **kwargs)
            return jsonify(data=result)
        return wrapped
    return decorate


@blueprint.before_request
def require_schema():
    current_actor()
    if not current_app.extensions.get("briva_core_schema_ready"):
        expected = CORE_TABLE_NAMES
        existing = set(inspect(db.engine).get_table_names())
        reject(not expected.issubset(existing), "Yeni stok altyapısı için yerel şema yükseltmesi gerekli.", "schema_upgrade_required", 503)
        current_app.extensions["briva_core_schema_ready"] = True


def _rows(model, actor, limit=200):
    return db.session.execute(select(model).where(model.company_id == actor.company_id).order_by(model.id.desc()).limit(limit)).scalars().all()


@blueprint.get("/bootstrap")
@reading()
def bootstrap(actor):
    """Bounded metadata read; sensitive finance/history requires explicit rights."""
    models, recipes, catalog_pagination = s.catalog_page(actor)
    locations = list(reversed(_rows(Location, actor, 1000)))
    customers = list(reversed(_rows(Customer, actor, 500))) if "sales" in actor.permissions or "customers" in actor.permissions else []
    plans = _rows(ProductionPlan, actor, 100) if "inventory" in actor.permissions else []
    receipts = _rows(Receipt, actor, 50) if "inventory" in actor.permissions else []
    sales = _rows(Sale, actor, 50) if "history" in actor.permissions else []
    picking = _rows(PickingTask, actor, 100) if "picking" in actor.permissions else []
    packages = _rows(Package, actor, 100) if "inventory" in actor.permissions else []
    all_plans = {row.id: row for row in plans}
    missing_plan_ids = {row.plan_id for row in receipts if row.plan_id} - set(all_plans)
    if missing_plan_ids:
        all_plans.update({row.id: row for row in db.session.execute(select(ProductionPlan).where(
            ProductionPlan.company_id == actor.company_id, ProductionPlan.id.in_(missing_plan_ids))).scalars()})
    s.prime_read_cache(actor, models=models, recipes=recipes, locations=locations,
                       customers=customers, plans=list(all_plans.values()), sales=sales, picking=picking, packages=packages)
    return {"schema_version": 2, "company": actor.company_id,
            "user": {"id": actor.user_id, "username": actor.username},
            "barcode_count": db.session.execute(select(func.count(Barcode.id)).where(Barcode.company_id == actor.company_id)).scalar_one(),
            "permissions": {name: name in actor.permissions for name in sorted(ALL_PERMISSIONS)},
            "currencies": list(s.CURRENCIES),
            "barcode_rules": s.barcode_rules(actor),
            "catalog_pagination": catalog_pagination,
            "models": [s.serialize_model(row, actor) for row in models],
            "recipes": [s.serialize_recipe(row) for row in recipes],
            "locations": [s.serialize_location(row) for row in locations],
            "customers": [s.serialize_customer(row, actor) if "payments" in actor.permissions else {"id": row.id, "name": row.name, "phone": row.phone}
                          for row in customers],
            "plans": [s.serialize_plan(row, actor) for row in plans],
            "receipts": [s.serialize_receipt(row, actor) for row in receipts],
            "sales": [s.serialize_sale(row, actor) for row in sales],
            "picking": [s.serialize_picking(row, actor) for row in picking],
            "packages": [s.serialize_package(row, actor) for row in packages]}


@blueprint.get("/catalog")
@reading()
def catalog(actor):
    models, recipes, pagination = s.catalog_page(actor, _query_id("after_model_id"), _query_id("limit") or 50,
        _query_id("variant_budget") or 5000, request.args.get("q", ""), _query_id("model_id"))
    s.prime_read_cache(actor, models=models, recipes=recipes)
    return {"models": [s.serialize_model(row, actor) for row in models],
            "recipes": [s.serialize_recipe(row) for row in recipes], "pagination": pagination, "search": pagination["search"]}


@blueprint.get("/barcode-rules")
@reading()
def get_barcode_rules(actor):
    return s.barcode_rules(actor)


@blueprint.put("/barcode-rules")
@command("barcode_rules")
def put_barcode_rules(actor, payload):
    return s.update_barcode_rules(actor, payload)


@blueprint.post("/catalog/models")
@command("catalog_create")
def create_model(actor, payload):
    return s.create_model(actor, payload)


@blueprint.patch("/catalog/models/<int:model_id>")
@command("catalog_edit")
def update_model(actor, payload, model_id):
    return s.update_model(actor, model_id, payload)


@blueprint.patch("/catalog/variants/<int:variant_id>/prices")
@command("price_override")
def update_prices(actor, payload, variant_id):
    return s.update_prices(actor, variant_id, payload)


@blueprint.post("/catalog/recipes")
@command("catalog_edit")
def create_recipe(actor, payload):
    return s.create_recipe(actor, payload)


@blueprint.post("/locations")
@command("inventory")
def create_location(actor, payload):
    return s.create_location(actor, payload)


@blueprint.get("/barcodes/<code>")
@reading()
def barcode_lookup(actor, code):
    return s.resolve_barcode(actor, code)


@blueprint.get("/barcodes/<code>.svg")
def barcode_svg(code):
    actor = current_actor()
    actor.require("read")
    resolved = s.resolve_barcode(actor, code)
    from .labels import code128_svg
    response = Response(code128_svg(resolved["barcode"]), mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'none'; sandbox"
    return response


@blueprint.get("/stock")
@reading()
def stock(actor):
    return s.stock_snapshot(actor, _query_id("location_id"), _query_id("after_balance_id"),
                            _query_id("limit") or 1000, _query_id("recipe_id"), _query_id("model_id"), _query_bool("include_descendants"))


def _query_bool(name):
    value = request.args.get(name)
    if value is None:
        return False
    reject(value not in ("true", "false"), "Seçim true veya false olmalı.")
    return value == "true"


def _query_id(name):
    value = request.args.get(name)
    if value is None:
        return None
    reject(re.fullmatch(r"[0-9]{1,10}", value) is None, "Kayıt numarası geçersiz.")
    return s.identifier(int(value))


@blueprint.get("/ledger")
@reading("inventory")
def ledger(actor):
    before = _query_id("before_id")
    query = select(StockMovement).where(StockMovement.company_id == actor.company_id)
    if before:
        query = query.where(StockMovement.id < before)
    rows = db.session.execute(query.order_by(StockMovement.id.desc()).limit(200)).scalars().all()
    return {"items": [{"id": row.id, "variant_id": row.variant_id, "location_id": row.location_id,
                       "quantity_delta": row.quantity_delta, "reserved_delta": row.reserved_delta,
                       "packaged_delta": row.packaged_delta, "kind": row.kind,
                       "document_type": row.document_type, "document_id": row.document_id,
                       "actor_id": row.actor_id, "created_at": s.iso_utc(row.created_at)} for row in rows],
            "next_before_id": rows[-1].id if len(rows) == 200 else None}


@blueprint.post("/production/plans")
@command("inventory")
def plan(actor, payload):
    return s.create_plan(actor, payload)


@blueprint.get("/production/plans/<int:plan_id>")
@reading("inventory")
def plan_detail(actor, plan_id):
    plan = s.owned(ProductionPlan, plan_id, actor, "Üretim planı")
    s.prime_read_cache(actor, plans=[plan])
    return s.serialize_plan(plan, actor, detailed=True)


@blueprint.post("/receipts")
@command("inventory")
def receipt(actor, payload):
    return s.create_receipt(actor, payload)


@blueprint.post("/transfers")
@command("inventory")
def transfer(actor, payload):
    return s.transfer_stock(actor, payload)


@blueprint.post("/stock-counts")
@command("inventory")
def stock_count(actor, payload):
    return s.record_stock_count(actor, payload)


@blueprint.get("/health")
@reading("audit")
def health(actor):
    return s.inventory_health(actor)


@blueprint.post("/customers")
@command("customers")
def customer(actor, payload):
    return s.create_customer(actor, payload)


@blueprint.post("/payments")
@command("payments")
def payment(actor, payload):
    return s.create_payment(actor, payload)


@blueprint.post("/sales")
@command("sales")
def sale(actor, payload):
    return s.create_sale(actor, payload)


@blueprint.get("/sales/<int:sale_id>")
@reading()
def sale_detail(actor, sale_id):
    sale = s.owned(Sale, sale_id, actor, "Satış")
    reject("history" not in actor.permissions and ("sales" not in actor.permissions or sale.actor_id != actor.user_id),
           "Bu satışın geçmişini görüntüleme yetkiniz yok.", "forbidden", 403)
    s.prime_read_cache(actor, sales=[sale])
    return s.serialize_sale(sale, actor)


@blueprint.post("/sales/<int:sale_id>/returns")
@command("returns")
def return_sale(actor, payload, sale_id):
    return s.return_sale(actor, sale_id, payload)


@blueprint.post("/packages")
@command("inventory")
def package(actor, payload):
    return s.create_package(actor, payload)


@blueprint.get("/packages/<int:package_id>")
@reading("inventory")
def package_detail(actor, package_id):
    package = s.owned(Package, package_id, actor, "Paket")
    s.prime_read_cache(actor, packages=[package])
    return s.serialize_package(package, actor, detailed=True)


@blueprint.post("/packages/<int:package_id>/open")
@command("inventory")
def open_package(actor, payload, package_id):
    return s.open_package(actor, package_id, payload)


@blueprint.post("/packages/<int:package_id>/transfer")
@command("inventory")
def transfer_package(actor, payload, package_id):
    return s.transfer_package(actor, package_id, payload)


@blueprint.get("/picking")
@reading("picking")
def picking_list(actor):
    tasks = _rows(PickingTask, actor, 200)
    locations = db.session.execute(select(Location).where(Location.company_id == actor.company_id,
        Location.id.in_([row.location_id for row in tasks]))).scalars().all() if tasks else []
    s.prime_read_cache(actor, picking=tasks, locations=locations)
    return {"tasks": [s.serialize_picking(row, actor) for row in tasks]}


@blueprint.post("/picking/tasks")
@command("picking")
def picking_create(actor, payload):
    return s.create_picking_task(actor, payload)


@blueprint.post("/picking/<int:task_id>/scan")
@command("picking")
def picking_scan(actor, payload, task_id):
    return s.scan_picking(actor, task_id, payload)


@blueprint.post("/picking/<int:task_id>/cancel")
@command("picking")
def picking_cancel(actor, payload, task_id):
    return s.cancel_picking(actor, task_id, payload)


@blueprint.get("/customers/<int:customer_id>/account")
@reading("payments")
def account(actor, customer_id):
    customer = s.owned(Customer, customer_id, actor, "Müşteri")
    movements = db.session.execute(select(AccountMovement).where(AccountMovement.company_id == actor.company_id,
        AccountMovement.customer_id == customer.id).order_by(AccountMovement.id.desc()).limit(500)).scalars().all()
    return {"customer": s.serialize_customer(customer, actor),
            "movements": [{"id": row.id, "currency": row.currency, "amount": s.money_string(row.amount),
                           "kind": row.kind, "document_type": row.document_type, "document_id": row.document_id,
                           "actor_id": row.actor_id, "created_at": s.iso_utc(row.created_at)} for row in movements]}


@blueprint.get("/audit")
@reading("audit")
def audit(actor):
    return {"items": [{"id": row.id, "action": row.action, "document_type": row.document_type,
                       "document_id": row.document_id, "details": row.details, "actor_id": row.actor_id,
                       "created_at": s.iso_utc(row.created_at)} for row in _rows(AuditEvent, actor, 300)]}
