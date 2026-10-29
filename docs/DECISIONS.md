# Decisions log

Format: date — what — why. Newest at the bottom of each phase.

## Phase 0 — skeleton, secret guard, Meta API probe (2026-10-28)

### Repo
- 2026-10-28 — Old prototype `backend/` (hard-coded key) moved whole to `_legacy/` (gitignored). No code carried forward. The old key must be considered leaked and revoked.
- 2026-10-28 — Pre-commit guard `scripts/check_secrets.py` scans **added lines of the staged diff** for `LLM_[A-Za-z0-9_]{10,}` and `sk-[A-Za-z0-9]{20,}`; hook at `.git/hooks/pre-commit` (local only — hooks are not versioned; re-create after a fresh clone). Prints only the first 6 chars of a hit. Env var names must therefore never start with `LLM_` + 10 chars.
- 2026-10-28 — Python 3.12.0 venv at `.venv`. `backend/requirements.txt` pins what pip resolved today (openai 3.40.0, meta-sam-parser 0.1.0, fastapi 0.162.0, numpy 2.5.4, …). Added `python-dotenv` (not in spec stack) for scripts that read `backend/.env`.

### Meta Model API — verified by `scripts/probe_meta_api.py` (results: `docs/probe/probe_results.json`)
Client: `openai.OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])`. All 6 checks PASS.

- **Model list** (`client.models.list()`), 9 ids: `muse-image-1.0, muse-spark-1.1, muse-spark-1.2, muse-spark-1.2-contributor, muse-spark-1.3, muse-spark-1.3-contributor, muse-voice-transcribe-1.0, muse-wonder-1.0, sam-3.1`. Spec ids confirmed.
- **Text**: `chat.completions.create(model="muse-spark-1.3", messages=[...], max_tokens=2000)`.
  - **Surprise:** with no `reasoning_effort`, Spark reasons by default and reasoning tokens count toward `max_tokens`. With `max_tokens=400` a one-sentence question used 398 reasoning tokens and returned **empty content** (finish by length). → `LLMClient` must use generous `max_tokens` (≥ 2000) and treat empty content + `finish_reason="length"` as an error event, not a valid answer.
  - Reasoning tokens: `usage.completion_tokens_details.reasoning_tokens`; billed as output (included in `completion_tokens`).
