# Evidence audit — library 2026-10 (v1.6.0)

This file records what was checked when the regimen library, trial registry,
indication declarations and interaction pack moved from `2026-08` to
`2026-10`: the corrections made to existing entries, what each new entry
rests on, and what could **not** be confirmed. It is a working record for
the clinician or pharmacist who reviews the library, not a substitute for
that review.

## How the checks were made

* Every new trial entry (results, source, approval, enrollment note) and
  every new regimen (fixed adult dose, schedule, label warnings behind its
  gates and monitoring list) was searched individually.
* 21 of the 39 existing trial entries were searched the same way: the
  perioperative and adjuvant trials (CheckMate 816, KEYNOTE-671, AEGEAN,
  CheckMate 77T, IMpower010, KEYNOTE-091, ADAURA), stage III (PACIFIC,
  PACIFIC-2, LAURA, RTOG 0617) and stage IV (FLAURA, CROWN, CheckMate 9LA,
  LIBRETTO-431, BRF113928, MARIPOSA-2, KEYNOTE-789, INSIGHT 2,
  TROPION-Lung05, Gomez). The other 18 were not re-checked in this round.
* The development environment's network policy blocked `fda.gov`,
  `api.fda.gov`, `clinicaltrials.gov` and PubMed, so no label PDF, FDA
  approval page, trial record or abstract was opened directly. The checks
  rest on web-search results: journal and PMC records, label PDFs and FDA
  pages **as indexed by the search tool**, ESMO abstracts and oncology news
  coverage (ASCO Post, OncLive, Targeted Oncology). Nothing was replaced by
  a mocked lookup.
* "Confirmed" below means a search result stated the figure, date or dose.
  Where results disagreed, the entry says which analysis it quotes.
* Doses: a fixed adult label dose is encoded only when a label copy or the
  approval summary stated it. Weight-tiered or ramp-up doses that could not
  be read from a label stay `per label (… not encoded)`, and the dose
  channel serves that string.

## Corrections to existing entries

| Entry | Before | Now | Basis |
|---|---|---|---|
| CheckMate 816 | "OS HR 0.72 (95% CI 0.523–0.998) at 5-year analysis; pCR patients 5-yr OS ~95%" | Final OS analysis HR 0.72 (95% CI 0.523–0.998); 5-year OS 65% vs 55%; the pCR split (95% vs 56%) marked exploratory | Coverage of the 2025 final OS analysis; the HR's CI itself was not in the results (see open items) |
| FLAURA (CNS) | CNS ORR 91% vs 68% labelled as the CNS full-analysis set | CNS PFS HR 0.48 (95% CI 0.26–0.86); CNS ORR 66% vs 43% in the full-analysis set (measurable and/or non-measurable lesions). 91% vs 68% is the measurable-lesion set and was removed | Reungwetwattana, J Clin Oncol 2018 (PMC record) |
| CROWN | HR 0.27 (0.18–0.39) quoted as the trial's PFS HR | Interim HR 0.28 (0.19–0.41); 3-year HR 0.27 (0.18–0.39); 5-year PFS 60% vs 8% (HR 0.19, 0.13–0.27); sources split by analysis | PMC records of the 3-year and 5-year reports (J Clin Oncol 2024;42:3400) |
| CheckMate 9LA | "OS HR 0.66 (96.71% CI 0.55–0.80)" — the updated HR with the interim analysis's CI level | Interim HR 0.69 (96.71% CI 0.55–0.87); HR 0.66 (95% CI 0.55–0.80) with longer follow-up | Lancet Oncol 2021;22:198 (PMC and PubMed records) |
| LIBRETTO-431 | Source NEJM 2024;390:1265 (that is the ALINA paper) | NEJM 2023;389:1839 (Zhou et al.); PFS 24.8 vs 11.2 months, HR 0.46, named as the pembrolizumab-ITT interim analysis; pralsetinib named as the alternative | Institutional publication record; ASCO Post (November 2023) |
| BRF113928 | "ORR 64% treatment-naïve; median PFS ~10.8 mo" | ORR 64% (23/36), median duration of response 10.4 months; median OS 17.3 months from the updated analysis (J Thorac Oncol 2022;17:103) | Search results for the 2017 and 2022 papers; the 10.8-month PFS was **not** confirmed (open items) |
| MARIPOSA-2 | Approval "FDA 2024-03" | FDA 2024-09; PFS 6.3 vs 4.2 months added; OS not significant at the second interim analysis | FDA approval page as indexed; ASCO Post (September 2024). The 2024-03 approval was PAPILLON (exon 20 insertions) |
| KEYNOTE-789 | "did not significantly improve OS (HR 0.84)"; source JCO 2024;42:1222 | Neither PFS nor OS improved significantly (final OS 15.9 vs 14.7 months, HR 0.84, 95% CI 0.69–1.02); source by DOI 10.1200/JCO.23.02747 | PMC record of Yang et al., J Clin Oncol 2024. Volume/page could not be confirmed, so the DOI is cited |
| INSIGHT 2 | Source "Lancet Oncol 2024;25:989" | Same, with first author and DOI 10.1016/S1470-2045(24)00270-5 | Search result for the primary publication (Wu et al., August 2024) |
| DESTINY-Lung02 | "first-line for HER2-mutant disease remains chemo±IO" | First line is chemo±IO, plus a HER2 TKI for non-squamous TKD-activating mutations (zongertinib, sevabertinib) | The 2026 first-line accelerated approvals (below) |
| CodeBreaK 100 | "adagrasib (KRYSTAL-1) is the alternative" | adagrasib (KRYSTAL-1/KRYSTAL-12) | KRYSTAL-12 (below) |

