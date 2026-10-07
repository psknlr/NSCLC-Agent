"""Build the paper's figures (Nature Portfolio format) from paper/data/*.json.

    python paper/analysis.py && python paper/make_figures.py

Writes paper/figures/{Fig1..Fig5,ExtendedDataFig1..3}.{pdf,svg,png} at final
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nature_style import (  # noqa: E402
    BLACK, BLUE, GREY, LIGHT_GREY, ORANGE, SIZE_SMALL, SIZE_TEXT, VERMILLION, apply_style,
    axes_mm,
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


def _band(ax, y0, y1, label, fill, *, x0=1.0, x1=182.0):
    ax.add_patch(mpatches.Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=fill,
                                    edgecolor="none", zorder=0))
    ax.text(x0 + 2, y1 - 1.2, label, fontsize=6.5, fontweight="bold", va="top", ha="left")


def _label(ax, x, y, text, *, ha="center", va="center", size=5.5, color=BLACK, **kw):
    ax.text(x, y, text, fontsize=size, ha=ha, va=va, color=color, **kw)


def _key(ax, x, y, edge, fill, text, *, dashed=False, w=3.2, h=2.2):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.5",
                                         linewidth=0.6, edgecolor=edge, facecolor=fill,
                                         linestyle=(0, (2, 1.5)) if dashed else "solid"))
    _label(ax, x + w + 1.2, y + h / 2, text, ha="left", size=SIZE_SMALL)


def _note(ax, x, y, w, h, lines, *, size=5.0, dashed=True, edge=GREY, fill="white"):
    """A small box with its lines centred vertically (no title)."""
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.8",
                                         linewidth=0.6, edgecolor=edge, facecolor=fill,
                                         linestyle=(0, (3, 2)) if dashed else "solid"))
    step = 2.6
    top = y + h / 2 + (len(lines) - 1) * step / 2
    for i, line in enumerate(lines):
        ax.text(x + 2, top - i * step, line, fontsize=size, va="center", ha="left")


def fig1(report: list) -> None:
    kn = load("knowledge")
    gs = load("gold_standard")
    pt = load("perturbation")
    a = kn["assets"]
    rt = kn["runtime"]
    full = rt["full"]
    n_info = len(full["information_tools"])
    n_kernel = len(rt["kernel_tools"])
    H = 168
    fig = figure("double", H)
    ax = axes_mm(fig, 0, 0, 183, H)
    ax.set_xlim(0, 183)
    ax.set_ylim(0, H)
    ax.set_axis_off()
    band = "#F3F3F3"

    # ---------------------------------------------------------- interfaces
    _band(ax, 147, 166.5, "Interfaces", band)
    _box(ax, 4, 149, 26, 12, "Clinician", ["Chinese or English", "notes, reports, images"])
    _box(ax, 36, 149, 56, 12, "Web app (static site)",
         ["Python kernel in the browser (WebAssembly)", "Live trace · plan · stop · rewind"])
    _box(ax, 98, 149, 42, 12, "Command line",
         [f"REPL · {kn['commands']} slash commands", "Text, JSON or stream-JSON output"])
    _box(ax, 146, 149, 34, 12, "Two languages",
         ["One shared dictionary", "English-output directive"])
    _arrow(ax, 30, 155, 36, 155, both=True)
    _arrow(ax, 66, 149, 66, 136)
    _arrow(ax, 76, 136, 76, 149)
    _label(ax, 64.8, 143.6, "message · facts · attachments", ha="right")
    _label(ax, 77.2, 143.6, "consult · live events", ha="left")
    _arrow(ax, 106, 149, 106, 136, both=True)

    # ------------------------------------------------------------- runtime
    _band(ax, 50, 141.5, "Agent runtime: full model autonomy (default)", "#EDF4FA")
    _key(ax, 110, 137.6, BLUE, BLUE_TINT, "Language model")
    _key(ax, 133, 137.6, GREY, "white", "Deterministic")
    _key(ax, 154, 137.6, GREY, "white", "Other mode or optional", dashed=True)

    # hooks (left column): what stays in the loop
    _box(ax, 4, 118, 40, 18, "Hook: each message",
         ["Emergency screen (bilingual)", "→ alert; context for the model"], fill=GREY_TINT)
    _note(ax, 6, 119.6, 36, 4.6, ["Case-note seeding (kernel-assisted mode)"])
    _box(ax, 4, 100, 40, 13, "Hook: after each tool",
         ["Evidence ledger: references and", "dose look-ups tools returned"],
         fill=GREY_TINT)
    _box(ax, 4, 56, 40, 39, "Hooks: on submit (advisory)",
         ["Citation provenance", "Dose provenance", "Emergency first", "",
          "Findings return to the model:", "revise, or justify an override",
          f"({len(full['hooks_on'])} of {a['Hooks']} hooks on; none blocks)"], fill=GREY_TINT)
    _note(ax, 6, 58, 36, 7.4, ["Rule engine · stage consistency", "(kernel-assisted mode)"])
    _arrow(ax, 44, 127, 54, 127)
    _label(ax, 49, 128.6, "context")
    _arrow(ax, 54, 106.5, 44, 106.5)
    _label(ax, 49, 108.1, "results")
    _arrow(ax, 54, 90, 44, 90)
    _label(ax, 49, 91.6, "submit")
    _arrow(ax, 44, 82, 54, 82)
    _label(ax, 49, 83.6, "findings")

    # lead agent: the decision-maker
    _box(ax, 54, 76, 62, 60, "Lead agent (language model) decides",
         ["Stage: cTNM and group (AJCC/UICC 9th ed.)",
          "Biomarker category (NSCL-21 to NSCL-39)",
          "Treatment intent · plan · work-up",
          "each with its rationale; plans, keeps notes,",
          "delegates and integrates the specialists", "",
          "No kernel decision enters the loop:",
          "tools inform, hooks check provenance"],
         edge=BLUE, fill=BLUE_TINT, lw=1.0)
    _box(ax, 57, 79, 56, 26, "Session state",
         ["Plan (live checklist) · case notes",
          "Memory (NSCLC.md), confirmed by clinician",
          "Checkpoints and rewind",
          "Compaction near the context limit",
          "Usage meter · cancellation at every step"],
         fill="white", dashed=True, title_size=SIZE_SMALL)
    _box(ax, 54, 56, 62, 14, "Model provider (any)",
         ["Poe · MiniMax · Azure OpenAI · LiteLLM",
          "Offline mock for tests and demonstrations"], edge=BLUE)
    _arrow(ax, 85, 70, 85, 76, both=True)

    # right column: specialists and information tools
    _box(ax, 126, 125, 54, 11, "External tools (MCP, opt-in)",
         ["Streamable-HTTP servers; namespaced tools"], dashed=True)
    _box(ax, 126, 99, 54, 22, f"Specialist sub-agents ({a['Specialist sub-agents']})",
         ["Radiologist · molecular pathologist", "Thoracic surgeon · radiation oncologist",
          "Medical oncologist · pharmacist · evidence", "Decide within their remit; concurrent"],
         edge=BLUE, fill=BLUE_TINT)
    _box(ax, 126, 70, 54, 25, f"Information tools ({n_info})",
         ["Trial registry · regimen library", "Library reference doses · pathways",
          "Guideline KB · PubMed · citation check", "Prognosis cohorts · interactions",
          "Reading images and reports"])
    _note(ax, 126, 56, 54, 11, [f"Decision tools ({n_kernel}), kernel-assisted mode only:",
                                "staging · biomarkers · indications · organ gates",
                                "CNS · later lines · rule review · governed reference"])
    _arrow(ax, 116, 130.5, 126, 130.5, both=True)
    _arrow(ax, 116, 110, 126, 110, both=True)
    _label(ax, 121, 112.2, "delegate")
    _arrow(ax, 116, 84, 126, 84, both=True)
    _label(ax, 121, 86.2, "call")
    _arrow(ax, 153, 99, 153, 95)
    _label(ax, 154.5, 97, "read-only allow-list", ha="left")

    # runtime -> kernel: after the consult only
    _arrow(ax, 85, 56, 85, 44.5, color=VERMILLION, lw=0.9)
    _label(ax, 87, 49.8, "Submitted consult → independent audit afterwards",
           ha="left", size=SIZE_SMALL)
    _label(ax, 87, 46.9, "(for the clinician and for evaluation; never returned to the model)",
           ha="left", size=5)

    # -------------------------------------------------------------- kernel
    _band(ax, 14, 44.5, "Deterministic clinical kernel: outside the decision loop", band)
    _box(ax, 4, 16, 30, 23, "Staging engine",
         ["AJCC/UICC 9th edition", "Reference stage group", "Refuses ambiguous input",
          "Edition-migration notes"])
    _box(ax, 37, 16, 36, 23, "Knowledge",
         [f"Trial registry ({a['Trials (registry)']} trials)",
          f"Regimen library ({a['Regimens (library)']})",
          f"Indication predicates ({a['Indication predicates']})",
          f"Guideline KB ({kn['kg_total']:,} items)"])
    _box(ax, 76, 16, 35, 23, "Clinical modules",
         ["Biomarker categories", "Metastatic sites → M", "Organ-function gates",
          "CNS · later lines · prognosis"])
    _box(ax, 114, 16, 29, 23, "Safety",
         ["Emergency screen", f"Rule engine ({a['Safety rules']})", "Claim and citation",
          "checks"])
    _box(ax, 146, 27.5, 34, 11.5, "Independent audit",
         ["/audit · evaluation harness"], edge=VERMILLION)
    _box(ax, 146, 16, 34, 9.5, "Other modes", ["Governed · kernel-assisted"], dashed=True)

    # ---------------------------------------------------------- evaluation
    _band(ax, 1.5, 12, "Evaluation", band)
    kinds = gs["kinds"]
    ax.text(25, 9.6, f"Reference standard: {a['Gold-standard cases']} gold-standard cases "
                     f"({kinds.get('pipeline', 0)} end-to-end, {kinds.get('audit', 0)} audit "
                     f"probes) · defect injection: {len(pt['detection'])} defect classes read "
                     f"by {len(pt['configs'])} configurations", fontsize=SIZE_SMALL, va="top")
    ax.text(25, 6.2, f"{kn['tests']} automated tests · in-browser (WebAssembly) smoke "
                     f"test · browser end-to-end tests of both interfaces and both languages",
            fontsize=SIZE_SMALL, va="top")
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
    "EGFR_III_CONSOLIDATION": "EGFR stage III consolidation",
    "TRIAL_EDITION_MIGRATION": "Trial edition migration",
    "EMERGENCY_NOT_ADDRESSED": "Emergency not addressed",
    "INDICATION_PREDICATE": "Indication predicate", "TRIAL_STAGE_BOUNDARY": "Trial stage boundary",
    "STAGE0_NO_SYSTEMIC": "Stage 0: no systemic therapy",
    "DRIVER_FIRST_LINE": "Driver-directed first line",
    "CNS_UNTREATED_SYMPTOMATIC": "Untreated symptomatic CNS",
    "PROGRESSION_SAME_DRUG": "Progression on same drug",
}


DEFECT_ORDER = ["Fabricated citation", "Dose without library lookup", "Dose addressed to a patient",
                "Emergency not addressed", "Stage differs from engine", "Wrong biomarker category",
                "Regimen outside its declared population",
                "Immunotherapy despite actionable driver"]
CONFIG_SHORT = {"full": "Full autonomy:\nin-loop hooks", "audit": "Independent\naudit (after)",
                "assisted": "Kernel-assisted:\nin-loop hooks"}


def fig3(report: list) -> None:
    pt = load("perturbation")
    det = pt["detection"]
    kinds = [k for k in DEFECT_ORDER if k in det]
    configs = pt["configs"]
    fig = figure("double", 82)
    rows = kinds + ["Clean consult (any finding)"]
    y = np.arange(len(rows))[::-1].astype(float)
    y[-1] -= 0.6
    ylim = (y[-1] - 0.7, y[0] + 0.7)

    # ---- a: detection by configuration (heatmap with k/n)
    ax = axes_mm(fig, 58, 13, 54, 58)
    panel_label(fig, 1.5, 1.5, "a")
    for j, c in enumerate(configs):
        for yi, k in zip(y, rows):
            if k in det:
                v = det[k][c]
                fill = SEQ(v["p"]) if v["p"] else "white"
                text_colour = "white" if v["p"] > 0.55 else BLACK
            else:
                v = pt["clean"][c]
                fill = "#EEEEEE" if v["k"] else "white"
                text_colour = BLACK
            ax.add_patch(mpatches.Rectangle((j, yi - 0.42), 1, 0.84, facecolor=fill,
                                            edgecolor="#E6E6E6", linewidth=0.4))
            ax.text(j + 0.5, yi, f"{v['k']}/{v['n']}", ha="center", va="center", fontsize=5.5,
                    color=text_colour)
    ax.axhline(y[-1] + 0.8, color=GREY, lw=0.4, ls=(0, (2, 2)), xmin=0, xmax=1)
    ax.set_xlim(0, len(configs))
    ax.set_ylim(*ylim)
    ax.set_yticks(y, rows)
    ax.set_xticks(np.arange(len(configs)) + 0.5, [CONFIG_SHORT[c] for c in configs])
    ax.xaxis.tick_top()
    ax.tick_params(length=0, pad=2)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel("Consults raising the expected finding (k/n)")
    cax = axes_mm(fig, 114, 30, 2, 24)
    sm = matplotlib.cm.ScalarMappable(cmap=SEQ, norm=matplotlib.colors.Normalize(0, 1))
    cb = fig.colorbar(sm, cax=cax)
    cb.outline.set_linewidth(0.4)
    cb.ax.tick_params(width=0.4, length=2)
    cb.set_ticks([0, 0.5, 1], labels=["0", "0.5", "1"])
    cb.set_label("Proportion", fontsize=SIZE_SMALL, labelpad=2)

    # ---- b: where each defect is caught in full autonomy
    ax = axes_mm(fig, 134, 13, 44, 58)
    panel_label(fig, 124, 1.5, "b")
    for yi, k in zip(y, rows):
        if k not in det:
            continue
        sp = det[k]["split"]
        left = 0.0
        for part, colour in (("in_loop", BLUE), ("audit_only", VERMILLION), ("neither", LIGHT_GREY)):
            w = sp[part] / sp["n"]
            if w:
                ax.barh(yi, w, left=left, height=0.62, color=colour, edgecolor="white",
                        linewidth=0.4)
            left += w
    ax.set_xlim(0, 1)
    ax.set_ylim(*ylim)
    ax.set_yticks([])
    ax.set_xticks([0, 0.5, 1], ["0", "0.5", "1"])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Proportion of consults")
    key = axes_mm(fig, 134, 71.5, 44, 9.5)
    key.set_axis_off()
    key.set_xlim(0, 44)
    key.set_ylim(0, 9.5)
    for i, (colour, label) in enumerate(((BLUE, "Full autonomy: caught in the loop"),
                                         (VERMILLION, "Caught only by the audit"),
                                         (LIGHT_GREY, "Caught by neither"))):
        key.add_patch(mpatches.Rectangle((0, 7.0 - i * 3.0), 2.4, 1.9, facecolor=colour,
                                         edgecolor="none"))
        key.text(3.4, 7.95 - i * 3.0, label, fontsize=SIZE_SMALL, va="center")
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


# =============================================================== Figure 5
SHORT = {
    "osimertinib_adjuvant": "Osimertinib (ADAURA)", "alectinib_adjuvant": "Alectinib (ALINA)",
    "nivo_chemo_neoadjuvant": "Nivolumab–chemo (CM816)",
    "pembro_perioperative": "Pembrolizumab (KN-671)", "durva_perioperative": "Durvalumab (AEGEAN)",
    "nivo_perioperative": "Nivolumab (CM77T)", "adjuvant_platinum_doublet": "Cisplatin doublet (LACE)",
    "atezolizumab_adjuvant": "Atezolizumab (IMpower010)", "pembro_adjuvant": "Pembrolizumab (KN-091)",
    "ccrt_60gy": "Concurrent CRT (RTOG 0617)", "durva_consolidation": "Durvalumab (PACIFIC)",
    "osimertinib_consolidation": "Osimertinib (LAURA)", "osimertinib_first_line": "Osimertinib (FLAURA)",
    "osimertinib_chemo_first_line": "Osimertinib–chemo (FLAURA2)",
    "amivantamab_lazertinib": "Amivantamab–lazertinib", "lorlatinib_first_line": "Lorlatinib (CROWN)",
    "pembro_monotherapy": "Pembrolizumab (KN-024)",
    "pembro_pemetrexed_platinum": "Pembro–pemetrexed (KN-189)",
    "pembro_carbo_taxane": "Pembro–taxane (KN-407)", "nivo_ipi_chemo": "Nivo–ipi–chemo (CM9LA)",
    "sbrt_definitive": "SBRT, stage I", "sbrt_oligomet_lct": "Oligometastatic LCT",
    "amivantamab_chemo_first_line": "Amivantamab–chemo (PAPILLON)",
    "afatinib_uncommon_first_line": "Afatinib (uncommon EGFR)",
    "repotrectinib_first_line": "Repotrectinib (ROS1)", "selpercatinib_first_line": "Selpercatinib (RET)",
    "capmatinib_first_line": "Capmatinib (MET ex14)",
    "dabrafenib_trametinib_first_line": "Dabrafenib–trametinib",
    "larotrectinib_first_line": "Larotrectinib (NTRK)", "tdxd_subsequent_line": "T-DXd (HER2)",
    "amivantamab_chemo_subsequent": "Amivantamab (MARIPOSA-2)",
    "platinum_pemetrexed_post_tki": "Chemotherapy after TKI",
    "lorlatinib_post_second_gen": "Lorlatinib after ALK TKI",
    "docetaxel_ramucirumab_second_line": "Docetaxel–ramucirumab",
    "docetaxel_second_line": "Docetaxel", "osimertinib_t790m_subsequent": "Osimertinib (T790M)",
    "platinum_etoposide_transformation": "Platinum–etoposide (SCLC)",
    "tepotinib_osimertinib_met_amp": "Tepotinib–osimertinib", "dato_dxd_egfr_subsequent": "Dato-DXd (EGFR)",
    "sotorasib_subsequent_line": "Sotorasib (KRAS G12C)",
}
SETTING_GROUP = {"adjuvant": "Perioperative", "neoadjuvant": "Perioperative",
                 "perioperative": "Perioperative", "definitive_crt": "Definitive",
                 "consolidation": "Definitive", "definitive_rt": "Definitive",
                 "local_consolidative": "Definitive", "first_line": "First line (stage IV)",
                 "subsequent": "Later lines"}
CONDITION_ROWS = [("Stage", ("stage",)), ("Resectable or operable", ("resectability", "operability")),
                  ("Histology", ("histology",)), ("Driver required", ("driver",)),
                  ("No actionable driver", ("no_actionable_driver",)),
                  ("PD-L1", ("pd_l1_tc", "pd_l1_tps")),
                  ("Prior therapy", ("prior_therapy", "prior_agents", "prior_platinum")),
                  ("Disease extent", ("disease_extent",))]
FINDING_SHORT = {"rule_review": "rule engine", "stage_consistency": "stage consistency",
                 "citation_provenance": "citation provenance", "dose_provenance": "dose provenance",
                 "emergency_addressed": "emergency first"}


def _lines(ax, x, y, lines, *, size=SIZE_SMALL, step=3.0, **kw):
    for i, line in enumerate(lines):
        ax.text(x, y - i * step, line, fontsize=size, va="top", ha="left", **kw)


def fig5(report: list) -> None:
    import textwrap

    cs = load("case_study")
    H = 168
    fig = figure("double", H)
    ax = axes_mm(fig, 0, 0, 183, H)
    ax.set_xlim(0, 183)
    ax.set_ylim(0, H)
    ax.set_axis_off()
    notes = cs["notes"]
    gov = cs["governed"]
    revised, draft = cs["revised"], cs["draft"]
    audit_ref = cs["audit"]["revised"]["reference"]

    # ---- a: the case (the clinician's report, summarised)
    panel_label(fig, 1.5, 1.5, "a")
    _box(ax, 4, 109, 70, 54, "Case: stage IVB, third line",
         [f"{notes['age']}-year-old man, 40 pack-years, ECOG {notes['ecog_ps']}",
          "Right upper lobe mass invading the mediastinal pleura;",
          "multistation mediastinal nodes; left adrenal, multiple",
          "bone and a single brain metastasis; adenocarcinoma",
          "KRAS G12C; EGFR, ALK, ROS1, RET, MET exon 14 and",
          f"BRAF V600E negative; PD-L1 TPS {notes['pd_l1']['tps']}%",
          "Line 1 pembrolizumab–pemetrexed–carboplatin: mixed",
          "response, new liver metastases; brain lesion after SRS",
          "Re-biopsy PD-L1 10%; ctDNA: KRAS G12C up, MET amplified",
          "Line 2 KRAS G12C inhibitor: primary and pleura progress,",
          "malignant pleural effusion",
          "Question: which next step?"])

    # ---- b: timed trace
    panel_label(fig, 78, 1.5, "b")
    rows = {"lead": 3, "radiology": 2, "medical_oncology": 1, "evidence": 0}
    tx = axes_mm(fig, 104, 117, 74, 37)
    spans = cs["spans"]
    for sp in spans:
        y = rows.get(sp["agent"])
        if y is None:
            continue
        if sp["kind"] == "model":
            tx.add_patch(mpatches.Rectangle((sp["start"], y - 0.2), sp["end"] - sp["start"], 0.4,
                                            facecolor=BLUE, edgecolor="white", linewidth=0.4))
        elif sp["name"] != "delegate":
            tx.plot([sp["end"]] * 2, [y - 0.3, y + 0.3], color=BLACK, lw=0.6)
    delegates = [sp for sp in spans if sp.get("name") == "delegate"]
    d0, d1 = min(sp["start"] for sp in delegates), max(sp["end"] for sp in delegates)
    tx.add_patch(mpatches.Rectangle((d0, 2.88), d1 - d0, 0.24, facecolor=GREY, edgecolor="none"))
    tx.text((d0 + d1) / 2, 3.75, f"delegate × {len(delegates)} (concurrent)", fontsize=5.5,
            ha="center", va="bottom")
    model_ends = sorted(sp["end"] for sp in spans if sp["agent"] == "lead" and sp["kind"] == "model")

    def tool_label(t0, text):
        batch = [sp for sp in spans if sp["agent"] == "lead" and sp["kind"] == "tool"
                 and sp["name"] != "update_plan" and t0 <= sp["end"] <= t0 + 0.05]
        if batch:
            tx.text(max(sp["end"] for sp in batch), 3.42, text, fontsize=5.5, ha="center",
                    va="bottom")

    tool_label(model_ends[0], "notes · prognosis")
    tool_label(model_ends[3], "dose look-up · citation check")
    names = {"radiology": "guideline search", "medical_oncology": "regimen search",
             "evidence": "trial search"}
    for agent, label in names.items():
        sp = next((x for x in spans if x["agent"] == agent and x["kind"] == "tool"), None)
        if sp:
            tx.text(sp["end"], rows[agent] - 0.36, label, fontsize=5.5, ha="center", va="top")
    for r in cs["reviews"]:
        tx.plot([r["t"]], [3], marker="D", ms=3.2, color=VERMILLION, mec="white", mew=0.4,
                zorder=5, clip_on=False)
        n = len(r["findings"])
        tx.text(r["t"], 2.7, f"hooks: {n} finding{'s' if n != 1 else ''}" if n else
                "hooks: none", fontsize=5.5, ha="right", va="top")
    tx.set_yticks([3, 2, 1, 0], ["Lead agent", "Radiologist", "Medical oncologist",
                                 "Evidence researcher"])
    tx.tick_params(axis="y", length=0)
    tx.spines["left"].set_visible(False)
    tx.set_xlim(0, cs["total_s"] + 0.3)
    tx.set_ylim(-1.0, 4.2)
    tx.spines["bottom"].set_bounds(0, int(cs["total_s"]))
    tx.set_xticks(range(0, int(cs["total_s"]) + 1))
    tx.set_xlabel("Time from the clinician's message (s)")
    key = axes_mm(fig, 84, 157.5, 94, 4)
    key.set_axis_off()
    key.set_xlim(0, 94)
    key.set_ylim(0, 4)
    key.add_patch(mpatches.Rectangle((0, 1), 4, 2, facecolor=BLUE, edgecolor="none"))
    key.text(5.2, 2, f"Model call ({cs['latency_s']:g} s, simulated)", fontsize=SIZE_SMALL,
             va="center")
    key.plot([40.5, 40.5], [0.6, 3.4], color=BLACK, lw=0.6)
    key.text(42, 2, "Tool call", fontsize=SIZE_SMALL, va="center")
    key.add_patch(mpatches.Rectangle((56, 1.4), 4, 1.2, facecolor=GREY, edgecolor="none"))
    key.text(61.2, 2, "Delegation", fontsize=SIZE_SMALL, va="center")
    key.plot([77.5], [2], marker="D", ms=3.2, color=VERMILLION, mec="white", mew=0.4)
    key.text(79.5, 2, "Stop hooks", fontsize=SIZE_SMALL, va="center")

    # ---- c: the review loop (provenance hooks in the loop)
    panel_label(fig, 1.5, 61, "c")
    first, second = cs["reviews"][0], cs["reviews"][-1]

    def decisions(consult):
        return [f"Stage {consult['stage_group']} · {', '.join(consult['biomarker_category'])} · "
                f"{consult['intent']}"]

    def opts(consult):
        out = []
        for i, o in enumerate(consult["options"]):
            ev = ", ".join(o.get("evidence") or [])
            out += textwrap.wrap(f"{i + 1}. {o['name']}" + (f" ({ev})" if ev else ""), 36)
        return out

    _box(ax, 4, 58, 50, 45, "Draft consult (round 1)",
         decisions(draft) + opts(draft) + ["", "Doses quoted from memory;",
                                           "a trial that does not exist"], edge=VERMILLION)
    _box(ax, 62, 58, 64, 45, f"In-loop hook findings ({len(first['findings'])})", [],
         fill=GREY_TINT)
    fy = 96.4
    for f in first["findings"]:
        colour = VERMILLION if f["severity"] == "block" else ORANGE
        ax.add_patch(mpatches.Rectangle((64, fy - 2.2), 2.2, 2.2, facecolor=colour,
                                        edgecolor="none"))
        ax.text(67.4, fy, f"{FINDING_LABEL.get(f['rule_id'], f['rule_id'])} "
                          f"({f['severity']}; {FINDING_SHORT.get(f['hook'], f['hook'])})",
                fontsize=SIZE_SMALL, fontweight="bold", va="top")
        wrapped = textwrap.wrap(f["message"], 58)
        _lines(ax, 67.4, fy - 3.0, wrapped, size=5.5, step=2.6)
        fy -= 3.0 + 2.6 * len(wrapped) + 1.8
    _box(ax, 134, 58, 46, 45, "Revised consult (round 2)",
         decisions(revised) + opts(revised)
         + ["", "Doses deferred to the library and", "the local protocol",
            f"Responses: {len(revised.get('rule_responses') or [])} findings accepted",
            "Hooks on resubmission: "
            + (f"{len(second['findings'])} findings" if second["findings"] else "none")],
         edge=BLUE)
    _arrow(ax, 54, 81, 62, 81)
    _label(ax, 58, 82.6, "submit")
    _arrow(ax, 126, 81, 134, 81)
    _label(ax, 130, 82.6, "revise")

    # ---- d: the model's decisions next to the independent audit
    panel_label(fig, 1.5, 113, "d")
    eligible = [r["regimen_id"] for r in cs["indications"] if r["verdict"] == "eligible"]
    picked = [r for o in revised["options"] for r in o.get("regimen_ids") or []]
    stage_ref = audit_ref["stage"] or {}
    m_ref = audit_ref.get("suggested_m") or {}
    cat_ref = audit_ref.get("biomarker_category") or {}
    gate_names = (("bleeding", "bleeding-risk screen"), ("blood count", "blood count"),
                  ("hepatic", "liver function"), ("renal", "renal function"), ("LVEF", "LVEF"))
    gates = [next((name for key, name in gate_names if key in g), g.split(" — ")[0])
             for g in cs["organ_gates_pending"]]
    audit_draft = [f["rule_id"] for f in cs["audit"]["draft"]["findings"]]
    audit_final = [f["rule_id"] for f in cs["audit"]["revised"]["findings"]]
    table = [
        ("Stage group", f"{revised['stage_group']} ({revised['tnm']})",
         f"Staging engine: {stage_ref.get('stage_group')} ({stage_ref.get('tnm')})",
         "Agrees" if stage_ref.get("stage_group") == revised["stage_group"] else "Differs"),
        ("M category", "M1c; M1c2 by sites (rationale)",
         f"Sites on record: {m_ref.get('m')} ({len(gov['metastatic_sites']['sites'])} sites, "
         f"incl. brain)", "Agrees (M1c family)"),
        ("Biomarker category", ", ".join(revised["biomarker_category"]),
         f"Classifier: {', '.join(cat_ref.get('codes') or [])}; untested: "
         f"{', '.join(m.split(' ')[0] for m in cat_ref.get('missing') or [])}",
         "Agrees" if set(cat_ref.get("codes") or []) == set(revised["biomarker_category"])
         else "Differs"),
        ("Treatment intent", revised["intent"].capitalize(),
         f"Governed pipeline: {gov['intent']}", "Agrees" if gov["intent"] == revised["intent"]
         else "Differs"),
        ("Regimens", "Docetaxel–ramucirumab; docetaxel",
         f"Governed: same; {len(eligible)} of {len(cs['indications'])} library regimens eligible",
         "Agrees" if set(picked) == set(gov["regimen_ids"]) and set(picked) <= set(eligible)
         else "Differs"),
        ("Before dosing", "Blood count, liver function, bleeding",
         "Organ gates still open: " + ", ".join(gates), "For the clinician"),
        ("Audit findings", "—",
         f"Draft: {', '.join(audit_draft) or 'none'}; revision: {', '.join(audit_final) or 'none'}",
         "Clean"),
    ]
    cols = [(4, "Decision"), (30, "Model (revised consult)"),
            (76, "Kernel's independent reading (after the consult)"), (158, "")]
    top = 50.5
    ax.plot([4, 180], [top + 1.6] * 2, color=BLACK, lw=0.6)
    for x, head in cols:
        ax.text(x, top, head, fontsize=SIZE_SMALL, fontweight="bold", va="center")
    ax.plot([4, 180], [top - 1.8] * 2, color=BLACK, lw=0.4)
    for i, (name, model, kernel, verdict) in enumerate(table):
        yy = top - 5.2 - i * 6.0
        ax.text(4, yy, name, fontsize=SIZE_SMALL, va="center")
        ax.text(30, yy, model, fontsize=SIZE_SMALL, va="center")
        for k, line in enumerate(textwrap.wrap(kernel, 62)[:2]):
            ax.text(76, yy + (1.4 if len(textwrap.wrap(kernel, 62)) > 1 else 0) - 2.8 * k, line,
                    fontsize=SIZE_SMALL, va="center")
        colour = BLUE if verdict.startswith(("Agrees", "Clean")) else GREY
        ax.add_patch(mpatches.Circle((159.2, yy), 0.9, facecolor=colour, edgecolor="none"))
        ax.text(161.2, yy, verdict, fontsize=SIZE_SMALL, va="center")
    ax.plot([4, 180], [top - 5.2 - (len(table) - 1) * 6.0 - 3.6] * 2, color=BLACK, lw=0.6)
    report.append(save(fig, "Fig5", OUT))


# ===================================================== Extended Data Fig. 4
def ed_fig4(report: list) -> None:
    from nsclc_agent.knowledge import regimens as regimen_lib

    cs = load("case_study")
    revised, draft = cs["revised"], cs["draft"]
    fig = figure("double", 72)
    by_id = {r["regimen_id"]: r for r in cs["indications"]}
    groups = ["Perioperative", "Definitive", "First line (stage IV)", "Later lines"]
    order = sorted(by_id, key=lambda rid: (groups.index(SETTING_GROUP[
        regimen_lib.get(rid).setting]), list(by_id).index(rid)))
    mx = axes_mm(fig, 36, 26, 124, 28)
    n_rows = len(CONDITION_ROWS) + 1
    rank = {"not_met": 3, "unknown": 2, "met": 1}
    for i, (label, keys) in enumerate(CONDITION_ROWS):
        y = n_rows - 2 - i
        count = 0
        for j, rid in enumerate(order):
            cond = [c for c in by_id[rid]["conditions"] if c["condition"] in keys]
            state = max((c["verdict"] for c in cond), key=lambda v: rank.get(v, 0),
                        default=None)
            fill = {"met": BLUE, "not_met": VERMILLION, "unknown": LIGHT_GREY}.get(state, "white")
            count += state == "not_met"
            mx.add_patch(mpatches.Rectangle((j, y), 1, 1, facecolor=fill,
                                            edgecolor="white" if state else "#E6E6E6",
                                            linewidth=0.5))
        mx.text(len(order) + 0.8, y + 0.5, str(count), fontsize=SIZE_SMALL, va="center")
    for j, rid in enumerate(order):
        eligible = by_id[rid]["verdict"] == "eligible"
        mx.add_patch(mpatches.Rectangle((j, n_rows - 1 + 0.15), 1, 0.85,
                                        facecolor=BLUE if eligible else "white",
                                        edgecolor=BLUE if eligible else "#BBBBBB",
                                        linewidth=0.5))
    proposed = {r for c in (draft, revised) for o in c["options"] for r in o.get("regimen_ids") or []}
    for j, rid in enumerate(order):
        if rid in proposed:
            mx.add_patch(mpatches.Rectangle((j, -0.05), 1, n_rows + 0.1, facecolor="none",
                                            edgecolor=BLACK, linewidth=0.8, clip_on=False))
    mx.text(len(order) + 0.8, n_rows + 0.3, "Not met (n)", fontsize=SIZE_SMALL, va="bottom")
    mx.set_xlim(0, len(order))
    mx.set_ylim(0, n_rows)
    mx.set_yticks(np.arange(n_rows) + 0.5, [l for l, _k in reversed(CONDITION_ROWS)]
                  + ["Eligible"])
    mx.set_xticks(np.arange(len(order)) + 0.5, [SHORT.get(r, r) for r in order], rotation=60,
                  ha="right", rotation_mode="anchor", fontsize=5)
    mx.tick_params(length=0, pad=1.5)
    for side in ("left", "bottom", "top", "right"):
        mx.spines[side].set_visible(False)
    start = 0
    for g in groups:
        n = sum(1 for r in order if SETTING_GROUP[regimen_lib.get(r).setting] == g)
        mx.plot([start + 0.1, start + n - 0.1], [n_rows + 0.55] * 2, color=BLACK, lw=0.6,
                clip_on=False)
        mx.text(start + n / 2, n_rows + 0.8, f"{g} ({n})", fontsize=SIZE_SMALL, ha="center",
                va="bottom")
        start += n
    key = axes_mm(fig, 36, 66.5, 140, 4)
    key.set_axis_off()
    key.set_xlim(0, 140)
    key.set_ylim(0, 4)
    x = 0.0
    for fill, edge, text, lw, width in ((BLUE, "none", "Met", 0.5, 12),
                                        (VERMILLION, "none", "Not met", 0.5, 16),
                                        (LIGHT_GREY, "none", "Not on record", 0.5, 24),
                                        ("white", "#BBBBBB", "Not a condition", 0.5, 26),
                                        ("none", BLACK, "Proposed by the model", 0.8, 34)):
        key.add_patch(mpatches.Rectangle((x, 0.7), 2.6, 2.6, facecolor=fill, edgecolor=edge,
                                         linewidth=lw))
        key.text(x + 3.6, 2.0, text, fontsize=SIZE_SMALL, va="center")
        x += width
    report.append(save(fig, "ExtendedDataFig4", OUT))


# ===================================================== Extended Data Fig. 1
def ed_fig1(report: list) -> None:
    ac = load("access")
    fig = figure("double", 72)
    ax = axes_mm(fig, 36, 29, 140, 33)
    rows = ["Lead agent"] + ac["agents"]
    kernel = np.array(ac["kernel"], dtype=bool)
    mat = np.vstack([np.ones(len(ac["tools"]))] + [np.array(r) for r in ac["matrix"]])
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            colour = GREY if i == 0 else BLUE
            y = mat.shape[0] - 1 - i
            if not mat[i, j]:
                ax.add_patch(mpatches.Rectangle((j, y), 1, 1, facecolor="white",
                                                edgecolor="#DDDDDD", linewidth=0.6))
            elif kernel[j]:
                # offered only in kernel-assisted mode: outlined, not filled
                ax.add_patch(mpatches.Rectangle((j + 0.08, y + 0.08), 0.84, 0.84,
                                                facecolor="white", edgecolor=colour,
                                                linewidth=0.8))
            else:
                ax.add_patch(mpatches.Rectangle((j, y), 1, 1, facecolor=colour,
                                                edgecolor="white", linewidth=0.6))
    ax.set_xlim(0, mat.shape[1])
    ax.set_ylim(0, mat.shape[0])
    ax.set_yticks(np.arange(mat.shape[0]) + 0.5, list(reversed(rows)))
    ax.set_xticks(np.arange(mat.shape[1]) + 0.5, ac["tool_labels"], rotation=55, ha="right",
                  rotation_mode="anchor")
    for label, is_kernel in zip(ax.get_xticklabels(), kernel):
        if is_kernel:
            label.set_color(GREY)
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    key = axes_mm(fig, 36, 64, 140, 7)
    key.set_axis_off()
    key.set_xlim(0, 140)
    key.set_ylim(0, 7)
    entries = ((0, 4.0, GREY, True, "Lead agent (also writes notes, plans, delegates, submits)"),
               (86, 4.0, BLUE, True, "Specialist read-only allow-list"),
               (0, 0.4, GREY, False, "Kernel tool: offered only in kernel-assisted mode "
                                      "(grey label); not in full autonomy"))
    for x, y, colour, filled, label in entries:
        if filled:
            key.add_patch(mpatches.Rectangle((x, y), 3, 2.6, facecolor=colour, edgecolor="none"))
        else:
            key.add_patch(mpatches.Rectangle((x + 0.2, y + 0.2), 2.6, 2.2, facecolor="white",
                                             edgecolor=colour, linewidth=0.8))
        key.text(x + 4.2, y + 1.3, label, fontsize=SIZE_SMALL, va="center")
    report.append(save(fig, "ExtendedDataFig1", OUT))


# ===================================================== Extended Data Fig. 2
TOPIC_LABEL = {"systemic_treatment": "Systemic therapy", "molecular_testing": "Molecular testing",
               "radiotherapy": "Radiotherapy", "diagnosis": "Diagnosis", "staging": "Staging",
               "surgery": "Surgery", "tcm_treatment": "Integrated TCM",
               "supportive_care": "Supportive care", "surveillance": "Surveillance",
               "other": "Other"}
SETTING_LABEL = {"first_line": "First line", "subsequent": "Later lines", "adjuvant": "Adjuvant",
                 "perioperative": "Perioperative", "consolidation": "Consolidation",
                 "neoadjuvant": "Neoadjuvant", "radiotherapy": "Radiotherapy",
                 "local_consolidative": "Local consolidative"}


def _count_heatmap(ax, counts, rows, cols, *, rotate=0):
    counts = np.asarray(counts, dtype=float)
    top = counts.max() or 1
    for i in range(counts.shape[0]):
        for j in range(counts.shape[1]):
            v = counts[i, j]
            fill = SEQ(0.08 + 0.85 * np.sqrt(v / top)) if v else "white"
            ax.add_patch(mpatches.Rectangle((j, counts.shape[0] - 1 - i), 1, 1,
                                            facecolor=fill, edgecolor="#E6E6E6", linewidth=0.4))
            if v:
                lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
                ax.text(j + 0.5, counts.shape[0] - 0.5 - i, f"{int(v):,}", ha="center",
                        va="center", fontsize=5, color="white" if lum < 0.55 else BLACK)
    ax.set_xlim(0, counts.shape[1])
    ax.set_ylim(0, counts.shape[0])
    ax.set_yticks(np.arange(counts.shape[0]) + 0.5, list(reversed(rows)))
    if rotate:
        ax.set_xticks(np.arange(counts.shape[1]) + 0.5, cols, rotation=rotate, ha="right",
                      rotation_mode="anchor")
    else:
        ax.set_xticks(np.arange(counts.shape[1]) + 0.5, cols)
    ax.tick_params(length=0)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)


def ed_fig2(report: list) -> None:
    kn = load("knowledge")
    fig = figure("double", 118)
    labels = {g["id"]: g["label"] for g in kn["guidelines"]}

    # ---- a: guideline knowledge base
    ax = axes_mm(fig, 44, 82, 52, 30)
    panel_label(fig, 1.5, 1.5, "a")
    rows = list(reversed(kn["guidelines"]))
    y = np.arange(len(rows))
    ax.barh(y, [r["recommendations"] for r in rows], height=0.62, color=GREY, linewidth=0)
    ax.set_yticks(y, [r["label"] for r in rows])
    ax.tick_params(axis="y", length=0)
    for yi, r in zip(y, rows):
        ax.text(r["recommendations"] + 12, yi, f"{r['recommendations']:,}", va="center",
                fontsize=SIZE_SMALL)
    ax.set_xlabel(f"Recommendations (n; total {kn['kg_total']:,})")
    ax.set_xlim(0, max(r["recommendations"] for r in rows) * 1.25)
    ax.set_xticks([0, 500, 1000, 1500], ["0", "500", "1,000", "1,500"])
    ax.spines["bottom"].set_bounds(0, 1500)

    # ---- b: kernel inventory
    ax = axes_mm(fig, 146, 82, 30, 30)
    panel_label(fig, 108, 1.5, "b")
    assets = sorted(kn["assets"].items(), key=lambda kv: kv[1])
    y = np.arange(len(assets))
    ax.hlines(y, 0, [v for _k, v in assets], color=GREY, lw=0.8)
    ax.plot([v for _k, v in assets], y, "o", ms=3, color=BLUE, mec="none")
    ax.set_yticks(y, [k for k, _v in assets])
    ax.tick_params(axis="y", length=0)
    for yi, (_k, v) in zip(y, assets):
        ax.text(v + 3, yi, str(v), va="center", fontsize=SIZE_SMALL)
    ax.set_xlim(0, max(v for _k, v in assets) * 1.3)
    ax.set_xlabel("Count (n)")

    # ---- c: topic x guideline
    tm = kn["topic_matrix"]
    ax = axes_mm(fig, 44, 22, 64, 34)
    panel_label(fig, 1.5, 56, "c")
    _count_heatmap(ax, tm["counts"], [labels.get(g, g) for g in tm["guidelines"]],
                   [TOPIC_LABEL.get(t, t) for t in tm["topics"]], rotate=50)
    ax.set_title("Recommendations by topic", fontsize=SIZE_TEXT, pad=4)

    # ---- d: trial registry coverage
    tc = kn["trial_coverage"]
    ax = axes_mm(fig, 136, 22, 44, 34)
    panel_label(fig, 112, 56, "d")
    _count_heatmap(ax, tc["counts"], [SETTING_LABEL.get(s, s) for s in tc["settings"]],
                   tc["stages"], rotate=50)
    ax.set_title(f"Registry trials by setting and stage (n = {tc['trials']})",
                 fontsize=SIZE_TEXT, pad=4)
    report.append(save(fig, "ExtendedDataFig2", OUT))


# ===================================================== Extended Data Fig. 3
STATE_LABEL = {
    "emergency_action_plan": "Emergency action plan",
    "needs_staging_workup": "Needs staging work-up",
    "needs_more_information": "Needs more information",
    "insufficient_evidence": "Insufficient evidence",
    "blocked": "Blocked",
    "draft_for_tumor_board": "Draft for tumour board",
    "approved_by_tumor_board": "Approved by tumour board",
    "treatment_recommendation": "Treatment recommendation",
    "failed_closed": "Failed closed",
}


def ed_fig3(report: list) -> None:
    gs = load("gold_standard")
    kn = load("knowledge")
    n = gs["releases"]
    total = sum(n.values())
    H = 124
    fig = figure("double", H)
    ax = axes_mm(fig, 0, 0, 183, H)
    ax.set_xlim(0, 183)
    ax.set_ylim(0, H)
    ax.set_axis_off()

    def state(x, y, key, kind, note="", *, w=58, dashed=False):
        edge = {"release": BLUE, "stop": VERMILLION, "info": GREY}[kind]
        fill = BLUE_TINT if kind == "release" else "white"
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, 9, boxstyle="round,pad=0,rounding_size=1.2",
                                             linewidth=0.8, edgecolor=edge, facecolor=fill,
                                             linestyle=(0, (3, 2)) if dashed else "solid"))
        ax.text(x + 2, y + 6.6, STATE_LABEL[key], fontsize=SIZE_TEXT, fontweight="bold",
                va="top")
        ax.text(x + w - 2, y + 6.6, f"{n.get(key, 0)}/{total}", fontsize=SIZE_SMALL, va="top",
                ha="right")
        if note:
            ax.text(x + 2, y + 3.0, note, fontsize=5.5, va="top")

    gates = [
        (108, 10, "Intake · emergency screen", "every message, bilingual"),
        (90, 10, "Staging engine", "AJCC/UICC 9th edition"),
        (72, 10, "Interview axes", f"{kn['assets']['Interview axes']} decision-relevant axes"),
        (54, 10, "Treatment plan", "deterministic, evidence-bound"),
        (31, 16, "Critic", f"{kn['assets']['Safety rules']} rules · claim and citation checks"),
        (14, 10, "Dose channel", "oncologist opt-in; every gate passes"),
    ]
    gx, gw = 44, 56
    for y, gh, title, note in gates:
        _box(ax, gx, y, gw, gh, title, [note], title_size=SIZE_TEXT)
    for (y0, *_), (y1, h1, *_) in zip(gates, gates[1:]):
        _arrow(ax, gx + gw / 2, y0, gx + gw / 2, y1 + h1)
        _label(ax, gx + gw / 2 + 1.2, (y0 + y1 + h1) / 2, "pass", ha="left")
    _label(ax, gx + gw / 2, 123, "Case", ha="center", va="top", size=SIZE_TEXT)
    _arrow(ax, gx + gw / 2, 120, gx + gw / 2, 118)

    # exits
    sx = 120
    exits = [  # (arrow y, state key, kind, condition, note)
        (113, "emergency_action_plan", "stop", "hard signal", "fixed action script, nothing else"),
        (95, "needs_staging_workup", "info", "TNM refused",
         "work-up plan ranked by value of information"),
        (77, "needs_more_information", "info", "required axis open",
         "questions; also when the plan rests on unconfirmed facts"),
        (44, "blocked", "stop", "block violation", "after the bounded repair loop"),
        (33.5, "insufficient_evidence", "info", "not evidence-bound",
         "unverified citation or unanchored number"),
        (19, "draft_for_tumor_board", "release", "dose-bearing draft",
         "awaits sign-off by the tumour board"),
    ]
    for y, key, kind, cond, note in exits:
        state(sx, y - 4.5, key, kind, note)
        _arrow(ax, gx + gw, y, sx, y)
        _label(ax, (gx + gw + sx) / 2, y + 1.6, cond)
    state(sx, 1, "approved_by_tumor_board", "release", "signed outside the system", dashed=True)
    _arrow(ax, sx + 29, 14.5, sx + 29, 10)
    # release without dose content
    state(gx, 1, "treatment_recommendation", "release", "stage-appropriate, no dose content",
          w=gw)
    _arrow(ax, gx + gw / 2, 14, gx + gw / 2, 10)
    _label(ax, gx + gw / 2 + 1.2, 12, "dose channel closed", ha="left")
    # repair loop (critic -> plan)
    ax.plot([gx, gx - 6, gx - 6], [39, 39, 59], color=BLACK, lw=0.7)
    _arrow(ax, gx - 6, 59, gx, 59)
    _label(ax, gx - 7.2, 50, "repair\n(bounded)", ha="right", size=5.5)
    # failed closed: from any step
    state(4, 96, "failed_closed", "stop", "", w=30)
    ax.text(6, 93.6, "From any step: an exception or a", fontsize=5.5, va="top")
    ax.text(6, 91.0, "replay divergence. Never resumed", fontsize=5.5, va="top")
    ax.text(6, 88.4, "past a safety decision.", fontsize=5.5, va="top")
    # key
    for i, (edge, fill, text) in enumerate(((BLUE, BLUE_TINT, "Release with a plan"),
                                            (VERMILLION, "white", "Safety stop"),
                                            (GREY, "white", "More information needed"))):
        _key(ax, 4, 74 - i * 5, edge, fill, text)
    ax.text(4, 56, f"Counts: gold-standard\npipeline cases (n = {total})", fontsize=SIZE_SMALL,
            va="top")
    report.append(save(fig, "ExtendedDataFig3", OUT))


if __name__ == "__main__":
    font = apply_style()
    print(f"font: {font}")
    out: list = []
    for build in (fig1, fig2, fig3, fig4, fig5, ed_fig1, ed_fig2, ed_fig3, ed_fig4):
        build(out)
    for r in out:
        print(f"{r['name']}: {r['width_mm']} x {r['height_mm']} mm  font={r['font']}  "
              f"warnings={r['warnings'] or 'none'}")
