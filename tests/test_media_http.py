"""Private image upload through the real app; synthetic retained data only."""
import base64
from io import BytesIO
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MediaHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / '.verification-data' / ('media-http-' + uuid.uuid4().hex)
        cls.directory.mkdir(parents=True)
        environment = patch.dict(os.environ, {'SECRET_KEY': 'synthetic-media-http-secret-at-least-32-characters',
            'BRIVA_DATA_DIR': str(cls.directory), 'PYTHON_DOTENV_DISABLED': '1', 'GROQ_API_KEY': '', 'OPENAI_API_KEY': ''})
        environment.start()
        cls.addClassCleanup(environment.stop)
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('No external image service is allowed'))
            guard.start()
            cls.addClassCleanup(guard.stop)
        module = importlib.import_module('app')
        cls.app, cls.db = module.app, module.db
        cls.app.config.update(TESTING=True)
        from models import User
        from briva_security import PERMISSIONS
        with cls.app.app_context():
            if Path(cls.db.engine.url.database).resolve() != (cls.directory / 'data.db').resolve():
                raise AssertionError('Refusing to use a non-synthetic database')
            cls.db.create_all()
            for name, company, role in (('media_owner', 'MEDIA_A', 'admin'), ('media_other', 'MEDIA_B', 'admin'),
                                        ('media_cashier', 'MEDIA_A', 'cashier'), ('media_creator', 'MEDIA_A', 'employee')):
                user = User(username=name, company=company, role=role,
                            **{permission: role == 'admin' for permission in PERMISSIONS})
                user.can_add_product = name == 'media_creator' or role == 'admin'
                user.set_password('Synthetic-media-password-2026')
                cls.db.session.add(user)
            cls.db.session.commit()
        cls.app.extensions['briva_security']['throttle'].attempts = 100
        cls.app.extensions['briva_security']['throttle'].ip_attempts = 1000
        cls.addClassCleanup(cls.close_database)

    @classmethod
    def close_database(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def setUp(self):
        self.client, self.csrf = self.login('media_owner')
        response = self.client.post('/api/v2/catalog/models', json={
            'model_code': 'PHOTO-' + uuid.uuid4().hex[:12].upper(), 'name': 'Sentetik fotoğraflı ürün',
            'colors': [{'code': 'SY', 'name': 'Siyah'}], 'sizes': ['M'], 'unit_prices': {'TRY': '100.00'},
        }, headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 201, response.json)
        self.product = response.json['data']
        self.path = f"/api/v2/media/models/{self.product['id']}/images"

    def login(self, name):
        client = self.app.test_client()
        token = client.get('/api/security/csrf').json['csrf_token']
        response = client.post('/login', data={'username': name, 'password': 'Synthetic-media-password-2026', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        return client, client.get('/api/security/csrf').json['csrf_token']

    @staticmethod
    def image_bytes(size=(40, 20), mode='RGB', format='PNG', exif=None):
        output = BytesIO()
        image = Image.new(mode, size, (200, 20, 20, 100) if mode == 'RGBA' else (200, 20, 20))
        options = {'exif': exif} if exif is not None else {}
        image.save(output, format=format, **options)
        return output.getvalue()

    def upload(self, raw=None, status=201, key=None, client=None, token=None, payload=None):
        if payload is None:
            raw = self.image_bytes() if raw is None else raw
            payload = {'image_base64': base64.b64encode(raw).decode('ascii')}
        response = (client or self.client).post(self.path, json=payload, headers={
            'X-CSRF-Token': token or self.csrf, 'Idempotency-Key': key or uuid.uuid4().hex})
        self.assertEqual(response.status_code, status, response.json)
        return response

    def test_photo_is_normalized_private_jpeg_without_changing_stock(self):
        result = self.upload().json['data']
        response = self.client.get(result['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(Image.open(BytesIO(response.data)).size, (40, 20))
        self.assertEqual(self.client.get('/api/v2/stock').json['data']['totals']['quantity'], 0)

    def test_exif_metadata_is_removed_and_orientation_is_applied(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = 'PRIVATE-IMAGE-METADATA-MARKER'
        result = self.upload(self.image_bytes(format='JPEG', exif=exif)).json['data']
        response = self.client.get(result['url'])
        normalized = Image.open(BytesIO(response.data))
        self.assertEqual(normalized.size, (20, 40))
        self.assertEqual(dict(normalized.getexif()), {})
        self.assertNotIn(b'PRIVATE-IMAGE-METADATA-MARKER', response.data)

    def test_rgba_background_and_maximum_dimensions_are_normalized(self):
        result = self.upload(self.image_bytes((2400, 1200), 'RGBA')).json['data']
        self.assertEqual((result['width'], result['height']), (1280, 640))
        response = self.client.get(result['url'])
        self.assertEqual(Image.open(BytesIO(response.data)).mode, 'RGB')

    def test_svg_html_random_bytes_and_empty_images_are_rejected(self):
        for raw in (b'', b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', b'<script>alert(1)</script>', b'FAKE-JPEG'):
            self.upload(raw, status=400)
        self.assertEqual(self.client.get(self.path).json['data']['items'], [])

    def test_invalid_base64_types_and_unknown_fields_are_rejected(self):
        for value in (None, True, 123, [], {}, 'not-base64!', 'Çınar', 'data:image/png;base64,AAAA'):
            self.upload(status=400, payload={'image_base64': value})
        self.upload(status=400, payload={'image_base64': '', 'filename': '../../outside.jpg'})

    def test_large_pixel_image_and_oversized_payload_are_rejected(self):
        self.upload(self.image_bytes((6000, 5000)), status=400)
        self.upload(status=400, payload={'image_base64': 'A' * 470_000})
        self.upload(status=413, payload={'image_base64': 'A' * 520_000})

    def test_other_company_and_anonymous_clients_cannot_read_photos(self):
        result = self.upload().json['data']
        other, token = self.login('media_other')
        self.assertEqual(other.get(result['url']).status_code, 404)
        self.assertEqual(other.get(self.path).status_code, 404)
        self.upload(status=404, client=other, token=token)
        self.assertEqual(self.app.test_client().get(result['url']).status_code, 401)

    def test_creator_and_cashier_cannot_change_product_photos(self):
        result = self.upload().json['data']
        for name in ('media_cashier', 'media_creator'):
            client, token = self.login(name)
            self.upload(status=403, client=client, token=token)
            self.assertEqual(client.get(result['url']).status_code, 200)

    def test_replay_retains_one_photo_and_replacements_preserve_history(self):
        key = uuid.uuid4().hex
        first = self.upload(key=key).json['data']
        replay = self.upload(status=200, key=key).json
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['data']['id'], first['id'])
        second = self.upload(self.image_bytes((30, 30))).json['data']
        self.assertNotEqual(first['id'], second['id'])
        images = self.client.get(self.path).json['data']['items']
        self.assertEqual([row['id'] for row in images], [second['id'], first['id']])
        self.assertEqual(self.client.get(first['url']).status_code, 200)

    def test_csrf_and_oversized_route_identifier_are_rejected(self):
        response = self.client.post(self.path, json={'image_base64': ''}, headers={'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/v2/media/images/999999999999999999999999').status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