Searched and left unchanged, because the results matched: KEYNOTE-671
(OS HR 0.72, 0.56–0.93), AEGEAN (EFS HR 0.68, pCR 17.2% vs 4.3%),
CheckMate 77T (EFS HR 0.58, 97.36% CI 0.42–0.81), IMpower010 (DFS HR 0.66 in
PD-L1 TC ≥1% stage II–IIIA), KEYNOTE-091 (DFS HR 0.76 in the overall
population), ADAURA (OS HR 0.49 in both populations), PACIFIC (5-year OS
42.9% vs 33.4%), PACIFIC-2 (primary endpoint not met; the entry quotes no
HR), LAURA (PFS 39.1 vs 5.6 months, HR 0.16), Gomez (PFS 14.2 vs 4.4, OS
41.2 vs 17.0 months) and the TROPION-Lung05 approval (ORR 45%, pooled).

Two corrections are behavioural, not textual:

* **Trastuzumab deruxtecan needs a HER2 mutation.** The DESTINY-Lung02
  indication declared "ERBB2 positive", so HER2 amplification alone after
  chemo-immunotherapy got T-DXd citing a trial of HER2-mutant disease. It
  now declares class `erbb2_mutant`: amplification or overexpression alone
  is ineligible (the HER2 IHC 3+ tumor-agnostic approval is not encoded and
  the plan says so), and a bare "HER2 positive" stays provisional.
* **Trial ids with an underscore now resolve from their usual spelling.**
  "DESTINY-Lung02", "TROPION-Lung05" and similar references did not
  resolve to `DESTINY_LUNG02` / `TROPION_LUNG05`, so checks keyed on the
  resolved id were skipped for them.

## New entries

Seventeen trials, sixteen regimens and their indication declarations.

