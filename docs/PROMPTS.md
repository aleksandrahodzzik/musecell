# MuseCell — prompts for Cursor, phase by phase

How to use: open a **new Agent chat** in Cursor for each phase (Ctrl+I → Agent mode, model: the strongest Claude available).
Paste the prompt. Let it finish. Read its report. Only then start the next phase.
If a phase breaks, use the **Debug prompt** at the bottom instead of starting over.

---

## KICKOFF + PHASE 0 — hygiene, setup, API probe (today)

```
You are the lead engineer on MuseCell, our entry for the Meta AI Developer Hackathon (deadline Nov 4, 9 PM ET).
Read docs/MASTER_SPEC.md completely and .cursor/rules/musecell.mdc before doing anything. Follow them strictly.

Do PHASE 0 only:

1. Repo hygiene
   - The folder contains an old prototype in backend/ (main.py with a HARD-CODED API key, index.html, .env). Move the whole old backend/ folder to _legacy/ (gitignored). Do not copy any of its code forward.
   - git init (if needed), branch main. Create the repo layout from spec §7 with empty packages, LICENSE (MIT, holder "Oleksandra Khodzinska"), DATA_LICENSE.md (spec §2 text), README.md stub, docs/DECISIONS.md, .env.example (spec §7). The .gitignore already exists — keep it.
   - Add a tiny pre-commit secret check (scripts/check_secrets.py + .git/hooks/pre-commit that runs it) blocking strings matching LLM_[A-Za-z0-9_]{10,} or sk-[A-Za-z0-9]{20,}.
2. Python env (Windows PowerShell): create .venv with Python 3.12, write backend/requirements.txt (spec §7 stack, pinned to currently installed latest versions), install it.
3. backend/.env: create it from .env.example and tell me to paste my NEW key into MODEL_API_KEY myself. Do not ask me to paste the key into chat. Wait for my "key is in".
4. scripts/probe_meta_api.py — runs 5 checks and prints a clear PASS/FAIL table plus token usage and $ cost of each call:
   a) GET /v1/models → print all ids; assert muse-spark-1.3, muse-wonder-1.0, sam-3.1 exist.
   b) Text call to muse-spark-1.3 (Chat Completions).
   c) Vision call: send any small PNG (generate one with numpy: a few bright blobs on black) as base64 data URL; ask for a JSON description using response_format json_schema strict.
   d) Tool calling: one function tool with strict schema; run the full call → tool result → final answer loop.
   e) SAM 3.1: responses.stream with input_text "circle" + the same PNG, metadata include_confidence "true"; parse with meta-sam-parser (pip install it; read its source to find the correct parse functions); print boxes + confidences.
   Also try reasoning effort parameter on muse-spark-1.3 (check https://dev.meta.ai/docs/reasoning for the exact field) and record the result.
5. Write every finding (exact request shapes that worked, any surprises) to docs/DECISIONS.md.
6. Commit: "chore: project skeleton, secret guard, Meta API probe". Report back with the PASS/FAIL table and total $ spent. STOP.
```

---

## PHASE 1 — data (BBBC021)

```
Read docs/MASTER_SPEC.md §2 and docs/DECISIONS.md. Do PHASE 1 only.

1. scripts/fetch_bbbc021.py: download the 3 metadata CSVs into DATA_DIR, print real column names and 3 sample rows of each, then build the subset plan (38 MoA-annotated compounds, all their concentrations, plus DMSO wells on the same plates). Print: number of plates, list, estimated GB. STOP and show me the plan before downloading anything big.
2. After I approve: implement ranged reads with remotezip (only the needed TIFFs); fall back to full zip + extract + delete if Range is not supported. Resumable, idempotent, progress bars, size checks.
3. scripts/preprocess.py per spec §2: robust per-plate contrast from DMSO, float32 npy per channel, composite PNG (DNA blue, tubulin green, actin red, max side 1024) + per-channel PNGs, data/index.parquet with opaque field ids.
4. Make 6 example composites (DMSO + 5 different MoAs) and save to docs/probe/ so I can eyeball them.
5. Tests: index integrity (every annotated compound has ≥1 concentration with images; every plate has DMSO).
6. Commit. Report counts (fields, wells, compounds, MoA classes) and disk usage. STOP.
```

