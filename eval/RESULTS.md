# MuseCell evaluation

Generated 2026-10-31T19:09:02+00:00 by `scripts/evaluate.py`. Numbers are written by the script, never edited by hand.

- Compounds: 38 MoA-annotated BBBC021 compounds, leave-one-compound-out (the atlas never contains the compound under test).
- Metric: compound-level MoA accuracy (one call per compound).
- Models: scientist `meta-models/muse-spark-open-1026`, microscopist `meta-models/muse-spark-open-1026`, reviewer `muse-wonder-1.0`, segmentation `classical (scikit-image)`.
- Blinding-leak test: pytest leak/blinding tests: 13 passed, 88 deselected in 8.29s; offline re-scan of 38 eval run logs (events before unblinding): 0 hits

| Method | Wells / compound | Accuracy | Correct | Before review | Mean wells used | Mean cost | Mean latency | Citations verified |
|---|---|---|---|---|---|---|---|---|
| NSC baseline (hand-crafted features), all wells | all | 71.0% | 27/38 | – | – | – | – | – |
| NSC baseline (hand-crafted features), random wells | 2 | 50.5% ± 6.2 | – | – | 2.00 | – | – | – |
| NSC baseline (hand-crafted features), random wells | 4 | 57.8% ± 5.8 | – | – | 4.00 | – | – | – |
| NSC baseline (hand-crafted features), random wells | 6 | 61.3% ± 5.0 | – | – | 6.00 | – | – | – |
| NSC baseline (ResNet50 + TVN), all wells | all | 92.1% | 35/38 | – | – | – | – | – |
| NSC baseline (ResNet50 + TVN), random wells | 2 | 74.9% ± 5.9 | – | – | 2.00 | – | – | – |
| NSC baseline (ResNet50 + TVN), random wells | 4 | 81.3% ± 4.9 | – | – | 4.00 | – | – | – |
| NSC baseline (ResNet50 + TVN), random wells | 6 | 83.9% ± 4.3 | – | – | 6.00 | – | – | – |
| MuseCell agent, Muse Spark Open (Baseten), agent v5 | 4 | 89.5% | 34/38 | 79.0% | 4.00 | $0.300 | 129 s | 117/117 (100.0%) |
| MuseCell agent, Muse Spark Open (Baseten), agent v4 | 4 | 79.0% | 30/38 | 76.3% | 4.00 | $0.360 | 171 s | 116/116 (100.0%) |
| MuseCell agent, Muse Spark Open (Baseten), agent v3 | 4 | 55.3% | 21/38 | 50.0% | 4.00 | $0.375 | 142 s | 128/129 (99.2%) |
| MuseCell agent, Muse Spark Open (Baseten) | 4 | 44.7% | 17/38 | 31.6% | 3.97 | $0.365 | 167 s | 111/112 (99.1%) |
| MuseCell agent (muse-spark-1.3-contributor) | 2 | 42.1% | 16/38 | 36.8% | 2.00 | $0.107 | 203 s | 103/103 (100.0%) |
| MuseCell agent (muse-spark-1.3-contributor) | 4 | 36.8% | 14/38 | 36.8% | 3.92 | $0.135 | 208 s | 109/109 (100.0%) |
| MuseCell agent (muse-spark-1.3-contributor) | 6 | 42.1% | 16/38 | 42.1% | 5.61 | $0.143 | 239 s | 108/108 (100.0%) |

± = standard deviation over random well draws. "Before review" = the agent's first conclusion, before the PI reviewer's critique and any requested experiment (from agent v5 the final call is the NSC vote over the agent's wells, so the gap to "Accuracy" is not the review's effect). ± is the spread of the baseline over draws, not a confidence interval for the agent; the agent's intervals are in the well-choice section below.

![Accuracy against wells per compound](accuracy_vs_budget.png)

## Agents side by side, budget 2

Same 38 compounds, same tools, same PI reviewer (`muse-wonder-1.0`, Meta API); the columns differ in the Scientist + Microscopist model, the agent version and the atlas features.

| | muse-spark-1.3-contributor | NSC baseline (hand-crafted features), random wells | NSC baseline (ResNet50 + TVN), random wells |
|---|---|---|---|
| Scientist model | `muse-spark-1.3-contributor` | – | – |
| Provider | Meta Model API | – | – |
| Atlas features | hand-crafted features | hand-crafted features | ResNet50 + TVN |
| Accuracy | 42.1% (16/38) | 50.5% ± 6.2 | 74.9% ± 5.9 |
| Before review | 36.8% | – | – |
| Scientist's own call (v5: final call is the protocol's) | – | – | – |
| NSC on the wells the agent chose | 47.4% | – | – |
| Failed runs | 0 | – | – |
| Mean cost / run | $0.107 | – | – |
| Mean latency | 203 s | – | – |
| Citations verified | 103/103 | – | – |
| Reviewer requests | 15 | – | – |

Cost on open weights is nominal: Baseten list price ($1.25 / $4.25 per 1M tokens) for the Scientist/Microscopist tokens, paid from hackathon credits, plus the reviewer at Meta API prices.

## Agents side by side, budget 4

