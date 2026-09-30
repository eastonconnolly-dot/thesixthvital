"""Shared ReportLab brand helpers — fonts, colors, layout primitives.

The Hub has no reusable ReportLab module to inherit (its PDF path is
xhtml2pdf-based HTML templates); this is built fresh, following its
HTML-template spirit loosely but using reportlab.platypus directly since
that's what this project's stack calls for.

Real Playfair Display / Source Sans 3 TTFs are vendored in
shared/brand/fonts/ — see shared/brand/fonts/README.md for their source
and license (both SIL Open Font License). Falls back to the built-in
Times-Roman/Helvetica substitutes if those files are ever missing.
"""

import os

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

INK = HexColor("#17263B")
IVORY = HexColor("#F4EFE6")
AMBER = HexColor("#E0A458")
MUTED = HexColor("#8A97A8")
WHITE = HexColor("#FFFFFF")

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared", "brand", "fonts")

DISPLAY_FONT = "Times-Bold"
BODY_FONT = "Helvetica"
BODY_FONT_BOLD = "Helvetica-Bold"

_registered = False


def register_brand_fonts():
    """Registers real brand fonts if their TTFs are present; otherwise the
    built-in substitutes above are used. Safe to call repeatedly."""
    global DISPLAY_FONT, BODY_FONT, BODY_FONT_BOLD, _registered
    if _registered:
        return
    _registered = True

    display_path = os.path.join(_FONTS_DIR, "PlayfairDisplay-Bold.ttf")
    body_path = os.path.join(_FONTS_DIR, "SourceSans3-Regular.ttf")
    body_bold_path = os.path.join(_FONTS_DIR, "SourceSans3-SemiBold.ttf")

    if os.path.exists(display_path):
        pdfmetrics.registerFont(TTFont("PlayfairDisplay-Bold", display_path))
        DISPLAY_FONT = "PlayfairDisplay-Bold"
    if os.path.exists(body_path):
        pdfmetrics.registerFont(TTFont("SourceSans3", body_path))
        BODY_FONT = "SourceSans3"
    if os.path.exists(body_bold_path):
        pdfmetrics.registerFont(TTFont("SourceSans3-SemiBold", body_bold_path))
        BODY_FONT_BOLD = "SourceSans3-SemiBold"


def brand_name():
    return os.environ.get("BRAND_NAME", "Sixth Vital")