---

## PHASE 2 — segmentation, features, atlas, baseline

```
Read docs/MASTER_SPEC.md §3 and docs/DECISIONS.md. Do PHASE 2 only.

1. science/segmentation.py: classical path (nuclei + cells) and SAM 3.1 path (backend/app/sam.py with disk cache keyed by field id + prompt). Bake-off on the 6 probe fields: save mask overlays to docs/probe/, print object counts per method, pick the winner per object type, record in DECISIONS.md. Keep both selectable via config.
2. science/features.py: per-cell and per-field features from spec §3; well profile; robust z-score vs same-plate DMSO; treatment profile.
3. scripts/build_atlas.py: compute and cache profiles for every annotated compound-concentration (parallelize with multiprocessing; show progress). science/atlas.py with LOCO neighbors.
4. Baseline: NSC (nearest compound) MoA accuracy under LOCO. Print a confusion matrix and accuracy. Save to eval/baseline.json.
5. science/stats.py: dose_response and compare_to_dmso.
6. pytest for features (synthetic image with known blobs → known counts/areas) and LOCO exclusion.
7. Commit. Report: segmentation choice, baseline accuracy, runtime per field. STOP.
```

---

## PHASE 3 — LLM client, external tools, agent tools

```
Read docs/MASTER_SPEC.md §4–§5 and docs/DECISIONS.md. Do PHASE 3 only.

1. backend/app/llm.py: LLMClient per spec §4 (provider config, startup model check via GET /v1/models, retries with backoff on 429/5xx, timeouts, usage → $ cost with the price table, per-run and daily caps).
2. external/pubchem.py, chembl.py, pubmed.py with httpx, disk cache, rate limits (NCBI ≤ 3 req/s with tool+email params). Each returns typed Pydantic models. Integration test hitting the real APIs for "nocodazole" (mark as slow).
3. agent/tools.py: the 9 tools from spec §5 with strict JSON schemas (every tool has required `rationale`), Pydantic validation of args, server-side budget enforcement, evidence registration. Microscopist tool uses muse-spark-1.3 vision with the strict schema in spec §4, sending treated + same-plate DMSO images AND our measured top features.
4. agent/schemas.py: AgentRun, Evidence, Hypothesis, events.
5. Guardrail code + tests: MoA enum validation and probability renormalization; citation validator (only PMIDs returned in-run survive); blinding-leak scanner over any payload.
6. A CLI smoke test: scripts/tool_smoke.py runs each tool once on one blinded specimen and prints results + cost.
7. Commit. Report cost of the smoke test. STOP.
```

---

## PHASE 4 — agent loop, reviewer, SSE, run store, replay

```
Read docs/MASTER_SPEC.md §5 and docs/DECISIONS.md. Do PHASE 4 only.

1. agent/prompts.py: system prompt for the Scientist. It must: explain the blinded protocol, the budget, the tools, that every tool call needs an honest short rationale, that it should acquire wells to DISCRIMINATE between its top hypotheses (not to confirm), call update_hypotheses after each informative result, and stop when confident or out of budget. Include the 12 MoA class names loaded at runtime. No example answers that could leak labels.
2. agent/loop.py: Chat Completions tool loop with parallel tool calls handled correctly (one tool message per call id, in order), max 20 turns, caps, emits events per spec §5 to an asyncio queue and runs/<run_id>.jsonl.
3. agent/reviewer.py: muse-wonder-1.0 PI review with strict schema; at most one revision round.
4. Unblinding → lookup_compound + search_literature → reflection → dossier (Markdown + JSON) with citation validation. Verdict computed by code.
5. API: POST /api/runs {specimen_id, budget, mode} → run_id; GET /api/runs/{id}/events (SSE, with heartbeat); GET /api/runs/{id}; GET /api/specimens (opaque ids only); POST /api/runs/{id}/replay?speed=; GET /api/images/{image_id}; GET /api/health. Serve frontend/dist at / when it exists.
6. scripts/run_agent_cli.py: run one blinded specimen in the terminal, pretty-print events, print verdict and cost.
7. Run it on 3 different specimens. Paste the summaries. Commit. STOP.
```

