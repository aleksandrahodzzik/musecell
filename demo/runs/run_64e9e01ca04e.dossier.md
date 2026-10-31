# MuseCell dossier · specimen s_0ed866d71e

Run `run_64e9e01ca04e` · blinded · 2026-10-31 19:37 UTC · models: scientist muse-spark-1.3, microscopist muse-spark-1.3, reviewer muse-wonder-1.0, segmentation classical (scikit-image), provider meta, atlas ResNet50 (ImageNet) + TVN, agent v5

**Question.** Which mechanism-of-action class explains this compound's phenotype in MCF-7 cells?

## Experiments (4 of 4 wells)

| Evidence | Dose (µM) | Replicate | Cells | Strongest changes vs DMSO |
|---|---|---|---|---|
| e2 | 1 | 1 | 165 | tub_glcm_contrast z=+108.2; tub_std z=+32.4; tub_mean z=+25.1 |
| e3 | 0.1 | 1 | 232 | tub_glcm_contrast z=+115.0; tub_std z=+37.7; tub_mean z=+29.7 |
| e7 | 0.3 | 1 | 251 | tub_glcm_contrast z=+104.0; tub_std z=+38.8; nuc_form_factor z=-27.9 |
| e8 | 0.1 | 2 | 410 | nuc_solidity z=-33.4; nuc_form_factor z=-22.7; frac_large_nucleus z=+21.3 |

## Steps

1. `list_available_experiments` — List doses and replicates to plan discriminating well choices.
2. `acquire_well` — Sample highest dose to reveal strong phenotype and narrow MoA hypotheses.
3. `acquire_well` — Sample mid dose to check dose-dependence and avoid high-dose toxicity.
4. `describe_phenotype` — Visual check of mid-dose microtubule and nuclear morphology vs DMSO.
5. `compare_to_reference_atlas` — Nearest reference treatments will separate stabilizer vs destabilizer hypotheses.
6. `update_hypotheses` — Strong tubulin increase plus atlas vote and microscopy converge on stabilizers.
7. `acquire_well` — Neighbouring active dose between two strong hits should reproduce stabilizer phenotype.
8. `acquire_well` — Replicate of mid-dose strong hit tests reproducibility of tubulin increase.
9. `compare_to_reference_atlas` — NSC vote over all four wells determines protocol final call.
10. `update_hypotheses` — Four wells unanimous atlas vote plus bright bundled tubulin converge on stabilizers.
11. `submit_conclusion` — Bright bundled tubulin, irregular nuclei and unanimous 4/4 atlas vote support stabilizers.
12. `lookup_compound` — Protocol step after the review: identify the unblinded specimen in PubChem and ChEMBL.
13. `search_literature` —  *(error: Invalid arguments: rationale: Field required; query: Field required; max_results: Field required)*
14. `search_literature` — Verify docetaxel tubulin stabilization mechanism for dossier citations.

## Hypothesis history

- after step 6: Microtubule stabilizers 0.85, Microtubule destabilizers 0.08, Aurora kinase inhibitors 0.03, Eg5 inhibitors 0.02, Actin disruptors 0.02
- after step 10: Microtubule stabilizers 0.93, Microtubule destabilizers 0.04, Aurora kinase inhibitors 0.02, Eg5 inhibitors 0.01

## Conclusion

**Microtubule stabilizers** (stated probability 0.93); key evidence e2, e3, e4, e5, e7, e9.

Why: Bright bundled tubulin, irregular nuclei and unanimous 4/4 atlas vote support stabilizers.

Next experiment if this were a real lab: Washout regrowth plus phospho-histone H3 mitotic index and in vitro tubulin polymerization assay comparing to a reference of the leading class.

## PI review

Verdict: approve.

Top call of Microtubule Stabilizers is well-supported across 4 wells at dose 5 twice and doses 6–7: the phenotype is not just an increase in microtubule intensity, it is increased tubule mean with bundled, coarse structuring that breaks down into the low-dimensional molecular signature. The runner-ups (Microtubule Destabilizers, Aurora kinase inhibitors, and Eg5 inhibitors) do not explain that increased polymer-like texture together with bundled perinuclear rings as well as the top class does, even allowing for the reduced strength in the dose 5 replicate.

