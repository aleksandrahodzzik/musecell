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
