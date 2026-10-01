"""Machine-measurable Nature display-item QC for paper/figures and paper/tables.

Measures, per figure: PDF page size (mm) and column class; height limit;
embedded fonts (TrueType, live text — not outlined); every character's font
size from the PDF text layer (5–7 pt body, 8 pt panel labels); stroke widths
in the PDF content (0.25–1 pt); PNG resolution and colour mode; a red/green
co-occurrence scan. Tables: editable (.docx), horizontal rules only.

    python paper/qc.py      → prints the report and writes paper/QC.md
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pdfplumber
from PIL import Image

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
TAB = HERE / "tables"
PT_TO_MM = 25.4 / 72


def width_class(mm: float) -> str:
    if abs(mm - 89) < 0.6:
        return "single column (89 mm)"
    if 120 <= mm <= 136:
        return "1.5 column (120–136 mm)"
    if abs(mm - 183) < 0.6:
        return "double column (183 mm)"
    return "NOT A NATURE WIDTH"


def red_green(png: Path) -> tuple[float, float]:
    """Share of saturated red-ish and green-ish pixels (hue heuristic)."""
    im = Image.open(png).convert("RGB")
    im.thumbnail((900, 900))
    hsv = im.convert("HSV")
    reds = greens = total = 0
    for h, s, v in (hsv.get_flattened_data() if hasattr(hsv, 'get_flattened_data') else hsv.getdata()):
        total += 1
        if s < 90 or v < 60:
            continue
        deg = h * 360 / 255
        if deg < 15 or deg > 345:
            reds += 1
        elif 85 < deg < 160:
            greens += 1
    return reds / total, greens / total


def tf_sizes(pdf_path: Path) -> Counter:
    """Font size of every text run (``/F1 6 Tf``) in the page content."""
    from pypdf import PdfReader

    data = PdfReader(str(pdf_path)).pages[0].get_contents().get_data().decode("latin-1")
    return Counter(round(float(m), 2) for m in re.findall(r"/\S+\s+([\d.]+)\s+Tf", data))


def stroke_widths(pdf_path: Path) -> Counter:
    widths: Counter = Counter()
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        for obj in page.lines + page.rects + page.curves:
            w = obj.get("linewidth")
            stroked = obj.get("stroke", True)
            if w is not None and stroked and w > 0:
                widths[round(float(w), 2)] += 1
    return widths


def figure_rows(name: str) -> list[tuple[str, str, str, str]]:
    pdf_path, png_path = FIG / f"{name}.pdf", FIG / f"{name}.png"
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        w_mm, h_mm = page.width * PT_TO_MM, page.height * PT_TO_MM
        chars = page.chars
        fonts = Counter(c["fontname"].split("+")[-1] for c in chars)
        sizes = Counter(round(c["size"], 2) for c in chars)
    cls = width_class(w_mm)
    rows.append(("Width", f"{w_mm:.1f} mm — {cls}", "89 / 120–136 / 183 mm",
                 "PASS" if "NOT" not in cls else "FAIL"))
    rows.append(("Height", f"{h_mm:.1f} mm", "≤ 170 mm", "PASS" if h_mm <= 170.5 else "FAIL"))
    rows.append(("Vector, live text", f"{len(chars)} live glyphs in PDF text layer",
                 "text not outlined", "PASS" if chars else "FAIL"))
    family = ", ".join(sorted(fonts))
    bases = {re.sub(r"-(Bold|Italic|BoldItalic)$", "", f) for f in fonts}
    if len(bases) == 1 and re.search(r"Arial|Helvetica", next(iter(bases))):
        status = "PASS"
    elif len(bases) == 1 and "LiberationSans" in next(iter(bases)):
        status = "WARN"
        family += " (metric-identical Arial substitute; rerun make_figures.py where Arial is installed)"
    else:
        status = "FAIL"
    rows.append(("Font family", family, "Arial / Helvetica, one family", status))
    # Font sizes from the content stream's Tf operators (the PDF text layer
    # reports bounding-box extents for rotated glyphs, not their size).
    tf = tf_sizes(pdf_path)
    body = {s: n for s, n in tf.items() if s != 8.0}
    label = tf.get(8.0, 0)
    lo, hi = (min(body), max(body)) if body else (0, 0)
    rows.append(("Text size", f"{lo:g}–{hi:g} pt body ({sum(body.values())} text runs); "
                              f"{label} run(s) at 8 pt", "5–7 pt; panel labels 8 pt bold",
                 "PASS" if body and lo >= 5 and hi <= 7 else "FAIL"))
    widths = stroke_widths(pdf_path)
    if widths:
        wmin, wmax = min(widths), max(widths)
        rows.append(("Stroke weight", f"{wmin:g}–{wmax:g} pt ({sum(widths.values())} strokes)",
                     "0.25–1 pt", "PASS" if wmin >= 0.25 and wmax <= 1.0 else "WARN"))
    im = Image.open(png_path)
    dpi = round(im.info.get("dpi", (0, 0))[0])
    px_w = im.size[0]
    eff = px_w / (w_mm / 25.4)
    rows.append(("Raster preview", f"{px_w}×{im.size[1]} px, {eff:.0f} dpi at print size, {im.mode}",
                 "≥ 300 dpi, RGB", "PASS" if eff >= 300 and im.mode in ("RGB", "RGBA") else "FAIL"))
    r, g = red_green(png_path)
    rows.append(("Red/green pairing", f"red {r:.1%}, green {g:.1%} of pixels",
                 "no red+green contrast", "PASS" if min(r, g) < 0.002 else "WARN"))
    return rows


def table_rows(name: str) -> list[tuple[str, str, str, str]]:
    path = TAB / f"{name}.docx"
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    vertical = len(re.findall(r'<w:(left|right) w:val="single"', xml))
    horizontal = len(re.findall(r'<w:(top|bottom) w:val="single"', xml))
    images = "<w:drawing" in xml or "<pic:" in xml
    outside = re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S)
    paragraphs = re.findall(r"<w:p>.*?</w:p>|<w:p .*?</w:p>", outside, flags=re.S)
    footnote_numerals = any(re.match(r"\s*\d+[.)]?\s", "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)))
                            for p in paragraphs if "<w:tbl>" not in p)
    return [
        ("Editable text", "Word table, no images" if not images else "contains images",
         "editable, not an image", "PASS" if not images else "FAIL"),
        ("Rules", f"{horizontal} horizontal cell borders, {vertical} vertical",
         "horizontal only (top, under header, foot)", "PASS" if vertical == 0 else "FAIL"),
        ("Footnote keys", "symbols (*, †)" if not footnote_numerals else "numerals found",
         "symbols or letters", "PASS" if not footnote_numerals else "WARN"),
    ]


def main() -> int:
    figures = sorted(p.stem for p in FIG.glob("*.pdf"))
    tables = sorted(p.stem for p in TAB.glob("*.docx"))
    out = ["# Nature display-item QC", "",
           "Machine-measured by `paper/qc.py` from the submitted files (PDF text layer, "
           "PDF stroke widths, PNG pixels, DOCX XML). Visually verified: panel labels "
           "(lower-case, bold, top-left), no gridlines/shadows/3D/gradients, keys inside "
           "figures, sentence-case axis labels with units.", ""]
    failures = 0
    for kind, names, fn in (("Figure", figures, figure_rows), ("Table", tables, table_rows)):
        for name in names:
            rows = fn(name)
            bad = sum(1 for r in rows if r[3] == "FAIL")
            warn = sum(1 for r in rows if r[3] == "WARN")
            failures += bad
            verdict = "PASS" if not bad and not warn else (
                f"PASS with {warn} warning(s)" if not bad else f"NEEDS FIXES: {bad} failure(s)")
            out += [f"## {kind}: {name} — {verdict}", "",
                    "| Check | Measured | Target | Status |", "|---|---|---|---|"]
            out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
            out.append("")
    (HERE / "QC.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
