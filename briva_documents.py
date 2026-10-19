"""Read-only sale documents rendered from immutable transaction snapshots."""
from decimal import Decimal

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func, select

from extensions import db
from briva_core.api import current_actor, require_schema
from briva_core.errors import DomainError, reject
from briva_core.models import ReturnDocument, ReturnItem, Sale, SaleItem
from briva_core.service import iso_utc, owned


blueprint = Blueprint('briva_documents', __name__)
ZERO = Decimal('0.00')


def register_documents(app):
    app.register_blueprint(blueprint)


@blueprint.errorhandler(DomainError)
def document_error(error):
    return jsonify(error={'code': error.code, 'message': error.message, 'details': error.details}), error.status


def formatted_money(value):
    """A Turkish display string; all arithmetic remains exact Decimal."""
    return format(Decimal(value).quantize(Decimal('0.01')), ',.2f').replace(',', '\u00a0').replace('.', ',')


def sale_document_data(actor, sale):
    """No current catalog/customer price, account balance or payment allocation."""
    rows = db.session.execute(select(SaleItem).where(
        SaleItem.company_id == actor.company_id, SaleItem.sale_id == sale.id,
    ).order_by(SaleItem.id)).scalars().all()
    returned = dict(db.session.execute(select(ReturnItem.sale_item_id, func.sum(ReturnItem.quantity))
        .join(ReturnDocument, ReturnDocument.id == ReturnItem.return_id)
        .where(ReturnItem.company_id == actor.company_id, ReturnDocument.company_id == actor.company_id,
               ReturnDocument.sale_id == sale.id).group_by(ReturnItem.sale_item_id)).all())
    items, list_total, discount_total, increase_total = [], ZERO, ZERO, ZERO
    for row in rows:
        snapshot = row.snapshot
        list_subtotal = row.list_price * row.quantity
        subtotal = row.unit_price * row.quantity
        reduction = max(ZERO, list_subtotal - subtotal)
        increase = max(ZERO, subtotal - list_subtotal)
        list_total += list_subtotal
        discount_total += reduction
        increase_total += increase
        items.append({
            'id': row.id, 'model_code': snapshot.get('model_code', ''),
            'model_name': snapshot.get('model_name', ''), 'color_code': snapshot.get('color_code', ''),
            'color_name': snapshot.get('color_name', ''), 'size': snapshot.get('size', ''),
            'barcode': snapshot.get('barcode', ''), 'quantity': row.quantity,
            'list_price': row.list_price, 'unit_price': row.unit_price, 'subtotal': subtotal,
            'reduction': reduction, 'increase': increase, 'price_reason': row.price_reason,
            'returned_quantity': returned.get(row.id, 0),
            'source': {'unit': 'Tekli', 'recipe': 'Seri', 'package': 'Paket', 'custom': 'Özel dağılım'}.get(
                snapshot.get('source', {}).get('kind'), 'Parça'),
        })
    returns_count, returns_total, refund_total = db.session.execute(select(
        func.count(ReturnDocument.id), func.coalesce(func.sum(ReturnDocument.total), 0),
        func.coalesce(func.sum(ReturnDocument.refund_amount), 0),
    ).where(ReturnDocument.company_id == actor.company_id, ReturnDocument.sale_id == sale.id)).one()
    return {
        'id': sale.id, 'reference': f'SAT-{sale.id:07d}', 'company': sale.company_id,
        'created_at': iso_utc(sale.created_at), 'customer_id': sale.customer_id,
        'currency': sale.currency, 'total': sale.total, 'paid': sale.paid,
        'account_at_sale': sale.total - sale.paid, 'items': items,
        'quantity': sum(row.quantity for row in rows), 'list_total': list_total,
        'discount_total': discount_total, 'increase_total': increase_total,
        'returns_count': returns_count, 'returns_total': returns_total,
        'refund_total': refund_total, 'returned_quantity': sum(returned.values()),
    }


@blueprint.get('/sale-document/<int:sale_id>')
def sale_document(sale_id):
    require_schema()
    actor = current_actor()
    sale = owned(Sale, sale_id, actor, 'Satış')
    reject('history' not in actor.permissions and ('sales' not in actor.permissions or sale.actor_id != actor.user_id),
           'Bu satışın işlem belgesini görüntüleme yetkiniz yok.', 'forbidden', 403)
    paper = request.args.get('paper', 'a4')
    reject(paper not in {'a4', '80mm'}, 'Kağıt ölçüsü a4 veya 80mm olmalı.')
    response = render_template('sale_document.html', document=sale_document_data(actor, sale),
                               money=formatted_money, paper=paper)
    return response, 200, {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                           'Referrer-Policy': 'same-origin',
                           'Content-Security-Policy': "default-src 'none'; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}
