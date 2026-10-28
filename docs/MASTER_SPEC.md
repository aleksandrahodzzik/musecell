# MuseCell — Master Specification

> Single source of truth for the coding agent (Cursor / Claude / Codex / Antigravity).
> Read this whole file before writing code. When the spec and your instinct disagree, the spec wins.
> When the spec and **reality** disagree (an API behaves differently, a file has other columns), reality wins:
> verify, adapt, and write the discrepancy into `docs/DECISIONS.md`.

---

## 0. Context: the competition we are building for

| Item | Value |
|---|---|
| Event | Meta AI Developer Hackathon (Cerebral Valley), online |
| Hacking window | Oct 26 – Nov 4, 2026. **Only work done in this window counts.** |
| Deadline | **Nov 4, 2026, 9:00 PM ET** (= Nov 5, 03:00 Warsaw). Internal deadline: Nov 4, 20:00 Warsaw. |
| Track | Indie Developer |
| Theme | **Push the Frontier** — "Use Meta's models to tackle challenges in software, robotics, manufacturing, energy, materials and beyond. Show us what becomes possible when these models meet the real world." |
| Submission | (1) CV platform form with every teammate, (2) 3-minute demo video, (3) **public** GitHub repo that opens in a logged-out browser |
| Judging stage 1 | Pass/fail: fits a theme AND uses Meta AI products |
| Judging stage 2 rubric (0–10 each) | **Impact 30%** (real-world potential, who benefits, fit with problem statement) · **Use of Meta AI tooling 25%** (creative use beyond basic integration) · **Technical depth 20%** (sound, refined engineering) · **Demo 25%** (working demo that holds up + video worth watching) |
| Stage 3 | Top 5 per track, live final Nov 9 |

**Anti-project list (instant risk at stage 1):** AI-generated medical advice (incl. skin-lesion detectors, mental-health advisors), simple image analyzers, dashboards as the main feature, basic RAG apps, Streamlit apps, "AI for Education" chatbots, AI nutrition coaches, job-application screeners, personality analyzers, sports analyzers.

**Rules that shape the code:** new work only; public repo; "demo only what you built — judges must be able to tell what your team created during the event"; nothing that uses code, data or assets we have no rights to.

**Bonus prizes we target:** Best Agentic Use-Case ($5K, judged on autonomy, effectiveness, practical usefulness). Stretch: Best Build on Meta Open Weights ($10K — must run on Muse Spark Open or Muse Glimmer, verified by an infra partner). Most Viral ($20K — X post, handled by the human).

---

## 1. Product

### One-liner
**MuseCell is an autonomous screening scientist.** Give it a blinded compound and a small budget of microscope wells. It images real cells, measures them, forms mechanism-of-action hypotheses, chooses the next experiment that best separates those hypotheses, has its conclusion reviewed by a second model, and only then unblinds the compound and checks itself against chemistry databases and real literature.

### The real problem
Phenotypic drug screens (treat cells with thousands of compounds, image them, look for interesting changes) find hits that target-based screens miss. The expensive part comes after: figuring out **how** a hit works (mechanism of action, "MoA deconvolution"). Today that means a biologist eyeballing images, comparing against reference compounds, ordering follow-up dose series, and digging through literature. MuseCell automates that loop end-to-end and — the frontier part — **decides which experiment to run next** instead of just labelling a picture.

### Users
Screening biologists and computational biologists in drug-discovery and academic labs. **Not** clinicians, **not** patients. Cell lines + small molecules. MuseCell never outputs diagnoses or treatment advice. Put this sentence in the README and the UI footer: *"Research tool for cell-biology screening data. Not for clinical use."*

### Why this passes stage 1 cleanly
| Anti-project | Why MuseCell is not it |
|---|---|
| Medical advice | Cultured MCF-7 cells treated with compounds; output is a lab hypothesis + next experiment, never a patient statement |
| Simple image analyzer | The image is one input to a closed-loop agent that spends an experiment budget, runs statistics, queries databases, and gets peer-reviewed by a second model |
| Dashboard as main feature | The product is the agent's run: decisions, experiments, verdict. Charts are secondary |
| Basic RAG | Literature search is one of nine tools and is used to *verify* a blinded conclusion, not to answer questions |
| Streamlit | Custom React UI |

