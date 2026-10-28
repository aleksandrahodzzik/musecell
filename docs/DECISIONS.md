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
