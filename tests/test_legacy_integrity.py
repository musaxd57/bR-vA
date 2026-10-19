"""Real Flask regressions against retained, isolated local data (no deletion)."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LegacyIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = ROOT / '.verification-data' / ('legacy-integrity-' + uuid.uuid4().hex)
        cls.data_dir.mkdir(parents=True)
        cls.environment = patch.dict(os.environ, {
            'SECRET_KEY': 'synthetic-integrity-session-key-at-least-32-characters',
            'BRIVA_DATA_DIR': str(cls.data_dir), 'PYTHON_DOTENV_DISABLED': '1',
            'GROQ_API_KEY': '', 'PYTHONDONTWRITEBYTECODE': '1',
        })
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        cls.module = importlib.import_module('app')
        cls.app, cls.db = cls.module.app, cls.module.db
        cls.app.config.update(TESTING=True, BRIVA_ALLOW_LEGACY_WRITES=True)
        cls.models = importlib.import_module('models')
        with cls.app.app_context():
            actual = Path(cls.db.engine.url.database).resolve()
            if actual != (cls.data_dir / 'data.db').resolve():
                raise AssertionError('Refusing to use any existing database')
            cls.db.create_all()
            user = cls.models.User(username='integrity_owner', company='INTEGRITY', role='admin',
                can_make_sale=True, can_edit_product=True, can_orders=True,
                can_manage_employees=True, can_manage_attributes=True)
            user.set_password('Synthetic-integrity-pass-43')
            cls.db.session.add(user)
            cls.db.session.commit()
            cls.uid = user.id
        cls.addClassCleanup(cls.close_database)

    @classmethod
    def close_database(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def setUp(self):
        self.client = self.app.test_client()
        token = self.client.get('/api/security/csrf').get_json()['csrf_token']
        response = self.client.post('/login', data={
            'username': 'integrity_owner', 'password': 'Synthetic-integrity-pass-43',
            'csrf_token': token,
        })
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.token = self.client.get('/api/security/csrf').get_json()['csrf_token']

    def product(self, stock=10, minimum=0, shelf=False):
        code = 'T' + uuid.uuid4().hex[:16]
        with self.app.app_context():
            product = self.models.Product(code=code, stock_id='FAMILY', name='Synthetic shirt',
                company_id='INTEGRITY', stock=stock, min_stock=minimum,
                sizes=[{'value': 'M', 'number': 1, 'stock': stock}],
                currency={'TL': 100}, shelves=[{'code': 'R1', 'quantity': stock,
                    'sizes': {'M': stock}}] if shelf else [])
            self.db.session.add(product)
            self.db.session.commit()
        return code

    def post(self, items, **extra):
        return self.client.post('/sale', json={'items': items, **extra},
            headers={'X-CSRF-Token': self.token})

    def row(self, code, **extra):
        return {'code': code, 'quantity': 1, 'price': 100, 'currency': 'TL', **extra}

    def assert_stock(self, code, expected):
        with self.app.app_context():
            product = self.models.Product.query.filter_by(code=code).one()
            self.assertEqual(product.sizes[0]['stock'], expected)
            self.assertEqual(product.stock, expected)

    def test_same_single_batch_twice_counts_all_pieces_and_money(self):
        code = self.product()
        response = self.post([self.row(code, quantity=2, price=200,
            is_single=True, single_sizes={'M': 2})])
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assert_stock(code, 6)
        with self.app.app_context():
            sale = self.models.Sale.query.filter_by(product_code=code).one()
            self.assertEqual(sale.total_price, 400)
            self.assertEqual(sale.sizes, [{'value': 'M', 'quantity': 4}])

    def test_negative_or_fractional_single_amount_rejected_without_mutation(self):
        for invalid in (-2, 1.5, '1.5', True):
            code = self.product()
            response = self.post([self.row(code, is_single=True, single_sizes={'M': invalid})])
            self.assertEqual(response.status_code, 400, response.get_json())
            self.assert_stock(code, 10)

    def test_unknown_size_rejected_without_sale(self):
        code = self.product()
        response = self.post([self.row(code, is_single=True, single_sizes={'Z': 1})])
        self.assertEqual(response.status_code, 400)
        self.assert_stock(code, 10)
        with self.app.app_context():
            self.assertEqual(self.models.Sale.query.filter_by(product_code=code).count(), 0)

    def test_notification_does_not_commit_part_of_failed_sale(self):
        code = self.product(stock=2, minimum=1)
        response = self.post([self.row(code), self.row('MISSING')])
        self.assertEqual(response.status_code, 400)
        self.assert_stock(code, 2)
        with self.app.app_context():
            self.assertEqual(self.models.Sale.query.filter_by(product_code=code).count(), 0)
            notifications = self.models.Notification.query.filter(
                self.models.Notification.message.contains(code)).count()
            self.assertEqual(notifications, 0)

    def test_invalid_debt_customer_rolls_back_notification_and_stock(self):
        code = self.product(stock=2, minimum=1)
        response = self.post([self.row(code)], is_debt=True, customerName='Missing customer')
        self.assertEqual(response.status_code, 400)
        self.assert_stock(code, 2)

    def test_shelf_and_piece_stock_decrease_together(self):
        code = self.product(shelf=True)
        response = self.post([self.row(code, is_single=True, single_sizes={'M': 3})])
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assert_stock(code, 7)
        with self.app.app_context():
            shelves = self.models.Product.query.filter_by(code=code).one().shelves
            self.assertEqual(shelves[0]['sizes']['M'], 7)
            self.assertEqual(shelves[0]['quantity'], 7)

    def test_insufficient_series_returns_business_error(self):
        code = self.product(stock=1)
        response = self.post([self.row(code, quantity=2)])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Yetersiz', response.get_json()['message'])
        self.assert_stock(code, 1)

    def test_invalid_price_quantity_and_discount_are_rejected(self):
        for values in ({'price': -100}, {'price': 0}, {'quantity': 0},
                       {'quantity': -1}, {'price': 'NaN'}, {'price': 'Infinity'},
                       {'discount': 101}, {'discount': -1}):
            code = self.product()
            response = self.post([self.row(code, **values)])
            self.assertEqual(response.status_code, 400, (values, response.get_json()))
            self.assert_stock(code, 10)

    def test_negative_paid_amount_rejected(self):
        code = self.product()
        response = self.post([self.row(code)], amount_paid=-1)
        self.assertEqual(response.status_code, 400)
        self.assert_stock(code, 10)

    def test_mixed_currency_cart_rejected(self):
        code = self.product()
        response = self.post([self.row(code), self.row(code, currency='USD')])
        self.assertEqual(response.status_code, 400)
        self.assert_stock(code, 10)

    def test_ten_ordinary_edits_preserve_barcode_family_and_sale_snapshot(self):
        from db_utils import update_product
        code = self.product()
        response = self.post([self.row(code)])
        self.assertEqual(response.status_code, 201)
        with self.app.app_context():
            product = self.models.Product.query.filter_by(code=code).one()
            for index in range(10):
                self.assertTrue(update_product(product.id, 'INTEGRITY',
                    name=f'Edit {index}', currency={'TL': 101 + index}))
                self.db.session.refresh(product)
                self.assertEqual(product.code, code)
                self.assertEqual(product.stock_id, 'FAMILY')
            self.assertEqual(self.models.Sale.query.filter_by(product_id=product.id).one().product_code, code)

    def test_csrf_required_and_logout_get_does_not_end_session(self):
        code = self.product()
        denied = self.client.post('/sale', json={'items': [self.row(code)]})
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.get_json()['code'], 'csrf_failed')
        self.client.get('/logout')
        with self.client.session_transaction() as session:
            self.assertEqual(session['user'], self.uid)
        self.assert_stock(code, 10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
