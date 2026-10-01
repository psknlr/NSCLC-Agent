"""Build the paper's figures (Nature Portfolio format) from paper/data/*.json.

    python paper/analysis.py && python paper/make_figures.py

Writes paper/figures/{Fig1..Fig4,ExtendedDataFig1}.{pdf,svg,png} at final
print size (183 mm double column) and prints a size/font/text-size report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from collections import Counter

import matplotlib
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nature_style import (  # noqa: E402
    BLACK, BLUE, GREY, LIGHT_GREY, SIZE_SMALL, SIZE_TEXT, VERMILLION, apply_style, axes_mm,
    figure, panel_label, save,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "figures"
BLUE_TINT = "#E5F0F8"
GREY_TINT = "#F2F2F2"
SEQ = LinearSegmentedColormap.from_list("white_blue", ["#FFFFFF", "#C6DBEF", "#6BAED6", BLUE, "#08306B"])


def load(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def clean_axis(ax) -> None:
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)


# =============================================================== Figure 1
def _box(ax, x, y, w, h, title, lines=(), *, edge=GREY, fill="white", bold=True,
         title_size=SIZE_TEXT, line_size=SIZE_SMALL, lw=0.8, dashed=False):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2",
                                         linewidth=lw, edgecolor=edge, facecolor=fill,
                                         linestyle=(0, (3, 2)) if dashed else "solid"))
    ax.text(x + 2, y + h - 2.2, title, fontsize=title_size, va="top", ha="left",
            fontweight="bold" if bold else "normal")
    for i, line in enumerate(lines):
        ax.text(x + 2, y + h - 6.2 - i * 3.1, line, fontsize=line_size, va="top", ha="left")


def _arrow(ax, x0, y0, x1, y1, *, color=BLACK, lw=0.7, both=False, style="-|>"):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops={"arrowstyle": ("<|-|>" if both else style), "color": color,
                            "lw": lw, "shrinkA": 0, "shrinkB": 0, "mutation_scale": 6})


def fig1(report: list) -> None:
    kn = load("knowledge")
    fig = figure("double", 132)

    # ---- a: system schematic (coordinates in mm inside the axes)
    ax = axes_mm(fig, 0, 52, 183, 76)
    ax.set_xlim(0, 183)
    ax.set_ylim(0, 76)
    ax.set_axis_off()
    panel_label(fig, 1.5, 1.5, "a")

    # left column: input → prompt hooks; governed-mode note
    _box(ax, 3, 55, 36, 16, "Clinical input",
         ["Narrative (Chinese or English)", "Structured facts", "Imaging and reports"])
    _box(ax, 3, 34, 36, 13, "Prompt hooks", ["Emergency screen", "Case-note seeding"],
         fill=GREY_TINT)
    _arrow(ax, 21, 55, 21, 47)
    _box(ax, 3, 4, 36, 22, "Governed mode",
         ["Same kernel as a hard control", "plane: stage, rules, release",
          "and doses are decided", "deterministically"], dashed=True)

    # centre: the lead agent and its session
    _box(ax, 50, 29, 46, 42, "Lead agent (model)",
         ["Keeps structured case notes", "Plans the consult (update_plan)",
          "Calls tools; reads observations", "Delegates to specialists",
          "Integrates and submits the consult", "Answers each hook finding"],
         edge=BLUE, fill=BLUE_TINT, lw=1.0)
    _box(ax, 53, 31.5, 40, 6.5, "Memory · checkpoints · compaction", [], bold=False,
         title_size=SIZE_SMALL, dashed=True)
    _arrow(ax, 39, 40.5, 50, 40.5)
    ax.text(44.5, 41.6, "context", fontsize=SIZE_SMALL, ha="center", color=GREY)

    # right column: instruments, specialists, external tools
    _box(ax, 110, 58, 70, 13, "Clinical instruments (20 deterministic tools)",
         ["Staging engine · trial registry · regimen library",
          "Indication and organ gates · CNS · later lines · KB"])
    _box(ax, 110, 40, 70, 13, "Specialist sub-agents (7, concurrent)",
         ["Radiology · pathology · thoracic surgery",
          "Radiation and medical oncology · pharmacy · evidence"],
         edge=BLUE, fill=BLUE_TINT)
    _box(ax, 110, 29, 70, 7, "External tools (MCP servers)", [], dashed=True)
    for y in (64.5, 46.5, 32.5):
        _arrow(ax, 96, y, 110, y, both=True)
    ax.text(103, 61, "call /\nobservation", fontsize=SIZE_SMALL, ha="center", va="top",
            color=GREY, linespacing=1.1)
    ax.text(145, 55.5, "post-tool hook: evidence ledger", fontsize=SIZE_SMALL, ha="center",
            va="center", color=GREY)

    # bottom: stop hooks and the review loop; output
    _box(ax, 50, 4, 62, 16, "Stop hooks (advisory, never blocking)",
         ["Rule engine (20 rules) · stage consistency",
          "Citation provenance · dose provenance", "Emergency first"], fill=GREY_TINT)
    _arrow(ax, 63, 29, 63, 20)
    _arrow(ax, 83, 20, 83, 29)
    ax.text(62, 24.5, "submit", fontsize=SIZE_SMALL, ha="right", va="center")
    ax.text(84, 24.5, "findings: revise or justify", fontsize=SIZE_SMALL, ha="left",
            va="center")
    _box(ax, 122, 4, 58, 16, "Consult to the clinician",
         ["Reply · stage · options with evidence", "Work-up · questions · trace"],
         edge=BLUE)
    _arrow(ax, 112, 12, 122, 12)
    ax.text(117, 13.2, "accepted", fontsize=SIZE_SMALL, ha="center", color=GREY)

    # ---- b: guideline knowledge base
    ax = axes_mm(fig, 50, 8, 62, 36)
    panel_label(fig, 1.5, 84, "b")
    rows = list(reversed(kn["guidelines"]))
    y = np.arange(len(rows))
    ax.barh(y, [r["recommendations"] for r in rows], height=0.62, color=GREY, linewidth=0)
    ax.set_yticks(y, [r["label"] for r in rows])
    ax.tick_params(axis="y", length=0)
    for yi, r in zip(y, rows):
        ax.text(r["recommendations"] + 12, yi, f"{r['recommendations']:,}", va="center",
                fontsize=SIZE_SMALL)
    ax.set_xlabel(f"Guideline recommendations (n; total {kn['kg_total']:,})")
    ax.set_xlim(0, max(r["recommendations"] for r in rows) * 1.22)

    # ---- c: kernel assets
    ax = axes_mm(fig, 146, 8, 32, 36)
    panel_label(fig, 116, 84, "c")
    assets = sorted(kn["assets"].items(), key=lambda kv: kv[1])
    y = np.arange(len(assets))
    ax.hlines(y, 0, [v for _k, v in assets], color=GREY, lw=0.8)
    ax.plot([v for _k, v in assets], y, "o", ms=3.2, color=BLUE, mec="none")
    ax.set_yticks(y, [k for k, _v in assets])
    ax.tick_params(axis="y", length=0)
    for yi, (_k, v) in zip(y, assets):
        ax.text(v + 3, yi, str(v), va="center", fontsize=SIZE_SMALL)
    ax.set_xlim(0, max(v for _k, v in assets) * 1.3)
    ax.set_xlabel("Count (n)")
    report.append(save(fig, "Fig1", OUT))


# =============================================================== Figure 2
STAGE_ORDER = ["0", "IA1", "IA2", "IA3", "IB", "IIA", "IIB", "IIIA", "IIIB", "IIIC", "IVA", "IVB"]


def _stage_colour(stage):
    if stage is None:
        return LIGHT_GREY, GREY
    if stage == "Occult":
        return "#F7F7F7", BLACK
    v = 0.08 + 0.8 * STAGE_ORDER.index(stage) / (len(STAGE_ORDER) - 1)
    rgba = SEQ(v)
    lum = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
    return rgba, ("white" if lum < 0.55 else BLACK)


def fig2(report: list) -> None:
    st = load("staging")
    gs = load("gold_standard")
    fig = figure("double", 74)

    # ---- a: T x N (M0)
    ax = axes_mm(fig, 14, 4, 74, 62)
    panel_label(fig, 1.5, 1.5, "a")
    T, N, M = st["t"], st["n"], st["matrix"]
    for i, t in enumerate(T):
        for j, n in enumerate(N):
            fill, ink = _stage_colour(M[i][j])
            ax.add_patch(mpatches.Rectangle((j, len(T) - 1 - i), 1, 1, facecolor=fill,
                                            edgecolor="white", linewidth=0.8))
            ax.text(j + 0.5, len(T) - 1 - i + 0.5, M[i][j] or "–", ha="center", va="center",
                    fontsize=5.5, color=ink)
    ax.axhline(len(T) - 9, color=BLACK, lw=0.6)
    ax.axvline(5, color=BLACK, lw=0.6)
    ax.set_xlim(0, len(N))
    ax.set_ylim(0, len(T))
    ax.set_xticks(np.arange(len(N)) + 0.5, N)
    ax.set_yticks(np.arange(len(T)) + 0.5, list(reversed(T)))
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel("N category (M0)", labelpad=3)
    ax.xaxis.set_label_position("top")
    ax.set_ylabel("T category")

    # ---- b: M category
    ax = axes_mm(fig, 102, 24, 14, 42)
    panel_label(fig, 92, 1.5, "b")
    rows = st["m_rows"]
    for i, r in enumerate(rows):
        stage = r["stage"] if r["m"] != "M0" else None
        fill, ink = _stage_colour(stage) if r["m"] != "M0" else ("#FFFFFF", BLACK)
        label = "by T×N" if r["m"] == "M0" else (r["stage"] or "–")
        ax.add_patch(mpatches.Rectangle((0, len(rows) - 1 - i), 1, 1, facecolor=fill,
                                        edgecolor="white" if r["m"] != "M0" else GREY,
                                        linewidth=0.8))
        ax.text(0.5, len(rows) - 1 - i + 0.5, label, ha="center", va="center", fontsize=5.5,
                color=ink)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, len(rows))
    ax.set_xticks([])
    ax.set_yticks(np.arange(len(rows)) + 0.5, [r["m"] for r in reversed(rows)])
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title("M category", fontsize=SIZE_TEXT, pad=4)
    key = axes_mm(fig, 94, 4, 30, 10)
    key.set_axis_off()
    key.set_xlim(0, 30)
    key.set_ylim(0, 10)
    key.add_patch(mpatches.Rectangle((0, 6), 3, 3, facecolor=LIGHT_GREY, edgecolor="none"))
    key.text(4.2, 7.5, "Refused", fontsize=SIZE_SMALL, va="center")
    key.add_patch(mpatches.Rectangle((0, 1), 3, 3, facecolor=SEQ(0.6), edgecolor="none"))
    key.text(4.2, 2.5, "Stage group", fontsize=SIZE_SMALL, va="center")

    # ---- c: gold standard
    ax = axes_mm(fig, 142, 13, 30, 50)
    panel_label(fig, 132, 1.5, "c")
    metrics = list(gs["metrics"].items())
    y = np.arange(len(metrics))[::-1]
    for yi, (name, m) in zip(y, metrics):
        colour = VERMILLION if name.startswith("Unsafe") else BLUE
        ax.plot([m["lo"], m["hi"]], [yi, yi], color=colour, lw=0.9, solid_capstyle="butt")
        ax.plot([m["p"]], [yi], "o", ms=3.5, color=colour, mec="none", clip_on=False)
        ax.text(1.1, yi, f"{m['k']}/{m['n']}", va="center", fontsize=SIZE_SMALL,
                transform=ax.get_yaxis_transform())
    ax.set_yticks(y, [n for n, _m in metrics])
    ax.set_xlim(-0.02, 1.02)
    ax.set_xticks([0, 0.5, 1], ["0", "0.5", "1"])
    ax.set_ylim(-0.6, len(metrics) - 0.4)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Proportion (95% CI)")
    report.append(save(fig, "Fig2", OUT))


# =============================================================== Figure 3
FINDING_LABEL = {
    "UNVERIFIED_CITATION": "Unverified citation", "DOSE_NOT_FROM_LIBRARY": "Dose not from library",
    "DOSE_IN_MODEL_OUTPUT": "Dose in model output", "DOSE_TO_PATIENT": "Dose to patient",
    "STAGE_DIFFERS_FROM_ENGINE": "Stage differs from engine",
    "TRIAL_EDITION_MIGRATION": "Trial edition migration",
    "EMERGENCY_NOT_ADDRESSED": "Emergency not addressed",
    "INDICATION_PREDICATE": "Indication predicate", "TRIAL_STAGE_BOUNDARY": "Trial stage boundary",
    "STAGE0_NO_SYSTEMIC": "Stage 0: no systemic therapy",
    "DRIVER_FIRST_LINE": "Driver-directed first line",
    "CNS_UNTREATED_SYMPTOMATIC": "Untreated symptomatic CNS",
    "PROGRESSION_SAME_DRUG": "Progression on same drug",
}


def fig3(report: list) -> None:
    pt = load("perturbation")
    fig = figure("double", 86)
    det = list(pt["detection"].items())
    rows = [(k, v, BLUE) for k, v in det] + [("Clean consult (no defect)", pt["clean"], GREY)]
    y = np.arange(len(rows))[::-1].astype(float)
    y[-1] -= 0.6
    ylim = (y[-1] - 0.7, y[0] + 0.6)

    # ---- a: detection (rows shared with b)
    ax = axes_mm(fig, 54, 26, 26, 54)
    panel_label(fig, 1.5, 1.5, "a")
    for yi, (name, v, colour) in zip(y, rows):
        ax.plot([v["lo"], v["hi"]], [yi, yi], color=colour, lw=0.9, solid_capstyle="butt")
        ax.plot([v["p"]], [yi], "o", ms=3.5, color=colour, mec="none", clip_on=False)
        ax.text(1.1, yi, f"{v['k']}/{v['n']}", va="center", fontsize=SIZE_SMALL,
                transform=ax.get_yaxis_transform())
    ax.set_yticks(y, [r[0] for r in rows])
    ax.axhline(y[-1] + 0.8, color=GREY, lw=0.4, ls=(0, (2, 2)))
    ax.set_xlim(-0.02, 1.02)
    ax.set_xticks([0, 0.5, 1], ["0", "0.5", "1"])
    ax.set_ylim(*ylim)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Consults with the expected\nfinding (proportion, 95% CI)")

    # ---- b: every finding raised, same rows
    mx = pt["matrix"]
    baseline = Counter(f for r in pt["clean"]["rows"] for f in r["findings"])
    findings = [f for f in FINDING_LABEL if f in mx["findings"] or f in baseline]
    props = np.zeros((len(rows), len(findings)))
    for i, k in enumerate(mx["perturbations"]):
        for j, f in enumerate(findings):
            if f in mx["findings"]:
                props[i, j] = mx["counts"][i][mx["findings"].index(f)] / mx["n"][i]
    for j, f in enumerate(findings):
        props[-1, j] = baseline.get(f, 0) / pt["clean"]["n"]
    ax = axes_mm(fig, 100, 26, 62, 54)
    panel_label(fig, 92, 1.5, "b")
    for i, yi in enumerate(y):
        for j in range(len(findings)):
            v = props[i, j]
            ax.add_patch(mpatches.Rectangle((j, yi - 0.42), 1, 0.84, facecolor=SEQ(v) if v else
                                            "white", edgecolor="#E6E6E6", linewidth=0.4))
            if v:
                ax.text(j + 0.5, yi, "1" if v == 1 else f"{v:.2f}", ha="center",
                        va="center", fontsize=5, color="white" if v > 0.55 else BLACK)
    ax.set_xlim(0, len(findings))
    ax.set_ylim(*ylim)
    ax.set_yticks([])
    ax.set_xticks(np.arange(len(findings)) + 0.5, [FINDING_LABEL[f] for f in findings],
                  rotation=55, ha="right", rotation_mode="anchor")
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title("Findings raised (proportion of consults)", fontsize=SIZE_TEXT, pad=4)
    cax = axes_mm(fig, 167, 50, 2, 26)
    sm = matplotlib.cm.ScalarMappable(cmap=SEQ, norm=matplotlib.colors.Normalize(0, 1))
    cb = fig.colorbar(sm, cax=cax)
    cb.outline.set_linewidth(0.4)
    cb.ax.tick_params(width=0.4, length=2)
    cb.set_ticks([0, 0.5, 1], labels=["0", "0.5", "1"])
    report.append(save(fig, "Fig3", OUT))


# =============================================================== Figure 4
def fig4(report: list) -> None:
    rt = load("runtime")
    cx = load("context")
    fig = figure("double", 62)

    # ---- a: concurrency
    ax = axes_mm(fig, 14, 11, 66, 44)
    panel_label(fig, 1.5, 1.5, "a")
    for parallel, colour, label in ((False, GREY, "Serial"), (True, BLUE, "Concurrent")):
        rows = [r for r in rt["rows"] if r["parallel"] is parallel]
        k = np.array([r["k"] for r in rows])
        med = np.array([r["median"] for r in rows])
        lo = med - np.array([r["min"] for r in rows])
        hi = np.array([r["max"] for r in rows]) - med
        ax.errorbar(k, med, yerr=[lo, hi], color=colour, lw=1.0, marker="o", ms=3, mec="none",
                    elinewidth=0.6, capsize=1.5, capthick=0.6)
        ax.text(k[-1] + 0.2, med[-1], label, color=BLACK, fontsize=SIZE_SMALL, va="center")
    ax.set_xlabel("Specialist sub-agents consulted (n)")
    ax.set_ylabel("Consult wall-clock time (s)")
    ax.set_xticks(range(1, 8))
    ax.set_xlim(0.6, 8.6)
    ax.set_ylim(0, None)
    ax.spines["bottom"].set_bounds(1, 7)

    # ---- b: context size
    ax = axes_mm(fig, 108, 11, 70, 44)
    panel_label(fig, 92, 1.5, "b")
    threshold = cx["window"] * cx["compact_at"] / 1000
    for label, colour in (("Without compaction", GREY), ("With compaction", BLUE)):
        pts = cx["series"][label]
        x = [p["turn"] for p in pts]
        yv = [p["tokens"] / 1000 for p in pts]
        ax.plot(x, yv, color=colour, lw=1.0)
        ev = [(p["turn"], p["tokens"] / 1000) for p in pts if p["compacted"]]
        if ev:
            ax.plot(*zip(*ev), "o", ms=3, mfc="white", mec=colour, mew=0.8)
        ax.text(x[-1] + 0.4, yv[-1], label, fontsize=SIZE_SMALL, va="center")
    ax.axhline(threshold, color=VERMILLION, lw=0.6, ls=(0, (3, 2)))
    ax.text(0.8, threshold + 0.8, "Compaction threshold", fontsize=SIZE_SMALL, color=BLACK)
    ax.set_xlabel("Consult turn")
    ax.set_ylabel("Prompt size (×1,000 tokens)")
    ax.set_xlim(0, cx["turns"] + 9)
    ax.set_xticks([1, 6, 12, 18, 24])
    ax.spines["bottom"].set_bounds(1, cx["turns"])
    ax.set_ylim(0, None)
    report.append(save(fig, "Fig4", OUT))


# ===================================================== Extended Data Fig. 1
def ed_fig1(report: list) -> None:
    ac = load("access")
    fig = figure("double", 70)
    ax = axes_mm(fig, 36, 29, 140, 33)
    rows = ["Lead agent"] + ac["agents"]
    mat = np.vstack([np.ones(len(ac["tools"]))] + [np.array(r) for r in ac["matrix"]])
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            colour = (GREY if i == 0 else BLUE) if mat[i, j] else "white"
            ax.add_patch(mpatches.Rectangle((j, mat.shape[0] - 1 - i), 1, 1, facecolor=colour,
                                            edgecolor="white" if mat[i, j] else "#DDDDDD",
                                            linewidth=0.6))
    ax.set_xlim(0, mat.shape[1])
    ax.set_ylim(0, mat.shape[0])
    ax.set_yticks(np.arange(mat.shape[0]) + 0.5, list(reversed(rows)))
    ax.set_xticks(np.arange(mat.shape[1]) + 0.5, ac["tool_labels"], rotation=55, ha="right",
                  rotation_mode="anchor")
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    key = axes_mm(fig, 36, 64.5, 140, 4)
    key.set_axis_off()
    key.set_xlim(0, 140)
    key.set_ylim(0, 4)
    for x, colour, label in ((0, GREY, "Lead agent (also writes case notes, plans, delegates, submits)"),
                             (86, BLUE, "Specialist read-only allow-list")):
        key.add_patch(mpatches.Rectangle((x, 0.5), 3, 3, facecolor=colour, edgecolor="none"))
        key.text(x + 4.2, 2, label, fontsize=SIZE_SMALL, va="center")
    report.append(save(fig, "ExtendedDataFig1", OUT))


if __name__ == "__main__":
    font = apply_style()
    print(f"font: {font}")
    out: list = []
    for build in (fig1, fig2, fig3, fig4, ed_fig1):
        build(out)
    for r in out:
        print(f"{r['name']}: {r['width_mm']} x {r['height_mm']} mm  font={r['font']}  "
              f"warnings={r['warnings'] or 'none'}")