### What makes it "Push the Frontier"
1. **Active experimentation**: the agent chooses doses/replicates to acquire under a budget (a self-driving-lab pattern), using real images as the oracle.
2. **Blinded protocol with self-verification**: the agent commits to a conclusion before it can see the compound identity, then unblinds and is scored. This is how real science controls bias, and it makes the demo's climax honest.
3. **Measured evaluation**: a benchmark script reports accuracy vs. budget against a classical baseline on a public dataset with published evaluation protocol.

---

## 2. Data

### Dataset: BBBC021 (Caie et al., 2010)
- MCF-7 breast-cancer cell line, 113 compounds × 8 concentrations, 24 h treatment, 3 channels: **DNA (DAPI), β-tubulin, F-actin**. 39,600 TIFFs (13,200 fields; 4 fields per well).
- MoA ground truth: `BBBC021_v1_moa.csv` covers 103 compound-concentrations from **38 compounds, 12 MoA classes**. Published evaluation protocol: **leave-one-compound-out (LOCO)**.
- Sources: Broad `https://data.broadinstitute.org/bbbc/BBBC021/` (55 plate zips, ~725–901 MB each, named `BBBC021_v1_images_Week<N>_<plate>.zip`; metadata `BBBC021_v1_image.csv`, `BBBC021_v1_compound.csv`, `BBBC021_v1_moa.csv`). Same images are in IDR study **idr0035** under **CC BY 4.0** (copyright AstraZeneca). 
- **Rights:** use under CC BY 4.0 via IDR with attribution. Create `DATA_LICENSE.md` with the citation: *"We used image set BBBC021v1 [Caie et al., Molecular Cancer Therapeutics, 2010], available from the Broad Bioimage Benchmark Collection [Ljosa et al., Nature Methods, 2012]; also distributed as IDR idr0035 under CC BY 4.0. Images © AstraZeneca."* Do **not** commit raw TIFFs. Commit only small derived demo thumbnails (≤ 40 PNGs, ≤ 15 MB total) under `demo/`.

### Fetch strategy (`scripts/fetch_bbbc021.py`)
1. Download the three metadata CSVs. **Print their real column names** and adapt; do not assume.
2. Build the needed subset: all 38 MoA-annotated compounds at all their available concentrations + DMSO control wells on the same plates. Compute which plate zips are needed and print the plan with total GB **before** downloading.
3. Prefer ranged reads with the `remotezip` package (reads only the TIFFs we need from the remote zip). If the server refuses ranges, fall back to full-zip download → extract needed members → delete zip. Hard cap: stop and ask if the plan exceeds 12 GB.
4. Resumable, idempotent, SHA/size checks, progress bars. `DATA_DIR` from env (default `../musecell-data`, **outside** OneDrive and outside the repo).

### Preprocessing (`scripts/preprocess.py`)
- Per field: load 3 TIFFs (16-bit) with `tifffile`; per-plate per-channel robust contrast (1st–99.8th percentile of DMSO fields); save float32 `.npy` for measurement and an 8-bit composite PNG for display/model input: **DNA→blue, tubulin→green, actin→red**, max side 1024. Also per-channel grayscale PNGs (the vision model gets both).
- Index table `data/index.parquet`: `field_id` (opaque, e.g. `f_7c1a…` hash), plate, well, field, compound, concentration_uM, moa (nullable), replicate, paths.
- **Blinding hygiene:** anything sent to the model or the browser during a blinded run uses opaque IDs only. No compound names in file names, URLs, alt text, logs streamed to UI, or prompt text. Add a unit test that greps every SSE payload of a blinded run for the true compound name and its synonyms and fails if found.

---

## 3. Perception and measurement (real science, no theater)

