"""Current UI and legacy-write boundaries in a retained, isolated real app."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CurrentRoutePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / '.verification-data' / ('route-policy-' + uuid.uuid4().hex)
        cls.directory.mkdir(parents=True)
        environment = patch.dict(os.environ, {
            'SECRET_KEY': 'synthetic-route-policy-secret-at-least-32-characters',
            'BRIVA_DATA_DIR': str(cls.directory), 'PYTHON_DOTENV_DISABLED': '1',
            'GROQ_API_KEY': '', 'OPENAI_API_KEY': '',
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Route tests are local only'))
            guard.start()
            cls.addClassCleanup(guard.stop)
        cls.module = importlib.import_module('app')
        cls.app, cls.db = cls.module.app, cls.module.db
        cls.app.config.update(TESTING=True, BRIVA_ALLOW_LEGACY_WRITES=False)
        from models import User, Product, Customer
        from briva_security import PERMISSIONS
        with cls.app.app_context():
            if Path(cls.db.engine.url.database).resolve() != (cls.directory / 'data.db').resolve():
                raise AssertionError('Refusing non-synthetic database')
            cls.db.create_all()
            user = User(username='route_owner', company='ROUTE_ONLY', role='admin',
                        **{permission: True for permission in PERMISSIONS})
            user.set_password('Synthetic-route-password-2026')
            cls.db.session.add(user)
            product = Product(code='ARCHIVE-ONLY', stock_id='ARCHIVE', name='Archive test',
                company_id='ROUTE_ONLY', stock=1, min_stock=0, sizes=[], currency={}, shelves=[])
            cls.db.session.add(product)
            customer = Customer(name='Archive customer', company_id='ROUTE_ONLY', debt={})
            cls.db.session.add(customer)
            cls.db.session.commit()
            cls.product_id = product.id
            cls.customer_id = customer.id
        cls.app.extensions['briva_security']['throttle'].attempts = 100
        cls.app.extensions['briva_security']['throttle'].ip_attempts = 1000
        cls.addClassCleanup(cls.close_db)

    @classmethod
    def close_db(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def setUp(self):
        self.client = self.app.test_client()
        token = self.client.get('/api/security/csrf').json['csrf_token']
        response = self.client.post('/login', data={'username': 'route_owner',
            'password': 'Synthetic-route-password-2026', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        self.token = self.client.get('/api/security/csrf').json['csrf_token']

    def test_environment_compatibility_flag_cannot_enable_normal_server_writes(self):
        with patch.dict(self.app.config, {'TESTING': False, 'BRIVA_ALLOW_LEGACY_WRITES': True}):
            for path in ('/sale', '/collect_payment', '/product', '/create_order', '/add_stock_route'):
                payload = {'customer_id': self.customer_id} if path == '/collect_payment' else {}
                response = self.client.post(path, json=payload, headers={'X-CSRF-Token': self.token})
                self.assertEqual(response.status_code, 410, (path, response.json))
                self.assertEqual(response.json['error']['code'], 'legacy_read_only')

    def test_retired_form_pages_go_to_current_workflow(self):
        pages = {'/customer_debts': 'customers', '/add_customer_route': 'customers',
            '/definitions': 'catalog', '/addcolor_route': 'catalog',
            '/addcategory_route': 'catalog', '/addshelf_route': 'inventory'}
        for path, section in pages.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(response.location.endswith('/workbench#' + section), response.location)

    def test_archive_product_has_no_active_edit_or_delete_triggers(self):
        response = self.client.get(f'/product_detail/{self.product_id}')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Salt okunur eski ürün arşivi', html)
        self.assertNotIn('onclick="toggleEditMode(', html)
        self.assertNotIn('onclick="deleteProduct(', html)
        self.assertNotIn('function saveInlineChanges(', html)

    def test_photo_page_is_private_and_has_current_local_assets(self):
        anonymous = self.app.test_client().get('/product-photos')
        self.assertEqual(anonymous.status_code, 302)
        response = self.client.get('/product-photos')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('/static/js/product_photos.js', html)
        self.assertIn('csrf-token', html)
        self.assertNotIn('cdn.', html)


if __name__ == '__main__':
    unittest.main()