| Trial (regimen) | Confirmed by the searches | Note |
|---|---|---|
| ALEX (`alectinib_first_line`) | NEJM 2017 PFS HRs and medians; CNS progression 12% vs 45%; 5-year OS update; FDA 2017-11 | The label's independent-review HR is 0.53 (0.38–0.73); the NEJM paper's is 0.50 (0.36–0.70) — the entry quotes the paper |
| ALTA-1L (`brigatinib_first_line`) | Final-analysis PFS 24.0 vs 11.1 months (HR 0.48), OS HR 0.81 (n.s.), intracranial PFS HR 0.29 — from the ESMO 2021 final-results abstract and coverage; 90 mg × 7 days then 180 mg; FDA 2020-05 | The J Thorac Oncol 2021;16:2091 paper's own text was not seen |
| EMPOWER-Lung 1 (`cemiplimab_monotherapy`) | Lancet 2021;397:592; OS HR 0.57, PFS 8.2 vs 5.7 months (HR 0.54); 350 mg q3w; approval February 2021, EGFR/ALK/ROS1 excluded | Library-only: the deterministic plan names pembrolizumab |
| IMpower110 (`atezolizumab_monotherapy`) | NEJM 2020;383:1328 (indexing record); OS 20.2 vs 13.1 months (HR 0.59, 0.40–0.89) in the high-PD-L1 wild-type group; FDA 2020-05 | Library-only. The label's immune-cell route (IC ≥10%) is not modeled; TPS ≥50% stands in for TC ≥50% across different assays |
| JMDB, PARAMOUNT (`pemetrexed_platinum_first_line`) | JMDB J Clin Oncol 2008;26:3543 OS by histology; PARAMOUNT final OS 13.9 vs 11.0 months (HR 0.78); 2012-10 maintenance label update | Offered only when the record rules out a checkpoint inhibitor |
| CA031 (`carbo_nab_paclitaxel_first_line`) | J Clin Oncol 2012;30:2055; ORR 33% vs 25% (squamous 41% vs 24%); FDA 2012-10 | Same use, squamous disease |
| TRUST-I/II (`taletrectinib_first_line`) | J Clin Oncol 2025;43:1920 (pooled); ORRs TKI-naive and pretreated; FDA 2025-06; 600 mg once daily, nothing to eat 2 hours before and after (label copy) | |
| STARTRK-2 pooled (`entrectinib_ros1_first_line`, `entrectinib_ntrk_first_line`) | Lancet Oncol 2020;21:261 (ROS1, ORR 77%); NTRK ORR 57%; FDA 2019-08; 600 mg once daily; QT prolongation warning | One trial row covers either driver (`ROS1|NTRK`) |
| PROFILE 1001 (`crizotinib_ros1_first_line`) | NEJM 2014;371:1963; ORR 66% (label analysis, n=50) and 72% updated; Ann Oncol 2019;30:1121; FDA 2016-03; QT and bradycardia warnings | |
| ARROW (`pralsetinib_first_line`) | Lancet Oncol 2021;22:959; regular approval 2023-08; 400 mg once daily, no food 2 hours before and 1 hour after (label copy) | |
| VISION (`tepotinib_first_line`) | ORR 57% treatment-naive, 45% previously treated (traditional approval 2024-02); 450 mg once daily with food | NEJM page numbers not confirmed |
| PHAROS (`encorafenib_binimetinib_first_line`) | J Clin Oncol 2023;41:3700; ORR 75% / 46%; FDA 2023-10; 450 mg once daily + 45 mg twice daily; binimetinib LVEF and encorafenib QTc warnings | |
| Beamion LUNG-1 (`zongertinib_her2_tkd`) | NEJM 2025;392:2321 (previously treated); first-line NEJM 2026 report; accelerated approvals 2025-08 and 2026-02; ORR 76% first line (n=72); warnings hepatotoxicity, LV dysfunction, ILD | Dose not encoded (weight-tiered; secondary sources give 120 mg below 90 kg, 180 mg from 90 kg) |
| SOHO-01 (`sevabertinib_her2_tkd`) | NEJM 2025 (doi 10.1056/NEJMoa2511065); accelerated approvals 2025-11 and 2026-09; ORR 75% first line (n=69) | Dose not encoded (20 mg twice daily per a press release only); warnings from secondary sources |
| KRYSTAL-1, KRYSTAL-12 (`adagrasib_subsequent_line`) | NEJM 2022;387:120; Lancet 2025;406:615 (PFS 5.5 vs 3.8 months, HR 0.58); accelerated approval 2022-12; 600 mg twice daily; QTc warning | Previously treated only |

Interaction rules added (pack `2026-10`): brigatinib with CYP3A inducers
(label: rifampin lowers brigatinib exposure by about 80% — avoid);
alectinib with inducers (`monitor`: the label needs no dose change, as the
active metabolite M4 rises while alectinib falls, about 18% lower combined
exposure); QT-prolonging TKIs (selpercatinib, entrectinib, crizotinib,
taletrectinib, encorafenib, adagrasib) with QT-prolonging co-medications;
cemiplimab joins the checkpoint-inhibitor rules. The pneumonitis list behind
`ILD_PNEUMONITIS_RISK` now holds every library TKI whose label (as found by
the searches) warns of ILD or pneumonitis.

## Not confirmed — open items

These stay in the library as written, from the publication or label as
recalled, and need a person to check them against the primary source:

