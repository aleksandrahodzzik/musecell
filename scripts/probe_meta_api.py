"""Phase-0 probe of the Meta Model API.

Runs: (a) model list, (b) text, (c) vision + strict JSON schema, (d) tool-calling
loop, (e) SAM 3.1 streamed segmentation, plus a reasoning-effort check.
Prints a PASS/FAIL table with token usage and $ cost per call, and writes
docs/probe/probe_results.json. Never prints the API key.

Usage: python scripts/probe_meta_api.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from PIL import Image

import openai
from meta_sam_parser import (
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

BASE_URL = os.getenv("MODEL_BASE_URL", "https://api.meta.ai/v1")
SPARK = "muse-spark-1.3"
WONDER = "muse-wonder-1.0"
SAM = "sam-3.1"
REQUIRED_MODELS = [SPARK, WONDER, SAM]

# USD per 1M tokens (input, output); SAM per image. Source: spec §4 / dev.meta.ai pricing.
PRICES = {SPARK: (1.25, 4.25), WONDER: (4.00, 20.00)}
SAM_PER_IMAGE = 2.50 / 1000


@dataclass
class Check:
    name: str
    status: str = "FAIL"
    detail: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


def token_cost(model: str, inp: int, out: int) -> float:
    pin, pout = PRICES[model]
    return inp / 1e6 * pin + out / 1e6 * pout


def add_chat_usage(check: Check, model: str, usage) -> None:
    if usage is None:
        return
    check.input_tokens += usage.prompt_tokens or 0
    check.output_tokens += usage.completion_tokens or 0
    details = getattr(usage, "completion_tokens_details", None)
    check.reasoning_tokens += (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
    check.cost_usd = token_cost(model, check.input_tokens, check.output_tokens)


def make_png() -> tuple[bytes, list[tuple[int, int, int]]]:
    """256x256 black image with three bright Gaussian blobs (circles)."""
    size = 256
    yy, xx = np.mgrid[0:size, 0:size]
    blobs = [(64, 70, 18), (180, 90, 24), (120, 190, 14)]  # (x, y, sigma-ish radius)
    img = np.zeros((size, size), dtype=np.float32)
    for x, y, r in blobs:
        img += np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * (r / 1.5) ** 2))
    img = np.clip(img, 0, 1)
    rgb = np.stack([img * 0.2, img * 0.6, img], axis=-1)  # bluish-white blobs
    buf = io.BytesIO()
    Image.fromarray((rgb * 255).astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue(), blobs


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def check_models(client: openai.OpenAI) -> Check:
    c = Check("a) GET /v1/models")
    ids = sorted(m.id for m in client.models.list())
    print("  model ids:", ", ".join(ids))
    missing = [m for m in REQUIRED_MODELS if m not in ids]
    c.extra["ids"] = ids
    c.status = "PASS" if not missing else "FAIL"
    c.detail = f"{len(ids)} models" + (f"; missing {missing}" if missing else "; all 3 required present")
    return c


def check_text(client: openai.OpenAI) -> Check:
    c = Check("b) text, muse-spark-1.3")
    r = client.chat.completions.create(
        model=SPARK,
        messages=[{"role": "user", "content": "In one sentence: what does DAPI stain in a cell?"}],
        # reasoning tokens count toward max_tokens; 400 was fully eaten by reasoning in the first run
        max_tokens=2000,
    )
    add_chat_usage(c, SPARK, r.usage)
    text = (r.choices[0].message.content or "").strip()
    c.status = "PASS" if text else "FAIL"
    c.detail = text[:120]
    return c


VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["background", "blob_count", "blobs"],
    "properties": {
        "background": {"type": "string"},
        "blob_count": {"type": "integer"},
        "blobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["position", "relative_size"],
                "properties": {
                    "position": {"type": "string"},
                    "relative_size": {"type": "string", "enum": ["small", "medium", "large"]},
                },
            },
        },
    },
}


def check_vision(client: openai.OpenAI, png: bytes, n_blobs: int) -> Check:
    c = Check("c) vision + json_schema strict")
    r = client.chat.completions.create(
        model=SPARK,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the bright blobs in this image."},
                {"type": "image_url", "image_url": {"url": data_url(png)}},
            ],
        }],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "blob_description", "schema": VISION_SCHEMA, "strict": True},
        },
        max_tokens=2000,
    )
    add_chat_usage(c, SPARK, r.usage)
    parsed = json.loads(r.choices[0].message.content)
    c.extra["response"] = parsed
    valid = set(parsed) == set(VISION_SCHEMA["required"])
    c.status = "PASS" if valid else "FAIL"
    c.detail = f"valid JSON, blob_count={parsed.get('blob_count')} (truth {n_blobs})"
    return c


TOOL = {
    "type": "function",
    "function": {
        "name": "count_cells",
        "description": "Return the measured cell count for a well.",
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["well_id", "rationale"],
            "properties": {
                "well_id": {"type": "string", "description": "Well id such as B04"},
                "rationale": {"type": "string", "description": "Why you are calling this tool"},
            },
        },
    },
}


def check_tools(client: openai.OpenAI) -> Check:
    c = Check("d) tool calling loop")
    messages: list[dict] = [{
        "role": "user",
        "content": "How many cells are in well B04? Use the tool, then answer with the number.",
    }]
    r1 = client.chat.completions.create(
        model=SPARK, messages=messages, tools=[TOOL], tool_choice="auto", max_tokens=1000,
    )
    add_chat_usage(c, SPARK, r1.usage)
    msg = r1.choices[0].message
    if not msg.tool_calls:
        c.detail = f"no tool call; finish_reason={r1.choices[0].finish_reason}"
        return c
    call = msg.tool_calls[0]
    args = json.loads(call.function.arguments)
    c.extra["tool_args"] = args
    messages.append(msg.model_dump(exclude_none=True))
    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps({"cell_count": 417})})
    r2 = client.chat.completions.create(
        model=SPARK, messages=messages, tools=[TOOL], tool_choice="auto", max_tokens=1000,
    )
    add_chat_usage(c, SPARK, r2.usage)
    final = (r2.choices[0].message.content or "").strip()
    c.extra["final"] = final
    ok = call.function.name == "count_cells" and args.get("well_id") == "B04" and "417" in final
    c.status = "PASS" if ok else "FAIL"
    c.detail = f"args={args}; final={final[:80]!r}"
    return c


def check_reasoning(client: openai.OpenAI) -> Check:
    c = Check("reasoning_effort (spark)")
    results = {}
    for effort in ["low", "max"]:
        try:
            r = client.chat.completions.create(
                model=SPARK,
                reasoning_effort=effort,
                messages=[{"role": "user", "content": "What is 17*23? Reply with the number only."}],
                max_tokens=4000,
            )
            before = c.reasoning_tokens
            add_chat_usage(c, SPARK, r.usage)
            results[effort] = {
                "ok": True,
                "answer": (r.choices[0].message.content or "").strip(),
                "reasoning_tokens": c.reasoning_tokens - before,
            }
        except openai.APIStatusError as e:
            results[effort] = {"ok": False, "error": f"{e.status_code}: {str(e)[:160]}"}
    c.extra["efforts"] = results
    c.status = "PASS" if all(v["ok"] for v in results.values()) else "FAIL"
    c.detail = "; ".join(
        f"{k}: {v.get('answer')!r} ({v.get('reasoning_tokens')} rt)" if v["ok"] else f"{k}: {v['error']}"
        for k, v in results.items()
    )
    return c


async def _sam(png: bytes, prompt: str) -> tuple[object, dict, str]:
    client = openai.AsyncOpenAI(base_url=BASE_URL, api_key=os.environ["MODEL_API_KEY"])
    request = dict(
        model=SAM,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url(png)},
            ],
        }],
        metadata={"include_confidence": "true"},
    )
    captured: dict = {}

    async def tap(events):
        async for event in events:
            if getattr(event, "type", "") == "response.completed":
                usage = getattr(event.response, "usage", None)
                captured["usage"] = usage.model_dump() if usage else None
            yield event

    try:
        async with client.responses.stream(**request) as stream:
            result = await parse_responses_stream(tap(stream), image_segmentation_format()).final_result()
        return result, captured, "responses.stream"
    except Exception as e:  # SDK helper may reject non-standard events; try raw stream
        captured["stream_helper_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        raw = await client.responses.create(**request, stream=True)
        result = await parse_responses_stream(tap(raw), image_segmentation_format()).final_result()
        return result, captured, "responses.create(stream=True)"


SAM_PROMPTS = ["circle", "blob"]  # "circle" returned 0 objects on this image in the first run


def check_sam(png: bytes, n_blobs: int) -> Check:
    c = Check("e) SAM 3.1 stream + parser")
    per_prompt = {}
    for prompt in SAM_PROMPTS:
        result, captured, method = asyncio.run(_sam(png, prompt))
        c.cost_usd += SAM_PER_IMAGE
        n = sum(isinstance(r, SegmentationBoxRecord) for r in result.records)
        per_prompt[prompt] = n
        print(f"  prompt {prompt!r}: {n} boxes, outcome={result.outcome.status}")
        if n:
            break
    boxes = [r for r in result.records if isinstance(r, SegmentationBoxRecord)]
    masks = [r for r in result.records if isinstance(r, SegmentationMaskRecord)]
    for b in boxes:
        print(f"  box id={b.object_id} [{b.left:.0f},{b.top:.0f},{b.right:.0f},{b.bottom:.0f}] conf={b.confidence}")
    areas = []
    for m in masks:
        raster = decode_mask_to_raster(m.mask)
        areas.append(sum(raster))
        print(f"  mask id={m.object_id} {m.mask.width}x{m.mask.height} {m.mask.encoding} "
              f"area={areas[-1]}px conf={m.confidence}")
    c.extra = {
        "method": method,
        "boxes_per_prompt": per_prompt,
        "outcome": result.outcome.status,
        "boxes": [asdict(b) for b in boxes],
        "mask_areas": areas,
        "diagnostics": [asdict(d) for d in result.diagnostics],
        "raw_output_head": result.raw_output[:300],
        **captured,
    }
    c.status = "PASS" if boxes and result.outcome.status == "completed" else "FAIL"
    c.detail = (f"{method}; per prompt {per_prompt}; {len(boxes)} boxes (truth {n_blobs}), {len(masks)} masks, "
                f"conf={[b.confidence for b in boxes]}")
    return c


def run(name: str, fn, *args) -> Check:
    print(f"\n== {name}")
    t0 = time.perf_counter()
    try:
        c = fn(*args)
    except Exception as e:
        c = Check(name, detail=f"{type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc(limit=2)
    c.seconds = time.perf_counter() - t0
    print(f"  -> {c.status}: {c.detail}")
    return c


def main() -> int:
    if not os.getenv("MODEL_API_KEY"):
        print("MODEL_API_KEY is empty. Put your key in backend/.env and re-run.")
        return 2
    client = openai.OpenAI(base_url=BASE_URL, api_key=os.environ["MODEL_API_KEY"], timeout=120, max_retries=2)
    png, blobs = make_png()
    (ROOT / "docs" / "probe").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "probe" / "probe_blobs.png").write_bytes(png)

    checks = [
        run("a) models", check_models, client),
        run("b) text", check_text, client),
        run("c) vision", check_vision, client, png, len(blobs)),
        run("d) tools", check_tools, client),
        run("e) sam", check_sam, png, len(blobs)),
        run("reasoning", check_reasoning, client),
    ]

    print("\n" + "-" * 104)
    print(f"{'check':34} {'result':6} {'in tok':>7} {'out tok':>7} {'reas.':>6} {'$ cost':>9} {'sec':>6}")
    print("-" * 104)
    for c in checks:
        print(f"{c.name:34} {c.status:6} {c.input_tokens:7d} {c.output_tokens:7d} "
              f"{c.reasoning_tokens:6d} {c.cost_usd:9.5f} {c.seconds:6.1f}")
    total = sum(c.cost_usd for c in checks)
    print("-" * 104)
    print(f"{'TOTAL':34} {'':6} {'':7} {'':7} {'':6} {total:9.5f}")

    out = ROOT / "docs" / "probe" / "probe_results.json"
    out.write_text(json.dumps({"base_url": BASE_URL, "openai_sdk": openai.__version__,
                               "checks": [asdict(c) for c in checks], "total_usd": total},
                              indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0 if all(c.status == "PASS" for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
