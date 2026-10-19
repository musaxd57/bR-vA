"""Authoritative stock, sales and receivable operations.

No function commits. The API transaction boundary owns commit/rollback and
idempotency; command-line callers must use the same transaction discipline.
"""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import case, func, or_, select, update
from extensions import db
from .errors import DomainError, reject
from .models import (AccountMovement, AuditEvent, Barcode, BarcodeRule, CatalogModel, Customer,
                     Location, Package, Payment, PickAllocation, PickingEvent, PickingLine,
                     PickingTask, ProductionPlan, Receipt, Recipe, ReturnDocument,
                     ReturnItem, Sale, SaleItem, StockBalance, StockCount, StockMovement,
                     Transfer, Variant)

CURRENCIES = ("TRY", "USD", "EUR")
METHODS = ("cash", "card", "transfer")
MAX_QUANTITY = 1_000_000
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
_READ_CACHE = ContextVar("briva_core_read_cache", default=None)


@contextmanager
def read_cache_scope(actor):
    """Request-local serialization cache; never shared across users or writes."""
    token = _READ_CACHE.set({"company_id": actor.company_id, "barcodes": {}})
    try:
        yield
    finally:
        _READ_CACHE.reset(token)


def prime_read_cache(actor, *, models=(), recipes=(), locations=(), packages=(),
                     sales=(), customers=(), plans=(), picking=()):
    """Batch related reads instead of querying once per displayed row."""
    cache = _READ_CACHE.get()
    if cache is None or cache["company_id"] != actor.company_id:
        return
    model_ids = [row.id for row in models]
    variants = db.session.execute(select(Variant).where(Variant.company_id == actor.company_id,
        Variant.model_id.in_(model_ids)).order_by(Variant.id)).scalars().all() if model_ids else []
    cache["model_variants"] = {key: [] for key in model_ids}
    for variant in variants:
        cache["model_variants"][variant.model_id].append(variant)
    targets = {"unit": [row.id for row in variants], "recipe": [row.id for row in recipes],
               "location": [row.id for row in locations], "package": [row.id for row in packages]}
    for kind, ids in targets.items():
        # Keep parameter counts portable to SQLite builds with a 999 variable
        # ceiling. Four bounded chunked queries serve 1000 variants + metadata.
        for offset in range(0, len(ids), 800):
            for row in db.session.execute(select(Barcode).where(Barcode.company_id == actor.company_id,
                Barcode.kind == kind, Barcode.target_id.in_(ids[offset:offset + 800]))).scalars():
                cache["barcodes"][(row.kind, row.target_id)] = row.code
    sale_ids = [row.id for row in sales]
    cache["sale_items"] = {key: [] for key in sale_ids}
    item_ids = []
    if sale_ids:
        for row in db.session.execute(select(SaleItem).where(SaleItem.company_id == actor.company_id,
            SaleItem.sale_id.in_(sale_ids)).order_by(SaleItem.id)).scalars():
            cache["sale_items"][row.sale_id].append(row)
            item_ids.append(row.id)
    cache["returned"] = {}
    for offset in range(0, len(item_ids), 800):
        cache["returned"].update(db.session.execute(select(ReturnItem.sale_item_id, func.sum(ReturnItem.quantity)).where(
            ReturnItem.company_id == actor.company_id, ReturnItem.sale_item_id.in_(item_ids[offset:offset + 800]))
            .group_by(ReturnItem.sale_item_id)).all())
    customer_ids = {row.id for row in customers} | {row.customer_id for row in sales if row.customer_id}
    cache["customer_balances"] = {key: {} for key in customer_ids}
    for offset in range(0, len(customer_ids), 800):
        ids = sorted(customer_ids)[offset:offset + 800]
        for customer_id, cur, amount in db.session.execute(select(AccountMovement.customer_id,
            AccountMovement.currency, func.sum(AccountMovement.amount)).where(AccountMovement.company_id == actor.company_id,
            AccountMovement.customer_id.in_(ids)).group_by(AccountMovement.customer_id, AccountMovement.currency)):
            cache["customer_balances"][customer_id][cur] = money_string(amount)
    plan_ids = [row.id for row in plans]
    cache["plan_receipts"] = {key: [] for key in plan_ids}
    if plan_ids:
        for row in db.session.execute(select(Receipt).where(Receipt.company_id == actor.company_id,
            Receipt.plan_id.in_(plan_ids))).scalars():
            cache["plan_receipts"][row.plan_id].append(row)
    cache["plans_by_id"] = {row.id: row for row in plans}
    component_ids = {part["variant_id"] for plan in plans for part in plan.expected} | {part["variant_id"] for package in packages for part in package.components}
    cache["component_model_ids"] = {}
    ids = sorted(component_ids)
    for offset in range(0, len(ids), 800):
        cache["component_model_ids"].update(db.session.execute(select(Variant.id, Variant.model_id)
            .where(Variant.company_id == actor.company_id, Variant.id.in_(ids[offset:offset + 800]))).all())
    task_ids = [row.id for row in picking]
    cache["picking_lines"] = {key: [] for key in task_ids}
    cache["picking_allocations"] = {key: [] for key in task_ids}
    cache["picking_verified"] = set()
    cache["picking_active_locations"] = {}
    cache["picking_variants"] = {}
    cache["picking_locations"] = {}
    if task_ids:
        for row in db.session.execute(select(PickingLine).where(PickingLine.company_id == actor.company_id,
            PickingLine.task_id.in_(task_ids)).order_by(PickingLine.id)).scalars():
            cache["picking_lines"][row.task_id].append(row)
        for row in db.session.execute(select(PickAllocation).where(PickAllocation.company_id == actor.company_id,
            PickAllocation.task_id.in_(task_ids)).order_by(PickAllocation.variant_id, PickAllocation.location_id)).scalars():
            cache["picking_allocations"][row.task_id].append(row)
        for task_id, code, location_id in db.session.execute(select(PickingEvent.task_id, PickingEvent.barcode, Barcode.target_id)
            .join(Barcode, (Barcode.code == PickingEvent.barcode) & (Barcode.company_id == PickingEvent.company_id))
            .where(PickingEvent.company_id == actor.company_id, PickingEvent.actor_id == actor.user_id,
            Barcode.kind == "location", PickingEvent.task_id.in_(task_ids)).order_by(PickingEvent.id)):
            cache["picking_verified"].add((task_id, code))
            cache["picking_active_locations"][task_id] = location_id
        meta, _ = variant_metadata(actor, [line.variant_id for lines in cache["picking_lines"].values() for line in lines])
        cache["picking_variants"] = {row["id"]: row for row in meta}
        source_location_ids = {row.location_id for row in picking} | {line.location_id for lines in cache["picking_allocations"].values() for line in lines}
        for row in db.session.execute(select(Location).where(Location.company_id == actor.company_id, Location.id.in_(source_location_ids))).scalars():
            cache["picking_locations"][row.id] = row
        for target_id, code in db.session.execute(select(Barcode.target_id, Barcode.code).where(Barcode.company_id == actor.company_id,
            Barcode.kind == "location", Barcode.target_id.in_(source_location_ids))):
            cache["barcodes"][("location", target_id)] = code


@dataclass(frozen=True)
class Actor:
    company_id: str
    user_id: int
    permissions: frozenset
    username: str = ""

    def require(self, permission):
        reject(permission not in self.permissions, "Bu işlem için yetkiniz yok.", "forbidden", 403)


def text_field(value, name, maximum=160, optional=False):
    reject(not isinstance(value, str), f"{name} metin olmalı.")
    value = value.strip()
    reject((not value and not optional) or len(value) > maximum, f"{name} 1–{maximum} karakter olmalı.")
    reject(any(ord(char) < 32 for char in value), f"{name} kontrol karakteri içeremez.")
    return value


def quantity(value, name="Adet", allow_zero=False):
    reject(type(value) is not int or value < (0 if allow_zero else 1) or value > MAX_QUANTITY,
           f"{name} {'sıfır veya ' if allow_zero else ''}pozitif tam sayı olmalı; üst sınır {MAX_QUANTITY}.")
    return value


def identifier(value, name="Kimlik"):
    reject(type(value) is not int or not 1 <= value <= 2_147_483_647, f"{name} geçerli bir kayıt numarası olmalı.")
    return value


