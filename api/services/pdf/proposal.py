import io

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import ParagraphStyle

from .base import AMBER, INK, MUTED, WHITE, register_brand_fonts, DISPLAY_FONT, BODY_FONT, BODY_FONT_BOLD, brand_name


def _styles():
    register_brand_fonts()
    return {
        "h1": ParagraphStyle("h1", fontName=DISPLAY_FONT, fontSize=22, leading=28, textColor=INK, spaceAfter=8, alignment=TA_LEFT),
        "eyebrow": ParagraphStyle("eyebrow", fontName=BODY_FONT_BOLD, fontSize=9, leading=12, textColor=AMBER, spaceAfter=14),
        "h2": ParagraphStyle("h2", fontName=BODY_FONT_BOLD, fontSize=13, leading=17, textColor=INK, spaceBefore=16, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=BODY_FONT, fontSize=10, textColor=INK, leading=15),
        "muted": ParagraphStyle("muted", fontName=BODY_FONT, fontSize=9, textColor=MUTED, leading=13),
    }


def render_proposal_pdf(deal, lead, package_label, deliverables, deposit_link, mailing_address, signature=None):
    """deal, lead: dict-like with the fields used below. deliverables: list[str].
    signature (optional): {"typed_name", "signer_name", "signed_at" (str),
    "png_bytes"} — when present, renders a signed certificate block instead
    of the "sign here" call to action."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
    )
    flow = []
    brand = brand_name()

    flow.append(Paragraph(brand.upper(), styles["eyebrow"]))
    flow.append(Paragraph("Proposal &amp; Agreement", styles["h1"]))
    flow.append(Paragraph(f"Prepared for {lead['name']}" + (f", {lead['org']}" if lead.get("org") else ""), styles["muted"]))
    flow.append(Spacer(1, 18))

    amount = deal["amount_cents"] / 100
    deposit = amount * 0.5
    balance = amount - deposit

    summary_rows = [
        ["Package", package_label],
        ["Total investment", f"${amount:,.2f}"],
        ["Deposit due to lock a date (50%)", f"${deposit:,.2f}"],
        ["Balance due (14 days before delivery)", f"${balance:,.2f}"],
        ["Proposed delivery date", deal.get("delivery_date") or "TBD upon deposit"],
    ]
    table = Table(summary_rows, colWidths=[2.6 * inch, 3.4 * inch])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), BODY_FONT),
        ("FONTNAME", (0, 0), (0, -1), BODY_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, MUTED),
    ]))
    flow.append(table)

    flow.append(Paragraph("Deliverables", styles["h2"]))
    for d in deliverables:
        flow.append(Paragraph(f"&bull;&nbsp;&nbsp;{d}", styles["body"]))

    flow.append(Paragraph("Payment &amp; Terms", styles["h2"]))
    flow.append(Paragraph(
        "A 50% deposit locks the delivery date above. The remaining balance is due 14 days before "
        "delivery. Deposits are non-refundable but may be applied toward a rescheduled date within "
        "12 months, given at least 14 days' notice. This proposal does not guarantee any admission, "
        "match, employment, or clinical outcome.",
        styles["body"],
    ))

    if signature:
        flow.append(Paragraph("Signature", styles["h2"]))
        img = Image(io.BytesIO(signature["png_bytes"]), width=2.6 * inch, height=0.9 * inch)
        img.hAlign = "LEFT"
        flow.append(img)
        flow.append(Paragraph(
            f'Signed by <b>{signature["typed_name"]}</b> ({signature["signer_name"]}) on {signature["signed_at"]}',
            styles["muted"],
        ))
        flow.append(Spacer(1, 10))
        flow.append(Paragraph(
            f'Deposit: <link href="{deposit_link}" color="#E0A458">{deposit_link}</link>',
            styles["body"],
        ))
    else:
        flow.append(Paragraph("Next Step", styles["h2"]))
        flow.append(Paragraph(
            f'Sign electronically via the link sent with this proposal, then pay the deposit here: '
            f'<link href="{deposit_link}" color="#E0A458">{deposit_link}</link>',
            styles["body"],
        ))

    flow.append(Spacer(1, 28))
    flow.append(Paragraph(f"{brand} &middot; {mailing_address}", styles["muted"]))

    doc.build(flow)
    buf.seek(0)
    return buf