## Unblinding

Compound: **docetaxel** (PubChem CID [148124](https://pubchem.ncbi.nlm.nih.gov/compound/148124))

Identity check: name and BBBC021 structure agree

ChEMBL mechanisms: Tubulin inhibitor; Tubulin stabiliser

## Verdict (computed by code)

Correct: the call was **Microtubule stabilizers**, BBBC021 annotates **Microtubule stabilizers**.

The final call is the nearest-neighbour vote over the 4 wells the agent chose (4/4). The Scientist's own call was **Microtubule stabilizers** (agrees; right).

## Reflection

The call was correct because three independent lines converged: extreme tubulin texture and intensity increases, microscopist description of bundled microtubules, and unanimous 4/4 NSC atlas votes for Microtubule stabilizers. The strongest discriminators were tub_glcm_contrast z +104 to +115 with tub_std z +32 to +39 and tub_mean z +25 to +30 across doses 5-7, which reads as more and coarser polymer rather than microtubule loss.

Sampling high dose first then mid-dose and a mid-dose replicate was efficient: it showed dose-persistence and reproducibility despite the replicate w_1c56739be6 having lower similarity (0.647) and a nucleus-dominated profile. The persistent 4/10 k-neighbour votes for destabilizers were a useful warning that intensity/texture alone partially overlaps stabilizer and destabilizer neighbours.

Unblinding as docetaxel with ChEMBL Tubulin stabiliser agrees with the phenotype-first call and required no revision of the protocol conclusion.

What was right:
- Interpreted very high tub_glcm_contrast (+108 to +115) plus high tub_std and tub_mean as bundled stabilized polymer, not depolymerization
- Used describe_phenotype density-lower with bundled morphology to favor Microtubule stabilizers at 0.85 then 0.93 over destabilizers, Aurora and Eg5
- Used compare_to_reference_atlas NSC 2/2 then 4/4 vote to lock the call despite kNN split 6/10 vs 4/10

What was wrong or missed:
- Underestimated the stabilizer vs destabilizer neighbour overlap: 4/10 k-neighbours were Microtubule destabilizers in both atlas comparisons, so texture similarity alone was less specific than the NSC vote
- Replicate w_1c56739be6 (dose 5 rep 2) shifted to nuclear shape features nuc_solidity z=-33.4 and frac_large_nucleus z=+21.3 with lower stabilizer similarity 0.647, which could have supported Aurora/Eg5 mitotic arrest if atlas voting had not been unanimous

Proposed next experiment: Cell-free tubulin polymerization assay with docetaxel vs paclitaxel and nocodazole controls, plus washout in cells with phospho-histone H3 mitotic index to confirm stabilized polymer persists after washout.

## Citations

Citations verified: 3/3

- PMID [36876916](https://pubmed.ncbi.nlm.nih.gov/36876916/): Structural insight into the stabilization of microtubules by taxanes. *Elife* (2023) — Taxanes stabilize microtubules, supporting docetaxel as a microtubule stabilizer.
- PMID [18068131](https://pubmed.ncbi.nlm.nih.gov/18068131/): Taxanes, microtubules and chemoresistant breast cancer. *Biochim Biophys Acta* (2008) — Taxanes target microtubules, consistent with increased tubulin intensity and bundling phenotype.
- PMID [33671665](https://pubmed.ncbi.nlm.nih.gov/33671665/): Taccalonolides: A Novel Class of Microtubule-Stabilizing Anticancer Agents. *Cancers (Basel)* (2021) — Microtubule-stabilizing agents represent a distinct anticancer mechanism defined by increased polymer stability.

Cost: $0.2279 over 13 model calls; 11 Scientist turns.

*Research tool for cell-biology screening data. Not for clinical use.*
