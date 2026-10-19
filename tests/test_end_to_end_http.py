"""Business acceptance through the real app, real login, CSRF and v2 API.

Each invocation retains a new SQLite fixture. No file cleanup, real data,
external service, physical printer or scanner is involved.
"""
from collections import defaultdict
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EndToEndHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / '.verification-data' / ('http-workflow-' + uuid.uuid4().hex)
        cls.directory.mkdir(parents=True, exist_ok=False)
        environment = patch.dict(os.environ, {
            'SECRET_KEY': 'synthetic-http-workflow-session-secret-at-least-32-characters',
            'BRIVA_DATA_DIR': str(cls.directory), 'PYTHON_DOTENV_DISABLED': '1',
            'GROQ_API_KEY': '', 'OPENAI_API_KEY': '', 'BRIVA_ALLOW_LEGACY_WRITES': '0',
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Network is forbidden in this fixture'))
            guard.start()
            cls.addClassCleanup(guard.stop)
        cls.module = importlib.import_module('app')
        cls.app, cls.db = cls.module.app, cls.module.db
        cls.app.config.update(TESTING=True)
        from briva_security import PERMISSIONS
        from models import User
        with cls.app.app_context():
            if Path(cls.db.engine.url.database).resolve() != (cls.directory / 'data.db').resolve():
                raise AssertionError('Refusing to use a non-synthetic database')
            cls.db.create_all()
            owner = User(username='http_test_owner', company='HTTP_SYNTHETIC', role='admin',
                         **{permission: True for permission in PERMISSIONS})
            owner.set_password('Synthetic-http-password-2026')
            cls.db.session.add(owner)
            cls.db.session.commit()
        cls.addClassCleanup(cls.close_database)

    @classmethod
    def close_database(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def setUp(self):
        self.client = self.app.test_client()
        token = self.client.get('/api/security/csrf').json['csrf_token']
        response = self.client.post('/login', data={
            'username': 'http_test_owner', 'password': 'Synthetic-http-password-2026',
            'csrf_token': token,
        })
        self.assertEqual(response.status_code, 302)
        self.csrf = self.client.get('/api/security/csrf').json['csrf_token']

    def command(self, path, body, status=201, key=None, method='post'):
        response = getattr(self.client, method)('/api/v2' + path, json=body, headers={
            'X-CSRF-Token': self.csrf, 'Idempotency-Key': key or uuid.uuid4().hex,
        })
        self.assertEqual(response.status_code, status, response.json)
        return response.json

    def get(self, path):
        response = self.client.get('/api/v2' + path)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['data']

    def catalogue(self):
        product = self.command('/catalog/models', {
            'model_code': 'HTTP-' + uuid.uuid4().hex[:12].upper(),
            'name': 'Sentetik atölye gömleği',
            'colors': [{'code': 'SY', 'name': 'Siyah'}, {'code': 'BY', 'name': 'Beyaz'}, {'code': 'MV', 'name': 'Mavi'}],
            'sizes': ['S', 'M', 'L', 'XL'],
            'unit_prices': {'TRY': '100.00', 'USD': '3.00', 'EUR': '2.75'},
        })['data']
        self.assertEqual(len(product['variants']), 12)
        self.assertEqual(len({row['barcode'] for row in product['variants']}), 12)
        variants = {row['size']: row for row in product['variants'] if row['color_code'] == 'SY'}
        recipe = self.command('/catalog/recipes', {
            'model_id': product['id'], 'color_code': 'SY', 'name': 'Standart seri',
            'components': [{'variant_id': variants[size]['id'], 'quantity': count}
                           for size, count in (('S', 1), ('M', 2), ('L', 2), ('XL', 1))],
        })['data']
        return product, variants, recipe

    def location(self, kind='store', parent_id=None):
        return self.command('/locations', {
            'code': 'HTTP-' + uuid.uuid4().hex[:12], 'name': 'Sentetik ' + kind,
            'kind': kind, 'parent_id': parent_id,
        })['data']

    def test_workshop_shelf_picking_sale_credit_return_and_refund(self):
        product, variants, recipe = self.catalogue()
        store = self.location()
        shelf = self.location('shelf', store['id'])
        expected = {'S': 10, 'M': 20, 'L': 20, 'XL': 10}
        actual = {'S': 10, 'M': 21, 'L': 20, 'XL': 9}
        plan = self.command('/production/plans', {
            'reference': uuid.uuid4().hex, 'workshop': 'Sentetik atölye',
            'items': [{'variant_id': variants[size]['id'], 'quantity': amount} for size, amount in expected.items()],
        })['data']
        self.assertFalse(any(row['quantity'] for row in self.get('/stock')['balances']
                             if row['variant_id'] in {item['id'] for item in variants.values()}))
        receipt_key = uuid.uuid4().hex
        body = {'location_id': store['id'], 'plan_id': plan['id'], 'reference': uuid.uuid4().hex,
                'items': [{'variant_id': variants[size]['id'], 'quantity': amount} for size, amount in actual.items()]}
        receipt = self.command('/receipts', body, key=receipt_key)['data']
        replay = self.command('/receipts', body, status=200, key=receipt_key)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['data']['id'], receipt['id'])
        capacity = next(row for row in self.get('/stock')['capacities']
                        if row['recipe_id'] == recipe['id'] and row['location_id'] == store['id'])
        self.assertEqual(capacity['series_capacity'], 9)
        self.assertEqual({row['variant_id']: row['quantity'] for row in capacity['leftovers']},
                         {variants[size]['id']: amount for size, amount in {'S': 1, 'M': 3, 'L': 2, 'XL': 0}.items()})
        self.command('/transfers', {'from_location_id': store['id'], 'to_location_id': shelf['id'],
            'reference': uuid.uuid4().hex,
            'items': [{'variant_id': row['variant_id'], 'quantity': row['quantity'] * 2} for row in recipe['components']]})
        customer = self.command('/customers', {'name': 'Sentetik müşteri ' + uuid.uuid4().hex[:8]})['data']
        cart = [{'barcode': recipe['barcode'], 'quantity': 1}, {'barcode': variants['M']['barcode'], 'quantity': 1}]
        task = self.command('/picking/tasks', {'location_id': shelf['id'], 'items': cart})['data']
        sale_body = {'location_id': shelf['id'], 'customer_id': customer['id'], 'currency': 'TRY',
                     'items': cart, 'picking_task_id': task['id'], 'expected_total': '700.00',
                     'payment': {'amount': '250.00', 'method': 'cash'}}
        self.command('/sales', sale_body, status=409)
        self.command(f"/picking/{task['id']}/scan", {'barcode': variants['M']['barcode']}, status=409)
        self.command(f"/picking/{task['id']}/scan", {'barcode': shelf['barcode']})
        for size, amount in (('S', 1), ('M', 3), ('L', 2)):
            task = self.command(f"/picking/{task['id']}/scan", {'barcode': variants[size]['barcode'], 'quantity': amount})['data']
        self.assertEqual(task['status'], 'picking')
        task = self.command(f"/picking/{task['id']}/scan", {'barcode': variants['XL']['barcode']})['data']
        self.assertEqual(task['status'], 'ready')
        sale_key = uuid.uuid4().hex
        sale = self.command('/sales', sale_body, key=sale_key)['data']
        self.command('/sales', sale_body, status=200, key=sale_key)
        self.assertEqual(sale['total'], '700.00')
        self.assertEqual(sale['customer_balances']['TRY'], '450.00')
        sold = defaultdict(int)
        for row in sale['items']:
            sold[row['variant_id']] += row['quantity']
        self.assertEqual(dict(sold), {variants[size]['id']: amount for size, amount in {'S': 1, 'M': 3, 'L': 2, 'XL': 1}.items()})
        balances = self.get(f"/stock?location_id={shelf['id']}")['balances']
        self.assertEqual({row['variant_id']: row['quantity'] for row in balances},
                         {variants[size]['id']: amount for size, amount in {'S': 1, 'M': 1, 'L': 2, 'XL': 1}.items()})
        self.assertTrue(all(row['reserved'] == 0 for row in balances))
        returned = self.command(f"/sales/{sale['id']}/returns", {
            'location_id': shelf['id'], 'reason': 'Sentetik tam iade', 'refund_amount': '0.00',
            'items': [{'sale_item_id': row['id'], 'quantity': row['quantity']} for row in sale['items']],
        })['data']
        self.assertEqual(returned['customer_balances']['TRY'], '-250.00')
        self.command('/payments', {'customer_id': customer['id'], 'currency': 'TRY', 'amount': '250.01',
                                  'direction': 'out', 'method': 'cash', 'reference': 'Too much'}, status=409)
        self.command('/payments', {'customer_id': customer['id'], 'currency': 'TRY', 'amount': '250.00',
                                  'direction': 'out', 'method': 'cash', 'reference': 'Sentetik geri ödeme'})
        self.assertEqual(self.get(f"/customers/{customer['id']}/account")['customer']['balances']['TRY'], '0.00')
        self.assertEqual(self.get('/health')['issue_count'], 0)
        saved_sale = self.get(f"/sales/{sale['id']}")
        self.assertEqual(saved_sale['total'], '700.00')
        self.assertEqual(sum(row['returned'] for row in saved_sale['items']), 7)

    def test_model_edits_preserve_printed_barcodes(self):
        product, variants, recipe = self.catalogue()
        original = {row['id']: row['barcode'] for row in product['variants']}
        for count in range(10):
            edited = self.command(f"/catalog/models/{product['id']}", {'name': f'Adı düzenlenen ürün {count}'}, method='patch')['data']
            self.assertEqual({row['id']: row['barcode'] for row in edited['variants']}, original)
        for barcode in [*original.values(), recipe['barcode']]:
            self.assertEqual(self.get('/barcodes/' + barcode)['barcode'], barcode)
        image = self.client.get('/api/v2/barcodes/' + variants['M']['barcode'] + '.svg')
        self.assertEqual(image.status_code, 200)
        self.assertIn(b'<svg', image.data)
        self.assertIn("default-src 'none'", image.headers['Content-Security-Policy'])

    def test_archive_is_escaped_and_cannot_write_a_parallel_stock(self):
        from models import Product
        with self.app.app_context():
            self.db.session.add(Product(code='HTTP-XSS-' + uuid.uuid4().hex[:10], name='<img src=x onerror=alert(1)>',
                company_id='HTTP_SYNTHETIC', stock=0, sizes=[]))
            self.db.session.commit()
        archive = self.client.get('/storage')
        self.assertEqual(archive.status_code, 200)
        self.assertIn('&lt;img', archive.get_data(as_text=True))
        self.assertNotIn('<img src=x', archive.get_data(as_text=True))
        self.assertNotIn('js/storage.js', archive.get_data(as_text=True))
        response = self.client.post('/sale', json={'items': []}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json['error']['code'], 'legacy_read_only')


if __name__ == '__main__':
    unittest.main(verbosity=2)