### Segmentation
- **Primary path A — SAM 3.1 (Meta):** Responses API, `model="sam-3.1"`, streamed. Concept prompts are short noun phrases; try `"cell"` and `"nucleus"` on the composite and the DNA channel. Request `metadata: {"include_confidence": "true"}`. Parse with `meta-sam-parser` (pip; import `meta_sam_parser`; read its source for the exact parse + `decode_mask_to_raster` API — do not guess). Cost: $2.50 per 1,000 images.
- **Path B — classical:** nuclei from DAPI (Gaussian → Otsu → distance-transform watershed → size filter); cell bodies by seeded watershed on tubulin+actin from nuclei.
- In Phase 0 probe both on 5 fields (DMSO + 4 MoAs), save overlays to `docs/probe/`, compare object counts vs. visual truth, and pick per object type. Record which segmenter produced every mask; the UI shows a toggle and a label. Cache SAM results to disk (never pay twice for the same field).

### Features (`science/features.py`, scikit-image `regionprops` + simple texture)
Per cell (~20): nuclear area, integrated + mean DNA intensity (DNA-content proxy), nuclear eccentricity, solidity, form factor; cell area, cell/nucleus area ratio; tubulin mean/std + GLCM contrast; actin mean/std + GLCM contrast; actin/tubulin ratio; roundness.
Per field: cell count; fraction mitotic-like (compact, bright, small nucleus); fraction large-nucleus (polyploid proxy); fraction multi-nucleated proxy.
**Well profile** = median over cells; **normalized** = robust z-score (median/MAD) vs. DMSO wells of the **same plate**. **Treatment profile** = median over replicate wells.

### Reference atlas (`science/atlas.py`)
- Profiles of all annotated compound-concentrations. Similarity: cosine on normalized profiles (optionally after PCA whitening fitted on DMSO).
- `neighbors(profile, k, exclude_compound)` always excludes the query compound (LOCO). Return neighbors with compound, concentration, MoA, similarity, thumbnail id.
- **Baseline classifier:** nearest-neighbor MoA (NSC) under LOCO. Report its accuracy in `eval/` — this is our honest yardstick.

### Statistics (`science/stats.py`)
`dose_response(feature, acquired_wells)` → per-concentration medians, Spearman ρ, p-value (scipy). `compare_to_dmso(profile)` → top ±k features by |z|, with values. Every number shown in the UI must come from these functions.

---

## 4. Meta AI usage (25% of the score — make it visible and non-trivial)

### Verified API facts (from dev.meta.ai docs, checked 2026-10-28; re-verify in Phase 0)
| Item | Value |
|---|---|
| Base URL | `https://api.meta.ai/v1` (OpenAI-compatible: Chat Completions, Responses, Messages APIs) |
| Auth | `Authorization: Bearer <key>`; env var **`MODEL_API_KEY`**; pass explicitly as `api_key=` to `openai.OpenAI(...)` |
| `muse-spark-1.3` | text/image/video/PDF in, 1M context, tool calling (parallel, streamed args), JSON-schema structured output, reasoning effort incl. `"max"`. $1.25 in / $4.25 out per 1M tokens |
| `muse-spark-1.3-contributor` | same model, $0.10 / $0.20 — allows Meta to train on prompts. OK **only** for public-data eval runs; flag `ALLOW_CONTRIBUTOR=true` |
| `muse-wonder-1.0` | frontier reasoning/agentic, tool calling, multimodal. $4 / $20 per 1M. Use sparingly |
| `sam-3.1` | text-prompt segmentation → boxes + masks, Responses API only |
| Image input | PNG/JPEG/WebP/GIF only (**no TIFF**), ≤ 50 images/request, ≤ 50 MB inline; base64 `data:` URL. Chat Completions: `{"type":"image_url","image_url":{"url":...}}`; Responses: `{"type":"input_image","image_url":...}` |
| Tool calling | `tools=[{"type":"function","function":{...}}]` on Chat Completions; `tool_choice` only `"auto"`; with `strict: true` every object needs `additionalProperties:false` and all props `required` |
| Structured output | Chat Completions `response_format={"type":"json_schema","json_schema":{"name":..,"schema":..,"strict":true}}`; no recursive `$ref` |
| Rate limits | 3,000 RPM / 4M TPM per model per team |
| Model list | `GET /v1/models` — **call it at startup; refuse to start if a configured model id is missing. Never invent model ids.** (Ids like `muse-wonder`, `muse-spark-open`, `meta-llama/...` do NOT exist on this API.) |
| Muse Spark Open (open weights) | Not on Meta Model API. Hosted on Baseten (`https://inference.baseten.co/v1`, `meta-models/muse-spark-open`), Fireworks (`accounts/fireworks/models/muse-spark-open`), Together, Groq, Nebius — OpenAI-compatible |