def money(value, name="Tutar", positive=False):
    reject(isinstance(value, bool) or value is None, f"{name} geçersiz.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise DomainError(f"{name} geçersiz.")
    reject(not result.is_finite() or result < 0 or result > Decimal("1000000000"), f"{name} sonlu, negatif olmayan bir sayı olmalı.")
    reject(result != result.quantize(CENT), f"{name} en fazla iki ondalık basamak içermeli.")
    reject(positive and result <= 0, f"{name} sıfırdan büyük olmalı.")
    return result.quantize(CENT)


def currency(value):
    reject(value not in CURRENCIES, "Para birimi TRY, USD veya EUR olmalı.")
    return value


def money_string(value):
    return format(Decimal(value).quantize(CENT), ".2f")


def iso_utc(value):
    # SQLite returns naive datetimes even for DateTime(timezone=True). All core
    # timestamps are written in UTC; explicitly attach it when reading back.
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


def prices(value):
    reject(not isinstance(value, dict) or not value, "En az bir para biriminde parça fiyatı girin.")
    return {currency(key): money_string(money(amount, "Parça fiyatı")) for key, amount in value.items()}


def catalog_page(actor, after_model_id=None, limit=50, variant_budget=5000, search="", model_id=None):
    """Keyset model page with a strict nested-variant budget.

    Whole models remain together. A direct model or exact barcode lookup works
    independently of the currently displayed page.
    """
    reject(type(limit) is not int or not 1 <= limit <= 100, "Model sayfa boyutu 1–100 olmalı.")
    reject(type(variant_budget) is not int or not 1000 <= variant_budget <= 5000, "Varyant bütçesi 1000–5000 olmalı.")
    search = text_field(search, "Arama", 100, True)
    filters = [CatalogModel.company_id == actor.company_id]
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        filters.append(or_(CatalogModel.model_code.ilike(pattern, escape="\\"), CatalogModel.name.ilike(pattern, escape="\\")))
    if model_id is not None:
        owned(CatalogModel, model_id, actor, "Model")
        filters.append(CatalogModel.id == model_id)
    totals = db.session.execute(select(func.count(func.distinct(CatalogModel.id)), func.count(Variant.id))
        .outerjoin(Variant, Variant.model_id == CatalogModel.id).where(*filters)).one()
    candidate_query = select(CatalogModel).where(*filters).order_by(CatalogModel.id).limit(limit + 1)
    if after_model_id is not None:
        candidate_query = candidate_query.where(CatalogModel.id > after_model_id)
    candidates = db.session.execute(candidate_query).scalars().all()
    counts = dict(db.session.execute(select(Variant.model_id, func.count(Variant.id)).where(
        Variant.company_id == actor.company_id, Variant.model_id.in_([row.id for row in candidates])).group_by(Variant.model_id)).all()) if candidates else {}
    selected = []
    loaded_variants = 0
    for model in candidates[:limit]:
        amount = counts.get(model.id, 0)
        if loaded_variants + amount > variant_budget:
            reject(not selected, "Bu model izin verilen varyant bütçesini aşıyor; veri yapısını denetleyin.", "catalog_budget", 409)
            break
        selected.append(model)
        loaded_variants += amount
    has_more = len(candidates) > len(selected)
    recipes = db.session.execute(select(Recipe).where(Recipe.company_id == actor.company_id,
        Recipe.model_id.in_([row.id for row in selected])).order_by(Recipe.id.desc()).limit(1001)).scalars().all() if selected else []
    recipes_truncated = len(recipes) > 1000
    recipes = recipes[:1000]
    return selected, recipes, {"limit": limit, "variant_budget": variant_budget,
        "total_models": totals[0], "total_variants": totals[1], "loaded_variants": loaded_variants,
        "has_more": has_more, "next_after_model_id": selected[-1].id if has_more and selected else None,
        "recipes_truncated": recipes_truncated, "search": search}


def variant_metadata(actor, variant_ids):
    ids = sorted(set(variant_ids))
    variants = []
    models = {}
    for offset in range(0, len(ids), 800):
        for variant, model, code in db.session.execute(select(Variant, CatalogModel, Barcode.code)
            .join(CatalogModel, Variant.model_id == CatalogModel.id)
            .join(Barcode, (Barcode.kind == "unit") & (Barcode.target_id == Variant.id))
            .where(Variant.company_id == actor.company_id, CatalogModel.company_id == actor.company_id,
                   Barcode.company_id == actor.company_id, Variant.id.in_(ids[offset:offset + 800]))):
            variants.append({**serialize_variant(variant, code), "model_code": model.model_code, "model_name": model.name})
            models[model.id] = {"id": model.id, "model_code": model.model_code, "name": model.name}
    return variants, list(models.values())


def owned(model, record_id, actor, name="Kayıt", lock=False):
    identifier(record_id)
    query = select(model).where(model.id == record_id, model.company_id == actor.company_id)
    if lock:
        query = query.with_for_update()
    record = db.session.execute(query).scalar_one_or_none()
    reject(record is None, f"{name} bulunamadı.", "not_found", 404)
    return record


def _list(value, name="Kalemler", maximum=1000):
    reject(not isinstance(value, list) or not value or len(value) > maximum, f"{name} 1–{maximum} satır içermeli.")
    reject(any(not isinstance(row, dict) for row in value), f"{name} nesne satırlarından oluşmalı.")
    return value


def _audit(actor, action, document_type, document_id, details):
    db.session.add(AuditEvent(company_id=actor.company_id, actor_id=actor.user_id,
                              action=action, document_type=document_type,
                              document_id=document_id, details=details))


def _new_barcode(actor, kind, target_id):
    rule = db.session.execute(select(BarcodeRule).where(BarcodeRule.company_id == actor.company_id)).scalar_one_or_none()
    code = format_barcode(kind, target_id, rule.mode if rule else "alphanumeric", rule.prefix if rule else "BRV")
    db.session.add(Barcode(company_id=actor.company_id, kind=kind, target_id=target_id, code=code))
    return code


def format_barcode(kind, target_id, mode, prefix):
    if mode == "numeric":
        reject(target_id > 99_999_999, "Sayısal barkod sayacı doldu; yeni kod biçimi tanımlayın.", "barcode_range", 409)
        type_code = {"unit": "1", "recipe": "2", "package": "3", "location": "9"}[kind]
        body = prefix + type_code + str(target_id).zfill(8)
        check = (-sum(int(value) * (1 if index % 2 == 0 else 3) for index, value in enumerate(body))) % 10
        return body + str(check)
    marker = {"unit": "U", "recipe": "R", "package": "P", "location": "L"}[kind]
    return prefix + marker + str(target_id).zfill(12)


def barcode_rules(actor):
    rule = db.session.execute(select(BarcodeRule).where(BarcodeRule.company_id == actor.company_id)).scalar_one_or_none()
    mode, prefix = (rule.mode, rule.prefix) if rule else ("alphanumeric", "BRV")
    return {"mode": mode, "prefix": prefix, "revision": rule.revision if rule else 0,
            "applies_to": "new_barcodes_only", "symbology": "Code128", "gs1_assigned": False,
            "examples": {kind: format_barcode(kind, 125, mode, prefix) for kind in ("unit", "recipe", "package", "location")}}


def update_barcode_rules(actor, data):
    actor.require("barcode_rules")
    mode = data.get("mode")
    reject(mode not in ("alphanumeric", "numeric"), "Barkod biçimi alphanumeric veya numeric olmalı.")
    prefix = text_field(data.get("prefix"), "Barkod öneki", 8).upper()
    if mode == "numeric":
        reject(re.fullmatch(r"[0-9]{3}", prefix) is None, "Sayısal barkod öneki tam 3 rakam olmalı.")
    else:
        reject(re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", prefix) is None, "Harfli önek 2–8 İngilizce harf/rakam olmalı ve harfle başlamalı.")
    before = barcode_rules(actor)
    rule = db.session.execute(select(BarcodeRule).where(BarcodeRule.company_id == actor.company_id).with_for_update()).scalar_one_or_none()
    if rule is None:
        rule = BarcodeRule(company_id=actor.company_id, mode=mode, prefix=prefix, revision=1)
        db.session.add(rule)
    else:
        rule.mode, rule.prefix, rule.revision = mode, prefix, rule.revision + 1
    db.session.flush()
    _audit(actor, "barcode.rules", "barcode_rule", rule.id, {"before": before, "after": barcode_rules(actor)})
    return barcode_rules(actor)


def _barcode_for(kind, target_id):
    cache = _READ_CACHE.get()
    key = kind, target_id
    if cache is not None and key in cache["barcodes"]:
        return cache["barcodes"][key]
    result = db.session.execute(select(Barcode.code).where(Barcode.kind == kind, Barcode.target_id == target_id)).scalar_one()
    if cache is not None:
        cache["barcodes"][key] = result
    return result


def create_model(actor, data):
    actor.require("catalog_create")
    model_code = text_field(data.get("model_code"), "Model kodu", 48).upper()
    reject(not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,47}", model_code), "Model kodunda harf, rakam, nokta, tire ve alt çizgi kullanın.")
    product = CatalogModel(company_id=actor.company_id, model_code=model_code,
                           name=text_field(data.get("name"), "Model adı"),
                           description=text_field(data.get("description", ""), "Açıklama", 2000, True))
    color_rows = _list(data.get("colors"), "Renkler", 100)
    size_rows = data.get("sizes")
    reject(not isinstance(size_rows, list) or not size_rows or len(size_rows) > 50, "1–50 beden girin.")
    sizes = [text_field(size, "Beden", 24).upper() for size in size_rows]
    reject(len(set(sizes)) != len(sizes), "Aynı beden birden fazla girilemez.")
    normalized_colors = [(text_field(row.get("code"), "Renk kodu", 32).upper(),
                          text_field(row.get("name"), "Renk adı", 80)) for row in color_rows]
    reject(len(set(code for code, _ in normalized_colors)) != len(normalized_colors), "Aynı renk kodu birden fazla girilemez.")
    reject(len(sizes) * len(color_rows) > 1000, "Bir işlemde en fazla 1000 varyant oluşturulabilir.")
    unit_prices = prices(data.get("unit_prices"))
    db.session.add(product)
    db.session.flush()
    for color_code, color_name in normalized_colors:
        for size in sizes:
            variant = Variant(company_id=actor.company_id, model_id=product.id,
                              color_code=color_code, color_name=color_name,
                              size=size, prices=unit_prices.copy())
            db.session.add(variant)
            db.session.flush()
            _new_barcode(actor, "unit", variant.id)
    db.session.flush()
    _audit(actor, "catalog.create", "model", product.id, {"variants": len(sizes) * len(color_rows)})
    return serialize_model(product, actor)


def update_model(actor, model_id, data):
    actor.require("catalog_edit")
    product = owned(CatalogModel, model_id, actor)
    reject("model_code" in data and data["model_code"] != product.model_code, "Model kodu değiştirilemez.")
    before = {"name": product.name, "description": product.description}
    if "name" in data:
        product.name = text_field(data["name"], "Model adı")
    if "description" in data:
        product.description = text_field(data["description"], "Açıklama", 2000, True)
    _audit(actor, "catalog.edit", "model", product.id, {"before": before, "after": {"name": product.name, "description": product.description}})
    db.session.flush()
    return serialize_model(product, actor)


def update_prices(actor, variant_id, data):
    actor.require("price_override")
    variant = owned(Variant, variant_id, actor)
    before = variant.prices.copy()
    variant.prices = prices(data.get("prices"))
    _audit(actor, "price.edit", "variant", variant.id,
           {"before": before, "after": variant.prices, "reason": text_field(data.get("reason"), "Fiyat değişikliği nedeni", 240)})
    return serialize_variant(variant)


def create_recipe(actor, data):
    actor.require("catalog_edit")
    product = owned(CatalogModel, data.get("model_id"), actor, "Model")
    color = text_field(data.get("color_code"), "Renk kodu", 32).upper()
    name = text_field(data.get("name", "Standart seri"), "Reçete adı", 100)
    components = normalize_matrix(actor, data.get("components"))
    for item in components:
        variant = owned(Variant, item["variant_id"], actor, "Varyant")
        reject(variant.model_id != product.id or variant.color_code != color, "Seri tek model ve renkten oluşmalı.")
    version = (db.session.execute(select(func.max(Recipe.version)).where(
        Recipe.company_id == actor.company_id, Recipe.model_id == product.id,
        Recipe.color_code == color, Recipe.name == name)).scalar() or 0) + 1
    recipe = Recipe(company_id=actor.company_id, model_id=product.id, color_code=color,
                    name=name, version=version, components=components)
    db.session.add(recipe)
    db.session.flush()
    _new_barcode(actor, "recipe", recipe.id)
    db.session.flush()
    _audit(actor, "recipe.create_version", "recipe", recipe.id, {"version": version, "components": components})
    return serialize_recipe(recipe)


def create_location(actor, data):
    actor.require("inventory")
    kind = data.get("kind", "shelf")
    reject(kind not in ("store", "warehouse", "shelf", "receiving", "quarantine"), "Konum türü geçersiz.")
    parent_id = data.get("parent_id")
    if parent_id is not None:
        parent = owned(Location, parent_id, actor, "Üst konum")
        reject(parent.kind == "quarantine" and kind != "quarantine", "Karantina altına satılabilir konum eklenemez.")
    location = Location(company_id=actor.company_id,
                        code=text_field(data.get("code"), "Konum kodu", 40).upper(),
                        name=text_field(data.get("name"), "Konum adı", 120),
                        kind=kind, parent_id=parent_id)
    db.session.add(location)
    db.session.flush()
    _new_barcode(actor, "location", location.id)
    db.session.flush()
    _audit(actor, "location.create", "location", location.id, {})
    return serialize_location(location)


def normalize_matrix(actor, rows, allow_zero=False):
    result = defaultdict(int)
    for row in _list(rows):
        variant_id = identifier(row.get("variant_id"), "Varyant")
        owned(Variant, variant_id, actor, "Varyant")
        result[variant_id] += quantity(row.get("quantity"), allow_zero=allow_zero)
        reject(result[variant_id] > MAX_QUANTITY, "Toplam beden adedi sınırı aşıldı.")
    return [{"variant_id": key, "quantity": result[key]} for key in sorted(result) if result[key] > 0]


def resolve_barcode(actor, code):
    code = text_field(code, "Barkod", 48)
    barcode = db.session.execute(select(Barcode).where(Barcode.company_id == actor.company_id, Barcode.code == code)).scalar_one_or_none()
    reject(barcode is None, "Barkod bulunamadı. Önce ürün kimliğini oluşturun.", "barcode_unknown", 404)
    if barcode.kind == "location":
        location = owned(Location, barcode.target_id, actor, "Konum")
        return {"barcode": code, "kind": "location", "location_id": location.id, "location": serialize_location(location), "pieces_per_unit": 0, "components": []}
    recipe_id = None
    if barcode.kind == "unit":
        variant = owned(Variant, barcode.target_id, actor, "Varyant")
        components = [{"variant_id": variant.id, "quantity": 1}]
    elif barcode.kind == "recipe":
        recipe = owned(Recipe, barcode.target_id, actor, "Seri reçetesi")
        components, recipe_id = recipe.components, recipe.id
        variant = owned(Variant, components[0]["variant_id"], actor)
    else:
        package = owned(Package, barcode.target_id, actor, "Paket")
        components, recipe_id = package.components, package.recipe_id
        variant = owned(Variant, components[0]["variant_id"], actor)
    product = owned(CatalogModel, variant.model_id, actor, "Model")
    enriched = []
    totals = {key: ZERO for key in CURRENCIES}
    uniform = {key: set() for key in CURRENCIES}
    for part in components:
        child = owned(Variant, part["variant_id"], actor)
        enriched.append({**part, "size": child.size, "color_code": child.color_code,
                         "barcode": _barcode_for("unit", child.id)})
        for key in CURRENCIES:
            if key not in child.prices:
                totals[key] = None
                uniform[key].add(None)
            else:
                amount = Decimal(child.prices[key])
                if totals[key] is not None:
                    totals[key] += amount * part["quantity"]
                uniform[key].add(child.prices[key])
    result = {"barcode": code, "kind": barcode.kind, "model_id": product.id,
              "model_name": product.name, "model_code": product.model_code,
              "color_code": variant.color_code, "color_name": variant.color_name,
              "size": variant.size if barcode.kind == "unit" else None,
              "variant_id": variant.id if barcode.kind == "unit" else None,
              "recipe_id": recipe_id, "components": enriched,
              "pieces_per_unit": sum(row["quantity"] for row in components),
              "prices": {key: money_string(value) for key, value in totals.items() if value is not None},
              "unit_prices": {key: next(iter(values)) if len(values) == 1 and None not in values else None for key, values in uniform.items()}}
    if barcode.kind == "package":
        result.update(package_id=package.id, status=package.status, location_id=package.location_id)
    return result


def expand_items(actor, rows):
    """Freeze barcode meaning before stock or money is touched."""
    expanded = []
    for row in _list(rows):
        count = quantity(row.get("quantity", 1))
        if "barcode" in row:
            resolved = resolve_barcode(actor, row["barcode"])
            reject(resolved["kind"] == "location", "Raf barkodu ürün olarak kullanılamaz.")
            reject(resolved["kind"] == "package" and count != 1, "Fiziksel paket yalnız bir kez seçilebilir.")
            parts = resolved["components"]
            source = {"barcode": resolved["barcode"], "kind": resolved["kind"],
                      "recipe_id": resolved["recipe_id"], "package_id": resolved.get("package_id")}
        else:
            variant = owned(Variant, row.get("variant_id"), actor, "Varyant")
            parts = [{"variant_id": variant.id, "quantity": 1}]
            source = {"barcode": _barcode_for("unit", variant.id), "kind": "unit", "recipe_id": None, "package_id": None}
        for part in parts:
            amount = part["quantity"] * count
            reject(amount > MAX_QUANTITY, "Toplam parça adedi sınırı aşıldı.")
            expanded.append({"variant_id": part["variant_id"], "quantity": amount,
                             "unit_price": row.get("unit_price"), "price_reason": row.get("price_reason", ""),
                             "source": source})
            reject(len(expanded) > 2000, "Bir işlem en fazla 2000 açılmış beden satırı içerebilir; işlemi daha küçük gruplara ayırın.", "too_many_components")
    return expanded


def _change_stock(actor, variant_id, location_id, delta, reserved_delta=0, packaged_delta=0,
                  kind="adjustment", document_type="unknown", document_id=0, expected_quantity=None):
    """Conditional UPDATE is the concurrency guard, including empty-row inserts.

    Only SQLite and PostgreSQL are supported for this command path. Conflict-free
    zero-row creation precedes the conditional update; a competing checkout
    cannot consume the same final piece or reserved piece.
    """
    table = StockBalance.__table__
    dialect = db.session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise DomainError("Bu stok motoru SQLite veya PostgreSQL gerektirir.", "unsupported_database", 503)
    db.session.execute(insert(table).values(company_id=actor.company_id, variant_id=variant_id,
                       location_id=location_id, quantity=0, reserved=0, packaged=0)
                       .on_conflict_do_nothing(index_elements=["variant_id", "location_id"]))
    predicate = (table.c.quantity + delta >= table.c.reserved + reserved_delta + table.c.packaged + packaged_delta)
    statement = update(table).where(table.c.company_id == actor.company_id,
        table.c.variant_id == variant_id, table.c.location_id == location_id,
        table.c.quantity + delta >= 0, table.c.reserved + reserved_delta >= 0,
        table.c.packaged + packaged_delta >= 0, predicate).values(
            quantity=table.c.quantity + delta, reserved=table.c.reserved + reserved_delta,
            packaged=table.c.packaged + packaged_delta)
    if expected_quantity is not None:
        statement = statement.where(table.c.quantity == expected_quantity)
    changed = db.session.execute(statement).rowcount
    reject(changed != 1, "Kullanılabilir stok yetersiz veya stok başka bir işlemde kullanıldı.", "insufficient_stock", 409,
           {"variant_id": variant_id, "location_id": location_id})
    db.session.add(StockMovement(company_id=actor.company_id, variant_id=variant_id,
        location_id=location_id, quantity_delta=delta, reserved_delta=reserved_delta,
        packaged_delta=packaged_delta, kind=kind, document_type=document_type,
        document_id=document_id, actor_id=actor.user_id))


def create_plan(actor, data):
    actor.require("inventory")
    expanded = expand_items(actor, data.get("items"))
    expected = _aggregate(expanded)
    plan = ProductionPlan(company_id=actor.company_id,
        reference=text_field(data.get("reference"), "Üretim referansı", 100),
        workshop=text_field(data.get("workshop", ""), "Atölye", 120, True),
        expected=expected, actor_id=actor.user_id)
    db.session.add(plan)
    db.session.flush()
    _audit(actor, "production.plan", "plan", plan.id, {"expected": expected})
    return serialize_plan(plan, actor)


def _aggregate(expanded):
    amounts = defaultdict(int)
    for row in expanded:
        amounts[row["variant_id"]] += row["quantity"]
        reject(amounts[row["variant_id"]] > MAX_QUANTITY, "Toplam beden adedi sınırı aşıldı.")
    return [{"variant_id": key, "quantity": amounts[key]} for key in sorted(amounts)]


def _lock_stock(actor, variant_ids, location_ids):
    """Canonical lock order reduces PostgreSQL cross-transfer deadlocks.

    Conditional writes remain authoritative if a row does not exist yet.
    SQLite ignores FOR UPDATE and is serialized by the command's request row.
    """
    variants, locations = sorted(set(variant_ids)), sorted(set(location_ids))
    result = []
    batch_size = max(1, 800 - len(locations))
    for offset in range(0, len(variants), batch_size):
        result.extend(db.session.execute(select(StockBalance).where(StockBalance.company_id == actor.company_id,
            StockBalance.variant_id.in_(variants[offset:offset + batch_size]), StockBalance.location_id.in_(locations))
            .order_by(StockBalance.variant_id, StockBalance.location_id).with_for_update()).scalars().all())
    return result


def _descendant_scope(actor, root, include_quarantine=False):
    reject(root.kind not in ("store", "warehouse"), "Alt raflardan toplama için mağaza veya depo kökü seçin.")
    children = defaultdict(list)
    for row in db.session.execute(select(Location).where(Location.company_id == actor.company_id)).scalars():
        children[row.parent_id].append(row)
    result, queue = {root.id}, [root.id]
    while queue:
        for child in children[queue.pop()]:
            # Nested store/warehouse roots are independent branches. Quarantine
            # and all its descendants never contribute to sellable inventory.
            if child.id in result or child.kind in ("store", "warehouse") or (child.kind == "quarantine" and not include_quarantine):
                continue
            result.add(child.id)
            reject(len(result) > 500, "Tek konum kapsamı en fazla 500 kaynak konum içerebilir; daha dar bir depo/mağaza seçin.", "location_scope_too_large")
            queue.append(child.id)
    return sorted(result)


def create_receipt(actor, data):
    actor.require("inventory")
    location = owned(Location, data.get("location_id"), actor, "Kabul konumu")
    reject(not isinstance(data.get("items"), list), "Kabul edilen ürünler bir liste olmalı.")
    accepted = normalize_matrix(actor, data["items"]) if data["items"] else []
    plan_id = data.get("plan_id")
    if plan_id is not None:
        owned(ProductionPlan, plan_id, actor, "Üretim planı")
    for field in ("declared", "defective"):
        reject(field in data and not isinstance(data[field], list), f"{field} bir liste olmalı.")
    declared = normalize_matrix(actor, data["declared"], allow_zero=True) if data.get("declared") else []
    defective = normalize_matrix(actor, data["defective"], allow_zero=True) if data.get("defective") else []
    reject(not accepted and not defective, "Mal kabulde en az bir kabul edilen veya kusurlu parça bulunmalı.")
    quarantine = None
    if defective:
        quarantine = owned(Location, data.get("quarantine_location_id"), actor, "Karantina konumu")
        reject(quarantine.kind != "quarantine", "Kusurlu ürünler karantina konumuna alınmalı.")
    receipt = Receipt(company_id=actor.company_id, plan_id=plan_id, location_id=location.id,
                      reference=text_field(data.get("reference"), "Mal kabul referansı", 100),
                      accepted=accepted, declared=declared, defective=defective, actor_id=actor.user_id)
    db.session.add(receipt)
    db.session.flush()
    for row in accepted:
        _change_stock(actor, row["variant_id"], location.id, row["quantity"], kind="receipt", document_type="receipt", document_id=receipt.id)
    for row in defective:
        _change_stock(actor, row["variant_id"], quarantine.id, row["quantity"], kind="receipt_defective", document_type="receipt", document_id=receipt.id)
    _audit(actor, "receipt.accept", "receipt", receipt.id, {"accepted": accepted, "defective": defective})
    return serialize_receipt(receipt, actor)


def transfer_stock(actor, data):
    actor.require("inventory")
    source = owned(Location, data.get("from_location_id"), actor, "Çıkış konumu")
    target = owned(Location, data.get("to_location_id"), actor, "Hedef konum")
    reject(source.id == target.id, "Çıkış ve hedef konum farklı olmalı.")
    items = normalize_matrix(actor, data.get("items"))
    _lock_stock(actor, [row["variant_id"] for row in items], [source.id, target.id])
    if source.kind == "quarantine" and target.kind != "quarantine":
        actor.require("price_override")
        text_field(data.get("reason"), "Karantinadan çıkarma nedeni", 240)
    transfer = Transfer(company_id=actor.company_id, from_location_id=source.id,
        to_location_id=target.id, reference=text_field(data.get("reference", "Konum transferi"), "Referans", 100),
        items=items, actor_id=actor.user_id)
    db.session.add(transfer)
    db.session.flush()
    for row in items:
        _change_stock(actor, row["variant_id"], source.id, -row["quantity"], kind="transfer_out", document_type="transfer", document_id=transfer.id)
        _change_stock(actor, row["variant_id"], target.id, row["quantity"], kind="transfer_in", document_type="transfer", document_id=transfer.id)
    _audit(actor, "stock.transfer", "transfer", transfer.id, {"from": source.id, "to": target.id, "items": items})
    return {"id": transfer.id, "from_location_id": source.id, "to_location_id": target.id, "items": items}


def record_stock_count(actor, data):
    actor.require("inventory")
    actor.require("price_override")
    location = owned(Location, data.get("location_id"), actor, "Sayım konumu")
    reason = text_field(data.get("reason"), "Sayım farkı nedeni", 240)
    reference = text_field(data.get("reference"), "Sayım referansı", 100)
    counts = []
    seen = set()
    for row in _list(data.get("items")):
        variant = owned(Variant, row.get("variant_id"), actor, "Varyant")
        reject(variant.id in seen, "Her beden için tek sayım satırı gönderin.")
        seen.add(variant.id)
        counted = quantity(row.get("counted_quantity"), "Sayılan adet", allow_zero=True)
        expected = quantity(row.get("expected_quantity"), "Önizlemedeki sistem adedi", allow_zero=True)
        counts.append({"variant_id": variant.id, "counted_quantity": counted,
                       "expected_quantity": expected, "delta": counted - expected})
    counts.sort(key=lambda row: row["variant_id"])
    for row in counts:
        balance = db.session.execute(select(StockBalance).where(StockBalance.company_id == actor.company_id,
            StockBalance.variant_id == row["variant_id"], StockBalance.location_id == location.id).with_for_update()).scalar_one_or_none()
        current = balance.quantity if balance else 0
        reject(current != row["expected_quantity"], "Sayım önizlemesinden sonra stok değişti. Güncel miktarı kontrol edip sayımı yeniden değerlendirin.",
               "stock_count_changed", 409, {"variant_id": row["variant_id"], "expected_quantity": row["expected_quantity"], "current_quantity": current})
        committed = (balance.reserved + balance.packaged) if balance else 0
        reject(row["counted_quantity"] < committed, "Sayılan stok ayrılmış ve kapalı paket stoğundan az olamaz. Önce ilgili toplama/paket işlemlerini çözün.",
               "stock_count_committed", 409, {"variant_id": row["variant_id"], "committed_quantity": committed})
    count = StockCount(company_id=actor.company_id, location_id=location.id, reference=reference,
                       reason=reason, items=counts, actor_id=actor.user_id)
    db.session.add(count)
    db.session.flush()
    for row in counts:
        _change_stock(actor, row["variant_id"], location.id, row["delta"], kind="stock_count",
                      document_type="stock_count", document_id=count.id, expected_quantity=row["expected_quantity"])
    _audit(actor, "stock.count", "stock_count", count.id, {"items": counts, "reason": reason})
    return {"id": count.id, "location_id": location.id, "reference": reference,
            "reason": reason, "items": counts, "created_at": iso_utc(count.created_at)}


def inventory_health(actor):
    """Read-only reconciliation. Discrepancies are reported, never auto-fixed."""
    actor.require("audit")
    ledger = {}
    for variant_id, location_id, physical, reserved, packaged in db.session.execute(select(
        StockMovement.variant_id, StockMovement.location_id, func.sum(StockMovement.quantity_delta),
        func.sum(StockMovement.reserved_delta), func.sum(StockMovement.packaged_delta)).where(
        StockMovement.company_id == actor.company_id).group_by(StockMovement.variant_id, StockMovement.location_id)):
        ledger[(variant_id, location_id)] = {"quantity": physical, "reserved": reserved, "packaged": packaged}
    balances = {(row.variant_id, row.location_id): {"quantity": row.quantity, "reserved": row.reserved, "packaged": row.packaged}
                for row in db.session.execute(select(StockBalance).where(StockBalance.company_id == actor.company_id)).scalars()}
    allocated = defaultdict(int)
    for variant_id, location_id, amount in db.session.execute(select(PickAllocation.variant_id, PickAllocation.location_id,
        func.sum(PickAllocation.quantity)).join(PickingTask, PickAllocation.task_id == PickingTask.id).where(
        PickingTask.company_id == actor.company_id, PickAllocation.company_id == actor.company_id,
        PickingTask.status.in_(("picking", "ready"))).group_by(PickAllocation.variant_id, PickAllocation.location_id)):
        allocated[(variant_id, location_id)] = amount
    legacy_tasks = select(PickAllocation.id).where(PickAllocation.task_id == PickingTask.id).exists()
    for variant_id, location_id, amount in db.session.execute(select(PickingLine.variant_id, PickingTask.location_id,
        func.sum(PickingLine.quantity)).join(PickingTask, PickingLine.task_id == PickingTask.id).where(
        PickingTask.company_id == actor.company_id, PickingLine.company_id == actor.company_id,
        PickingTask.status.in_(("picking", "ready")), ~legacy_tasks).group_by(PickingLine.variant_id, PickingTask.location_id)):
        allocated[(variant_id, location_id)] += amount
    sealed = defaultdict(int)
    for package in db.session.execute(select(Package).where(Package.company_id == actor.company_id, Package.status == "sealed")).scalars():
        for row in package.components:
            sealed[(row["variant_id"], package.location_id)] += row["quantity"]
    issues = []
    empty = {"quantity": 0, "reserved": 0, "packaged": 0}
    for key in sorted(set(balances) | set(ledger) | set(allocated) | set(sealed)):
        balance, movement = balances.get(key, empty), ledger.get(key, empty)
        if balance != movement:
            issues.append({"code": "ledger_balance_mismatch", "variant_id": key[0], "location_id": key[1],
                           "balance": balance, "ledger": movement,
                           "difference": {field: balance[field] - movement[field] for field in empty}})
        if balance["quantity"] < balance["reserved"] + balance["packaged"] or min(balance.values()) < 0:
            issues.append({"code": "invalid_balance", "variant_id": key[0], "location_id": key[1], "balance": balance})
        if balance["reserved"] != allocated[key]:
            issues.append({"code": "reservation_mismatch", "variant_id": key[0], "location_id": key[1],
                           "balance_reserved": balance["reserved"], "task_reserved": allocated[key]})
        if balance["packaged"] != sealed[key]:
            issues.append({"code": "package_mismatch", "variant_id": key[0], "location_id": key[1],
                           "balance_packaged": balance["packaged"], "sealed_package_quantity": sealed[key]})
    return {"status": "ok" if not issues else "attention", "checked_balance_count": len(balances),
            "issue_count": len(issues), "issues": issues, "automatic_repair": False,
            "totals": {field: sum(balance[field] for balance in balances.values()) for field in empty}}


def create_customer(actor, data):
    actor.require("customers")
    customer = Customer(company_id=actor.company_id, name=text_field(data.get("name"), "Müşteri adı"),
                        phone=text_field(data.get("phone", ""), "Telefon", 30, True))
    db.session.add(customer)
    db.session.flush()
    _audit(actor, "customer.create", "customer", customer.id, {})
    return serialize_customer(customer, actor)


def _account(actor, customer_id, cur, amount, kind, document_type, document_id):
    db.session.add(AccountMovement(company_id=actor.company_id, customer_id=customer_id,
        currency=cur, amount=amount, kind=kind, document_type=document_type,
        document_id=document_id, actor_id=actor.user_id))


def _payment(actor, customer_id, sale_id, return_id, cur, amount, direction, method, reference=""):
    reject(method not in METHODS, "Ödeme yöntemi cash, card veya transfer olmalı.")
    payment = Payment(company_id=actor.company_id, customer_id=customer_id, sale_id=sale_id,
        return_id=return_id, currency=cur, amount=amount, direction=direction,
        method=method, reference=reference, actor_id=actor.user_id)
    db.session.add(payment)
    db.session.flush()
    if customer_id:
        _account(actor, customer_id, cur, -amount if direction == "in" else amount,
                 "payment" if direction == "in" else "refund", "payment", payment.id)
    return payment


def create_payment(actor, data):
    actor.require("payments")
    customer = owned(Customer, data.get("customer_id"), actor, "Müşteri", lock=True)
    cur = currency(data.get("currency", "TRY"))
    amount = money(data.get("amount"), positive=True)
    direction = data.get("direction", "in")
    reject(direction not in ("in", "out"), "Ödeme yönü in veya out olmalı.")
    if direction == "out":
        balance = Decimal(customer_balance(actor, customer.id).get(cur, "0"))
        reject(balance >= 0 or amount > -balance, "Para iadesi müşterinin alacağını aşamaz.", "refund_exceeds_credit", 409)
    payment = _payment(actor, customer.id, None, None, cur, amount, direction,
                       data.get("method", "cash"), text_field(data.get("reference", ""), "Referans", 100, True))
    _audit(actor, "payment.record", "payment", payment.id, {"amount": money_string(amount), "direction": direction})
    db.session.flush()
    return {"id": payment.id, "customer_id": customer.id, "currency": cur,
            "amount": money_string(amount), "direction": direction,
            "balances": customer_balance(actor, customer.id)}


def customer_balance(actor, customer_id):
    cache = _READ_CACHE.get()
    if cache is not None and customer_id in cache.get("customer_balances", {}):
        return cache["customer_balances"][customer_id]
    rows = db.session.execute(select(AccountMovement.currency, func.sum(AccountMovement.amount)).where(
        AccountMovement.company_id == actor.company_id, AccountMovement.customer_id == customer_id)
        .group_by(AccountMovement.currency)).all()
    return {cur: money_string(amount) for cur, amount in rows}


def create_sale(actor, data):
    actor.require("sales")
    location = owned(Location, data.get("location_id"), actor, "Satış konumu")
    reject(location.kind == "quarantine", "Karantina konumundan satış yapılamaz.")
    customer_id = data.get("customer_id")
    if customer_id is not None:
        owned(Customer, customer_id, actor, "Müşteri", lock=True)
    cur = currency(data.get("currency", "TRY"))
    expanded = expand_items(actor, data.get("items"))
    aggregate = _aggregate(expanded)
    task = None
    task_allocations = []
    if data.get("picking_task_id") is not None:
        task = owned(PickingTask, data["picking_task_id"], actor, "Toplama görevi", lock=True)
        reject(task.location_id != location.id or task.status != "ready", "Toplama tamamlanmadı veya konum uyuşmuyor.", "picking_incomplete", 409)
        task_lines = db.session.execute(select(PickingLine).where(PickingLine.task_id == task.id)).scalars().all()
        reject({row["variant_id"]: row["quantity"] for row in aggregate} != {row.variant_id: row.quantity for row in task_lines},
               "Satış içeriği ayrılmış toplama göreviyle aynı olmalı.", "picking_mismatch", 409)
        reject(any(row["source"]["kind"] == "package" for row in expanded),
               "Açık parçalardan toplanmış görev kapalı paket satışına çevrilemez.", "picking_mismatch", 409)
        task_allocations = db.session.execute(select(PickAllocation).where(PickAllocation.task_id == task.id,
            PickAllocation.company_id == actor.company_id).order_by(PickAllocation.variant_id, PickAllocation.location_id)).scalars().all()
        reject(any(row.picked != row.quantity for row in task_allocations), "Kaynak raf tahsislerinin tamamı toplanmadı.", "picking_incomplete", 409)
    total = ZERO
    priced = []
    for row in expanded:
        variant = owned(Variant, row["variant_id"], actor, "Varyant")
        reject(cur not in variant.prices, f"{variant.size} için {cur} fiyatı tanımlı değil.", "missing_price")
        list_price = money(variant.prices[cur], "Liste fiyatı")
        unit_price = list_price if row["unit_price"] is None else money(row["unit_price"], "Parça fiyatı")
        reason = ""
        if unit_price != list_price:
            actor.require("price_override")
            reason = text_field(row["price_reason"], "Fiyat değişikliği nedeni", 240)
        product = owned(CatalogModel, variant.model_id, actor)
        snapshot = {"barcode": _barcode_for("unit", variant.id), "model_code": product.model_code,
                    "model_name": product.name, "color_code": variant.color_code,
                    "color_name": variant.color_name, "size": variant.size, "source": row["source"]}
        priced.append({**row, "list_price": list_price, "unit_price": unit_price,
                       "price_reason": reason, "snapshot": snapshot})
        total += unit_price * row["quantity"]
    reject(total > Decimal("1000000000"), "Satış toplamı üst sınırı aşıyor.")
    if "expected_total" in data:
        expected_total = money(data["expected_total"], "Önizlemedeki satış toplamı")
        reject(expected_total != total, "Sepet önizlemesinden sonra fiyat değişti. Güncel fiyatları kontrol edip tekrar onaylayın.",
               "pricing_changed", 409, {"expected_total": money_string(expected_total), "actual_total": money_string(total), "currency": cur})
    payment_input = data.get("payment") or {}
    reject(not isinstance(payment_input, dict), "Ödeme bilgisi geçersiz.")
    paid = money(payment_input.get("amount", "0"), "Ödenen tutar")
    reject(paid > total, "Ödeme satış toplamını aşamaz; para üstünü ödeme tutarından çıkarın.")
    reject(paid < total and customer_id is None, "Veresiye veya kısmi ödeme için müşteri seçin.")
    if paid > 0:
        reject(payment_input.get("method", "cash") not in METHODS, "Ödeme yöntemi geçersiz.")
    sale = Sale(company_id=actor.company_id, customer_id=customer_id, location_id=location.id,
                currency=cur, total=total, paid=paid, snapshot={"items": [
                    {"variant_id": row["variant_id"], "quantity": row["quantity"], "source": row["source"]} for row in expanded],
                    "picking_task_id": task.id if task else None,
                    "allocations": [{"variant_id": row.variant_id, "location_id": row.location_id, "quantity": row.quantity} for row in task_allocations]}, actor_id=actor.user_id)
    db.session.add(sale)
    db.session.flush()
    packaged_counts = defaultdict(int)
    packages = {}
    for package_id in sorted({row["source"]["package_id"] for row in expanded if row["source"]["package_id"]}):
        package = owned(Package, package_id, actor, "Paket", lock=True)
        reject(package.status != "sealed" or package.location_id != location.id,
               "Paket kapalı değil veya satış konumunda bulunmuyor.", "package_unavailable", 409)
        packages[package_id] = package
    for row in expanded:
        if row["source"]["package_id"]:
            packaged_counts[row["variant_id"]] += row["quantity"]
    for package in packages.values():
        expected = {row["variant_id"]: row["quantity"] for row in package.components}
        actual = defaultdict(int)
        for row in expanded:
            if row["source"]["package_id"] == package.id:
                actual[row["variant_id"]] += row["quantity"]
        reject(dict(actual) != expected, "Aynı fiziksel paket sepete birden fazla eklenemez.")
        package.status = "sold"
    if task_allocations:
        _lock_stock(actor, [row.variant_id for row in task_allocations], [row.location_id for row in task_allocations])
        for row in task_allocations:
            _change_stock(actor, row.variant_id, row.location_id, -row.quantity,
                reserved_delta=-row.quantity, kind="sale", document_type="sale", document_id=sale.id)
    else:
        for row in aggregate:
            _change_stock(actor, row["variant_id"], location.id, -row["quantity"],
                reserved_delta=-row["quantity"] if task else 0,
                packaged_delta=-packaged_counts[row["variant_id"]],
                kind="sale", document_type="sale", document_id=sale.id)
    for row in priced:
        item = SaleItem(company_id=actor.company_id, sale_id=sale.id,
            variant_id=row["variant_id"], quantity=row["quantity"], unit_price=row["unit_price"],
            list_price=row["list_price"], price_reason=row["price_reason"], snapshot=row["snapshot"])
        db.session.add(item)
        if row["unit_price"] != row["list_price"]:
            _audit(actor, "sale.price_override", "sale", sale.id, {
                "variant_id": item.variant_id, "list_price": money_string(item.list_price),
                "unit_price": money_string(item.unit_price), "reason": item.price_reason})
    if customer_id:
        _account(actor, customer_id, cur, total, "sale", "sale", sale.id)
    if paid > 0:
        _payment(actor, customer_id, sale.id, None, cur, paid, "in", payment_input.get("method", "cash"))
    if task:
        task.status = "completed"
    _audit(actor, "sale.complete", "sale", sale.id, {"total": money_string(total), "paid": money_string(paid), "currency": cur})
    db.session.flush()
    return serialize_sale(sale, actor)


def return_sale(actor, sale_id, data):
    actor.require("returns")
    sale = owned(Sale, sale_id, actor, "Satış", lock=True)
    if sale.customer_id:
        owned(Customer, sale.customer_id, actor, lock=True)
    location = owned(Location, data.get("location_id"), actor, "İade konumu")
    reason = text_field(data.get("reason"), "İade nedeni", 240)
    requested = defaultdict(int)
    for row in _list(data.get("items")):
        item_id = identifier(row.get("sale_item_id"), "Satış kalemi")
        requested[item_id] += quantity(row.get("quantity"))
    total = ZERO
    resolved = []
    for item_id in sorted(requested):
        item = owned(SaleItem, item_id, actor, "Satış kalemi", lock=True)
        reject(item.sale_id != sale.id, "İade kalemi bu satışa ait değil.")
        already = db.session.execute(select(func.coalesce(func.sum(ReturnItem.quantity), 0)).where(ReturnItem.sale_item_id == item.id)).scalar_one()
        reject(already + requested[item_id] > item.quantity, "Satılan adetten fazla iade yapılamaz.", "return_exceeds_sale", 409)
        resolved.append((item, requested[item_id]))
        total += item.unit_price * requested[item_id]
    refund = money(data.get("refund_amount", "0"), "Para iadesi")
    if sale.customer_id:
        balance = Decimal(customer_balance(actor, sale.customer_id).get(sale.currency, "0"))
        max_refund = min(total, max(ZERO, total - balance))
        reject(refund > max_refund, "Para iadesi iade sonrası müşteri alacağını aşamaz.", "refund_exceeds_credit", 409)
    else:
        reject(refund != total, "Misafir müşterinin iadesinde tutarın tamamı geri ödenmeli.")
    document = ReturnDocument(company_id=actor.company_id, sale_id=sale.id,
        location_id=location.id, currency=sale.currency, total=total, refund_amount=refund,
        reason=reason, actor_id=actor.user_id)
    db.session.add(document)
    db.session.flush()
    for item, count in resolved:
        db.session.add(ReturnItem(company_id=actor.company_id, return_id=document.id, sale_item_id=item.id, quantity=count))
        _change_stock(actor, item.variant_id, location.id, count, kind="return", document_type="return", document_id=document.id)
    if sale.customer_id:
        _account(actor, sale.customer_id, sale.currency, -total, "return", "return", document.id)
    if refund > 0:
        _payment(actor, sale.customer_id, sale.id, document.id, sale.currency, refund, "out", data.get("refund_method", "cash"))
    _audit(actor, "sale.return", "return", document.id, {"sale_id": sale.id, "total": money_string(total), "refund": money_string(refund), "reason": reason})
    db.session.flush()
    return {"id": document.id, "sale_id": sale.id, "total": money_string(total),
            "refund_amount": money_string(refund), "currency": sale.currency,
            "customer_balances": customer_balance(actor, sale.customer_id) if sale.customer_id and "payments" in actor.permissions else {}}


def create_package(actor, data):
    actor.require("inventory")
    location = owned(Location, data.get("location_id"), actor, "Paket konumu")
    reject(location.kind == "quarantine", "Karantina stoğu paketlenemez.")
    recipe_id = data.get("recipe_id")
    if recipe_id is not None:
        recipe = owned(Recipe, recipe_id, actor, "Reçete")
        components = recipe.components
    else:
        components = normalize_matrix(actor, data.get("items"))
    package = Package(company_id=actor.company_id, location_id=location.id, recipe_id=recipe_id,
                      components=components, actor_id=actor.user_id)
    db.session.add(package)
    db.session.flush()
    code = _new_barcode(actor, "package", package.id)
    for row in components:
        _change_stock(actor, row["variant_id"], location.id, 0, packaged_delta=row["quantity"], kind="package_seal", document_type="package", document_id=package.id)
    _audit(actor, "package.seal", "package", package.id, {"components": components})
    return {"id": package.id, "barcode": code, "status": "sealed", "location_id": location.id, "components": components}


def open_package(actor, package_id, data):
    actor.require("inventory")
    package = owned(Package, package_id, actor, "Paket", lock=True)
    reject(package.status != "sealed", "Paket daha önce açılmış veya satılmış.", "package_unavailable", 409)
    for row in package.components:
        _change_stock(actor, row["variant_id"], package.location_id, 0, packaged_delta=-row["quantity"], kind="package_open", document_type="package", document_id=package.id)
    package.status = "opened"
    _audit(actor, "package.open", "package", package.id, {"reason": text_field(data.get("reason", "Tekli satış için açıldı"), "Neden", 240)})
    return {"id": package.id, "status": package.status, "components": package.components}


def transfer_package(actor, package_id, data):
    actor.require("inventory")
    package = owned(Package, package_id, actor, "Paket", lock=True)
    reject(package.status != "sealed", "Yalnız kapalı paket taşınabilir.", "package_unavailable", 409)
    target = owned(Location, data.get("to_location_id"), actor, "Hedef konum")
    reject(target.id == package.location_id, "Paket zaten bu konumda.")
    reject(target.kind == "quarantine", "Kapalı paket karantina aktarımından önce kontrol için açılmalı.")
    source_id = package.location_id
    _lock_stock(actor, [row["variant_id"] for row in package.components], [source_id, target.id])
    for row in package.components:
        _change_stock(actor, row["variant_id"], source_id, -row["quantity"],
                      packaged_delta=-row["quantity"], kind="package_transfer_out", document_type="package", document_id=package.id)
        _change_stock(actor, row["variant_id"], target.id, row["quantity"],
                      packaged_delta=row["quantity"], kind="package_transfer_in", document_type="package", document_id=package.id)
    package.location_id = target.id
    _audit(actor, "package.transfer", "package", package.id, {"from": source_id, "to": target.id,
        "reference": text_field(data.get("reference", "Kapalı paket transferi"), "Referans", 100)})
    return {"id": package.id, "barcode": _barcode_for("package", package.id), "status": package.status,
            "location_id": target.id, "components": package.components}


def create_picking_task(actor, data):
    actor.require("picking")
    location = owned(Location, data.get("location_id"), actor, "Toplama konumu")
    reject(location.kind == "quarantine", "Karantinadan sipariş toplanamaz.")
    expanded = expand_items(actor, data.get("items"))
    reject(any(row["source"]["kind"] == "package" for row in expanded), "Paketler doğrudan paket barkoduyla satılır; parça toplamak için paketi açın.")
    components = _aggregate(expanded)
    include_descendants = data.get("include_descendants", False)
    reject(type(include_descendants) is not bool, "Alt raflardan toplama seçimi true veya false olmalı.")
    scope = _descendant_scope(actor, location) if include_descendants else [location.id]
    stock_rows = _lock_stock(actor, [row["variant_id"] for row in components], scope)
    by_variant = defaultdict(list)
    for row in stock_rows:
        by_variant[row.variant_id].append(row)
    allocations = []
    for item in components:
        remaining = item["quantity"]
        for balance in by_variant[item["variant_id"]]:
            take = min(remaining, balance.quantity - balance.reserved - balance.packaged)
            if take > 0:
                allocations.append({"variant_id": item["variant_id"], "location_id": balance.location_id, "quantity": take})
                remaining -= take
            if remaining == 0:
                break
        reject(remaining > 0, "Seçilen konum kapsamındaki kullanılabilir stok yetersiz.", "insufficient_stock", 409,
               {"variant_id": item["variant_id"], "missing_quantity": remaining})
    task = PickingTask(company_id=actor.company_id, location_id=location.id,
                       snapshot={"items": components, "sources": [row["source"] for row in expanded],
                                 "include_descendants": include_descendants, "scope_location_ids": scope}, actor_id=actor.user_id)
    db.session.add(task)
    db.session.flush()
    for row in allocations:
        _change_stock(actor, row["variant_id"], row["location_id"], 0, reserved_delta=row["quantity"], kind="reserve", document_type="picking", document_id=task.id)
        db.session.add(PickAllocation(company_id=actor.company_id, task_id=task.id,
            variant_id=row["variant_id"], location_id=row["location_id"], quantity=row["quantity"], picked=0))
    for row in components:
        db.session.add(PickingLine(company_id=actor.company_id, task_id=task.id,
                                  variant_id=row["variant_id"], quantity=row["quantity"], picked=0))
    _audit(actor, "picking.create", "picking", task.id, {"components": components})
    db.session.flush()
    return serialize_picking(task, actor)


def scan_picking(actor, task_id, data):
    actor.require("picking")
    task = owned(PickingTask, task_id, actor, "Toplama görevi", lock=True)
    reject(task.status not in ("picking", "ready"), "Bu toplama görevi artık açık değil.", "invalid_state", 409)
    allocations = db.session.execute(select(PickAllocation).where(PickAllocation.task_id == task.id,
        PickAllocation.company_id == actor.company_id)).scalars().all()
    source_locations = {row.location_id for row in allocations} or {task.location_id}
    resolved = resolve_barcode(actor, data.get("barcode"))
    if resolved["kind"] == "location":
        reject(resolved["location_id"] not in source_locations, "Yanlış raf okutuldu. Görevdeki kaynak raf etiketini okutun.", "wrong_location", 409)
        db.session.add(PickingEvent(company_id=actor.company_id, task_id=task.id,
                                   barcode=resolved["barcode"], components=[], actor_id=actor.user_id))
        db.session.flush()
        return serialize_picking(task, actor)
    root_location = owned(Location, task.location_id, actor)
    active_location = _active_pick_location(task.id, actor)
    if task.snapshot.get("include_descendants") or root_location.kind == "shelf":
        reject(active_location not in source_locations, "Ürünleri toplamadan önce kaynak rafın barkodunu okutun.", "location_scan_required", 409)
    else:
        active_location = task.location_id
    if "location_id" in data:
        reject(data["location_id"] != active_location, "Yanlış raf veya konum okutuldu.", "wrong_location", 409)
    reject(resolved["kind"] not in ("unit", "package"), "Toplamada gerçek ürün etiketi okutun; soyut seri reçetesi miktarı doğrulamaz.")
    reject(resolved["kind"] == "package", "Kapalı paket ayrılmış açık parçalara sayılamaz; önce paketi açın.")
    count = quantity(data.get("quantity", 1))
    for row in resolved["components"]:
        amount = row["quantity"] * count
        if allocations:
            allocated = db.session.execute(update(PickAllocation).where(PickAllocation.task_id == task.id,
                PickAllocation.company_id == actor.company_id, PickAllocation.variant_id == row["variant_id"],
                PickAllocation.location_id == active_location, PickAllocation.picked + amount <= PickAllocation.quantity)
                .values(picked=PickAllocation.picked + amount))
            reject(allocated.rowcount != 1, "Bu rafta bu bedenden beklenen miktar yok veya fazla ürün okutuldu.", "picking_quantity", 409)
        result = db.session.execute(update(PickingLine).where(PickingLine.task_id == task.id,
            PickingLine.company_id == actor.company_id, PickingLine.variant_id == row["variant_id"],
            PickingLine.picked + amount <= PickingLine.quantity).values(picked=PickingLine.picked + amount))
        reject(result.rowcount != 1, "Yanlış beden veya istenenden fazla ürün okutuldu.", "picking_quantity", 409)
    db.session.add(PickingEvent(company_id=actor.company_id, task_id=task.id,
                               barcode=resolved["barcode"], components=[{**row, "quantity": row["quantity"] * count, "location_id": active_location} for row in resolved["components"]], actor_id=actor.user_id))
    db.session.flush()
    remaining = db.session.execute(select(func.count(PickingLine.id)).where(PickingLine.task_id == task.id, PickingLine.picked < PickingLine.quantity)).scalar_one()
    task.status = "ready" if remaining == 0 else "picking"
    db.session.flush()
    return serialize_picking(task, actor)


def cancel_picking(actor, task_id, data):
    actor.require("picking")
    task = owned(PickingTask, task_id, actor, "Toplama görevi", lock=True)
    reject(task.status not in ("picking", "ready"), "Bu görev iptal edilemez.", "invalid_state", 409)
    allocations = db.session.execute(select(PickAllocation).where(PickAllocation.task_id == task.id,
        PickAllocation.company_id == actor.company_id).order_by(PickAllocation.variant_id, PickAllocation.location_id)).scalars().all()
    if allocations:
        _lock_stock(actor, [row.variant_id for row in allocations], [row.location_id for row in allocations])
        for row in allocations:
            _change_stock(actor, row.variant_id, row.location_id, 0, reserved_delta=-row.quantity, kind="unreserve", document_type="picking", document_id=task.id)
    else:
        for row in db.session.execute(select(PickingLine).where(PickingLine.task_id == task.id)).scalars():
            _change_stock(actor, row.variant_id, task.location_id, 0, reserved_delta=-row.quantity, kind="unreserve", document_type="picking", document_id=task.id)
    task.status = "cancelled"
    _audit(actor, "picking.cancel", "picking", task.id, {"reason": text_field(data.get("reason", "İptal"), "Neden", 240)})
    return serialize_picking(task, actor)


def _active_pick_location(task_id, actor):
    cache = _READ_CACHE.get()
    if cache is not None and task_id in cache.get("picking_lines", {}):
        return cache.get("picking_active_locations", {}).get(task_id)
    return db.session.execute(select(Barcode.target_id).join(PickingEvent,
        (PickingEvent.barcode == Barcode.code) & (PickingEvent.company_id == Barcode.company_id))
        .where(PickingEvent.task_id == task_id, PickingEvent.actor_id == actor.user_id,
               PickingEvent.company_id == actor.company_id, Barcode.kind == "location")
        .order_by(PickingEvent.id.desc()).limit(1)).scalar_one_or_none()


def serialize_variant(variant, barcode=None):
    return {"id": variant.id, "model_id": variant.model_id, "color_code": variant.color_code,
            "color_name": variant.color_name, "size": variant.size,
            "prices": variant.prices, "barcode": barcode if barcode is not None else _barcode_for("unit", variant.id)}


def serialize_model(product, actor):
    cache = _READ_CACHE.get()
    if cache is not None and product.id in cache.get("model_variants", {}):
        variants = cache["model_variants"][product.id]
    else:
        variants = db.session.execute(select(Variant).where(Variant.model_id == product.id,
                                      Variant.company_id == actor.company_id).order_by(Variant.id)).scalars().all()
    return {"id": product.id, "model_code": product.model_code, "name": product.name,
            "description": product.description, "variants": [serialize_variant(row) for row in variants]}


def serialize_recipe(recipe, barcode=None):
    return {"id": recipe.id, "model_id": recipe.model_id, "color_code": recipe.color_code,
            "name": recipe.name, "version": recipe.version, "barcode": barcode if barcode is not None else _barcode_for("recipe", recipe.id),
            "components": recipe.components, "pieces_per_unit": sum(row["quantity"] for row in recipe.components)}


def serialize_location(location):
    return {"id": location.id, "code": location.code, "name": location.name,
            "kind": location.kind, "parent_id": location.parent_id, "barcode": _barcode_for("location", location.id)}


def serialize_customer(customer, actor):
    return {"id": customer.id, "name": customer.name, "phone": customer.phone,
            "balances": customer_balance(actor, customer.id)}


def _component_refs(actor, components, detailed=False):
    if detailed:
        metadata, _ = variant_metadata(actor, [row["variant_id"] for row in components])
        by_id = {row["id"]: row for row in metadata}
        return [{**{key: value for key, value in by_id.get(row["variant_id"], {}).items() if key not in ("id", "prices")}, **row} for row in components]
    cache = _READ_CACHE.get()
    mapping = cache.setdefault("component_model_ids", {}) if cache is not None else {}
    ids = sorted({row["variant_id"] for row in components if row["variant_id"] not in mapping})
    for offset in range(0, len(ids), 800):
        mapping.update(db.session.execute(select(Variant.id, Variant.model_id).where(
            Variant.company_id == actor.company_id, Variant.id.in_(ids[offset:offset + 800]))).all())
    return [{**row, "model_id": mapping.get(row["variant_id"])} for row in components]


def serialize_plan(plan, actor, detailed=False):
    actual = defaultdict(int)
    defective = defaultdict(int)
    cache = _READ_CACHE.get()
    if cache is not None and plan.id in cache.get("plan_receipts", {}):
        receipts = cache["plan_receipts"][plan.id]
    else:
        receipts = db.session.execute(select(Receipt).where(Receipt.plan_id == plan.id, Receipt.company_id == actor.company_id)).scalars().all()
    for receipt in receipts:
        for row in receipt.accepted:
            actual[row["variant_id"]] += row["quantity"]
        for row in receipt.defective:
            defective[row["variant_id"]] += row["quantity"]
    expected = {row["variant_id"]: row["quantity"] for row in plan.expected}
    keys = sorted(set(expected) | set(actual) | set(defective))
    expected_refs = _component_refs(actor, plan.expected, detailed)
    return {"id": plan.id, "reference": plan.reference, "workshop": plan.workshop,
            "expected": expected_refs, "model_ids": sorted({row["model_id"] for row in expected_refs if row.get("model_id")}),
            "accepted": [{"variant_id": key, "quantity": actual[key]} for key in keys],
            "differences": [{"variant_id": key, "expected": expected.get(key, 0), "accepted": actual[key],
                             "defective": defective[key], "difference": actual[key] - expected.get(key, 0)} for key in keys]}


def serialize_receipt(receipt, actor):
    cache = _READ_CACHE.get()
    plan = cache.get("plans_by_id", {}).get(receipt.plan_id) if cache is not None else None
    if receipt.plan_id and plan is None:
        plan = owned(ProductionPlan, receipt.plan_id, actor)
    return {"id": receipt.id, "plan_id": receipt.plan_id, "location_id": receipt.location_id,
            "reference": receipt.reference, "items": receipt.accepted, "declared": receipt.declared,
            "defective": receipt.defective, "created_at": iso_utc(receipt.created_at),
            "plan": serialize_plan(plan, actor) if plan else None}


def serialize_package(package, actor, detailed=False):
    components = _component_refs(actor, package.components, detailed)
    return {"id": package.id, "location_id": package.location_id, "recipe_id": package.recipe_id,
            "barcode": _barcode_for("package", package.id), "status": package.status, "components": components,
            "model_ids": sorted({row["model_id"] for row in components if row.get("model_id")})}


def serialize_sale(sale, actor):
    items = []
    cache = _READ_CACHE.get()
    if cache is not None and sale.id in cache.get("sale_items", {}):
        sale_items = cache["sale_items"][sale.id]
    else:
        sale_items = db.session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id).order_by(SaleItem.id)).scalars().all()
    for row in sale_items:
        returned = cache["returned"].get(row.id, 0) if cache is not None and sale.id in cache.get("sale_items", {}) else db.session.execute(select(func.coalesce(func.sum(ReturnItem.quantity), 0)).where(ReturnItem.sale_item_id == row.id)).scalar_one()
        items.append({"id": row.id, "variant_id": row.variant_id, "quantity": row.quantity,
                      "unit_price": money_string(row.unit_price), "list_price": money_string(row.list_price),
                      "total": money_string(row.unit_price * row.quantity), "returned": returned,
                      "price_reason": row.price_reason, "snapshot": row.snapshot})
    return {"id": sale.id, "reference": f"SAT-{sale.id:07d}", "customer_id": sale.customer_id,
            "location_id": sale.location_id, "currency": sale.currency,
            "total": money_string(sale.total), "paid": money_string(sale.paid),
            "balance_due": money_string(sale.total - sale.paid), "items": items,
            "created_at": iso_utc(sale.created_at),
            "customer_balances": customer_balance(actor, sale.customer_id) if sale.customer_id and "payments" in actor.permissions else {}}


