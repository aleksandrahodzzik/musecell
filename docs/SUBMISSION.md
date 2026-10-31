# Submission draft

Text for the hackathon form. Every number is from `eval/results.json` (checked by `scripts/check_readme_numbers.py` for the README).

## Name
MuseCell

## One line
A blinded screening agent that chooses which microscope wells to image, has its call reviewed, and only then learns the compound's name and checks itself against chemistry databases and literature.

## Short description
Phenotypic drug screens find active compounds, but working out *how* a hit acts (its mechanism of action) is slow, manual work. MuseCell runs that loop on real BBBC021 images of MCF-7 cells (38 annotated compounds, 12 mechanism classes). It works under a blinded protocol. The agent sees only opaque ids and a budget of 4 of about 24 wells. It acquires wells, measures the cells, looks at the images, keeps a hypothesis board and submits a conclusion. A second model reviews that conclusion. Only then is the compound unblinded, and code scores the call. A scanner stops the run if the compound's identity leaks to the model or the browser before then. The final dossier cites only papers the agent actually retrieved in that run.

Result on all 38 compounds (agent v5, Muse Spark 1.3): the right mechanism for 34/38, and 18/19 on the held-out half of a dev/test split frozen before v4 (Muse Spark Open: 34/38, 17/19). The Scientist chooses the wells, and the published nearest-neighbour (NSC) protocol makes the call from them. Reported plainly: a fixed "4 highest doses" rule is right on exactly the same 34 compounds; Muse Spark 1.3 chose 140 of its 152 wells among those doses. So on this dataset the model rediscovers the rule a biologist would use but is not more accurate than it. What it adds is a blinded, auditable record: why each well, the reviewer's critique, an explanation, and a dossier with verified citations.

## What each Meta model does
| Model | Role |
|---|---|
| Muse Spark 1.3 (`muse-spark-1.3`) / Muse Spark Open (`meta-models/muse-spark-open-1026` on Baseten) | **Scientist:** tool-calling loop over 9 strict-schema tools. It plans which wells to image, reads the measurements, keeps hypotheses, submits a blinded conclusion and writes the reflection. **Microscopist:** vision. It describes treated vs same-plate DMSO images, grounded in our measured features. |
| Muse Wonder 1.0 (`muse-wonder-1.0`) | **PI reviewer:** reads the blinded evidence ledger and the NSC vote, then approves or requests one more experiment. |
| SAM 3.1 (`sam-3.1`) | Segmentation bake-off and an independent check of classical nuclei (median IoU 0.85 on DMSO tiles). Classical segmentation won on full fields, so it makes the masks. |

The benchmark headline is agent v5 on full-price `muse-spark-1.3` (about $0.29 and 94 s per run); Muse Spark Open is reported beside it. The recorded demo replays are agent v5 (three on `muse-spark-1.3`, one on Muse Spark Open).

## How to run
- **Live demo, no install:** https://aleksandrahodzzik.github.io/musecell/ (replays of recorded live runs, dossiers, benchmark).
- **Locally, replay mode, no key:** README → Quickstart (Windows PowerShell commands included).
- **Live runs:** a Meta Model API key plus the BBBC021 subset (~10.8 GB); README → Live mode.

## Limitations
- One dataset and one cell line; 38 annotated compounds, so differences of a few compounds are within noise.
- The language model does not add accuracy here. The Scientist's own call is 81.6%, and a fixed top-doses rule matches the agent's 89.5% compound for compound.
- v1–v3 were developed on all 38 compounds. Only v4/v5 have a held-out check, on 19 compounds.
- The virtual microscope replays images from 2010 and cannot order new experiments.

## Links
- Repo: https://github.com/aleksandrahodzzik/musecell
- Video (≤ 3:00): *to add*