### Model roles (a real multi-model, multi-agent system)
| Role | Model | What it does |
|---|---|---|
| **Scientist agent** | `muse-spark-1.3` | Runs the tool-calling loop: plans acquisitions, reads measurements, maintains hypotheses, submits a blinded conclusion |
| **Microscopist** (tool) | `muse-spark-1.3` vision | Given treated composite + per-channel images **and a same-plate DMSO control**, returns a structured morphology description (schema below). Grounded: it receives our measured top-deviating features alongside the pixels and must say which visual findings agree/disagree with the numbers |
| **PI reviewer** | `muse-wonder-1.0` | Reads the full evidence ledger and the conclusion. Returns `{verdict: "approve" | "request_experiment", critique, requested_experiment?}`. At most one revision round. This is the "second opinion" that makes the agent loop credible |
| **Segmenter** | `sam-3.1` | Cell/nucleus masks (if it wins the Phase-0 bake-off) |
| **Open-weights mode** (stretch) | `muse-spark-open` via `PROVIDER=baseten|fireworks|together` | Same Scientist loop on open weights; eval table compares it to `muse-spark-1.3` |

One `LLMClient` wrapper: provider config (base_url, key env, model ids) from `.env`; retries with exponential backoff on 429/5xx; timeout; token usage + **$ cost accounting per call** (price table in config); per-run hard cap (default $1.00) and daily cap (default $20) → abort with a clear error event.

### Microscopist output schema (structured output, strict)
```json
{
  "cell_density": "lower|similar|higher",          // vs DMSO
  "observations": [{"channel": "dna|tubulin|actin|composite", "finding": "string", "agrees_with_measurements": true}],
  "notable_cells": [{"field_id": "string", "x": 0, "y": 0, "why": "string"}],
  "candidate_mechanisms": [{"moa": "<enum of the 12 classes + unknown>", "why": "string"}],
  "uncertainty": "string"
}
```
The 12 MoA enum values must be read from `BBBC021_v1_moa.csv` at runtime (do not hard-code spellings).

---

## 5. The agent

### Run state (`agent/schemas.py`, Pydantic v2)
`AgentRun {run_id, mode: "blinded"|"open", specimen_id (opaque), budget_total, budget_used, steps[], hypotheses[], evidence[], conclusion?, review?, unblinding?, verdict?, cost_usd, model_ids}`.
Every tool result becomes an `Evidence {evidence_id, kind, summary, data, image_ids[]}`.

### Tools (JSON schema, `strict: true`)
**Every tool has a required `rationale` string (≤ 240 chars).** The UI shows it as "Why". This is the agent's stated reason — we never claim to show hidden chain-of-thought.

Blinded phase:
1. `list_available_experiments()` → concentrations available for the specimen (as indices + µM values), replicates per concentration, wells already acquired, remaining budget. Free.
2. `acquire_well(concentration_index, replicate)` → **costs 1 budget unit**. "Virtual microscope": returns the well's 4 field ids, thumbnails, segmentation summary, normalized profile, top ±8 deviating features vs. DMSO. Server enforces budget; over budget → tool error the agent sees.
3. `describe_phenotype(field_ids[])` → Microscopist call (above).
4. `compare_to_reference_atlas(well_ids[], k)` → LOCO nearest neighbors with MoA, similarity, thumbnails.
5. `dose_response(feature)` → statistics over acquired wells.
6. `update_hypotheses(hypotheses[{moa, probability, supporting_evidence_ids[], contradicting_evidence_ids[]}])` → server validates MoA enum, renormalizes probabilities, rejects unknown evidence ids. Drives the live hypothesis board.
7. `submit_conclusion(top_moa, probability, key_evidence_ids[], next_experiment_if_this_were_a_real_lab)` → ends blinded phase.