Same 38 compounds, same tools, same PI reviewer (`muse-wonder-1.0`, Meta API); the columns differ in the Scientist + Microscopist model, the agent version and the atlas features.

| | Muse Spark Open (Baseten), agent v5 | Muse Spark Open (Baseten), agent v4 | Muse Spark Open (Baseten), agent v3 | Muse Spark Open (Baseten) | muse-spark-1.3-contributor | NSC baseline (hand-crafted features), random wells | NSC baseline (ResNet50 + TVN), random wells |
|---|---|---|---|---|---|---|---|
| Scientist model | `meta-models/muse-spark-open-1026` | `meta-models/muse-spark-open-1026` | `meta-models/muse-spark-open-1026` | `meta-models/muse-spark-open-1026` | `muse-spark-1.3-contributor` | – | – |
| Provider | Baseten | Baseten | Baseten | Baseten | Meta Model API | – | – |
| Atlas features | ResNet50 (ImageNet) + TVN | ResNet50 (ImageNet) + TVN | hand-crafted features | hand-crafted features | hand-crafted features | hand-crafted features | ResNet50 + TVN |
| Accuracy | 89.5% (34/38) | 79.0% (30/38) | 55.3% (21/38) | 44.7% (17/38) | 36.8% (14/38) | 57.8% ± 5.8 | 81.3% ± 4.9 |
| Before review | 79.0% | 76.3% | 50.0% | 31.6% | 36.8% | – | – |
| Scientist's own call (v5: final call is the protocol's) | 79.0% | – | – | – | – | – | – |
| NSC on the wells the agent chose | 89.5% | 92.1% | 63.2% | 60.5% | 60.5% | – | – |
| Failed runs | 0 | 0 | 1 | 3 | 0 | – | – |
| Mean cost / run | $0.300 (incl. credits) | $0.360 (incl. credits) | $0.375 (incl. credits) | $0.365 (incl. credits) | $0.135 | – | – |
| Mean latency | 129 s | 171 s | 142 s | 167 s | 208 s | – | – |
| Citations verified | 117/117 | 116/116 | 128/129 | 111/112 | 109/109 | – | – |
| Reviewer requests | 2 | 10 | 14 | 21 | 18 | – | – |

Cost on open weights is nominal: Baseten list price ($1.25 / $4.25 per 1M tokens) for the Scientist/Microscopist tokens, paid from hackathon credits, plus the reviewer at Meta API prices.

## Agents side by side, budget 6

Same 38 compounds, same tools, same PI reviewer (`muse-wonder-1.0`, Meta API); the columns differ in the Scientist + Microscopist model, the agent version and the atlas features.

| | muse-spark-1.3-contributor | NSC baseline (hand-crafted features), random wells | NSC baseline (ResNet50 + TVN), random wells |
|---|---|---|---|
| Scientist model | `muse-spark-1.3-contributor` | – | – |
| Provider | Meta Model API | – | – |
| Atlas features | hand-crafted features | hand-crafted features | ResNet50 + TVN |
| Accuracy | 42.1% (16/38) | 61.3% ± 5.0 | 83.9% ± 4.3 |
| Before review | 42.1% | – | – |
| Scientist's own call (v5: final call is the protocol's) | – | – | – |
| NSC on the wells the agent chose | 63.2% | – | – |
| Failed runs | 2 | – | – |
| Mean cost / run | $0.143 | – | – |
| Mean latency | 239 s | – | – |
| Citations verified | 108/108 | – | – |
| Reviewer requests | 13 | – | – |

Cost on open weights is nominal: Baseten list price ($1.25 / $4.25 per 1M tokens) for the Scientist/Microscopist tokens, paid from hackathon credits, plus the reviewer at Meta API prices.

## Does the agent choose wells better than a fixed rule? (budget 4)

MuseCell agent, Muse Spark Open (Baseten), agent v5: the final call is the NSC vote over the wells the Scientist chose, so its only lever is which wells to image. Each rule below picks wells without labels and is scored by the protocol call's own vote function (`app.agent.tools.well_vote`), in the same atlas space, on the same {rep['n']} compounds. Rules that read measurements look at every well, which the agent cannot do without spending its budget.

| Well choice | All | Dev half | Held-out test half |
|---|---|---|---|
| agent (wells chosen by the Scientist) | 34/38 | 17/19 | 17/19 |
| random wells (expected) | 30.9/38 | 15.6/19 | 15.3/19 |
| top doses | 34/38 | 16/19 | 18/19 |
| top 2 doses x replicates | 28/38 | 16/19 | 12/19 |
| dose spread | 31/38 | 16/19 | 15/19 |
| top doses, cell count z >= -2 | 32/38 | 15/19 | 17/19 |
| strongest wells (embedding norm) | 27/38 | 14/19 | 13/19 |

Agent minus each alternative, percentage points, mean over compounds with a paired bootstrap 95% interval (10,000 resamples of compounds):

