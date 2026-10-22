"""Builds a .pptx file from a slide outline produced by the model (Slides mode)."""
import re
from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

NAVY = RGBColor(0x14, 0x16, 0x2B)
NAVY2 = RGBColor(0x22, 0x25, 0x4A)
CREAM = RGBColor(0xF6, 0xF3, 0xEC)
CARD = RGBColor(0xFF, 0xFD, 0xF8)
AMBER = RGBColor(0xF2, 0xB4, 0x5A)
AMBER_DARK = RGBColor(0xB5, 0x70, 0x1A)
MUTED = RGBColor(0x5A, 0x5D, 0x78)
SOFT = RGBColor(0xC9, 0xCB, 0xE0)
LINE = RGBColor(0xE4, 0xDF, 0xD3)

ORB_PNG = Path(__file__).parent / "assets" / "orb.png"

HEAD_FONT = "Arial"
BODY_FONT = "Calibri"

# JSON schema the model must follow (passed to Ollama as "format")
SLIDES_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["title", "bullets", "notes"],
            },
        },
    },
    "required": ["title", "slides"],
}


def is_russian(text: str) -> bool:
    return bool(re.search("[а-яё]", text.lower()))


def clean_deck(deck: dict) -> dict:
    """Keeps the outline short and safe: limits on slides, bullets and lengths."""
    slides = []
    for s in (deck.get("slides") or [])[:15]:
        title = str(s.get("title", "")).strip()[:80]
        bullets = [str(b).strip()[:120] for b in (s.get("bullets") or []) if str(b).strip()][:5]
        if title:
            slides.append({"title": title, "bullets": bullets, "notes": str(s.get("notes", "")).strip()[:2000]})
    if not slides:
        raise ValueError("no slides")
    return {
        "title": str(deck.get("title", "")).strip()[:100] or slides[0]["title"],
        "subtitle": str(deck.get("subtitle", "")).strip()[:140],
        "slides": slides,
    }


def outline_markdown(deck: dict, download_url: str) -> str:
    """Chat message that shows the outline and a download link."""
    ru = is_russian(deck["title"] + " ".join(s["title"] for s in deck["slides"]))
    lines = [f"**{deck['title']}**"]
    if deck["subtitle"]:
        lines.append(deck["subtitle"])
    lines.append("")
    for i, s in enumerate(deck["slides"], 1):
        lines.append(f"{i}. **{s['title']}**" + (f" — {'; '.join(s['bullets'])}" if s["bullets"] else ""))
    lines.append("")
    label = "Скачать презентацию (.pptx)" if ru else "Download the presentation (.pptx)"
    lines.append(f"[⬇ {label}]({download_url})")
    return "\n".join(lines)


# ---------- drawing helpers ----------

def _bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _text(slide, x, y, w, h, text, size, color, bold=False, font=BODY_FONT, align=PP_ALIGN.LEFT,
          anchor=MSO_ANCHOR.TOP, spacing: Optional[float] = None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.name = font
    f.color.rgb = color
    if spacing is not None:
        rPr = run._r.get_or_add_rPr()
        rPr.set("spc", str(int(spacing * 100)))
    return box


def _shape(slide, kind, x, y, w, h, fill, line=None):
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
    shp.shadow.inherit = False
    return shp


def _glow(slide, cx, cy, r):
    """Lumo's glowing orb (a PNG with soft edges); plain circles if the file is missing."""
    if ORB_PNG.is_file():
        slide.shapes.add_picture(str(ORB_PNG), Inches(cx - r), Inches(cy - r), Inches(2 * r), Inches(2 * r))
        return
    for k, color in ((1.0, NAVY2), (0.5, AMBER), (0.26, RGBColor(0xFF, 0xF4, 0xDD))):
        rr = r * k
        _shape(slide, MSO_SHAPE.OVAL, cx - rr, cy - rr, 2 * rr, 2 * rr, color)


def build_pptx(deck: dict, path: Path) -> Path:
    ru = is_russian(deck["title"] + " ".join(s["title"] for s in deck["slides"]))
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    total = len(deck["slides"])

    # title slide
    s = prs.slides.add_slide(blank)
    _bg(s, NAVY)
    _glow(s, 10.2, 3.75, 2.6)
    _text(s, 0.8, 1.7, 6.5, 0.4, ("Презентация" if ru else "Presentation").upper(), 14, AMBER, bold=True, spacing=4)
    _text(s, 0.8, 2.3, 6.8, 2.4, deck["title"], 48, CREAM, bold=True, font=HEAD_FONT, anchor=MSO_ANCHOR.BOTTOM)
    if deck["subtitle"]:
        _text(s, 0.8, 4.9, 6.6, 1.0, deck["subtitle"], 22, SOFT)
    _text(s, 0.8, 6.6, 6, 0.4, "Lumo AI", 14, SOFT)

    # content slides
    for i, item in enumerate(deck["slides"], 1):
        s = prs.slides.add_slide(blank)
        _bg(s, CREAM)
        _text(s, 0.8, 0.45, 6, 0.3, f"{i:02d} / {total:02d}", 12, AMBER_DARK, bold=True, spacing=3)
        _text(s, 0.8, 0.85, 11.7, 1.2, item["title"], 36, NAVY, bold=True, font=HEAD_FONT)
        n = len(item["bullets"])
        if n:
            cols = 2 if n == 4 else min(n, 3)
            rows = (n + cols - 1) // cols
            gap = 0.3
            cw = (11.7 - gap * (cols - 1)) / cols
            ch = 2.1 if rows == 1 else min(2.1, (4.6 - gap * (rows - 1)) / rows)
            for j, b in enumerate(item["bullets"]):
                x = 0.8 + (j % cols) * (cw + gap)
                y = 2.3 + (j // cols) * (ch + gap)
                dark = j == 0
                _shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, ch, NAVY if dark else CARD, None if dark else LINE).adjustments[0] = 0.08
                _shape(s, MSO_SHAPE.OVAL, x + 0.35, y + 0.35, 0.5, 0.5, AMBER)
                _text(s, x + 0.35, y + 0.35, 0.5, 0.5, str(j + 1), 16, NAVY, bold=True, font=HEAD_FONT,
                      align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
                _text(s, x + 0.35, y + 1.05, cw - 0.7, ch - 1.25, b, 22, CREAM if dark else NAVY, bold=True,
                      font=HEAD_FONT)
        else:
            _glow(s, 6.67, 4.6, 1.6)
        _text(s, 12.0, 6.95, 0.6, 0.3, str(i + 1), 11, MUTED, align=PP_ALIGN.RIGHT)
        if item["notes"]:
            s.notes_slide.notes_text_frame.text = item["notes"]

    # closing slide
    s = prs.slides.add_slide(blank)
    _bg(s, NAVY)
    _glow(s, 6.67, 3.1, 2.2)
    _text(s, 0.8, 5.5, 11.7, 0.9, "Спасибо за внимание!" if ru else "Thank you for your attention!", 40, CREAM,
          bold=True, font=HEAD_FONT, align=PP_ALIGN.CENTER)
    _text(s, 0.8, 6.4, 11.7, 0.5, "Вопросы?" if ru else "Any questions?", 22, AMBER, align=PP_ALIGN.CENTER)

    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    return path