def serialize_picking(task, actor=None):
    cache = _READ_CACHE.get()
    if cache is not None and task.id in cache.get("picking_lines", {}):
        lines = cache["picking_lines"][task.id]
    else:
        lines = db.session.execute(select(PickingLine).where(PickingLine.task_id == task.id).order_by(PickingLine.id)).scalars().all()
    if cache is not None and task.id in cache.get("picking_allocations", {}):
        allocations = cache["picking_allocations"][task.id]
        metadata = cache["picking_variants"]
    else:
        allocations = db.session.execute(select(PickAllocation).where(PickAllocation.task_id == task.id,
            PickAllocation.company_id == task.company_id).order_by(PickAllocation.variant_id, PickAllocation.location_id)).scalars().all()
        variants, _ = variant_metadata(actor, [row.variant_id for row in lines]) if actor else ([], [])
        metadata = {row["id"]: row for row in variants}
    source_ids = {row.location_id for row in allocations} or {task.location_id}
    if cache is not None and task.id in cache.get("picking_allocations", {}):
        source_locations = [cache["picking_locations"][key] for key in sorted(source_ids)]
    else:
        source_locations = db.session.execute(select(Location).where(Location.company_id == task.company_id,
            Location.id.in_(source_ids)).order_by(Location.id)).scalars().all()
    location_code = _barcode_for("location", task.location_id)
    active_location = _active_pick_location(task.id, actor) if actor else None
    verified = active_location in source_ids
    return {"id": task.id, "location_id": task.location_id, "status": task.status,
            "location_barcode": location_code, "location_verified": verified,
            "include_descendants": bool(task.snapshot.get("include_descendants")), "active_location_id": active_location,
            "source_locations": [serialize_location(row) for row in source_locations],
            "allocations": [{**metadata.get(row.variant_id, {}), "id": row.id, "variant_id": row.variant_id,
                "location_id": row.location_id, "quantity": row.quantity, "picked": row.picked, "remaining": row.quantity - row.picked} for row in allocations],
            "lines": [{**metadata.get(row.variant_id, {}), "id": row.id, "variant_id": row.variant_id, "quantity": row.quantity,
                       "picked": row.picked, "remaining": row.quantity - row.picked} for row in lines]}


