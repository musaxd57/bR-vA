"""Additive, tenant-scoped inventory schema.

The legacy tables are intentionally not migrated implicitly. A v2 catalogue
starts with zero stock; only accepted receipts create physical stock. Quantities
are pieces, money is NUMERIC, and every balance mutation has an immutable ledger
entry in the same transaction.
"""
from datetime import datetime, timezone

from sqlalchemy import event, inspect
from extensions import db


def now():
    return datetime.now(timezone.utc)


class TenantMixin:
    company_id = db.Column(db.String(100), nullable=False, index=True)


class CatalogModel(TenantMixin, db.Model):
    __tablename__ = "briva_model"
    id = db.Column(db.Integer, primary_key=True)
    model_code = db.Column(db.String(48), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "model_code", name="uq_briva_model_code"),)


class Variant(TenantMixin, db.Model):
    __tablename__ = "briva_variant"
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("briva_model.id"), nullable=False, index=True)
    color_code = db.Column(db.String(32), nullable=False)
    color_name = db.Column(db.String(80), nullable=False)
    size = db.Column(db.String(24), nullable=False)
    prices = db.Column(db.JSON, nullable=False, default=dict)
    __table_args__ = (db.UniqueConstraint("model_id", "color_code", "size", name="uq_briva_variant"),)


class Recipe(TenantMixin, db.Model):
    __tablename__ = "briva_recipe"
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("briva_model.id"), nullable=False)
    color_code = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    components = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "model_id", "color_code", "name", "version", name="uq_briva_recipe_version"),)


class Barcode(TenantMixin, db.Model):
    __tablename__ = "briva_barcode"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(48), unique=True, nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("kind", "target_id", name="uq_briva_barcode_target"), db.CheckConstraint("kind IN ('unit','recipe','package','location')", name="ck_briva_barcode_kind"))


class BarcodeRule(TenantMixin, db.Model):
    __tablename__ = "briva_barcode_rule"
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(16), nullable=False, default="alphanumeric")
    prefix = db.Column(db.String(8), nullable=False, default="BRV")
    revision = db.Column(db.Integer, nullable=False, default=1)
    __table_args__ = (db.UniqueConstraint("company_id", name="uq_briva_barcode_rule_company"), db.CheckConstraint("mode IN ('alphanumeric','numeric')", name="ck_briva_barcode_rule_mode"))


class Location(TenantMixin, db.Model):
    __tablename__ = "briva_location"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=True)
    __table_args__ = (db.UniqueConstraint("company_id", "code", name="uq_briva_location"), db.CheckConstraint("kind IN ('store','warehouse','shelf','receiving','quarantine')", name="ck_briva_location_kind"))


class StockBalance(TenantMixin, db.Model):
    __tablename__ = "briva_stock_balance"
    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("briva_variant.id"), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    reserved = db.Column(db.Integer, nullable=False, default=0)
    packaged = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("variant_id", "location_id", name="uq_briva_balance"), db.CheckConstraint("quantity >= 0 AND reserved >= 0 AND packaged >= 0 AND quantity >= reserved + packaged", name="ck_briva_nonnegative_stock"))


class StockMovement(TenantMixin, db.Model):
    __tablename__ = "briva_stock_movement"
    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("briva_variant.id"), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False, index=True)
    quantity_delta = db.Column(db.Integer, nullable=False)
    reserved_delta = db.Column(db.Integer, nullable=False, default=0)
    packaged_delta = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(32), nullable=False)
    document_type = db.Column(db.String(32), nullable=False)
    document_id = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class ProductionPlan(TenantMixin, db.Model):
    __tablename__ = "briva_production_plan"
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(100), nullable=False)
    workshop = db.Column(db.String(120), nullable=False, default="")
    expected = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "reference", name="uq_briva_plan_ref"),)


class Receipt(TenantMixin, db.Model):
    __tablename__ = "briva_receipt"
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("briva_production_plan.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    accepted = db.Column(db.JSON, nullable=False)
    declared = db.Column(db.JSON, nullable=False, default=list)
    defective = db.Column(db.JSON, nullable=False, default=list)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "reference", name="uq_briva_receipt_ref"),)


class Transfer(TenantMixin, db.Model):
    __tablename__ = "briva_transfer"
    id = db.Column(db.Integer, primary_key=True)
    from_location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    to_location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    items = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class StockCount(TenantMixin, db.Model):
    __tablename__ = "briva_stock_count"
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    items = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "reference", name="uq_briva_stock_count_reference"),)


class CoreCustomer(TenantMixin, db.Model):
    __tablename__ = "briva_customer"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(30), nullable=False, default="")


class CoreSale(TenantMixin, db.Model):
    __tablename__ = "briva_sale"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("briva_customer.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    total = db.Column(db.Numeric(18, 2), nullable=False)
    paid = db.Column(db.Numeric(18, 2), nullable=False)
    snapshot = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.CheckConstraint("total >= 0 AND paid >= 0 AND paid <= total", name="ck_briva_sale_money"),)


