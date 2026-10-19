"""Read-only printable sale document checks with a retained real-app fixture."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.pure-python'))


class SaleDocumentHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / '.verification-data' / ('documents-http-' + uuid.uuid4().hex)
        cls.directory.mkdir(parents=True)
        environment = patch.dict(os.environ, {
            'SECRET_KEY': 'synthetic-documents-http-secret-at-least-32-characters',
            'BRIVA_DATA_DIR': str(cls.directory), 'PYTHON_DOTENV_DISABLED': '1',
            'GROQ_API_KEY': '', 'OPENAI_API_KEY': '', 'BRIVA_ALLOW_LEGACY_WRITES': '0',
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Document tests cannot contact a network service'))
            guard.start()
            cls.addClassCleanup(guard.stop)
        original_stdout = sys.stdout
        module = importlib.import_module('app')
        sys.stdout = original_stdout
        cls.app, cls.db = module.app, module.db
        if 'briva_documents' not in cls.app.blueprints:
            from briva_documents import register_documents
            register_documents(cls.app)
        cls.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=True)
        cls.app.extensions['briva_security']['throttle'].attempts = 100
        cls.app.extensions['briva_security']['throttle'].ip_attempts = 1000
        from models import User
        from briva_security import PERMISSIONS
        with cls.app.app_context():
            if Path(cls.db.engine.url.database).resolve() != (cls.directory / 'data.db').resolve():
                raise AssertionError('Refusing to write a non-synthetic database')
            cls.db.create_all()
            identities = (
                ('documents_owner', 'DOC_A', 'admin', ()),
                ('documents_foreign', 'DOC_B', 'admin', ()),
                ('documents_cashier', 'DOC_A', 'cashier', ('can_make_sale',)),
                ('documents_cashier_other', 'DOC_A', 'cashier', ('can_make_sale',)),
                ('documents_history', 'DOC_A', 'employee', ('can_view_sales_history',)),
                ('documents_viewer', 'DOC_A', 'employee', ()),
            )
            for name, company, role, permissions in identities:
                user = User(username=name, company=company, role=role,
                            **{permission: role == 'admin' or permission in permissions for permission in PERMISSIONS})
                user.set_password('Synthetic-documents-password-2026')
                cls.db.session.add(user)
            cls.db.session.commit()
            cls.user_ids = {user.username: user.id for user in User.query.all()}
        cls.addClassCleanup(cls.close_database)
        print('Retained document fixture: ' + str(cls.directory))

    @classmethod
    def close_database(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def login(self, username):
        client = self.app.test_client()
        token = client.get('/api/security/csrf').json['csrf_token']
        response = client.post('/login', data={'username': username,
            'password': 'Synthetic-documents-password-2026', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        return client, client.get('/api/security/csrf').json['csrf_token']

    def post(self, path, body, client=None, csrf=None, method='POST'):
        response = (client or self.client).open('/api/v2' + path, method=method, json=body,
            headers={'X-CSRF-Token': csrf or self.csrf, 'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['data']

    def setUp(self):
        self.client, self.csrf = self.login('documents_owner')
        suffix = uuid.uuid4().hex[:10].upper()
        self.model = self.post('/catalog/models', {
            'model_code': 'DOC-' + suffix, 'name': 'ORIGINAL_DOCUMENT_MODEL_' + suffix,
            'colors': [{'code': 'SY', 'name': 'Siyah'}], 'sizes': ['M'], 'unit_prices': {'TRY': '100.00'},
        })
        self.variant = self.model['variants'][0]
        self.location = self.post('/locations', {'code': 'D-' + suffix, 'name': 'Belge mağazası', 'kind': 'store'})
        self.customer = self.post('/customers', {'name': 'Document customer ' + suffix, 'phone': ''})
        self.post('/receipts', {'location_id': self.location['id'], 'reference': 'DOC-RECEIPT-' + suffix,
                               'items': [{'variant_id': self.variant['id'], 'quantity': 20}]})

    def sale(self, *, client=None, csrf=None, amount='50.00', override=None):
        item = {'barcode': self.variant['barcode'], 'quantity': 2}
        if override:
            item.update(unit_price=override, price_reason='Kampanya indirimi')
        return self.post('/sales', {'location_id': self.location['id'], 'customer_id': self.customer['id'],
            'currency': 'TRY', 'items': [item], 'payment': {'amount': amount, 'method': 'cash'}}, client, csrf)

    def document(self, sale, *, client=None, query=''):
        return (client or self.client).get(f"/sale-document/{sale['id']}" + query)

    def test_original_snapshot_prices_and_discount_survive_catalog_change(self):
        sale = self.sale(override='80.00')
        self.post(f"/catalog/models/{self.model['id']}", {'name': 'NEW_CURRENT_MODEL_NAME'}, method='PATCH')
        self.post(f"/catalog/variants/{self.variant['id']}/prices", {'prices': {'TRY': '999.00'},
            'reason': 'Güncel liste değişikliği'}, method='PATCH')
        response = self.document(sale)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(self.model['name'], html)
        self.assertNotIn('NEW_CURRENT_MODEL_NAME', html)
        self.assertNotIn('999,00', html)
        self.assertIn('80,00', html)
        self.assertIn('160,00 TRY', html)
        self.assertIn('40,00 TRY', html)
        self.assertIn('50,00 TRY', html)
        self.assertIn('110,00 TRY', html)
        self.assertIn('Kampanya indirimi', html)

    def test_history_reader_and_only_original_cashier_may_read_same_company_sale(self):
        cashier, csrf = self.login('documents_cashier')
        sale = self.sale(client=cashier, csrf=csrf)
        self.assertEqual(self.document(sale, client=cashier).status_code, 200)
        history, _ = self.login('documents_history')
        self.assertEqual(self.document(sale, client=history).status_code, 200)
        other, _ = self.login('documents_cashier_other')
        self.assertEqual(self.document(sale, client=other).status_code, 403)
        viewer, _ = self.login('documents_viewer')
        self.assertEqual(self.document(sale, client=viewer).status_code, 403)

    def test_foreign_owner_and_missing_ids_return_same_not_found(self):
        sale = self.sale()
        foreign, _ = self.login('documents_foreign')
        self.assertEqual(self.document(sale, client=foreign).status_code, 404)
        self.assertEqual(foreign.get('/sale-document/2147483647').status_code, 404)
        self.assertIn(self.app.test_client().get(f"/sale-document/{sale['id']}").status_code, (302, 401))

    def test_persisted_snapshot_strings_are_autoescaped(self):
        dangerous = '<script id="unsafe-model">alert(1)</script>'
        self.post(f"/catalog/models/{self.model['id']}", {'name': dangerous}, method='PATCH')
        sale = self.sale()
        response = self.document(sale)
        html = response.get_data(as_text=True)
        self.assertIn('&lt;script', html)
        self.assertNotIn(dangerous, html)
        self.assertNotRegex(html, r'\son\w+=')
        self.assertNotIn('https://', html)
        self.assertNotIn('http://', html)

    def test_later_customer_payment_never_changes_original_paid_or_account_amounts(self):
        sale = self.sale()
        before = self.document(sale).get_data(as_text=True)
        self.post('/payments', {'customer_id': self.customer['id'], 'currency': 'TRY',
                               'amount': '150.00', 'direction': 'in', 'method': 'transfer'})
        after = self.document(sale).get_data(as_text=True)
        self.assertEqual(before, after)
        self.assertIn('50,00 TRY', after)
        self.assertIn('150,00 TRY', after)
        self.assertIn('Sonraki müşteri tahsilatları bu satışa paylaştırılmaz.', after)

    def test_partial_return_is_separate_and_original_totals_are_preserved(self):
        sale = self.sale()
        self.post(f"/sales/{sale['id']}/returns", {'location_id': self.location['id'], 'reason': 'Beden iadesi',
            'items': [{'sale_item_id': sale['items'][0]['id'], 'quantity': 1}], 'refund_amount': '0.00'})
        html = self.document(sale).get_data(as_text=True)
        self.assertIn('Sonradan kaydedilen iadeler', html)
        self.assertIn('1 iade işlemi · 1 parça', html)
        self.assertIn('200,00 TRY', html)
        self.assertIn('100,00 TRY', html)
        self.assertIn('50,00 TRY', html)
        self.assertIn('150,00 TRY', html)
        self.assertIn('İadeler özgün satış toplamını değiştirmez.', html)

    def test_guest_foreign_currency_full_refund_is_separate_from_original_cash(self):
        self.post(f"/catalog/variants/{self.variant['id']}/prices", {'prices': {'EUR': '12.34'},
                  'reason': 'Euro liste fiyatı'}, method='PATCH')
        sale = self.post('/sales', {'location_id': self.location['id'], 'currency': 'EUR',
            'items': [{'barcode': self.variant['barcode'], 'quantity': 2}],
            'payment': {'amount': '24.68', 'method': 'card'}})
        self.post(f"/sales/{sale['id']}/returns", {'location_id': self.location['id'],
            'reason': 'Misafir tam iade', 'items': [{'sale_item_id': sale['items'][0]['id'], 'quantity': 2}],
            'refund_amount': '24.68', 'refund_method': 'card'})
        html = self.document(sale).get_data(as_text=True)
        self.assertIn('Misafir müşteri', html)
        self.assertIn('24,68 EUR', html)
        self.assertIn('0,00 EUR', html)
        self.assertIn('1 iade işlemi · 2 parça', html)
        self.assertIn('Bu iade işlemlerinde geri ödenen', html)
        self.assertNotIn('TRY', html)

    def test_document_get_and_head_do_not_write_any_business_table(self):
        sale = self.sale()
        from briva_core.models import CORE_TABLE_NAMES

        def state():
            with self.app.app_context():
                # Compare complete values as well as row counts: a GET which
                # silently updates an existing balance must also be detected.
                return {name: [repr(tuple(row)) for row in self.db.session.execute(
                    select(self.db.metadata.tables[name]).order_by(*self.db.metadata.tables[name].primary_key.columns)).all()]
                    for name in sorted(CORE_TABLE_NAMES)}

        before = state()
        for query in ('', '?paper=a4', '?paper=80mm'):
            response = self.document(sale, query=query)
            self.assertEqual(response.status_code, 200)
            self.assertIn('private', response.headers['Cache-Control'])
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn('unsafe-inline', response.headers['Content-Security-Policy'])
        self.assertEqual(self.client.head(f"/sale-document/{sale['id']}").status_code, 200)
        self.assertEqual(state(), before)

    def test_default_a4_optional_roll_and_invalid_paper_are_explicit(self):
        sale = self.sale()
        html = self.document(sale).get_data(as_text=True)
        self.assertIn('class="paper-a4"', html)
        self.assertIn('İşlem belgesi', html)
        self.assertIn('Bu belge e-fatura veya mali fiş değildir.', html)
        self.assertIn('js/sale_document.js', html)
        self.assertNotIn('onclick=', html)
        roll = self.document(sale, query='?paper=80mm').get_data(as_text=True)
        self.assertIn('class="paper-80mm"', roll)
        self.assertIn('sale_document_80mm.css', roll)
        self.assertEqual(self.document(sale, query='?paper=unknown').status_code, 400)
        self.assertEqual(self.client.get('/sale-document/999999999999999999999999').status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
