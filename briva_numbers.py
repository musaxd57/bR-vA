"""Shared strict numeric validation; no database writes or implicit coercion."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


MAX_QUANTITY = 1_000_000
MAX_MONEY = Decimal('1000000000000')
CENT = Decimal('0.01')


def quantity(value, label='Adet', *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f'{label} tam sayı olmalı.')
    raw = str(value).strip()
    if not re.fullmatch(r'[0-9]{1,10}', raw):
        raise ValueError(f'{label} pozitif tam sayı olmalı.')
    result = int(raw)
    minimum = 0 if allow_zero else 1
    if not minimum <= result <= MAX_QUANTITY:
        raise ValueError(f'{label} {minimum}–{MAX_QUANTITY} arasında olmalı.')
    return result


def money(value, label='Tutar', *, allow_zero=True):
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{label} geçerli bir sayı olmalı.')
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label} geçerli bir sayı olmalı.') from None
    if not result.is_finite() or result < 0 or result > MAX_MONEY:
        raise ValueError(f'{label} sonlu ve negatif olmayan bir tutar olmalı.')
    result = result.quantize(CENT, rounding=ROUND_HALF_UP)
    if not allow_zero and result <= 0:
        raise ValueError(f'{label} sıfırdan büyük olmalı.')
    return result


def legacy_currency(value):
    if not isinstance(value, str):
        raise ValueError('Para birimi gerekli.')
    normalized = {'TRY': 'TL', 'EUR': 'EURO'}.get(value.strip().upper(), value.strip().upper())
    if normalized not in {'TL', 'USD', 'EURO'}:
        raise ValueError('Desteklenmeyen para birimi.')
    return normalized


def legacy_cart(items):
    """Keep the legacy batch contract explicit while rejecting invalid input.

    A single-sale row contains a size distribution for ONE batch and quantity
    counts batches. Both price and size amounts must use this same multiplier.
    The new /api/v2 contract uses explicit variant quantities instead.
    """
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('Sepet 1–200 geçerli satır içermeli.')
    output = []
    for original in items:
        if not isinstance(original, dict):
            raise ValueError('Geçersiz sepet satırı.')
        item = dict(original)
        code = item.get('code')
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError('Geçerli ürün kodu gerekli.')
        item['code'] = code.strip()
        single = item.get('is_single', False)
        if not isinstance(single, bool):
            raise ValueError('Satış tipi geçersiz.')
        item['is_single'] = single
        item['quantity'] = quantity(item.get('quantity', 1 if single else None))
        price = money(item.get('price'), 'Fiyat', allow_zero=False)
        discount = money(item.get('discount', 0), 'İndirim')
        gross = price * item['quantity']
        if discount > gross:
            raise ValueError('İndirim satır tutarını aşamaz.')
        item['price'] = float(price)
        item['discount'] = float(discount)
        item['currency'] = legacy_currency(item.get('currency'))
        if single:
            sizes = item.get('single_sizes')
            if not isinstance(sizes, dict) or not 1 <= len(sizes) <= 100:
                raise ValueError('Tekli satış için beden ve adet gerekli.')
            clean = {}
            for name, value in sizes.items():
                if not isinstance(name, str) or not name.strip() or len(name) > 50:
                    raise ValueError('Geçersiz beden.')
                clean[name.strip()] = quantity(value, f'{name} adedi')
            if len(clean) != len(sizes):
                raise ValueError('Tekrarlı beden.')
            item['single_sizes'] = clean
        output.append(item)
    return output