- **Vision + strict structured output**: Chat Completions content `[{"type":"text",...},{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]` with `response_format={"type":"json_schema","json_schema":{"name":..,"schema":..,"strict":true}}`. Returned schema-valid JSON; counted 3/3 blobs. Schema used `additionalProperties:false`, all props `required`, an `enum` — all accepted.
- **Tool calling**: `tools=[{"type":"function","function":{"name","description","strict":true,"parameters":{...additionalProperties:false, required:[all]}}}]`, `tool_choice="auto"`. Loop that works: append `message.model_dump(exclude_none=True)` (assistant with `tool_calls`), then `{"role":"tool","tool_call_id":id,"content":json}`, call again → final text used the tool result. The `rationale` arg was filled in sensibly.
- **Reasoning effort**: Chat Completions top-level `reasoning_effort` (docs: Responses API uses `reasoning: {"effort": ...}`). Values `none|minimal|low|medium|high|xhigh|max`; `none` → HTTP 400 on Spark/Wonder; `max` not on `-contributor`. Probe: `"low"` and `"max"` both accepted on `muse-spark-1.3` (trivial prompt, ~150–210 reasoning tokens each — effort didn't visibly change cost on a trivial task).
- **SAM 3.1**: `AsyncOpenAI().responses.stream(model="sam-3.1", input=[{"role":"user","content":[{"type":"input_text","text":"<noun phrase>"},{"type":"input_image","image_url":"data:image/png;base64,..."}]}], metadata={"include_confidence":"true"})`. The SDK's `responses.stream` helper works as the async event source (no need for `create(stream=True)`).
  - Parse: `await parse_responses_stream(stream, image_segmentation_format()).final_result()` → `ImageSegmentationResult` with `records` (`SegmentationBoxRecord`: `object_id, left, top, right, bottom, confidence`; `SegmentationMaskRecord`: `mask` + `bounds`), `diagnostics`, `outcome`, `raw_output`. Masks are **box-local** (mask w×h = box size); `decode_mask_to_raster(record.mask)` → bytes of 0/1, row-major, length w*h. Must be placed at `bounds` to get a full-frame mask.
  - Wire format seen: `<0f>0<|box;x1=33;y1=42;x2=89;y2=101;w=256;h=256;c=0.5077|><|mask;x=0;y=0;c=0.5077;data=60,57,~...|>`.
  - Usage on `response.completed`: all token counts 0, `usage.images_processed=1` → cost by image ($2.50 / 1000).
  - **Surprise — prompt wording matters a lot.** On a 256² image with 3 soft Gaussian blobs: `"circle"` → 0 objects (completed, empty output); `"bright spot"` → 0; `"blob"` → 3/3 correct boxes, confidence ≈ 0.51–0.52. On hard-edged white discs, `"circle"` and `"white circle"` also → 0. An empty result is a normal `completed` outcome, not an error. → For cells, the Phase-0 bake-off must try several noun phrases (`cell`, `nucleus`, `nuclei`, `blob`…) per channel and log per-prompt counts; the segmenter must treat "0 objects" as a result to handle, and confidences are around 0.5 even on easy objects, so don't threshold at e.g. 0.8.
- **Cost of Phase-0 probing**: ≈ $0.04 total (first run $0.0138, 4 extra SAM prompt trials $0.01, final run $0.0162).

## Phase 1 — BBBC021 data (2026-10-28)

### Metadata (real columns, verified)
- `BBBC021_v1_image.csv` (13,200 fields): `TableNumber, ImageNumber, Image_FileName_{DAPI,Tubulin,Actin}, Image_PathName_{DAPI,Tubulin,Actin}, Image_Metadata_Plate_DAPI, Image_Metadata_Well_DAPI, Replicate, Image_Metadata_Compound, Image_Metadata_Concentration`. The field (site) number exists only in the file name (`<well>_s<N>_w…`). All 3 channels of a field share one folder.
- `BBBC021_v1_compound.csv`: `compound, smiles` (113 rows). `BBBC021_v1_moa.csv`: `compound, concentration, moa` — 104 rows = 103 compound-concentrations of 38 compounds, 12 MoA classes, plus one `DMSO` row. Matches spec.
- Zips: `BBBC021_v1_images_<plate>.zip`, members `<plate>/<file>.tif`, deflate, TIFF ≈ 2.6 MB raw / ≈ 1.1 MB compressed, uint16 1024×1280. Server sends `accept-ranges: bytes`; `remotezip` works.

### Subset ("G") — deviation from spec §2, approved by the human
- Spec plan (38 compounds × all 8 concentrations × 4 fields + same-plate DMSO) needs all 55 plates and **21.05 GB** of ranged reads — over the 12 GB hard cap.
- Taxol 0.3 µM is a positive control on every plate (6 wells/plate, 333 wells): dropped except on the 3 plates that carry taxol's own dose series (Week1_22123/22141/22161). Rule is generic: a compound's wells on plates where it has no other concentration are dropped.
- MoA-labelled compound-concentrations (103): all 4 fields. Unlabelled concentrations (201): fields 1–2. DMSO: all 6 wells per plate, fields 1–2 (12 DMSO fields/plate).
- Result: 55 plates, 1,228 wells, 3,096 fields, 9,288 TIFFs, **10.83 GB** ranged. Every compound keeps all 8 doses (the agent's dose choice needs them); eval/atlas on labelled doses is unaffected.

### Paths and ids
- `DATA_DIR` default changed from `../musecell-data` (resolves onto the OneDrive Desktop or into the repo) to `~/musecell-data`. `scripts/data_paths.py` refuses a DATA_DIR inside OneDrive or the repo.
- `data/index.parquet` stays in the repo's gitignored `data/`; all paths in it are relative to DATA_DIR. Outputs: `DATA_DIR/raw/<plate>/*.tif`, `DATA_DIR/processed/fields/<field_id>/{dna,tubulin,actin}.{npy,png}, composite.png`, `DATA_DIR/processed/contrast.json`.
- `field_id = "f_" + sha256(salt/plate/well/site)[:12]`, salt random per machine in `DATA_DIR/field_id_salt` (never committed). An unsalted hash could be reversed by enumerating the public metadata. Consequence: ids differ between machines; committed demo runs must carry their own thumbnails.
- `.npy` = (raw − p1) / (p99.8 − p1) of the plate's DMSO pixels, float32, **not clipped** (bright mitotic cells keep values > 1). PNGs are clipped to [0, 1]. Contrast values per plate/channel in `contrast.json`, so raw intensities are recoverable.
- Added `tqdm` (progress bars) to requirements.

### Result (2026-10-29)
- Fetched 9,288 TIFFs (10.6 GB ranged, ~4 MB/s with 8–16 workers; Range never refused, so the full-zip fallback has not been exercised). The first fetch was interrupted overnight (laptop sleep, then the background process was killed); re-running resumed without re-downloading, and a final re-run reported 0 missing.
- Index: 3,096 fields, 1,228 wells (898 treated, 330 DMSO), 55 plates, 38 compounds, 304 compound-concentrations (103 MoA-labelled), 12 MoA classes. Every plate has 12 DMSO fields.
- Example composites (DMSO + 5 MoAs) in `docs/probe/example_*.jpg` (JPEG q90, ~0.9 MB total). With DMSO-based contrast, strongly affected cells saturate in the 8-bit PNGs; the .npy keeps the full range.

## Phase 2 — segmentation, features, atlas, baseline (2026-10-29)

### Segmentation bake-off (`scripts/segmentation_bakeoff.py`, results `docs/probe/segmentation_bakeoff.json`, overlays `docs/probe/seg_*.jpg`)
- 6 probe fields (DMSO + 5 MoAs). Classical nuclei per field: 359 / 87 / 82 / 118 / 75 / 137; visually correct at full resolution (cells by seeded watershed look plausible, incl. taxol/demecolcine/Eg5).
- SAM 3.1 on the whole field (PNG, 1024 px side): **0 nuclei** with `nucleus`, `nuclei`, `blob` on the DNA PNG, also 0 on a contrast-stretched DNA image and on a 256-px DNA tile; `nucleus` on the composite → 0. `cell` on the composite → 2 / 43 / 39 / 58 / 18 / 103 objects (DMSO: 2 vs 359 nuclei), conf ≈ 0.5–0.7.
- On a 256-px composite tile, `cell` → 20 cells at conf ≈ 0.9 (classical: 20 nuclei in that tile). SAM is limited by object count/size per image, not by the stain. Tiling a field needs ~20 calls ≈ $0.05/field ≈ $155 for the atlas — not justified.
- **Winner: classical for nuclei and for cells.** SAM path stays selectable (`NUCLEI_SEGMENTER` / `CELL_SEGMENTER` = `classical|sam` in Settings, `backend/app/config.py`); SAM cells are paired to nuclei by centroid. Cache `DATA_DIR/cache/sam/<field_id>/<image>__<prompt>.npz`. Bake-off cost ≈ $0.10.
- Possible later use: SAM `cell` on tiles of the few fields the agent actually acquires (display/second opinion), not for the atlas.

### Features
- 20 per-cell features (spec §3) + 4 per-field. GLCM contrast over in-cell pixel pairs only (outside pixels → reserved level 0, dropped). Border-touching cells count toward `cell_count` but are excluded from medians and fractions.
- Phenotype fractions are relative to the pooled DMSO medians (`reference.json`: nucleus area 786 px, DNA mean 0.433): mitotic-like = area < 0.6×, DNA mean > 1.5×, solidity > 0.9; large nucleus = area > 2×.
- **Multinucleation proxy (deviation):** a seeded watershed gives exactly one nucleus per cell, so true multinucleation can't be read off. Proxy = fraction of nuclei that share a connected DNA-positive component with another nucleus (touching / clumped nuclei).
- Robust z vs same-plate DMSO wells (6 per plate). Per-plate MAD can be ~0, so the scale is floored at 0.5 × the median per-plate MAD of that feature.
- Known bias (tested): smoothing + global Otsu moves bright nuclei edges out by ≤1.5 px (mean DNA diluted, integrated DNA preserved); cell areas ~8 % high because the cytoplasm threshold is 0.5 × Otsu.

### Atlas build (`scripts/build_atlas.py`)
- All 3,096 fields (not just annotated doses — the agent also acquires unannotated doses). 10 workers, 1,891 s wall; per field (one worker, under load) median 5.4 s, p90 10.1 s; unloaded ≈ 1 s segmentation + ~2 s features. Masks + cell tables cached per field in `DATA_DIR/processed/cells/classical-classical/`.
- Output `DATA_DIR/processed/atlas/`: 1,228 wells (330 DMSO), 304 treatments (103 MoA-annotated, 38 compounds).

### Baseline (`scripts/baseline_nsc.py` → `eval/baseline.json`)
- NSC 1-NN, LOCO, cosine on robust-z profiles: **treatment-level 75.7 % (78/103), compound-level (majority vote) 71.1 % (27/38)**. Weak classes: Epithelial 1/8, Actin disruptors 1/5, Protein degradation 3/7.
- DMSO PCA whitening: 60.2 % / 57.9 % — reported, not used. The default stays the spec's plain cosine; it was not chosen by comparing these numbers.

### Phase 2 follow-up (2026-10-29)
- **Independent check of classical nuclei with SAM tiles.** On 256-px composite tiles SAM does answer, and what it outlines for `cell` are **nuclei** (DMSO tile: median IoU 0.85 with classical nuclei; 93 % matched at IoU > 0.5), not cell bodies. F1 at IoU ≥ 0.5 vs classical nuclei, objects not cut by the tile edge: DMSO 0.97 (18 vs 17) and 0.82 (10 vs 12); etoposide 1.00 (6 vs 6). Treated tiles at fixed positions hold 0–4 nuclei and are uninformative; on one demecolcine tile SAM returned 22 objects vs 3 nuclei (not a clean reference there). Cost $0.03. Classical cell bodies still have no independent check.
- Bug fixed in the bake-off's `match_f1`: it counted objects as `labels.max()`, wrong for crops (non-sequential labels) — now relabels first. Earlier full-field numbers were unaffected (0 SAM nuclei).
- GLCM contrast vectorised (`glcm_contrast_all`, bincount over same-label neighbour pairs): bit-identical to the cached per-cell values (checked on 3 fields, max diff 0) and tested against the skimage version. Per-field time is now dominated by `solidity` (convex hulls, ~55 %); left as is because the atlas is cached and changing it would alter feature values.
- `.env.example`: added `NUCLEI_SEGMENTER`, `CELL_SEGMENTER`.
- Not done on purpose: tuning thresholds or the feature set to raise the LOCO baseline — the 38 compounds are the evaluation set, so tuning on them would inflate the yardstick.

## Phase 3 — LLM client, external tools, agent tools (2026-10-29)

### LLMClient (`backend/app/llm.py`)
- Async (`openai.AsyncOpenAI`), because the Phase-4 loop and SSE are asyncio. SDK retries are off (`max_retries=0`); ours: up to `MODEL_MAX_RETRIES=4` retries on 429 / 5xx / connection errors / timeouts, backoff 1, 2, 4, 8 s (×1–1.25 jitter, cap 30 s), `Retry-After` honoured. Other 4xx fail at once with a readable message (401 → "check MODEL_API_KEY").
- Settings are `MODEL_TIMEOUT_S` / `MODEL_MAX_RETRIES`, not `LLM_…`: the pre-commit guard blocks `LLM_` + 10 characters.
- Caps are checked **before** each call (a call's cost is unknown until it returns), so a run can overshoot its cap by at most one call. Run cap → `CostMeter`; daily cap → append-only `DATA_DIR/cache/costs/<UTC date>.jsonl`, so it survives restarts. A model without a price in `config.PRICES` is refused (never unmetered). `-contributor` models need `ALLOW_CONTRIBUTOR=true`.
- Empty content with no tool calls is an error (with `finish_reason=length` → "raise max_tokens"); the tokens are still billed and recorded. Default `max_tokens=8000`.
- Open-weights providers: Baseten URL from the spec; Fireworks (`https://api.fireworks.ai/inference/v1`) and Together (`https://api.together.xyz/v1`) URLs are **not verified yet** — stretch goal, check before use.

### External databases (`backend/app/external/`)
- Shared `cached_http.CachedJSONClient`: JSON GET, disk cache `DATA_DIR/cache/http/<source>/<sha256>.json` with no expiry (reproducible runs), 404 cached as "not found", retries on 429/5xx/network. Rate limits per process: NCBI 3/s, PubChem 4/s, ChEMBL 4/s.
- PubChem (verified): property names are now `SMILES` / `ConnectivitySMILES` (not `IsomericSMILES` / `CanonicalSMILES`); unknown name → 404. Structure lookup uses `/compound/smiles/property/…/JSON?smiles=` (query parameter, since SMILES contain `/`, `#` and `\`).
- **ChEMBL deviation from spec §5:** `/molecule/search` ranks unrelated molecules first (ALLN → an "oxalate hydrolytic enzyme" entry, all four AZ-letter codes → one unrelated molecule, taxol → CHEMBL306601, methotrexate → CHEMBL2074969). We match only exactly: `pref_name__iexact`, then `molecule_synonyms__molecule_synonym__iexact`, then the PubChem InChIKey (`molecule_structures__standard_inchi_key`); otherwise "not found". Mechanisms are filtered by `parent_molecule_chembl_id` (ChEMBL_37 stores e.g. vincristine's mechanism on a salt form: `molecule_chembl_id=CHEMBL90555` → 0 records, parent filter → "Tubulin inhibitor").
- ChEMBL has a curated mechanism for **14 of the 38** compounds (none for nocodazole, taxol's entry has "Tubulin inhibitor" only, none for most research compounds and AZ codes). The UI must say "no curated mechanism in ChEMBL" rather than leave a gap.
- **Identity of the unblinded specimen:** BBBC021 internal codes match unrelated PubChem synonyms by name ("AZ-C" → Alizarincomplexone, "AZ-U" → Anthrarufin). So for the specimen's own compound `lookup_compound` checks the name hit against the dataset SMILES (same InChIKey connectivity block). Result over 38: 28 agree, 2 flagged mismatches (ALLN, cisplatin — the dataset's cisplatin SMILES resolves to a "Cantide + Cisplatin" record; the name hit is kept and the mismatch shown), 2 from the structure only (AZ258, AZ841), 1 name only (cytochalasin D: its dataset SMILES is not in PubChem), 4 shown as "no public identity" (AZ-A/C/J/U have no structure in BBBC021), 1 not found anywhere (AZ138). `identity_check` is part of the evidence.
- `NCBI_EMAIL` is empty in `backend/.env`; requests then carry `tool=musecell` only. The e-mail (when set) is kept out of cache keys and cache files. The human should set it.
- Integration test for nocodazole: `pytest --run-slow` (skipped by default).

### Agent tools (`backend/app/agent/`)
- `specimens.py`: specimen = one MoA-annotated compound with all its doses (38). Opaque salted ids `s_…` (specimen) and `w_…` (well). Plate names and well positions are never sent: with the public metadata they reveal the compound. Replicates are numbered 1..n per dose (the dataset's `replicate` column repeats within a dose for 17 treatments).
- Strict schemas are hand-written (`additionalProperties:false`, all properties required); numeric ranges, `minItems` and the 240-char rationale limit are stated in descriptions and **enforced server-side**, not as JSON-schema keywords (strict-mode support for `maxLength`/`minimum` on the Meta API is unverified). An over-long rationale is cut to 240 chars with "…" rather than rejected (a rejection wastes a model turn). The Meta API accepted all 7 blinded schemas (smoke test).
- Phase gating: blinded tools only while `phase == "blinded"`; `lookup_compound` / `search_literature` only after unblinding (or in `open` mode). `submit_conclusion` moves the run to `review`; unblinding itself is Phase 4.
- `acquire_well`: top 8 features by |z| (spec "±8" read as top-8 by absolute z, as in `compare_to_dmso`), with raw value and same-plate DMSO median. Duplicate acquisition → error, no budget spent. Budget 1–8 enforced in `ToolContext`.
- `describe_phenotype`: ≤ 4 fields per call; one same-plate DMSO field per plate; per field the composite (≤ 1024 px; notable-cell coordinates refer to it) + DNA/tubulin/actin grayscale (≤ 512 px). The response schema's `field_id` enum is the treated field ids; invented ids are dropped anyway. `unknown` is allowed as an MoA only here. Cost: 12 images ≈ 7,050 input tokens, ~3,100 output (≈ 2,600 reasoning), ≈ $0.022 and ~14 s per call.
- Image ids are `<field_id>.<image>` (e.g. `f_….composite`) for `/api/images/{image_id}`.
- `update_hypotheses` and `submit_conclusion` accept only the 12 classes from `BBBC021_v1_moa.csv` (case-insensitive, stored canonical); probabilities renormalised; unknown evidence ids rejected.
- Blinding-leak scanner terms: compound name, PubChem synonyms (first 50), dataset SMILES, plate names, plate/well keys; matched case-insensitively on word boundaries with separators collapsed (`R 17,934` = `R-17934` = `R17934`); terms < 4 chars ignored.

### Smoke test (`scripts/tool_smoke.py`, output `docs/probe/tool_smoke.json`)
- Specimen `s_01943ff958` (blinded; PD-169316, Kinase inhibitors). 10 tool calls, 0 failed, leak scan clean (113 terms). One real Scientist call (schema check; it called `list_available_experiments` with a sensible rationale) + one real Microscopist call.
- **Cost of the final smoke run: $0.0251** (schema check $0.0029, Microscopist $0.0222). Phase-3 API spend today incl. two earlier smoke attempts: $0.0800.

## Phase 4 — agent loop, reviewer, SSE, run store, replay (2026-10-29)

### Loop (`backend/app/agent/loop.py`)
- One `AgentLoop` per run: blinded tool loop → PI review (≤ 1 revision round) → unblinding → literature → reflection → dossier. The API and `scripts/run_agent_cli.py` share it through `runs/manager.py`.
- **Turn cap split** (spec: max 20 model turns per run): blinded 13, revision 4, literature 2, reflection 1. The last blinded/revision turn offers only `submit_conclusion`; a call to a tool not offered that turn is refused server-side (one tool message per call id is still sent). No conclusion → the run fails visibly; nothing is filled in.
- Parallel tool calls run in the order given; each gets one tool message with its call id, same order. Calls after a successful `submit_conclusion` in the same batch get a phase error, not silence.
- The assistant message is re-sent as a plain dict (`role, content, tool_calls[id, type, function{name, arguments}]`), not the SDK object's dump.
- Visible assistant text (not hidden reasoning) is streamed as `thinking {note}`. After unblinding, notes pass the citation validator too (a note in the first live run cited PMIDs).
- **Unblinding:** the protocol (code, not the model) runs `lookup_compound` on the specimen as a recorded step + evidence **without** its own `tool_call`/`tool_result` events, so the `unblinded` event is the first payload that names the compound (first draft emitted the lookup's `tool_call` one event earlier; the test caught it). Verdict = exact match of canonical MoA strings, computed by code.
- **Blinding guard at runtime:** during `blinded`/`review` every event (except `error`/`run_finished`) and every request to a model (Scientist messages + tools, tool results, reviewer ledger) is scanned with `BlindingScanner` (name, PubChem synonyms found via the dataset SMILES, SMILES, plates, well keys). A hit stops the run with an error that never names the term; the leaking evidence/steps are blanked in the run state so `GET /api/runs/{id}` cannot show them. Internal codes without SMILES get no PubChem synonyms (a name lookup would return an unrelated compound's synonyms).
- Reflection is a separate strict-JSON call on a fresh context (ledger summaries + literature list), not the long tool history: smaller, and no tool-history/response_format mix on an unverified API path.

### Reviewer (`agent/reviewer.py`)
- `muse-wonder-1.0`, strict schema `{verdict: approve|request_experiment, critique, requested_experiment}`; "no request" is an empty string (nullable types in strict mode are unverified on the Meta API). `max_tokens=12000` because Wonder reasons first. It sees the same blinded ledger as the Scientist. No second review after a revision (cost; the spec allows one revision round).

### Dossier (`agent/dossier.py`)
- Markdown + JSON from the run state, by code. Every PMID in every reflection field and in the citation list counts as "cited"; only PMIDs `search_literature` returned in this run survive; label "Citations verified: n/m". Files: `runs/<run_id>.dossier.{md,json}`, run state `runs/<run_id>.json`, events `runs/<run_id>.jsonl` (`/runs/` is gitignored now, not just `*.jsonl`).

### API (`backend/app/main.py`)
- SSE without extra packages (`StreamingResponse`): `id:` = event seq, `event:` = type, heartbeat comment every 15 s, resume via `Last-Event-ID` or `?after=`. A late subscriber gets the whole run so far.
- Startup: no data → replay-only; no `MODEL_API_KEY` → replay-only; key set → `GET /v1/models` and refuse to start if an id is missing. `/api/images/{f_….composite|dna|tubulin|actin}` only (regex), falling back to `demo/thumbnails/` for recorded runs.
- Replay: recorded events unchanged on a new `replay_…` bus with original pacing / speed (0.25–20×); one recorded pause is capped at 60 s; `run_started` gains `replay {replay_of, recorded_at, model_ids}` for the badge. Replays are not re-recorded.
- `model_ids.segmentation` says `classical (scikit-image)`: SAM did not win the bake-off, so the UI must not claim SAM 3.1 for masks.

### Live runs (budget 4; specimens drawn with `random.Random(4).sample`, not picked by MoA)
| Run | Specimen | Wells | Turns | Review | Conclusion | Truth (compound) | Verdict | Citations | Cost |
|---|---|---|---|---|---|---|---|---|---|
| run_87e2787d1f1e | s_7526ba207c | 4/4 | 14 | request → free re-look at low dose | Microtubule stabilizers 0.58 → 0.68 | Eg5 inhibitors (AZ-C) | wrong | 5/5 | $0.4074 |
| run_39e2d32f1edc | s_89c9462717 | 3/4 | 11 | approve | Microtubule stabilizers 0.94 | Microtubule stabilizers (taxol) | correct | 3/3 | $0.2147 |
| run_93391a0830e0 | s_2cdd0232ac | 4/4 | 12 | approve | Protein synthesis 0.70 | Epithelial (PP-2) | wrong | 3/3 | $0.3388 |
- 1/3 correct. Both misses follow the atlas/tubulin signal into a neighbouring class (Eg5 vs stabilizer; Epithelial is the baseline's weakest class, 1/8). In run 1 the reviewer pointed at the right problem (the 4-dose atlas favoured Eg5) but the requested free check did not flip the call. Agent behaviour: it acquires high + mid dose first, then lower doses; it used replicates 0 times. Not tuned here on purpose (the 38 compounds are the eval set).
- Leak re-check of the three recorded logs (all events before `unblinded`, 55/156/109 terms): clean. Phase-4 API spend: $0.96 for the three runs; ≈ $1.07 for the day.
- 2026-10-29 — Correction to the Phase-3 note: `NCBI_EMAIL` **is set** in `backend/.env` (checked without printing it; `Settings.ncbi_email` loads). PubMed requests carry `tool=musecell&email=…`; the Phase-4 live runs already used it. No action needed.

## Phase 5 — frontend (2026-10-29)

### Stack and structure
- `frontend/`: Vite 5.4.21, React 18.3.1, TypeScript 5.6, Tailwind 3.4.15, framer-motion 11.11.17, lucide-react 0.460, exact pins. Dev on 5174 (`strictPort`), `/api` → 127.0.0.1:8787. Build → `frontend/dist`, served by FastAPI's static mount at http://127.0.0.1:8787 (checked).
- `npm audit`: one moderate advisory left (esbuild ≤ 0.24.2, **dev server only**: a website could read responses from a running `vite` dev server). The fix is Vite 6+/8, a major upgrade; not done mid-hackathon. Production is the built bundle served by FastAPI, so it is unaffected. postcss and vite were bumped to patched versions.
- Hash routing (`#/run/<id>`, `#/dossier/<id>`, `#/benchmark`) so the static mount needs no SPA catch-all route.
- Tokens from spec §8 are the **only** Tailwind colours (the default palette is removed), so a non-spec colour cannot slip in. Fonts: Source Serif 4 (narrative, headings) + IBM Plex Sans (UI, numbers; `tabular-nums` on body).
- `useRunEvents(runId)` is the single source of run state: one EventSource, a listener per named event type (the server sets `event:`, so `onmessage` never fires), reducer in `lib/runState.ts`, dedupe by `seq`, reconnect via the browser's Last-Event-ID and then manual `?after=<seq>` with backoff; closes on `run_finished`. Replays use the same screen; `run_started.replay` drives the permanent badge.
- framer-motion is used for the unblinding only (≈1.2 s: name → structure 0.3 s → ChEMBL 0.6–0.8 s → verdict 1.0 s), plus the header label swap that is part of the same moment. Hypothesis bars use a CSS width transition. `prefers-reduced-motion` disables both (framer `useReducedMotion` + a global CSS rule).

### Backend additions for the UI (tests in `test_api.py`)
- `GET /api/recorded`: finished recorded runs for "Watch a recorded run" — specimen label, date, wells, cost, model ids; **no compound, MoA or verdict** (a replay is still a blinded story; test runs the blinding scanner over it).
- `POST /api/runs/{id}/stop`: cancels a live run (the loop already emits "The run was stopped.") or a replay (emits `error` "Replay stopped." + `run_finished`). The spec's "■ Stop" needed it.
- `GET /api/runs/{id}/dossier.{md,json}`: the files the loop writes; a replay id maps to its source run.
- `GET /api/fields/{field_id}/cells` (per-cell measurements from the atlas cache, centroids as fractions, phenotype flags vs `reference.json`) and `/labels` (cells + nuclei label maps at half resolution, uint16 LE, gzip ≈ 14 KB). Used for mask outlines and hover hit-testing, so every hover number is the one `cell_features` computed. 404 when the cache is absent (fresh clone) — the UI says so.
- `GET /api/eval/{results|baseline}`: the eval files as written.

### `eval/results.json` contract for Phase 6 (the Benchmark page reads this)
`{generated_at, models: {role: id}, n_compounds, blinding_leak_test, notes?: [str], rows: [{method, label, budget (wells per compound, null = all wells → drawn as a reference line), accuracy (0–1), correct?, n?, mean_wells?, mean_cost_usd?, mean_latency_s?, citation_validity? (0–1)}]}`. Rows with the same `label` form one line on the chart. Until the file exists the page shows an empty state plus the measured NSC baseline from `eval/baseline.json`. The chart was checked with a clearly labelled fixture injected by Playwright route interception (scratchpad only, never saved to the repo).

### Display choices
- Microscope: the three grayscale channel PNGs are composed on a canvas, each tinted with its stain token (additive). Masks are drawn in neutral white/grey (they are not stains). If a field has no channel PNGs (some demo thumbnails) the channels are split from the 8-bit composite, so the toggles still work and a note says so.
- Spec §8 model line names SAM 3.1 for segmentation, but SAM lost the Phase-2 bake-off; the UI shows `model_ids.segmentation` from the server ("classical watershed (scikit-image)"), never a hard-coded SAM claim.
- Plate map shows 3 replicate columns (more only if the agent used them); doses with more wells (taxol 0.3 µM has 21: plate positive control) show "+n".
- Tool names are shown in plain words ("Acquire well", "Describe phenotype"); feature ids map to plain names (`tub_glcm_contrast` → "Tubulin texture"), raw id in the tooltip.

### Screenshot critique (`scripts/screenshots.py` → `docs/screens/`, 1280×720, replay of run_39e2d32f1edc / run_87e2787d1f1e)
Five issues found against §8 in the first pass, all fixed:
1. **Stain colours inconsistent.** Thumbnails (notebook, dossier) showed the raw composite (pure RGB) while the microscope used the token tints, so one stain had two colours. Fix: an SVG `feColorMatrix` (`.stain-tint`) maps composite R/G/B onto the actin/tubulin/DNA tokens, matching the canvas composite exactly.
2. **Hover card escaped the microscope pane** over the plate map, and its estimated height let it overflow the bottom. Fix: clamp to the pane horizontally, measure the card height, `overflow-hidden` on the pane.
3. **Plate map header wrapped** ("Rep / 1") and labels read ambiguously ("8 1 µM"). Fix: "Replicate" column group with 1–3, dose index and concentration in separate columns. (The taxol replay then showed 21 replicates overflowing; capped as above.)
4. **Unblinding did not fit 1280×720.** A 200-px empty structure placeholder pushed the reflection below the fold. Fix: no placeholder box (one line of text), 150-px structure, centre area grows from 56 % to 66 % of the height on the Unblinding tab.
5. **Dossier experiments table cramped** ("Rep Cells" headers touching, evidence id against the thumbnail). Fix: fixed column widths, right-aligned numbers, padding, evidence id on its own line.
Also fixed: duplicate "(blinded)" + "Blinded" chip in the run header; chart end-labels overlapping when values are close; the specimen radiogroup now has one tab stop + arrow keys; and a real bug the screenshot script's console check caught — EventSource's native `error` event (no data) hit the listener for the server's `error` event type → `JSON.parse(undefined)`. The script now fails on any console error; final pass: 0.

### Not done / known
- Thinking notes in the three recorded runs are cut at 1,000 chars by the loop version that recorded them (current loop: 2,000); the UI shows them as recorded.
- Recorded runs live in gitignored `runs/` for now; `demo/runs/` + thumbnails are Phase 6. In a fresh clone without DATA_DIR, masks/hover and channel images are unavailable (the UI says so).

## Phase 6 — evaluation and demo recordings (2026-10-29)

### Eval (`scripts/evaluate.py` → `eval/results.json`, `eval/RESULTS.md`, `eval/accuracy_vs_budget.png`)
- All 38 annotated compounds × budgets 2/4/6 = 114 full agent runs (blinded loop, PI review, unblinding, literature, reflection). Scientist/Microscopist/reflection on **`muse-spark-1.3-contributor`** (user's choice after the cost estimate; public data; labelled in every output), reviewer `muse-wonder-1.0`. Estimate printed first: ~$12 (≤ $16); actual **$14.60** for the 114 logged runs.
- Baselines (no API): (a) NSC on all wells = the published protocol already in `eval/baseline.json` (treatment profiles, compound majority vote); (b) NSC on k random wells of the specimen = 1-NN per well, majority vote, mean ± sd over 500 draws. (b) votes over single wells, so it is not identical to (a) at k = all; noted in the results.
- Per-run records in `eval/agent_runs.jsonl` (committed, ~70 KB) so the tables can be rebuilt with `--aggregate-only`; run logs in gitignored `runs/eval/`.
- Failed runs count as wrong and are listed: one stopped by the **runtime blinding guard** (the Microscopist's description of a taxol specimen matched an identifying term; the guard stopped it before any event carried the term), one where the contributor Scientist spent all 8,000 tokens on reasoning. Not re-run, not tuned (the 38 compounds are the eval set).
- One more run (s_66b994c89e, budget 2) crashed on our own bug (below); its record was removed and the run repeated.
- Blinding-leak test: pytest leak/blinding tests 13 passed; offline re-scan of all 114 eval logs (events before `unblinded`): 0 hits.

### Result: the agent is below the baseline
Agent 42.1 / 36.8 / 42.1 % at 2 / 4 / 6 wells vs NSC on the same number of random wells 50.5 / 57.8 / 61.3 % and NSC on all wells 71.0 %. More wells do not help the agent. Error analysis (in RESULTS.md, computed by the script):
- **Over-calling one class:** "Microtubule stabilizers" is the agent's call in 35 of 114 runs (9 true). Strong cytoskeletal/mitotic phenotypes (stabilizers 8/9, destabilizers 12/12, Aurora 9/9) are nearly always right; subtle classes are never right (Actin disruptors, Cholesterol-lowering, Eg5, Epithelial, Protein degradation: 0 correct; Epithelial/Protein degradation/Cholesterol-lowering are never even called).
- Our reading: the model's prior follows the most visible feature (tubulin texture/intensity z-scores, which are large for many treatments) more than the atlas vote, and it rarely calls classes whose phenotype is weak. The NSC baseline has no prior and simply follows the nearest profile. Same failure mode as the Phase-4 misses.
- The PI review rarely changes the outcome: 46 requests, final call wrong→right in 3 runs, right→wrong in 1.
- Citation validity 100 % (320/320 cited PMIDs were returned by `search_literature` in the run).
- Caveat: the eval ran on the contributor tier; Phase 4's three full-price runs (1/3 correct) do not suggest a large difference, but full-price accuracy is not measured here.

### Bug found: cost ledger torn by two processes
Running the eval and the demo recorder at the same time made two processes append to `DATA_DIR/cache/costs/<day>.jsonl`; on Windows one line was overwritten mid-record and `DailyLedger.total_today()` raised `JSONDecodeError`, which crashed the eval. Fix: `add()` appends under a cross-process lock (`msvcrt.locking` / `fcntl.flock`), `total_today()` skips an unreadable line instead of crashing (the lost record was ≈ $0.001). Tests: torn line skipped (fails without the fix); 4 processes × 200 appends stay whole (passes on old code too — the race did not reproduce in the test, so it guards the lock but does not prove it).

### Demo recordings (`scripts/record_demo_runs.py` → `demo/`)
- Specimens ranked by phenotype strength (max norm of the robust-z treatment profile), one per MoA class, strongest first — chosen before seeing outcomes. Recorded on full-price `muse-spark-1.3` / `muse-wonder-1.0`, budget 5:
  | Run | Class strength rank | Review | Verdict | Cost |
  |---|---|---|---|---|
  | run_78be77167c4d | 1 (stabilizer) | approve | correct | $0.198 |
  | run_19b4ee810120 | 2 (Aurora) | request_experiment | correct | $0.372 |
  | run_022fd8765474 | 3 (actin) | request_experiment | wrong | $0.491 |
  | run_cae9422218ee | 4 (cholesterol) | request_experiment | wrong | $0.533 |
- Leak re-scan of the four logs: 0 hits. Events, state, dossier copied to `demo/runs/` (0.8 MB).
- Thumbnails: spec limit ≤ 40 PNGs / ≤ 15 MB, but the four runs reference 62 well/DMSO fields. Kept: the first field of every acquired well, then each well's same-plate DMSO field, then other fields, round-robin; composites downscaled to 512 px → 40 PNGs, 12.1 MB. Channel PNGs are not shipped (the microscope splits the composite, as before). Fields left out render as a labelled "not in demo" box (`FieldThumb`) instead of a broken image.

### Fresh-clone check
`git clone` into a scratch folder, `npm ci; npm run build`, uvicorn on **8797** (8787 was occupied by a running dev server on this machine) with `DATA_DIR` pointing to an empty folder and no `backend/.env`. `/api/health`: `live_runs false`, data and key errors as designed; `/api/recorded` lists the 4 demo runs; Playwright replayed each at 20× to `run_finished`: replay badge, verdict and dossier link shown, every served thumbnail loaded. The browser logs 404s by design — in replay-only mode: channel PNGs (3 per viewed field, fallback to composite), cell tables/label maps (masks/hover need DATA_DIR; the UI says so), composites beyond the 40-PNG cap. `scripts/screenshots.py` fails on any console error, so it should be run with data present.

### Spend
Phase 6: ≈ $16.2 (eval $14.60 + demo $1.59 + in-flight runs lost in the crash). Day total $17.28 of the $20 cap.

## Phase 7 — README, hero GIF, release checks (2026-10-29)

- **README** rewritten per spec §10. Model-role table links to the call sites (`loop.py#L166`, `tools.py#L337`, `reviewer.py#L66`, `llm.py#L175/L198/L218`). The results table is copied verbatim from `eval/RESULTS.md`; the README says plainly that the agent is below the NSC baseline. SAM 3.1 is described as what it is here (bake-off + independent nuclei check; classical segmentation produces the masks), not as the production segmenter.
- **Hero GIF** `docs/hero.gif`: `scripts/record_hero_gif.py` replays `run_19b4ee810120` (Aurora, reviewer requests an experiment, correct; chosen because it shows the full story incl. a review request) at 6× and films it with Playwright (1280×720), then ffmpeg → 960 px, 10 fps, 128-colour palette: 25 s, **6.75 MB** (≤ 8 MB). No system ffmpeg on this machine: the converter uses the binary bundled in `imageio-ffmpeg` (added to `requirements-dev.txt`); Playwright's video capture needed `python -m playwright install ffmpeg` (its own small build). The source `docs/hero.webm` is kept locally for the video edit and gitignored.
- **Footgun fixed in docs:** `scripts/evaluate.py --aggregate-only` keeps only log records whose scientist model equals `--model` (default `muse-spark-1.3`), so the documented command without `--model muse-spark-1.3-contributor` rewrote `eval/` with no agent rows. Outputs restored from git; usage line now includes `--model`. With it, the rebuild reproduces `results.json` rows exactly (only `generated_at` and the pytest timing differ).

### Definition of done (spec §11), 2026-10-29
| Item | Result |
|---|---|
| Fresh clone → README quickstart, replay mode, no key | Pass. Local clone into the scratchpad, new venv, `pip install -r backend/requirements.txt`, `npm ci; npm run build`, no `backend/.env`, `DATA_DIR` = empty folder, port 8799 (8787 busy here). Health: `live_runs false` with both reasons. Playwright replayed all 4 recorded runs at 20× to the reflection: verdicts 2 match / 2 do not match, citations verified 3/3, 3/3, 3/3, 4/4, dossier page + `dossier.md` HTTP 200; benchmark page renders. |
| Live run with a key on ≥ 3 specimens | Pass on existing evidence, no new spend: 4 live full-price demo runs (29 Oct, recorded with the code committed in `b3100c9`; `git diff b3100c9 HEAD -- backend/` is empty) + 114 live eval runs. |
| pytest green incl. blinding-leak + citation tests | Pass: 93 passed with `--run-slow` (92 + 1 network test skipped by default); `-k "leak or blinding or citation"`: 13 passed. |
| `eval/RESULTS.md` generated by the script | Pass: regenerated from `eval/agent_runs.jsonl`, identical apart from timestamp/test timing (committed file kept). |
| No secrets in git history | Pass, with a note: `git log -p --all` contains 6 lines with `LLM_`, all documentation (the spec's own checklist line, the guard regex, DECISIONS notes), so the literal `Select-String "LLM_"` is not empty. The real patterns `LLM_[A-Za-z0-9_]{10,}` / `sk-[A-Za-z0-9]{20,}`: 0 matches in 11 commits. The actual key values in `backend/.env` and `_legacy/` occur 0 times in history; no `.env` or `_legacy` file was ever tracked; `check_secrets.py` over all tracked files exits 0. |
| Repo public, opens logged out | **Not done (human):** `https://github.com/aleksandrahodzzik/musecell` returns 404 logged out; `origin/main` is at `7c3bd77` (5 commits behind before this phase). |
| Video ≤ 3:00, CV form | **Not done (human).** |
