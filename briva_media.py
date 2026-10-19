"""Private, bounded product photos in the same database transaction.

Only normalized JPEG bytes are stored. Previous photos are retained; uploads
cannot write files, change stock, run SVG/HTML or contact an external service.
"""
import base64
import binascii
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import warnings

from flask import Blueprint, current_app, jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import defer

from extensions import db
from briva_core.api import command, current_actor
from briva_core.errors import DomainError, reject
from briva_core.models import CatalogModel
from briva_core.service import owned, identifier

MAX_INPUT_BYTES = 350_000
MAX_PIXELS = 24_000_000
MAX_EDGE = 1280
MAX_IMAGES_PER_MODEL = 30
blueprint = Blueprint('briva_media', __name__, url_prefix='/api/v2/media')


class CatalogImage(db.Model):
    __tablename__ = 'briva_catalog_image'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.String(100), nullable=False, index=True)
    model_id = db.Column(db.Integer, db.ForeignKey('briva_model.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, nullable=False)
    image_bytes = db.Column(db.LargeBinary, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.CheckConstraint('width > 0 AND height > 0', name='ck_briva_photo_dimensions'),)


@event.listens_for(CatalogImage, 'before_update')
@event.listens_for(CatalogImage, 'before_delete')
def protect_image_history(mapper, connection, target):
    raise ValueError('Ürün fotoğraf geçmişi değiştirilemez; yeni fotoğraf ekleyin.')


def create_media_schema():
    db.metadata.create_all(bind=db.engine, tables=[CatalogImage.__table__], checkfirst=True)
    current_app.extensions['briva_media_schema_ready'] = True
    return [CatalogImage.__tablename__]


def register_media(app):
    app.register_blueprint(blueprint)


@blueprint.before_request
def ready():
    current_actor()
    if not current_app.extensions.get('briva_media_schema_ready'):
        reject(CatalogImage.__tablename__ not in set(inspect(db.engine).get_table_names()),
               'Ürün fotoğrafları için yerel şema yükseltmesi gerekli.', 'schema_upgrade_required', 503)
        current_app.extensions['briva_media_schema_ready'] = True


@blueprint.errorhandler(DomainError)
def media_error(error):
    db.session.rollback()
    return jsonify(error={'code': error.code, 'message': error.message, 'details': error.details}), error.status


def normalized_image(encoded):
    reject(not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_INPUT_BYTES + 2) // 3),
           'Fotoğrafı en fazla 350 KB olacak şekilde hazırlayın.', 'invalid_image')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise DomainError('Geçerli bir fotoğraf gönderin.', 'invalid_image') from None
    reject(not raw or len(raw) > MAX_INPUT_BYTES, 'Fotoğraf boyutu geçersiz.', 'invalid_image')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                reject(source.format not in {'JPEG', 'PNG', 'WEBP'}, 'Yalnız JPEG, PNG ve WebP fotoğrafları kabul edilir.', 'invalid_image')
                reject(source.width * source.height > MAX_PIXELS or source.width < 1 or source.height < 1,
                       'Fotoğraf en fazla 24 megapiksel olmalı.', 'invalid_image')
                reject(getattr(source, 'n_frames', 1) != 1, 'Hareketli görseller ürün fotoğrafı olarak kullanılamaz.', 'invalid_image')
                source.load()
                image = ImageOps.exif_transpose(source)
                image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
                if image.mode in {'RGBA', 'LA'} or 'transparency' in image.info:
                    rgba = image.convert('RGBA')
                    background = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
                    image = Image.alpha_composite(background, rgba).convert('RGB')
                else:
                    image = image.convert('RGB')
                # A fresh pixel-only image drops EXIF, ICC, comments and other
                # upload metadata even if a decoder retained it in image.info.
                clean = Image.new('RGB', image.size)
                clean.paste(image)
                output = BytesIO()
                clean.save(output, format='JPEG', quality=85, optimize=True)
                data = output.getvalue()
                reject(len(data) > MAX_INPUT_BYTES, 'Hazırlanan fotoğraf çok büyük; daha küçük bir görsel seçin.', 'invalid_image')
                return data, clean.width, clean.height
    except DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DomainError('Fotoğraf okunamadı veya güvenli boyut sınırını aşıyor.', 'invalid_image') from None


def metadata(row):
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {'id': row.id, 'model_id': row.model_id, 'url': f'/api/v2/media/images/{row.id}',
            'width': row.width, 'height': row.height, 'created_at': created.isoformat()}


@blueprint.post('/models/<int:model_id>/images')
@command('catalog_edit')
def add_photo(actor, payload, model_id):
    product = owned(CatalogModel, model_id, actor, 'Model', lock=True)
    reject(set(payload) - {'image_base64'}, 'Yalnız fotoğraf verisini gönderin.')
    count = db.session.query(CatalogImage.id).filter_by(company_id=actor.company_id, model_id=product.id).count()
    reject(count >= MAX_IMAGES_PER_MODEL, 'Bu model için fotoğraf geçmişi sınırına ulaşıldı.', 'photo_limit', 409)
    data, width, height = normalized_image(payload.get('image_base64'))
    photo = CatalogImage(company_id=actor.company_id, model_id=product.id, actor_id=actor.user_id,
                         image_bytes=data, sha256=sha256(data).hexdigest(), width=width, height=height)
    db.session.add(photo)
    db.session.flush()
    from briva_core.models import AuditEvent
    db.session.add(AuditEvent(company_id=actor.company_id, actor_id=actor.user_id,
        action='catalog.photo', document_type='model', document_id=product.id,
        details={'photo_id': photo.id, 'width': width, 'height': height}))
    return metadata(photo)


@blueprint.get('/models/<int:model_id>/images')
def model_photos(model_id):
    actor = current_actor()
    owned(CatalogModel, model_id, actor, 'Model')
    rows = db.session.execute(select(CatalogImage).options(defer(CatalogImage.image_bytes)).where(CatalogImage.company_id == actor.company_id,
        CatalogImage.model_id == model_id).order_by(CatalogImage.id.desc()).limit(MAX_IMAGES_PER_MODEL)).scalars().all()
    return jsonify(data={'items': [metadata(row) for row in rows]})


@blueprint.get('/images/<int:image_id>')
def photo(image_id):
    actor = current_actor()
    identifier(image_id)
    row = db.session.execute(select(CatalogImage).where(CatalogImage.id == image_id,
        CatalogImage.company_id == actor.company_id)).scalar_one_or_none()
    reject(row is None, 'Fotoğraf bulunamadı.', 'not_found', 404)
    response = send_file(BytesIO(row.image_bytes), mimetype='image/jpeg',
                         download_name=f'briva-model-{row.model_id}.jpg', max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
