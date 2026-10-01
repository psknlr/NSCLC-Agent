"""Nature Portfolio figure conventions for matplotlib.

Widths 89 / 120–136 / 183 mm; height ≤ 170 mm; Arial/Helvetica 5–7 pt;
panel labels 8 pt bold lowercase; strokes 0.25–1 pt; white background, no
gridlines; Okabe–Ito colour-blind-safe palette; vector PDF with live text
(TrueType, fonttype 42) plus a 600-dpi PNG preview, both at exact size.

Arial is used when installed; otherwise Liberation Sans, which is
metric-identical to Arial (regenerate on a machine with Arial for the final
submission files).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

MM = 1 / 25.4
WIDTH = {"single": 89.0, "onehalf": 120.0, "double": 183.0}
MAX_HEIGHT = 170.0

# Okabe–Ito, as published in the Nature figure guide.
BLACK, ORANGE, SKY, GREEN = "#000000", "#E69F00", "#56B4E9", "#009E73"
YELLOW, BLUE, VERMILLION, PURPLE, GREY = "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"
LIGHT_GREY = "#E5E5E5"

SIZE_TEXT, SIZE_SMALL, SIZE_LABEL = 7.0, 6.0, 8.0


def font_name() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Arial", "Helvetica", "Liberation Sans"):
        if name in names:
            return name
    return "DejaVu Sans"


def apply_style() -> str:
    font = font_name()
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [font],
        "font.size": SIZE_SMALL, "axes.titlesize": SIZE_TEXT, "axes.labelsize": SIZE_TEXT,
        "xtick.labelsize": SIZE_SMALL, "ytick.labelsize": SIZE_SMALL,
        "legend.fontsize": SIZE_SMALL, "legend.frameon": False,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.direction": "out",
        "ytick.direction": "out", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": False, "axes.facecolor": "white", "figure.facecolor": "white",
        "lines.linewidth": 1.0, "patch.linewidth": 0.6, "hatch.linewidth": 0.5,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "savefig.bbox": "standard", "savefig.dpi": 600, "mathtext.default": "regular",
        "axes.titleweight": "normal", "axes.labelcolor": BLACK, "text.color": BLACK,
        "xtick.color": BLACK, "ytick.color": BLACK,
    })
    return font


def figure(width: str | float, height_mm: float):
    width_mm = WIDTH[width] if isinstance(width, str) else float(width)
    fig = plt.figure(figsize=(width_mm * MM, height_mm * MM))
    fig.width_mm, fig.height_mm = width_mm, height_mm
    return fig


def axes_mm(fig, left: float, bottom: float, width: float, height: float, **kw):
    """Axes placed in millimetres from the figure's bottom-left corner."""
    return fig.add_axes([left / fig.width_mm, bottom / fig.height_mm,
                         width / fig.width_mm, height / fig.height_mm], **kw)


def panel_label(fig, x_mm: float, y_mm_from_top: float, letter: str) -> None:
    fig.text(x_mm / fig.width_mm, 1 - y_mm_from_top / fig.height_mm, letter,
             fontsize=SIZE_LABEL, fontweight="bold", va="top", ha="left")


def text_sizes(fig) -> list[tuple[str, float]]:
    """(text, size) of every non-empty rendered text outside 5–7 pt that is
    not an 8-pt panel label."""
    bad = []
    for artist in fig.findobj(matplotlib.text.Text):
        text = artist.get_text().strip()
        if not text or not artist.get_visible():
            continue
        size = artist.get_fontsize()
        if size == SIZE_LABEL and len(text) == 1 and text.islower():
            continue
        if not 5.0 <= size <= 7.0:
            bad.append((text[:30], size))
    return bad


def save(fig, name: str, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    w, h = fig.get_size_inches()
    report = {"name": name, "width_mm": round(w / MM, 1), "height_mm": round(h / MM, 1),
              "font": plt.rcParams["font.sans-serif"][0], "warnings": []}
    if round(w / MM) not in (89, 183) and not 120 <= w / MM <= 136:
        report["warnings"].append(f"width {w / MM:.1f} mm is not a Nature column width")
    if h / MM > MAX_HEIGHT + 0.5:
        report["warnings"].append(f"height {h / MM:.1f} mm exceeds {MAX_HEIGHT} mm")
    for text, size in text_sizes(fig):
        report["warnings"].append(f"text {text!r} at {size} pt (outside 5–7 pt)")
    fig.savefig(outdir / f"{name}.pdf")
    fig.savefig(outdir / f"{name}.png", dpi=600, facecolor="white")
    from PIL import Image

    with Image.open(outdir / f"{name}.png") as im:  # RGB, no alpha channel
        im.convert("RGB").save(outdir / f"{name}.png", dpi=(600, 600))
    fig.savefig(outdir / f"{name}.svg")
    plt.close(fig)
    return report
