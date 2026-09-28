"""CR80 badge — the RPSAS Method wordmark, letters rising bottom-left to
top-right with a word above each letter. Deliberately doesn't spell out
the sub-words behind each letter (Room/Emotion/Angle/Desire, etc.) --
that breakdown is what the actual training teaches, not something to
give away on a badge. Print-ready PDF (with bleed) plus a PNG for the
lead magnet.

Colored on its own true-black/signal-red card (see BADGE_* below) rather
than the shared INK/AMBER/IVORY/MUTED constants from .base -- the badge is
the one physical-product artifact that moved to the new Sixth Vital mark's
palette; proposal.py and scorecard.py (formal documents) intentionally
still use the shared ivory/navy palette from .base until/unless those get
the same treatment."""

import io

from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .base import register_brand_fonts, DISPLAY_FONT, BODY_FONT_BOLD, BODY_FONT

BADGE_BG = HexColor("#0B0B0B")
BADGE_IVORY = HexColor("#F4EFE6")
BADGE_RED = HexColor("#E4573D")
BADGE_MUTED = HexColor("#9C9A92")

CARD_W = 3.375 * inch
CARD_H = 2.125 * inch
BLEED = 0.125 * inch
PAGE_W = CARD_W + 2 * BLEED
PAGE_H = CARD_H + 2 * BLEED

LETTERS = [
    ("READ", "R"),
    ("PICK", "P"),
    ("SPEAK", "S"),
    ("ASK", "A"),
    ("SHIFT", "S"),
]


def _anchor_points(n, margin_x, margin_y, w, h):
    usable_w = w - 2 * margin_x
    usable_h = h - 2 * margin_y
    return [
        (margin_x + usable_w * i / (n - 1), margin_y + usable_h * i / (n - 1))
        for i in range(n)
    ]


def render_badge_pdf(participant_name=None):
    register_brand_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    c.setFillColor(BADGE_BG)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    points = _anchor_points(len(LETTERS), BLEED + 0.35 * inch, BLEED + 0.3 * inch, PAGE_W, PAGE_H)

    c.setStrokeColor(BADGE_RED)
    c.setLineWidth(1.2)
    c.line(points[0][0], points[0][1], points[-1][0], points[-1][1])

    for (word, letter), (x, y) in zip(LETTERS, points):
        c.setFillColor(BADGE_RED)
        c.setFont(BODY_FONT_BOLD, 6)
        c.drawCentredString(x, y + 0.3 * inch, word)

        c.setFillColor(BADGE_IVORY)
        c.setFont(DISPLAY_FONT, 24)
        c.drawCentredString(x, y - 0.06 * inch, letter)

    c.setFillColor(BADGE_IVORY)
    c.setFont(BODY_FONT_BOLD, 7)
    c.drawCentredString(PAGE_W / 2, BLEED + 0.06 * inch, "RPSAS METHOD")

    if participant_name:
        c.setFillColor(BADGE_MUTED)
        c.setFont(BODY_FONT, 5.5)
        c.drawCentredString(PAGE_W / 2, PAGE_H - BLEED - 0.14 * inch, participant_name)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def render_badge_png(participant_name=None, scale=4):
    """Rasterizes the same design directly via Pillow (no poppler dependency).
    Looks best once real brand TTFs are dropped into shared/brand/fonts/ —
    see services/pdf/base.py."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = int(PAGE_W / inch * 300 * scale / 4), int(PAGE_H / inch * 300 * scale / 4)
    img = Image.new("RGB", (w, h), "#0B0B0B")
    draw = ImageDraw.Draw(img)

    def font(size):
        try:
            return ImageFont.truetype("Arial Bold.ttf", size)
        except Exception:
            return ImageFont.load_default()

    margin_x, margin_y = int(0.4 * inch / inch * 300 * scale / 4), int(0.35 * inch / inch * 300 * scale / 4)
    points = _anchor_points(len(LETTERS), margin_x, margin_y, w, h)
    points = [(x, h - y) for x, y in points]  # flip y for image coords

    draw.line([points[0], points[-1]], fill="#E4573D", width=2)

    for (word, letter), (x, y) in zip(LETTERS, points):
        draw.text((x, y - 34), word, fill="#E4573D", font=font(11), anchor="mm")
        draw.text((x, y), letter, fill="#F4EFE6", font=font(50), anchor="mm")

    draw.text((w / 2, h - 14), "RPSAS METHOD", fill="#F4EFE6", font=font(12), anchor="mm")
    if participant_name:
        draw.text((w / 2, 14), participant_name, fill="#9C9A92", font=font(10), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