class SaleItem(TenantMixin, db.Model):
    __tablename__ = "briva_sale_item"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("briva_sale.id"), nullable=False, index=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("briva_variant.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(18, 2), nullable=False)
    list_price = db.Column(db.Numeric(18, 2), nullable=False)
    price_reason = db.Column(db.String(240), nullable=False, default="")
    snapshot = db.Column(db.JSON, nullable=False)
    __table_args__ = (db.CheckConstraint("quantity > 0 AND unit_price >= 0 AND list_price >= 0", name="ck_briva_sale_item"),)


class ReturnDocument(TenantMixin, db.Model):
    __tablename__ = "briva_return"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("briva_sale.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    total = db.Column(db.Numeric(18, 2), nullable=False)
    refund_amount = db.Column(db.Numeric(18, 2), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class ReturnItem(TenantMixin, db.Model):
    __tablename__ = "briva_return_item"
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey("briva_return.id"), nullable=False)
    sale_item_id = db.Column(db.Integer, db.ForeignKey("briva_sale_item.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.CheckConstraint("quantity > 0", name="ck_briva_return_qty"),)


class AccountMovement(TenantMixin, db.Model):
    __tablename__ = "briva_account_movement"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("briva_customer.id"), nullable=False, index=True)
    currency = db.Column(db.String(3), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    document_type = db.Column(db.String(24), nullable=False)
    document_id = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class Payment(TenantMixin, db.Model):
    __tablename__ = "briva_payment"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("briva_customer.id"), nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("briva_sale.id"), nullable=True)
    return_id = db.Column(db.Integer, db.ForeignKey("briva_return.id"), nullable=True)
    currency = db.Column(db.String(3), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    direction = db.Column(db.String(3), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    reference = db.Column(db.String(100), nullable=False, default="")
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.CheckConstraint("amount > 0 AND direction IN ('in','out')", name="ck_briva_payment"),)


class Package(TenantMixin, db.Model):
    __tablename__ = "briva_package"
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    recipe_id = db.Column(db.Integer, db.ForeignKey("briva_recipe.id"), nullable=True)
    components = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="sealed")
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.CheckConstraint("status IN ('sealed','opened','sold')", name="ck_briva_package_status"),)


class PickingTask(TenantMixin, db.Model):
    __tablename__ = "briva_picking_task"
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="picking")
    snapshot = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class PickingLine(TenantMixin, db.Model):
    __tablename__ = "briva_picking_line"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("briva_picking_task.id"), nullable=False, index=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("briva_variant.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    picked = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("task_id", "variant_id", name="uq_briva_pick_variant"), db.CheckConstraint("quantity > 0 AND picked >= 0 AND picked <= quantity", name="ck_briva_picking_line"))


class PickAllocation(TenantMixin, db.Model):
    __tablename__ = "briva_pick_allocation"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("briva_picking_task.id"), nullable=False, index=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("briva_variant.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("briva_location.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    picked = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("task_id", "variant_id", "location_id", name="uq_briva_pick_allocation"),
                      db.CheckConstraint("quantity > 0 AND picked >= 0 AND picked <= quantity", name="ck_briva_pick_allocation"))


class PickingEvent(TenantMixin, db.Model):
    __tablename__ = "briva_picking_event"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("briva_picking_task.id"), nullable=False)
    barcode = db.Column(db.String(48), nullable=False)
    components = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class RequestRecord(TenantMixin, db.Model):
    __tablename__ = "briva_request"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), nullable=False)
    digest = db.Column(db.String(64), nullable=False)
    operation = db.Column(db.String(180), nullable=False)
    result = db.Column(db.JSON, nullable=True)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (db.UniqueConstraint("company_id", "key", name="uq_briva_request_key"),)


class AuditEvent(TenantMixin, db.Model):
    __tablename__ = "briva_audit_event"
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(64), nullable=False)
    document_type = db.Column(db.String(32), nullable=False)
    document_id = db.Column(db.Integer, nullable=False)
    details = db.Column(db.JSON, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


# Aliases keep the domain vocabulary concise without colliding with the legacy
# registry's string relationships (models.Customer and models.Sale).
Customer = CoreCustomer
Sale = CoreSale


def _immutable(mapper, connection, target):
    raise ValueError("Geçmiş kayıt değiştirilemez; yeni bir ters hareket oluşturun.")


for immutable_model in (Recipe, Barcode, StockMovement, ProductionPlan, Receipt, Transfer, StockCount,
                        Sale, SaleItem, ReturnDocument, ReturnItem, AccountMovement,
                        Payment, PickingEvent, AuditEvent):
    event.listen(immutable_model, "before_update", _immutable)
    event.listen(immutable_model, "before_delete", _immutable)


@event.listens_for(CatalogModel, "before_update")
def _protect_model_code(mapper, connection, target):
    if inspect(target).attrs.model_code.history.has_changes():
        raise ValueError("Model kodu kalıcıdır; yalnız ad ve açıklama değiştirilebilir.")


@event.listens_for(Variant, "before_update")
def _protect_variant_identity(mapper, connection, target):
    state = inspect(target)
    if any(state.attrs[field].history.has_changes() for field in ("model_id", "color_code", "size")):
        raise ValueError("Varyant kimliği kalıcıdır; yeni beden için yeni varyant oluşturun.")


CORE_TABLE_NAMES = frozenset(value.__table__.name for value in tuple(globals().values())
    if isinstance(value, type) and value.__module__ == __name__ and hasattr(value, "__table__"))