| Agent minus | All | Dev | Test |
|---|---|---|---|
| random wells (expected) | +8.3 [-0.2, +16.9] | +7.6 [-6.5, +21.2] | +8.9 [-0.7, +20.4] |
| top doses (best rule, picked after seeing all compounds) | +0.0 [-10.5, +10.5] | +5.3 [-10.5, +21.1] | -5.3 [-15.8, +0.0] |
| top 2 doses x replicates | +15.8 [+5.3, +28.9] | +5.3 [+0.0, +15.8] | +26.3 [+5.3, +47.4] |
| dose spread | +7.9 [-7.9, +23.7] | +5.3 [-15.8, +26.3] | +10.5 [-10.5, +31.6] |
| top doses, cell count z >= -2 | +5.3 [-7.9, +18.4] | +10.5 [-10.5, +31.6] | +0.0 [-15.8, +15.8] |
| strongest wells (embedding norm) | +18.4 [+7.9, +31.6] | +15.8 [+0.0, +31.6] | +21.1 [+5.3, +42.1] |

Of the 152 wells the Scientist chose, 114 were among the 4 highest of 8 doses.

Reading: on these 38 compounds the agent's well choice is not distinguishable from the best fixed rule ("top doses"); against random wells the interval is [-0.2, +16.9] points. What the agent adds beyond a rule is the blinded, audited record: why each well, the reviewer's critique, the explanation, and a dossier with verified citations.

## Where the agent goes wrong

All 38 agent runs pooled over budgets (total API cost $11.40).

| Mechanism class | Agent calls | True runs | Correct |
|---|---|---|---|
| Protein degradation | 5 | 4 | 4/4 |
| Aurora kinase inhibitors | 4 | 3 | 3/3 |
| Microtubule destabilizers | 4 | 4 | 4/4 |
| Cholesterol-lowering | 3 | 2 | 2/2 |
| DNA damage | 3 | 4 | 3/4 |
| Eg5 inhibitors | 3 | 2 | 2/2 |
| Epithelial | 3 | 3 | 3/3 |
| Kinase inhibitors | 3 | 3 | 3/3 |
| Microtubule stabilizers | 3 | 3 | 3/3 |
| Protein synthesis | 3 | 3 | 3/3 |
| Actin disruptors | 2 | 3 | 2/3 |
| DNA replication | 2 | 4 | 2/4 |

PI review (it acts on the Scientist's conclusion): 2 requests for another experiment, 36 approvals; the Scientist's call changed from wrong to right in 0 runs and from right to wrong in 0.

## Notes

- The v1 budget 2/4/6 rows used `muse-spark-1.3-contributor` (same model, cheaper tier that lets Meta train on prompts; allowed only because BBBC021 is public). Rows labelled Muse Spark Open ran on Baseten. The live app defaults to full-price `muse-spark-1.3`, which was not benchmarked with agent v4/v5.
- MuseCell agent, Muse Spark Open (Baseten), agent v4, budget 4: chosen on the dev half of a split frozen before it was explored (eval/split.json); dev 13/19, held-out test 17/19.
- MuseCell agent, Muse Spark Open (Baseten), agent v5, budget 4: chosen on the dev half of a split frozen before it was explored (eval/split.json); dev 17/19, held-out test 17/19.
- Agent v2/v3 (the atlas tool also returns the NSC vote over the wells acquired so far, the prompt says how to weigh it, and the blinded phase names no compound) was designed after reading the v1 runs on these same 38 compounds (`scripts/nsc_on_agent_wells.py`). BBBC021 has no other annotated compounds, so v3 accuracy is not a held-out estimate. A v2 eval was stopped after 19 runs (11 correct, 2 stopped by the blinding guard) to add the naming rule; its runs stay in eval/agent_runs.jsonl.
- MuseCell agent, Muse Spark Open (Baseten), agent v3, budget 4, s_89c9462717: run failed before a conclusion; counted as wrong: Blinding guard stopped the run: a 'review' event contained compound-identifying text.
- MuseCell agent, Muse Spark Open (Baseten), budget 4, s_14844eef0c: run failed before a conclusion; counted as wrong: meta-models/muse-spark-open-1026 used all 8000 tokens (incl. 7997 reasoning) without answering. Raise max_tokens for this call.
- MuseCell agent, Muse Spark Open (Baseten), budget 4, s_8dd1eb85a8: run failed after the verdict, which stands (right): meta-models/muse-spark-open-1026 returned invalid JSON for reflection: {
- MuseCell agent, Muse Spark Open (Baseten), budget 4, s_89e4b24a88: run failed after the verdict, which stands (right): meta-models/muse-spark-open-1026 returned invalid JSON for reflection: {
- MuseCell agent (muse-spark-1.3-contributor), budget 6, s_89c9462717: run failed before a conclusion; counted as wrong: Blinding guard stopped the run: the result of describe_phenotype contained compound-identifying text.
- MuseCell agent (muse-spark-1.3-contributor), budget 6, s_3069b9414e: run failed before a conclusion; counted as wrong: muse-spark-1.3-contributor used all 8000 tokens (incl. 7998 reasoning) without answering. Raise max_tokens for this call.
- The random-well baseline votes over 1-NN calls of single wells; the all-wells baseline uses treatment profiles (median over replicates), the published NSC protocol.