def stock_snapshot(actor, location_id=None, after_balance_id=None, limit=1000, recipe_id=None, model_id=None, include_descendants=False):
    reject(type(limit) is not int or not 1 <= limit <= 1000, "Stok sayfa boyutu 1–1000 olmalı.")
    filters = [StockBalance.company_id == actor.company_id]
    physical_scope = sellable_scope = None
    reject(type(include_descendants) is not bool, "Alt konum seçimi true veya false olmalı.")
    reject(include_descendants and location_id is None, "Alt rafları birleştirmeden önce tek mağaza veya depo kökü seçin.")
    if location_id is not None:
        root = owned(Location, location_id, actor, "Konum")
        if include_descendants:
            physical_scope = _descendant_scope(actor, root, include_quarantine=True)
            sellable_scope = _descendant_scope(actor, root)
            filters.append(StockBalance.location_id.in_(physical_scope))
        else:
            filters.append(StockBalance.location_id == location_id)
    if model_id is not None:
        owned(CatalogModel, model_id, actor, "Model")
        filters.append(StockBalance.variant_id.in_(select(Variant.id).where(Variant.model_id == model_id, Variant.company_id == actor.company_id)))
    page_query = select(StockBalance).where(*filters).order_by(StockBalance.id).limit(limit + 1)
    if after_balance_id is not None:
        page_query = page_query.where(StockBalance.id > after_balance_id)
    rows = db.session.execute(page_query).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    locations = db.session.execute(select(Location).where(Location.company_id == actor.company_id).order_by(Location.id)).scalars().all()
    quarantine_ids = {row.id for row in locations if row.kind == "quarantine"}
    balances = [{"id": row.id, "variant_id": row.variant_id, "location_id": row.location_id,
                 "quantity": row.quantity, "reserved": row.reserved, "packaged": row.packaged,
                 "quarantined": row.quantity if row.location_id in quarantine_ids else 0,
                 "available": 0 if row.location_id in quarantine_ids else row.quantity - row.reserved - row.packaged} for row in rows]
    totals_row = db.session.execute(select(func.count(StockBalance.id),
        func.coalesce(func.sum(StockBalance.quantity), 0), func.coalesce(func.sum(StockBalance.reserved), 0),
        func.coalesce(func.sum(StockBalance.packaged), 0),
        func.coalesce(func.sum(case((Location.kind == "quarantine", StockBalance.quantity), else_=0)), 0),
        func.coalesce(func.sum(case((Location.kind == "quarantine", 0), else_=StockBalance.quantity - StockBalance.reserved - StockBalance.packaged)), 0))
        .join(Location, Location.id == StockBalance.location_id).where(*filters)).one()
    if recipe_id is not None:
        recipes = [owned(Recipe, recipe_id, actor, "Reçete")]
        recipes_truncated = False
    else:
        recipe_query = select(Recipe).where(Recipe.company_id == actor.company_id)
        if model_id is not None:
            recipe_query = recipe_query.where(Recipe.model_id == model_id)
        recipes = db.session.execute(recipe_query.order_by(Recipe.id.desc()).limit(201)).scalars().all()
        recipes_truncated = len(recipes) > 200
        recipes = recipes[:200]
    usable_locations = [location_id] if include_descendants else [row.id for row in locations if row.kind != "quarantine" and (location_id is None or row.id == location_id)]
    # Never materialize an unbounded recipes × locations × components response.
    # A location/recipe filter provides the full relevant view after truncation.
    pair_limit = 500
    pairs = []
    for loc_id in usable_locations:
        for recipe in recipes:
            if len(pairs) == pair_limit:
                break
            pairs.append((loc_id, recipe))
        if len(pairs) == pair_limit:
            break
    by_location = defaultdict(dict)
    needed_locations = sellable_scope if include_descendants else sorted({loc_id for loc_id, _ in pairs})
    variant_ids = sorted({row["variant_id"] for _, recipe in pairs for row in recipe.components})
    variant_batch_size = max(1, 800 - len(needed_locations))
    for offset in range(0, len(variant_ids), variant_batch_size):
        for variant_id_value, loc_id, available in db.session.execute(select(StockBalance.variant_id,
            StockBalance.location_id, StockBalance.quantity - StockBalance.reserved - StockBalance.packaged)
            .where(StockBalance.company_id == actor.company_id, StockBalance.location_id.in_(needed_locations),
                   StockBalance.variant_id.in_(variant_ids[offset:offset + variant_batch_size]))):
            if include_descendants:
                by_location[location_id][variant_id_value] = by_location[location_id].get(variant_id_value, 0) + available
            else:
                by_location[loc_id][variant_id_value] = available
    capacities = []
    for loc_id, recipe in pairs:
        available = by_location[loc_id]
        capacity = min(available.get(row["variant_id"], 0) // row["quantity"] for row in recipe.components)
        capacities.append({"recipe_id": recipe.id, "location_id": loc_id,
            "series_capacity": capacity, "alternative_capacity": True,
            "allocation_required": include_descendants, "include_descendants": include_descendants,
            "capacity_type": "collectable_from_shelves" if include_descendants else "exact_location",
            "leftovers": [{"variant_id": row["variant_id"], "quantity": available.get(row["variant_id"], 0) - capacity * row["quantity"]} for row in recipe.components],
            "limiting_variant_ids": [row["variant_id"] for row in recipe.components if available.get(row["variant_id"], 0) // row["quantity"] == capacity]})
    metadata_ids = set(variant_ids) | {row.variant_id for row in rows}
    variants, model_metadata = variant_metadata(actor, metadata_ids)
    recipe_codes = dict(db.session.execute(select(Barcode.target_id, Barcode.code).where(
        Barcode.company_id == actor.company_id, Barcode.kind == "recipe", Barcode.target_id.in_([row.id for row in recipes]))).all()) if recipes else {}
    return {"balances": balances, "capacities": capacities, "variants": variants, "models": model_metadata,
            "recipes": [serialize_recipe(row, recipe_codes[row.id]) for row in recipes],
            "scope": {"include_descendants": include_descendants, "location_id": location_id,
                      "physical_location_ids": physical_scope, "sellable_location_ids": sellable_scope,
                      "quarantine_in_physical_total": bool(quarantine_ids & (set(physical_scope) if physical_scope else ({location_id} if location_id else {row.id for row in locations}))),
                      "allocation_required": include_descendants},
            "pagination": {"limit": limit, "total_balances": totals_row[0], "has_more": has_more,
                           "next_after_balance_id": rows[-1].id if has_more else None},
            "capacities_truncated": recipes_truncated or len(usable_locations) * len(recipes) > pair_limit,
            "capacity_limit": pair_limit,
            "totals": dict(zip(("quantity", "reserved", "packaged", "quarantined", "available"), totals_row[1:]))}