---

## PHASE 5 — frontend

```
Read docs/MASTER_SPEC.md §8 (follow tokens, type, layout, copy rules exactly) and §5 event types. Do PHASE 5 only.

1. Scaffold frontend/ with Vite + React 18 + TS + Tailwind + framer-motion + lucide-react. Dev server port 5174 with /api proxy to 8787. Fonts: Source Serif 4 + IBM Plex Sans (Google Fonts), tabular numbers.
2. Screens: Choose a specimen; Run view (plate map, microscope with channel toggles + masks + same-plate DMSO compare + zoom/pan + hover measurements, hypothesis board, notebook timeline); Unblinding moment; Dossier (printable, export MD/JSON); Benchmark page reading eval/results.json.
3. A single useRunEvents(runId) hook: EventSource, typed events, reconnect, reducer-based state. Replay uses the same UI with a permanent "Replay of a live run · date · models" badge.
4. Error and empty states per copy rules. Keyboard focus visible. prefers-reduced-motion respected. Looks right at 1280×720.
5. Build to frontend/dist and confirm FastAPI serves it at http://127.0.0.1:8787.
6. Take screenshots of each screen (Playwright script scripts/screenshots.ts or .py) into docs/screens/ and critique them yourself against spec §8: list 5 concrete issues and fix them.
7. Commit. STOP.
```

---

## PHASE 6 — evaluation and demo recordings

```
Read docs/MASTER_SPEC.md §6. Do PHASE 6 only.

1. scripts/evaluate.py per spec §6. First print the cost estimate and ask me. Allow --model muse-spark-1.3-contributor for cheaper eval (label it in results).
2. Produce eval/results.json, eval/RESULTS.md, eval/accuracy_vs_budget.png.
3. scripts/record_demo_runs.py: record 4 live runs (pick specimens with visually clear phenotypes and at least one where the reviewer requests an experiment), copy their events + needed thumbnails into demo/ (respect size limits).
4. Verify a fresh clone with no MODEL_API_KEY can open the UI and play the recorded runs.
5. Commit. Report the results table verbatim. STOP.
```

---

## PHASE 7 — README, polish, release checks

```
Read docs/MASTER_SPEC.md §9–§11. Do PHASE 7 only.

1. README.md per spec §10, with a Mermaid architecture diagram, model-role table linking to code, Windows quickstart, results table pulled from eval/RESULTS.md, limitations, data attribution, research-use disclaimer.
2. Record a hero GIF of a replay run (Playwright video → ffmpeg to GIF ≤ 8 MB) into docs/.
3. Run the definition-of-done checklist in spec §11 and paste each result. Scan the whole git history for secrets.
4. Commit and tag v1.0. STOP.
```

---

## STRETCH — open-weights mode (only after Phase 6, only if Baseten/Fireworks/Together credits exist)

```
Add PROVIDER=baseten|fireworks|together to LLMClient using the base URLs and model ids in spec §4. Run the Scientist loop on muse-spark-open for the eval subset, add a column to eval/RESULTS.md, add a provider switch in the UI. Record one demo run on open weights.
```

---

## DEBUG prompt (use whenever something breaks)

```
Something is broken. Do not rewrite large parts of the code.
1. Reproduce the problem with the smallest command and show the full error.
2. State 2–3 hypotheses for the cause, then test them one by one with evidence.
3. Fix the root cause with the smallest change, add a regression test, re-run the original command and show it passing.
4. Note the cause in docs/DECISIONS.md. Commit.
Problem: <paste error / describe what you see>
```
