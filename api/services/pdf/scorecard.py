import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend

from shared.rubric import DIMENSIONS, score_encounter, score_lift, cohort_lift
from .base import AMBER, INK, MUTED, register_brand_fonts, DISPLAY_FONT, BODY_FONT, BODY_FONT_BOLD, brand_name

DIM_LABELS = {
    "read_accuracy": "Read",
    "mode_match": "Mode",
    "delivery": "Delivery",
    "adaptation": "Adaptation",
    "outcome": "Outcome",
}


def _styles():
    register_brand_fonts()
    return {
        "h1": ParagraphStyle("h1", fontName=DISPLAY_FONT, fontSize=20, leading=26, textColor=INK, spaceAfter=8, alignment=TA_LEFT),
        "eyebrow": ParagraphStyle("eyebrow", fontName=BODY_FONT_BOLD, fontSize=9, leading=12, textColor=AMBER, spaceAfter=14),
        "h2": ParagraphStyle("h2", fontName=BODY_FONT_BOLD, fontSize=12, leading=16, textColor=INK, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=BODY_FONT, fontSize=10, textColor=INK, leading=15),
        "muted": ParagraphStyle("muted", fontName=BODY_FONT, fontSize=9, textColor=MUTED, leading=13),
    }


def _five_bar_chart(baseline_scores, final_scores, width=440, height=200):
    d = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x = 40
    chart.y = 30
    chart.width = width - 100
    chart.height = height - 60
    chart.data = [
        [baseline_scores[dim] for dim in DIMENSIONS],
        [final_scores[dim] for dim in DIMENSIONS],
    ]
    chart.categoryAxis.categoryNames = [DIM_LABELS[dim] for dim in DIMENSIONS]
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 5
    chart.valueAxis.valueStep = 1
    chart.bars[0].fillColor = MUTED
    chart.bars[1].fillColor = AMBER
    chart.barSpacing = 4
    chart.groupSpacing = 14
    d.add(chart)

    legend = Legend()
    legend.x = width - 55
    legend.y = height - 15
    legend.dx = 8
    legend.dy = 8
    legend.fontName = BODY_FONT
    legend.fontSize = 8
    legend.colorNamePairs = [(MUTED, "Baseline"), (AMBER, "Final")]
    d.add(legend)
    return d


def render_scorecard_pdf(session_label, participant_name, baseline_scores, final_scores, clip_timestamps=None, mailing_address=""):
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
    )
    flow = []
    brand = brand_name()
    lift = score_lift(baseline_scores, final_scores)

    flow.append(Paragraph(brand.upper(), styles["eyebrow"]))
    flow.append(Paragraph("Encounter Scorecard", styles["h1"]))
    flow.append(Paragraph(f"{participant_name} &middot; {session_label}", styles["muted"]))
    flow.append(Spacer(1, 16))

    flow.append(_five_bar_chart(baseline_scores, final_scores))
    flow.append(Spacer(1, 10))

    lift_rows = [["Dimension", "Baseline", "Final", "Lift"]]
    for dim in DIMENSIONS:
        lift_rows.append([
            DIM_LABELS[dim], str(baseline_scores[dim]), str(final_scores[dim]),
            f"{'+' if lift['dimensions'][dim] >= 0 else ''}{lift['dimensions'][dim]}",
        ])
    lift_rows.append(["Total", str(lift["baseline_total"]), str(lift["final_total"]),
                      f"{'+' if lift['total_lift'] >= 0 else ''}{lift['total_lift']}"])
    table = Table(lift_rows, colWidths=[1.8 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), BODY_FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), BODY_FONT),
        ("FONTNAME", (0, -1), (-1, -1), BODY_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, MUTED),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(table)

    if clip_timestamps:
        flow.append(Paragraph("Clip Timestamps", styles["h2"]))
        for ts in clip_timestamps:
            flow.append(Paragraph(f"&bull;&nbsp;&nbsp;{ts}", styles["body"]))

    flow.append(Spacer(1, 24))
    flow.append(Paragraph(f"{brand} &middot; {mailing_address}", styles["muted"]))

    doc.build(flow)
    buf.seek(0)
    return buf


def render_cohort_scorecard_pdf(session_label, participant_pairs, mailing_address=""):
    """participant_pairs: list of (name, baseline_scores, final_scores)."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
    )
    flow = []
    brand = brand_name()
    pairs = [(b, f) for _, b, f in participant_pairs]
    agg = cohort_lift(pairs)

    flow.append(Paragraph(brand.upper(), styles["eyebrow"]))
    flow.append(Paragraph("Cohort Score Report", styles["h1"]))
    flow.append(Paragraph(f"{session_label} &middot; {agg['n']} participants", styles["muted"]))
    flow.append(Spacer(1, 16))

    flow.append(Paragraph(f"Average total lift: {agg['avg_total_lift']:+.1f} points", styles["h2"]))
    flow.append(Paragraph(
        "Top gaps to drill next: " + ", ".join(DIM_LABELS[d] for d in agg["top_gaps"]),
        styles["body"],
    ))
    flow.append(Spacer(1, 10))

    rows = [["Participant", "Baseline", "Final", "Lift"]]
    for name, baseline, final in participant_pairs:
        lift = score_lift(baseline, final)
        rows.append([name, str(lift["baseline_total"]), str(lift["final_total"]),
                     f"{'+' if lift['total_lift'] >= 0 else ''}{lift['total_lift']}"])
    table = Table(rows, colWidths=[2.6 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), BODY_FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), BODY_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(table)

    flow.append(Spacer(1, 24))
    flow.append(Paragraph(f"{brand} &middot; {mailing_address}", styles["muted"]))

    doc.build(flow)
    buf.seek(0)
    return buf
