# Paper display items (Nature Portfolio format)

Figures and tables for the NSCLC-Agent paper. Every number shown is computed by
running this repository. Nothing is typed in by hand.

```bash
pip install matplotlib python-docx pdfplumber pypdf pillow
python paper/analysis.py      # experiments → paper/data/*.json   (~3 min; or name steps)
python paper/make_figures.py  # → paper/figures/*.pdf|svg|png   (183 mm, final size)
python paper/make_tables.py   # → paper/tables/*.docx|tex|md    (three-line, editable)
python paper/qc.py            # Nature display-item QC → paper/QC.md
```

**Decision mode.** The agent-runtime figures (Fig. 1, 3, 4, 5, Extended Data
Fig. 1) describe the kernel-assisted mode (`autonomy: "assisted"`), in which the
deterministic kernel is offered as tools and advisory hooks. Since v1.3.0 the
product default is full autonomy: the model decides the stage, the treatment
intent and the plan, and the kernel's decision tools and kernel-comparison
hooks are off. `analysis.py` pins the assisted mode so the numbers reproduce.

## Argument, one message per figure

| Item | Message | Data |
|---|---|---|
| **Fig. 1** | What the system is: a model-led agent runtime on a deterministic clinical kernel | Full framework: interfaces, agent runtime (lead, specialists, tools, hooks, session, providers), kernel, evaluation; counts read from the code |
| **Fig. 2** | Stage is computed, never generated; ambiguity is refused unless it cannot change the stage | AJCC/UICC 9th-edition T×N×M matrix; gold-standard accuracy with exact 95% CIs (70 cases) |
| **Fig. 3** | Advisory hooks cover the defect space of agent consults | Perturbation study: 35 released plans × 7 injected defect classes; clean baseline 2/35 |
| **Fig. 4** | The runtime scales and stays bounded | Wall clock vs number of specialists (serial vs concurrent); prompt size with and without compaction |
| **Fig. 5** | A worked consult: the hooks catch real errors and the model revises | Unresectable IIIB EGFR L858R: timed MDT trace, draft → 3 findings → revision with none; all 40 library regimens checked against the case |
| **Extended Data Fig. 1** | Each specialist reads only what it needs | Lead and specialist × tool access matrix |
| **Extended Data Fig. 2** | What the kernel knows | Guideline KB by source and topic; trial registry by setting and stage; kernel inventory |
| **Extended Data Fig. 3** | How governed mode decides what may be released | Release state machine with the gold-standard count in each state |
| **Table 1** | The runtime, mapped to mainstream agent-runtime patterns | — |
| **Table 2** | The hooks that hold the clinical safety nets | — |
| **Extended Data Table 1** | Gold-standard evaluation | 70 cases, Clopper–Pearson CIs |
| **Extended Data Table 2** | Bilingual emergency screen | 25-phrase battery (positive, negated, third-party, hypothetical) |
| **Extended Data Table 3** | The deterministic safety rules | 20 rules, 23 finding identifiers, severities, audit probes raising each |

Legends are in [`legends.md`](legends.md) (each under 300 words, with n, the
interval definition and the method stated). The QC report is in [`QC.md`](QC.md).

## Format

The figures follow the Nature Portfolio requirements:

- **Size.** 183 mm double column, height ≤ 170 mm.
- **Fonts.** Text is 5–7 pt, measured from the PDF text operators. Panel labels are 8 pt bold lower case.
- **Lines.** Strokes are 0.4–1 pt.
- **Background.** White, with no gridlines, shadows or 3D effects.
- **Colour.** Okabe–Ito colour-blind-safe palette; no red–green pairs; no rainbow scales.
- **Files.** Vector PDF/SVG with live (TrueType) text, plus a 600-dpi RGB PNG preview.

The tables are editable Word files with horizontal rules only and footnotes keyed by symbols.

**Font.** The scripts use Arial when it is installed. This build environment has
no Arial, so these files use Liberation Sans, which has identical metrics. Before
submitting, re-run `make_figures.py` on a machine with Arial. `qc.py` flags the
substitution as a warning.

## Caveats (state them in the paper)

- **Gold-standard set.** The cases were written by the developers. Dual
  clinician adjudication has not been done yet (70/70 cases are unadjudicated).
- **Perturbation study (Fig. 3).** The defects were constructed by the authors.
  The study measures how much of the defect space the hooks cover, not how often
  real models make these errors.
- **Runtime scaling (Fig. 4a).** Model latency is simulated at 250 ms per call;
  the tools are real. Concurrency is capped at six worker threads.
- **Case study (Fig. 5).** The model's decisions are scripted by the authors to
  reproduce a common error (8th-edition stage; PACIFIC consolidation in EGFR-mutant
  disease). Tools, sub-agents, hooks and the review loop are the real runtime. Model
  latency is simulated at 0.6 s per call. The figure shows what the runtime does with
  such an error; it does not show how often a real model makes it.
- **Context study (Fig. 4b).** Uses the offline mock model. Token counts are
  provider-neutral estimates.
- **Model quality.** Agent-mode clinical quality with real models has not been
  evaluated yet.