Review: PI reviewer (`muse-wonder-1.0`) → approve, or request one more experiment (costs budget if available; if none left, agent must state the limitation).

Unblinding phase (tools unlocked only now):
8. `lookup_compound(name)` → PubChem PUG-REST (CID, IUPAC name, SMILES, 2D structure PNG URL `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG`) + ChEMBL REST (`/chembl/api/data/molecule/search`, `/chembl/api/data/mechanism?molecule_chembl_id=`) → known targets and action type.
9. `search_literature(query)` → NCBI E-utilities `esearch` + `esummary` (db=pubmed; params `tool=musecell&email=$NCBI_EMAIL`; ≤ 3 req/s; cache). Returns PMID, title, journal, year, URL.

Then the agent writes a **reflection** (what it got right/wrong and why) and the **dossier**.

### Hard guardrails (non-negotiable)
- Max 20 model turns per run, max budget 8 wells, per-run $ cap.
- **Citations:** the final dossier may only cite PMIDs returned by `search_literature` in this run. A validator strips anything else and the UI shows "Citations verified: n/n".
- **Verdict** is computed by code (agent top MoA vs. ground truth), never by the model.
- **No canned outputs presented as live.** If a model call fails, the UI shows a clear error event. No silent fallbacks to hard-coded text. No made-up confidence numbers anywhere.

### Event stream (SSE) — the backbone of the UI and the video
Endpoint `GET /api/runs/{run_id}/events` (`text/event-stream`). Event types:
`run_started, thinking (agent turn started), tool_call {name, args, rationale}, tool_result {evidence_id, summary, image_ids}, hypotheses_updated {hypotheses}, budget_updated, conclusion, review, unblinded {compound, structure_url, chembl_mechanisms}, verdict {correct, truth_moa, agent_moa}, report_ready, cost_updated, error, run_finished`.
Every event is appended to `runs/<run_id>.jsonl` with a timestamp.

### Replay (demo insurance, honestly labeled)
`POST /api/runs/{run_id}/replay?speed=1.5` streams a recorded **real** run with original pacing. UI shows a permanent badge: "Replay of a live run · <date> · <model ids>". Ship 3–4 good recorded runs in `demo/runs/` (with their thumbnails) so the public repo demo works without an API key.

---

## 6. Evaluation (`scripts/evaluate.py`) — technical-depth evidence
- Held-out set: all 38 annotated compounds (LOCO), or a stratified subset of ≥ 24 if cost requires.
- Methods: (a) NSC baseline on all wells; (b) NSC baseline on the same number of wells chosen at random; (c) MuseCell agent at budgets 2, 4, 6.
- Metrics: MoA accuracy (compound level), mean wells used, mean $ cost and latency per run, citation validity rate, blinding-leak test result.
- Outputs `eval/results.json` + `eval/RESULTS.md` (table + accuracy-vs-budget chart PNG). README embeds them. **Numbers are whatever the code produces — never hand-edited.** If the agent underperforms the baseline somewhere, say so and explain; honesty here earns credibility with technical judges.
- Eval runs may use `muse-spark-1.3-contributor` (public data) to save credits; label it.

---

## 7. Architecture and repo layout

```
musecell/
├─ backend/
│  ├─ app/
│  │  ├─ main.py            # FastAPI app; serves frontend/dist in prod; /api/*
│  │  ├─ config.py          # pydantic-settings; prices; model ids; caps
│  │  ├─ llm.py             # LLMClient (Meta API + optional open-weights provider)
│  │  ├─ sam.py             # SAM 3.1 client + parser + cache
│  │  ├─ agent/  loop.py tools.py schemas.py prompts.py reviewer.py dossier.py
│  │  ├─ science/ segmentation.py features.py atlas.py stats.py
│  │  ├─ external/ pubchem.py chembl.py pubmed.py   # httpx, cached, rate-limited
│  │  └─ runs/ store.py replay.py
│  ├─ tests/                # pytest: tools, guardrails, blinding leak, citation validator
│  └─ requirements.txt
├─ frontend/                # Vite + React 18 + TypeScript + Tailwind
├─ scripts/ probe_meta_api.py fetch_bbbc021.py preprocess.py build_atlas.py evaluate.py record_demo_runs.py
├─ demo/ thumbnails/ runs/  # small, committed
├─ eval/                    # results committed
├─ docs/ MASTER_SPEC.md DECISIONS.md ARCHITECTURE.md probe/
├─ README.md  LICENSE (MIT)  DATA_LICENSE.md  AGENTS.md  .env.example  .gitignore
```

