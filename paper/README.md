# Paper display items (Nature Portfolio format)

Figures and tables for the NSCLC-Agent paper. Every number shown is computed by
running this repository. Nothing is typed in by hand.

```bash
pip install matplotlib python-docx pdfplumber pypdf pillow
python paper/analysis.py      # experiments → paper/data/*.json   (~2 min; or name steps)
python paper/make_figures.py  # → paper/figures/*.pdf|svg|png   (183 mm, final size)
python paper/make_tables.py   # → paper/tables/*.docx|tex|md    (three-line, editable)
python paper/qc.py            # Nature display-item QC → paper/QC.md
```

**Main configuration: full model autonomy.** The paper describes the product's
default since v1.3.0. In agent mode the language model decides the stage, the
biomarker category, the treatment intent and the plan; during the consult it is
offered information tools only, and the in-loop hooks check provenance and
emergencies. The deterministic kernel stays outside the decision loop: it is the
reference standard (gold standard, Fig. 2) and it audits each consult
independently afterwards (`nsclc_agent/agentic/audit.py`; `/audit` in the web
app and the command line). The audit is shown to the clinician and used for
evaluation; it is never returned to the model. Kernel-assisted mode (the kernel's
decision tools and comparison hooks offered to the model) and governed mode (the
kernel as control plane) appear as comparators.

## Argument, one message per figure

| Item | Message | Data |
|---|---|---|
| **Fig. 1** | What the system is: the model decides; the kernel audits from outside the loop | Full framework: interfaces, agent runtime in full autonomy (lead, specialists, information tools, in-loop hooks, session, providers), the kernel and its independent audit, comparator modes, evaluation; counts read from the code |
| **Fig. 2** | The reference standard: stage is computed, never guessed; ambiguity is refused unless it cannot change the stage | AJCC/UICC 9th-edition T×N×M matrix; gold-standard accuracy with exact 95% CIs (70 cases) |
| **Fig. 3** | In full autonomy the hooks catch provenance and emergency defects in the loop; the independent audit catches the judgement defects | Defect injection: 35 released plans × 8 defect classes, read by the full-autonomy hooks, the independent audit and the kernel-assisted hooks |
| **Fig. 4** | The runtime scales and stays bounded | Wall clock vs number of specialists (serial vs concurrent); prompt size with and without compaction; full autonomy |
| **Fig. 5** | A worked autonomous consult: the model decides, the hooks catch provenance errors, the audit agrees | Third-line stage IVB KRAS G12C (a clinician-reported case): timed MDT trace, draft → 2 findings → revision, the model's decisions next to the kernel's independent reading |
| **Extended Data Fig. 1** | Each specialist reads only what it needs; kernel tools are absent in full autonomy | Lead and specialist × tool access matrix |
| **Extended Data Fig. 2** | What the kernel knows | Guideline KB by source and topic; trial registry by setting and stage; kernel inventory |
| **Extended Data Fig. 3** | Comparator: how governed mode decides what may be released | Release state machine with the gold-standard count in each state |
| **Extended Data Fig. 4** | The worked case against every regimen's indication predicate | 40 library regimens × predicate conditions; the model's choices outlined |
| **Table 1** | The runtime, mapped to mainstream agent-runtime patterns | Decision authority row: full autonomy |
| **Table 2** | The in-loop hooks and which are on in full autonomy | — |
| **Extended Data Table 1** | Validity of the reference standard | 70 cases, Clopper–Pearson CIs |
| **Extended Data Table 2** | Bilingual emergency screen | 25-phrase battery (positive, negated, third-party, hypothetical) |
| **Extended Data Table 3** | The deterministic safety rules (run by the audit in full autonomy) | 20 rules, 24 finding identifiers, severities, audit probes raising each |

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

- **No real-model evaluation yet.** Every agent-mode experiment uses scripted or
  mock model decisions; no API key is available to the build. The figures show
  what the runtime and the audit do with a given consult. They do not show how
  often a real language model decides correctly. The audit is the instrument for
  that evaluation: run the 42 gold-standard pipeline cases through a connected
  model in full autonomy and score each consult with `agentic.audit.audit`.
- **Gold-standard set.** The cases were written by the developers. Dual
  clinician adjudication has not been done yet (70/70 cases are unadjudicated).
- **Defect injection (Fig. 3).** The defects were constructed by the authors.
  The study measures how much of the defect space each reader covers, not how
  often real models make these errors. The audit does not see the evidence
  ledger, so it cannot judge citation provenance; that check stays in the loop.
- **Runtime scaling (Fig. 4a).** Model latency is simulated at 250 ms per call;
  the tools are real. Concurrency is capped at six worker threads.
- **Case study (Fig. 5).** The case is a clinician's report; the model's
  decisions and its two provenance errors are scripted by the authors. Tools,
  sub-agents, hooks, the review loop and the audit are the real runtime. Model
  latency is simulated at 0.6 s per call.
- **Context study (Fig. 4b).** Uses the offline mock model. Token counts are
  provider-neutral estimates.