* CheckMate 816: the final-analysis OS HR's 95% CI (0.523–0.998).
* CROWN: the 2020 NEJM interim HR 0.28 (0.19–0.41); the intracranial
  response figures (82% vs 23%).
* BRF113928: treatment-naive median PFS ~10.8 months.
* VISION: NEJM 2020;383:931 page numbers (a citation record lists the
  paper, the pages were not seen).
* ALTA-1L: the figures of the J Thorac Oncol 2021 final-results paper
  itself (the entry uses the ESMO 2021 abstract).
* KEYNOTE-789: volume and page (cited by DOI instead).
* LAURA: NEJM 2024;391:585 volume and page; AEGEAN NEJM 2023;389:1672
  and CheckMate 77T NEJM 2024;390:1756 volume and page.
* RTOG 0617: median OS 28.7 vs 20.3 months, HR 1.38 (1.09–1.76).
* MARIPOSA-2: the confidence interval and the Ann Oncol 2024;35:77 citation
  of the second interim analysis.
* INSIGHT 2: ORR of about 50% in the FISH-defined population.
* LIBRETTO-431: the approval date as written.
* JMDB: the month of the 2008 approval (the source gives the year only).
* Zongertinib: the exact day of the first approval in August 2025 (sources
  say 8 or 18 August).
* Cemiplimab: the approval day (sources agree on February 2021; one gives
  22 February).
* Alectinib "with food": from the label as recalled; no search result
  stated it.
* Sevabertinib label warnings (diarrhea, hepatotoxicity, ILD,
  LV dysfunction) and its dose: secondary sources only.
* The HER2 kinase-domain codon range used by the classifier (720–987,
  UniProt P04626) is from reference knowledge, not a search result.

Known gaps, not errors: zidesamtinib (FDA 2026-07, ROS1 after a prior TKI)
is not encoded and the ROS1 post-TKI branch says so; T-DXd's HER2 IHC 3+
tumor-agnostic approval is not encoded; the interaction pack remains a
teaching set, not a pharmacist's full review.

## Sources

Search results the checks above relied on, by entry (FDA pages and label
PDFs as returned by the search tool; they were not opened directly).

* ALEX — https://pmc.ncbi.nlm.nih.gov/articles/PMC5690958 ;
  https://pmc.ncbi.nlm.nih.gov/articles/PMC9434408 ;
  https://onclive.com/view/asco-new-data-for-alecensa-shows-5year-overall-survival-in-alkpositive-nsclc ;
  alectinib interactions: https://medlibrary.org/lib/rx/meds/alecensa/page/5/
* ALTA-1L — https://oncologypro.esmo.org/meeting-resources/esmo-congress-2021/brigatinib-brg-vs-crizotinib-crz-in-alk-tki-naive-alk-nsclc-final-results-from-alta-1l ;
  https://www.onclive.com/view/brigatinib-maintains-superior-pfs-benefit-over-crizotinib-with-high-intracranial-efficacy-in-alk-nsclc ;
  https://www.onclive.com/view/fda-approves-brigatinib-for-frontline-alk-nsclc ;
  brigatinib interactions: https://medlibrary.org/lib/rx/meds/alunbrig/page/5/
* EMPOWER-Lung 1 — https://pubmed.ncbi.nlm.nih.gov/33581821/ ;
  https://ascopost.com/news/march-2021/first-line-cemiplimab-rwlc-vs-platinum-doublet-chemotherapy-in-advanced-nsclc-with-pd-l1-expression-50
* IMpower110 — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-atezolizumab-first-line-treatment-metastatic-nsclc-high-pd-l1-expression ;
  https://ascopost.com/news/october-2020/first-line-atezolizumab-vs-chemotherapy-in-pd-l1-positive-metastatic-nsclc/
* JMDB — https://pubmed.ncbi.nlm.nih.gov/18506025/ ;
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4798219
* PARAMOUNT — https://pmc.ncbi.nlm.nih.gov/articles/PMC4132027 ;
  https://www.medthority.com/news/2012/10/fda-extend-approval-of-alimta--eli-lilly--for-maintenance-treatment-in-nsclc/