**Stack:** Python 3.12, FastAPI, uvicorn, pydantic v2 + pydantic-settings, openai SDK (check installed version supports `responses.stream`), httpx, numpy, scipy, pandas, pyarrow, scikit-image, tifffile, pillow, remotezip, meta-sam-parser, pytest. Frontend: Vite, React 18, TypeScript, Tailwind, framer-motion (one orchestrated moment only), lucide-react icons.
**Why not Next.js:** no SSR need; one port and one process for the demo (FastAPI serves the built SPA); fewer moving parts on a Windows laptop.

**Ports:** backend **8787**, Vite dev **5174** (proxy `/api` → 8787). **Never use 8000, 8001 or 3000** — another project ("Slotwise") occupies them on the dev machine.

**Windows/PowerShell:** chain commands with `;` not `&&`. venv: `py -3.12 -m venv .venv` then `.\.venv\Scripts\Activate.ps1` (if blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`). Use `python -m uvicorn`, `python -m pip`.

**Secrets:** only in `backend/.env` (gitignored). `.env.example` lists `MODEL_API_KEY=`, `NCBI_EMAIL=`, `PROVIDER=meta`, `BASETEN_API_KEY=`, `FIREWORKS_API_KEY=`, `DATA_DIR=`, caps. Code must **never** contain a key or a default key value. Add a pre-commit check (simple script or `detect-secrets`) that blocks commits containing `LLM_` keys or `sk-` strings.

---

## 8. UI / UX

### Concept: "the lab notebook and the dark field"
The subject is fluorescence microscopy, where images live on true black and each stain has a fixed color. The interface borrows that: a calm, cool, paper-like notebook chrome (where the agent writes) with true-black microscope panes (where the cells are). The only saturated colors in the app are the three stains, and they always mean the same channel. The memorable element is **the plate map + hypothesis board updating live** as the agent spends its budget, ending in one orchestrated **unblinding** moment. Everything else stays quiet.

### Tokens
| Token | Hex | Use |
|---|---|---|
| paper | `#E8ECEF` | app background (cool slide-glass grey) |
| sheet | `#F6F8F9` | notebook surfaces |
| ink | `#17202A` | primary text, borders at 12% |
| graphite | `#5B6672` | secondary text |
| darkfield | `#000000` | microscope panes only |
| dna | `#5B8CFF` | DNA/DAPI channel, nothing else |
| tubulin | `#3FCF7F` | tubulin channel, nothing else |
| actin | `#FF5468` | actin channel, nothing else |
| verdict-yes / verdict-no | `#1E6B47` / `#A3302A` | unblinding verdict only |

Type: **Source Serif 4** for the notebook narrative (agent rationale, reflection, dossier) and headings; **IBM Plex Sans** for UI and all numbers with `font-variant-numeric: tabular-nums`. Sentence case everywhere; no all-caps labels; no eyebrow labels; no "→" glued to buttons. Radii: 6px controls, 10px panels, 0 on microscope panes (they are instruments).

### Screens
**1. Choose a specimen** — a short row of sealed specimen cards ("Specimen 7 · 8 doses · mechanism hidden"), a budget stepper (2–8 wells, default 4), a model line ("Scientist: Muse Spark 1.3 · Reviewer: Muse Wonder 1.0 · Segmentation: SAM 3.1"), primary button **"Start blinded run"**, secondary **"Watch a recorded run"**. Footer: research-use disclaimer + data attribution.

**2. Run view**
```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Specimen 7 (blinded)     Budget ●●●○○○ 3 of 6 wells     $0.21     ■ Stop      │
├──────────────┬────────────────────────────────────────┬───────────────────────┤
│ Plate map    │ Microscope (true black)                │ Hypotheses            │
│ 8 doses × 3  │ [DNA][Tubulin][Actin][Masks]  DMSO ⇄   │ Microtubule destab. ▇▇▇▇▇ 0.61
│ reps grid;   │  composite / per-channel, zoom+pan,    │ Eg5 inhibitor       ▇▇ 0.22 │
│ acquired     │  side-by-side with same-plate DMSO;    │ Aurora kinase inh.  ▇ 0.09  │
│ wells glow   │  hover a cell → its measurements       │ … (animated on update;     │
│ in order     │                                        │  click → evidence)          │
├──────────────┴────────────────────────────────────────┴───────────────────────┤
│ Notebook: step 5 · acquire_well(dose 6, rep 2)                                │
│   Why: "Dose 4 was ambiguous between Eg5 and tubulin; a higher dose should    │
│   separate mitotic arrest from spindle collapse."                             │
│   Result: 41 cells, 38% mitotic-like (DMSO 4%), DNA int. z=+3.1 … [images]    │
│ (scrolling timeline of every event; reviewer critique appears inline)         │
└───────────────────────────────────────────────────────────────────────────────┘
```
**3. Unblinding (the one orchestrated moment, ~1.2 s, respects `prefers-reduced-motion`)** — the specimen label resolves to the compound name, the PubChem 2D structure fades in, ChEMBL mechanism lines appear, and the verdict marks the agent's top hypothesis as matching or not. Then the reflection paragraph.

**4. Dossier** — printable page: question, experiments run (with images), measurements, hypothesis history (small multiples of the board over time), reviewer critique, verdict, verified citations, proposed next real-lab experiment. Export Markdown + JSON.

**5. Benchmark** — eval table and accuracy-vs-budget chart from `eval/results.json`. Secondary page.

### Copy rules
Plain verbs, user-facing names ("Start blinded run", not "POST /runs"). Errors say what happened and what to do ("Meta API rejected the key. Check MODEL_API_KEY in backend/.env."). Empty states invite action. No marketing adjectives, no fake percentages, no "99.4% confidence" anywhere.

### Quality floor
Keyboard navigable, visible focus, color never the sole carrier (channel names always printed), works at 1280×720 (video resolution) and degrades gracefully to tablet width. Images lazy-loaded WebP/PNG. No layout shift while streaming.

---

## 9. Demo video (3:00) — what the product must make easy to film
- 0:00–0:20 Problem: phenotypic screens produce hits; working out how a hit acts is slow manual work.
- 0:20–1:50 Live blinded run at 1280×720: start → agent acquires a low dose → microscope + measurements → hypotheses split → agent asks for a higher dose (show its "Why") → board converges → reviewer challenges once → conclusion → **unblinding** → verdict + verified citations.
- 1:50–2:30 Under the hood: diagram (SAM 3.1 → measurements → Muse Spark 1.3 scientist with 9 tools → Muse Wonder reviewer → PubChem/ChEMBL/PubMed), blinding + citation guardrails, cost per run.
- 2:30–2:55 Benchmark table: accuracy vs. budget vs. baseline (real numbers).
- 2:55–3:00 Repo URL + "research tool, not for clinical use".

## 10. README must contain
Hero GIF of a run; one-paragraph pitch; "Why this matters"; "How it uses Meta AI" (table of the models and their roles, links to code lines); architecture diagram (Mermaid); quickstart (Windows + macOS/Linux, with and without API key → replay mode); evaluation results; guardrails (blinding, citations, cost caps); data attribution; "Built during the hackathon (Oct 26 – Nov 4, 2026)" with commit-history note; limitations (cell line, dataset scope, crude features, virtual microscope is replayed data).

## 11. Definition of done (submission)
- [ ] Fresh clone → README quickstart works in replay mode with no key.
- [ ] Live run works with a key end-to-end on ≥ 3 specimens.
- [ ] `pytest` green, including blinding-leak and citation-validator tests.
- [ ] `eval/RESULTS.md` generated by the script.
- [ ] No secrets in git history (`git log -p | Select-String "LLM_"` returns nothing).
- [ ] Repo public, opens logged-out. Video ≤ 3:00 uploaded. CV form submitted with every teammate.
