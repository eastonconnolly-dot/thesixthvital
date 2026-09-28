"""CR80 badge — the RPSAS Method wordmark, letters rising bottom-left to
top-right with a word above each letter and sub-words in a horizontal row
beneath it. Print-ready PDF (with bleed) plus a PNG for the lead magnet."""

import io

from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .base import AMBER, INK, IVORY, MUTED, register_brand_fonts, DISPLAY_FONT, BODY_FONT_BOLD, BODY_FONT

CARD_W = 3.375 * inch
CARD_H = 2.125 * inch
BLEED = 0.125 * inch
PAGE_W = CARD_W + 2 * BLEED
PAGE_H = CARD_H + 2 * BLEED

LETTERS = [
    ("READ", "R", "Room · Emotion · Angle · Desire"),
    ("PICK", "P", "Proof · Plan · Permission · Power"),
    ("SPEAK", "S", "Sit · Pace · Eyes · Air · Kill fillers"),
    ("ASK", "A", "Acknowledge · Stop · Know"),
    ("SHIFT", "S", "See · Hold · Identify · Flip · Test"),
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

    c.setFillColor(IVORY)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    points = _anchor_points(len(LETTERS), BLEED + 0.35 * inch, BLEED + 0.3 * inch, PAGE_W, PAGE_H)

    c.setStrokeColor(AMBER)
    c.setLineWidth(1.2)
    c.line(points[0][0], points[0][1], points[-1][0], points[-1][1])

    for (word, letter, subwords), (x, y) in zip(LETTERS, points):
        c.setFillColor(AMBER)
        c.setFont(BODY_FONT_BOLD, 6)
        c.drawCentredString(x, y + 0.34 * inch, word)

        c.setFillColor(INK)
        c.setFont(DISPLAY_FONT, 22)
        c.drawCentredString(x, y - 0.06 * inch, letter)

        c.setFillColor(MUTED)
        c.setFont(BODY_FONT, 4.6)
        c.drawCentredString(x, y - 0.22 * inch, subwords)

    c.setFillColor(INK)
    c.setFont(BODY_FONT_BOLD, 7)
    c.drawCentredString(PAGE_W / 2, BLEED + 0.06 * inch, "RPSAS METHOD")

    if participant_name:
        c.setFillColor(MUTED)
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
    img = Image.new("RGB", (w, h), "#F4EFE6")
    draw = ImageDraw.Draw(img)

    def font(size):
        try:
            return ImageFont.truetype("Arial Bold.ttf", size)
        except Exception:
            return ImageFont.load_default()

    margin_x, margin_y = int(0.4 * inch / inch * 300 * scale / 4), int(0.35 * inch / inch * 300 * scale / 4)
    points = _anchor_points(len(LETTERS), margin_x, margin_y, w, h)
    points = [(x, h - y) for x, y in points]  # flip y for image coords

    draw.line([points[0], points[-1]], fill="#E0A458", width=2)

    for (word, letter, subwords), (x, y) in zip(LETTERS, points):
        draw.text((x, y - 34), word, fill="#E0A458", font=font(11), anchor="mm")
        draw.text((x, y), letter, fill="#17263B", font=font(46), anchor="mm")
        draw.text((x, y + 28), subwords, fill="#8A97A8", font=font(9), anchor="mm")

    draw.text((w / 2, h - 14), "RPSAS METHOD", fill="#17263B", font=font(12), anchor="mm")
    if participant_name:
        draw.text((w / 2, 14), participant_name, fill="#8A97A8", font=font(10), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