* CA031 — https://ascopost.com/issues/august-15-2012/response-rate-is-significantly-improved-with-nab-vs-solvent-based-paclitaxel-plus-carboplatin-in-advanced-nsclc
* TRUST — https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/219713s004lbl.pdf ;
  https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-taletrectinib-ros1-positive-non-small-cell-lung-cancer ;
  https://www.targetedonc.com/view/taletrectinib-data-published-as-fda-weighs-approval-for-ros1-nsclc
* STARTRK-2 — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-entrectinib-ntrk-solid-tumors-and-ros-1-nsclc ;
  https://ascopost.com/news/january-2020/entrectinib-in-advanced-ros1-fusion-positive-nsclc/ ;
  https://www.gene.com/download/pdf/rozlytrek_prescribing.pdf
* PROFILE 1001 — https://ascopost.com/issues/march-25-2016/fda-approves-crizotinib-for-ros1-positive-metastatic-non-small-cell-lung-cancer/ ;
  https://www.pfizermedical.com/xalkori/warnings
* ARROW — https://pubmed.ncbi.nlm.nih.gov/34118197/ ;
  https://www.fda.gov/drugs/drug-approvals-and-databases/fda-approves-pralsetinib-non-small-cell-lung-cancer-ret-gene-fusions ;
  https://www.accessdata.fda.gov/drugsatfda_docs/label/2020/214701s000lbl.pdf
* VISION — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-tepotinib-metastatic-non-small-cell-lung-cancer ;
  https://ascopost.com/news/february-2024/fda-approves-tepotinib-for-metastatic-nsclc
* PHAROS — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-encorafenib-binimetinib-metastatic-non-small-cell-lung-cancer-braf-v600e-mutation ;
  https://www.pfizermedical.com/mektovi/warnings ;
  https://www.pfizermedical.com/braftovi/warnings
* Beamion LUNG-1 — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-grants-accelerated-approval-zongertinib-unresectable-or-metastatic-non-squamous-non-small-cell ;
  https://ascopost.com/news/february-2026/advanced-nonsquamous-nsclc-fda-grants-accelerated-approval-to-zongertinib/ ;
  https://clinicaltrials.gov/study/NCT04886804
* SOHO-01 — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-grants-accelerated-approval-sevabertinib-locally-advanced-or-metastatic-non-squamous-non-small ;
  https://www.pharmacytimes.com/view/fda-expands-accelerated-approval-of-sevabertinib-to-first-line-her2-mutant-nsclc ;
  https://ascopost.com/news/october-2025/reversible-tki-in-her2-mutant-nsclc/
* KRYSTAL-1 / KRYSTAL-12 — https://www.fda.gov/drugs/resources-information-approved-drugs/fda-grants-accelerated-approval-adagrasib-kras-g12c-mutated-nsclc ;
  https://ascopost.com/news/august-2025/adagrasib-vs-docetaxel-in-previously-treated-advanced-or-metastatic-krasg12c-mutated-nsclc/ ;
  https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/216340s011lbl.pdf
* QT warnings — https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/213246s017lbl.pdf
  (selpercatinib) ; https://www.ema.europa.eu/en/documents/product-information/krazati-epar-product-information_en.pdf
  (adagrasib)
* Corrections — CheckMate 816: https://ascopost.com/news/june-2025/neoadjuvant-nivolumab-and-chemotherapy-shows-significant-os-benefit-at-5-years-in-resectable-nsclc ;
  FLAURA CNS: https://pmc.ncbi.nlm.nih.gov/articles/PMC6389567 ;
  CROWN: https://pmc.ncbi.nlm.nih.gov/articles/PMC11458101 and
  https://pmc.ncbi.nlm.nih.gov/articles/PMC10906525 ;
  CheckMate 9LA: https://pmc.ncbi.nlm.nih.gov/articles/PMC8254731 ;
  LIBRETTO-431: https://ascopost.com/news/november-2023/first-line-selpercatinib-vs-chemotherapy-with-or-without-pembrolizumab-in-ret-fusion-positive-nsclc ;
  MARIPOSA-2: https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-amivantamab-vmjw-carboplatin-and-pemetrexed-non-small-cell-lung-cancer-egfr-exon-19 ;
  KEYNOTE-789: https://pmc.ncbi.nlm.nih.gov/articles/PMC11608596 ;
  BRF113928: https://onclive.com/interactive-tools/biomarker-consortium/lung-cancer/nsclc/biomarkers/braf/dabrafenib-and-trametinib
