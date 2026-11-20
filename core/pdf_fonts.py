from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_REGULAR = 'StroyDejaVuSans'
FONT_BOLD = 'StroyDejaVuSans-Bold'
FONT_DIR = Path(__file__).resolve().parent / 'assets' / 'fonts'
_registered = False


def register_pdf_fonts():
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONT_DIR / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily(
        FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD,
        italic=FONT_REGULAR, boldItalic=FONT_BOLD)
    _registered = True
