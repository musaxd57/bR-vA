"""Offline Code 128 B/C renderer. No CDN, tracking URL or stock write.

ReportLab's established encoder chooses B/C switches and checksum; the SVG
uses the Code 128 symbol table (ISO/IEC 15417). Numeric-pair compression keeps
production codes wider per module at the same physical label width.
Official source: https://hg.reportlab.com/hg-public/reportlab/file/tip/src/reportlab/graphics/barcode/code128.py
Physical print/reader calibration remains required before atelier production.
"""
from html import escape
from .errors import DomainError, reject

PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
)


def code128_symbols(code):
    reject(not isinstance(code, str) or not 1 <= len(code) <= 48 or any(not 32 <= ord(c) <= 126 for c in code),
           "Code 128 etiketi 1–48 yazdırılabilir ASCII karakter içermeli.")
    try:
        from reportlab.graphics.barcode.code128 import Code128
    except ImportError:
        raise DomainError("Etiket motoru kurulu değil; ReportLab bağımlılığını kurun.", "barcode_engine_unavailable", 503)
    encoder = Code128(code)
    encoder.validate()
    return encoder.encode()


def code128_svg(code):
    symbols = code128_symbols(code)
    quiet = 12
    width = 2 * quiet + sum(sum(int(value) for value in PATTERNS[symbol]) for symbol in symbols)
    x = quiet
    bars = []
    for symbol in symbols:
        for index, char in enumerate(PATTERNS[symbol]):
            step = int(char)
            if index % 2 == 0:
                bars.append(f'<rect x="{x}" y="4" width="{step}" height="48"/>')
            x += step
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 68" preserveAspectRatio="none" role="img" '
            f'aria-label="{escape(code, quote=True)}"><rect width="{width}" height="68" fill="white"/>'
            f'<g fill="black" shape-rendering="crispEdges">{"".join(bars)}</g><text x="{width / 2}" y="64" '
            f'text-anchor="middle" font-family="monospace" font-size="10">{escape(code)}</text></svg>')
