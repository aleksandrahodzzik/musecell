"""Stretch probe: Muse Spark Open (open weights) on Baseten.

Runs: (a) model list + per-model rate limits, (b) plain text, (c) vision with a real BBBC021
composite, (c2) the 8-images-per-request limit, (d) strict tool calling incl. a parallel-call loop,
(e) json_schema structured output (strict, then non-strict, then json_object), (f) reasoning_effort.
Records exactly what comes back (reasoning field, finish reasons, usage, errors) and writes
docs/probe/baseten_probe.json. Never prints the API key.

Usage: python scripts/probe_baseten.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"  # the hackathon key only works with this exact id
PRICE = (1.25, 4.25)  # USD per 1M in/out, baseten.co/pricing 2026-10-30 (paid from credits)
OUT = ROOT / "docs" / "probe" / "baseten_probe.json"


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


def add_usage(c: Check, r) -> None:
    u = r.usage
    if u is None:
        c.extra.setdefault("notes", []).append("no usage in response")
        return
    c.input_tokens += u.prompt_tokens or 0
    c.output_tokens += u.completion_tokens or 0
    details = getattr(u, "completion_tokens_details", None)
    c.reasoning_tokens += (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
    c.cost_usd = c.input_tokens / 1e6 * PRICE[0] + c.output_tokens / 1e6 * PRICE[1]
    c.extra.setdefault("usage_raw", []).append(u.model_dump())


def describe_message(r) -> dict:
    """What the response actually carries: finish reason, which reasoning field, lengths."""
    ch = r.choices[0]
    m = ch.message
    raw = m.model_dump()
    reasoning_fields = {k: len(v) for k, v in raw.items() if "reason" in k and isinstance(v, str)}
    return {"finish_reason": ch.finish_reason, "message_keys": sorted(k for k, v in raw.items() if v is not None),
            "reasoning_fields_len": reasoning_fields, "content_len": len(m.content or ""),
            "content_head": (m.content or "")[:200], "n_tool_calls": len(m.tool_calls or [])}


def data_url(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def composite() -> Image.Image:
    path = sorted((ROOT / "demo" / "thumbnails").glob("*.composite.png"))[0]
    with Image.open(path) as im:
        return im.convert("RGB")


def check_models(client: openai.OpenAI, key: str) -> Check:
    c = Check("a) GET /v1/models + rate limits")
    try:
        ids = sorted(m.id for m in client.models.list())
        c.extra["ids"] = ids
        c.extra["has_model"] = MODEL in ids
        c.detail = f"{len(ids)} ids; {MODEL} {'listed' if MODEL in ids else 'NOT listed'}"
    except Exception as exc:  # noqa: BLE001
        c.detail = f"models.list failed: {type(exc).__name__}: {str(exc)[:160]}"
        c.extra["has_model"] = None
    try:
        r = httpx.get(f"https://api.baseten.co/v1/model_apis/{MODEL.replace('/', '%2F')}",
                      headers={"Authorization": f"Bearer {key}"}, timeout=30)
        c.extra["model_api_status"] = r.status_code
        if r.status_code == 200:
            c.extra["model_api"] = r.json()
    except Exception as exc:  # noqa: BLE001
        c.extra["model_api_error"] = f"{type(exc).__name__}: {exc}"
    c.status = "PASS" if c.extra.get("has_model") else "FAIL"
    return c


def check_text(client: openai.OpenAI) -> Check:
    c = Check("b) plain text")
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": "In one sentence: what does DAPI stain in a cell?"}],
        max_tokens=4000)
    add_usage(c, r)
    c.extra["message"] = describe_message(r)
    text = (r.choices[0].message.content or "").strip()
    c.status = "PASS" if text else "FAIL"
    c.detail = text[:120]
    return c


def check_vision(client: openai.OpenAI) -> Check:
    c = Check("c) vision, BBBC021 composite")
    img = composite()
    r = client.chat.completions.create(model=MODEL, max_tokens=6000, messages=[{"role": "user", "content": [
        {"type": "text", "text": "Fluorescence micrograph of MCF-7 cells: DNA blue, tubulin green, actin red. "
                                 "Roughly how many nuclei are visible, and is any cell in mitosis? Two sentences."},
        {"type": "image_url", "image_url": {"url": data_url(img)}}]}])
    add_usage(c, r)
    c.extra["message"] = describe_message(r)
    c.extra["image_size"] = img.size
    text = (r.choices[0].message.content or "").strip()
    c.status = "PASS" if text and c.input_tokens > 300 else "FAIL"  # an ignored image would cost ~50 tokens
    c.detail = text[:160]
    return c


def check_image_limit(client: openai.OpenAI) -> Check:
    """Docs say <= 8 images per request. Send 8 (expect OK) and 9 (expect 400) tiny images."""
    c = Check("c2) images per request: 8 ok, 9 refused?")
    tiny = data_url(composite().resize((64, 64)))
    for n in (8, 9):
        content = [{"type": "text", "text": f"How many images do you see? Answer with a number."}]
        content += [{"type": "image_url", "image_url": {"url": tiny}}] * n
        try:
            r = client.chat.completions.create(model=MODEL, max_tokens=3000,
                                               messages=[{"role": "user", "content": content}])
            add_usage(c, r)
            c.extra[f"n{n}"] = {"ok": True, "answer": (r.choices[0].message.content or "")[:40]}
        except openai.APIStatusError as exc:
            c.extra[f"n{n}"] = {"ok": False, "status": exc.status_code, "error": str(exc)[:300]}
    c.status = "PASS" if c.extra["n8"]["ok"] else "FAIL"
    c.detail = f"8 → {'ok' if c.extra['n8']['ok'] else 'refused'}, 9 → " \
               f"{'ok' if c.extra['n9']['ok'] else 'HTTP %s' % c.extra['n9'].get('status')}"
    return c


Z_TABLE = {"nucleus_area": 2.1, "tubulin_intensity": 5.4, "actin_texture": -0.7}
TOOL = {"type": "function", "function": {
    "name": "get_feature_z", "description": "Robust z-score of one measured feature vs DMSO.", "strict": True,
    "parameters": {"type": "object", "additionalProperties": False, "required": ["feature", "rationale"],
                   "properties": {"feature": {"type": "string", "enum": list(Z_TABLE)},
                                  "rationale": {"type": "string", "description": "Why you need it, <= 240 chars."}}}}}


def check_tools(client: openai.OpenAI) -> Check:
    c = Check("d) strict tool calling, parallel loop")
    messages: list[dict] = [
        {"role": "system", "content": "Use the tools. Call get_feature_z for ALL THREE features in ONE turn "
                                      "(parallel tool calls), then answer which feature deviates most."},
        {"role": "user", "content": "Which of nucleus_area, tubulin_intensity, actin_texture deviates most?"}]
    turns = []
    for turn in range(4):
        r = client.chat.completions.create(model=MODEL, messages=messages, tools=[TOOL], tool_choice="auto",
                                           max_tokens=6000)
        add_usage(c, r)
        info = describe_message(r)
        m = r.choices[0].message
        calls = m.tool_calls or []
        args_ok = []
        for tc in calls:
            try:
                a = json.loads(tc.function.arguments)
                args_ok.append(set(a) == {"feature", "rationale"} and a["feature"] in Z_TABLE)
            except json.JSONDecodeError:
                args_ok.append(False)
        info["calls"] = [{"name": tc.function.name, "args": tc.function.arguments[:200]} for tc in calls]
        info["args_schema_valid"] = args_ok
        turns.append(info)
        if not calls:
            break
        # Re-send the assistant message as a plain dict, like the agent loop does (no reasoning_content).
        messages.append({"role": "assistant", "content": m.content or "", "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name,
                                                           "arguments": tc.function.arguments}} for tc in calls]})
        for tc in calls:
            try:
                feat = json.loads(tc.function.arguments).get("feature")
            except json.JSONDecodeError:
                feat = None
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps({"feature": feat, "z": Z_TABLE.get(feat)})})
    c.extra["turns"] = turns
    first = turns[0]
    final = turns[-1]
    parallel = first["n_tool_calls"] >= 2
    valid = all(all(t["args_schema_valid"]) for t in turns)
    answered = final["n_tool_calls"] == 0 and "tubulin" in final["content_head"].lower()
    c.status = "PASS" if (first["n_tool_calls"] >= 1 and valid and answered) else "FAIL"
    c.detail = (f"turn1 {first['n_tool_calls']} call(s) ({'parallel' if parallel else 'not parallel'}), "
                f"args valid={valid}, final answer {'names tubulin' if answered else 'missing/wrong'}, "
                f"{len(turns)} turns")
    return c


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["cell_density", "observations"],
          "properties": {"cell_density": {"type": "string", "enum": ["lower", "similar", "higher"]},
                         "observations": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False, "required": ["channel", "finding"],
                             "properties": {"channel": {"type": "string", "enum": ["dna", "tubulin", "actin"]},
                                            "finding": {"type": "string"}}}}}}


def _valid(obj) -> bool:
    return (isinstance(obj, dict) and set(obj) == {"cell_density", "observations"}
            and obj["cell_density"] in ("lower", "similar", "higher") and isinstance(obj["observations"], list)
            and all(isinstance(o, dict) and set(o) == {"channel", "finding"}
                    and o["channel"] in ("dna", "tubulin", "actin") for o in obj["observations"]))


def check_json(client: openai.OpenAI) -> Check:
    c = Check("e) structured output (json_schema strict / non-strict / json_object)")
    prompt = ("Treated MCF-7 cells look sparser than DMSO, with bright condensed DNA and bundled tubulin. "
              "Return JSON with cell_density (lower|similar|higher) and observations "
              "[{channel: dna|tubulin|actin, finding}]. JSON only.")
    variants = {
        "json_schema_strict": {"type": "json_schema", "json_schema": {"name": "m", "schema": SCHEMA, "strict": True}},
        "json_schema_nonstrict": {"type": "json_schema", "json_schema": {"name": "m", "schema": SCHEMA}},
        "json_object": {"type": "json_object"},
        "none": None,
    }
    for name, fmt in variants.items():
        kwargs = {"response_format": fmt} if fmt else {}
        try:
            r = client.chat.completions.create(model=MODEL, max_tokens=6000,
                                               messages=[{"role": "user", "content": prompt}], **kwargs)
            add_usage(c, r)
            text = (r.choices[0].message.content or "").strip()
            body = text.strip("`").removeprefix("json").strip() if text.startswith("```") else text
            try:
                obj = json.loads(body)
                parsed, valid = True, _valid(obj)
            except json.JSONDecodeError:
                parsed, valid = False, False
            c.extra[name] = {"accepted": True, "finish_reason": r.choices[0].finish_reason, "parsed": parsed,
                             "schema_valid": valid, "fenced": text.startswith("```"), "head": text[:160]}
        except openai.APIStatusError as exc:
            c.extra[name] = {"accepted": False, "status": exc.status_code, "error": str(exc)[:300]}
    strict = c.extra["json_schema_strict"]
    c.status = "PASS" if strict.get("schema_valid") else "FAIL"
    c.detail = "; ".join(f"{k}: " + ("valid" if v.get("schema_valid") else
                                       "parsed-invalid" if v.get("parsed") else
                                       f"HTTP {v['status']}" if not v.get("accepted") else "not JSON")
                         for k, v in c.extra.items() if k in variants)
    return c


CONFLICT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "score"],
                   "properties": {"verdict": {"type": "string", "enum": ["approve", "request_experiment"]},
                                  "score": {"type": "integer"}}}


def check_enforcement(client: openai.OpenAI) -> Check:
    """Is the schema enforced (constrained decoding) or only a hint? The prompt asks for a different shape;
    an enforced strict schema must still win."""
    c = Check("e2) json_schema strict enforced against a conflicting prompt?")
    prompt = ('Reply with exactly this JSON and nothing else: {"answer": "maybe", "colour": "green", '
              '"notes": ["a", "b"]}')
    out = {}
    for name, strict in (("strict", True), ("nonstrict", False)):
        fmt = {"type": "json_schema", "json_schema": {"name": "review", "schema": CONFLICT_SCHEMA, "strict": strict}}
        r = client.chat.completions.create(model=MODEL, max_tokens=4000, response_format=fmt,
                                           messages=[{"role": "user", "content": prompt}])
        add_usage(c, r)
        text = (r.choices[0].message.content or "").strip()
        try:
            obj = json.loads(text)
            ok = (isinstance(obj, dict) and set(obj) == {"verdict", "score"}
                  and obj["verdict"] in ("approve", "request_experiment") and isinstance(obj["score"], int))
        except json.JSONDecodeError:
            ok = False
        out[name] = {"follows_schema": ok, "head": text[:160]}
    c.extra.update(out)
    c.status = "PASS" if out["strict"]["follows_schema"] else "FAIL"
    c.detail = f"strict: {'schema wins' if out['strict']['follows_schema'] else 'prompt wins (NOT enforced)'}; " \
               f"non-strict: {'schema wins' if out['nonstrict']['follows_schema'] else 'prompt wins'}"
    return c


def check_effort(client: openai.OpenAI) -> Check:
    c = Check("f) reasoning_effort low vs default")
    out = {}
    for effort in ("low", None):
        kwargs = {"reasoning_effort": effort} if effort else {}
        try:
            r = client.chat.completions.create(model=MODEL, max_tokens=4000, messages=[
                {"role": "user", "content": "Is 391 prime? Answer yes or no with one reason."}], **kwargs)
            u = r.usage
            det = getattr(u, "completion_tokens_details", None)
            add_usage(c, r)
            out[effort or "default"] = {"ok": True, "completion_tokens": u.completion_tokens,
                                        "reasoning_tokens": getattr(det, "reasoning_tokens", None) if det else None,
                                        "reasoning_chars": describe_message(r)["reasoning_fields_len"],
                                        "answer": (r.choices[0].message.content or "")[:80]}
        except openai.APIStatusError as exc:
            out[effort or "default"] = {"ok": False, "status": exc.status_code, "error": str(exc)[:200]}
    c.extra.update(out)
    c.status = "PASS" if out.get("low", {}).get("ok") else "FAIL"
    c.detail = ", ".join(f"{k}: {v.get('completion_tokens', 'HTTP %s' % v.get('status'))} out tok"
                         for k, v in out.items())
    return c


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = os.getenv("BASETEN_API_KEY")
    if not key:
        print("BASETEN_API_KEY is empty in backend/.env")
        return 2
    client = openai.OpenAI(base_url=BASE_URL, api_key=key, timeout=300, max_retries=2)
    checks = []
    for fn in (lambda: check_models(client, key), lambda: check_text(client), lambda: check_vision(client),
               lambda: check_image_limit(client), lambda: check_tools(client), lambda: check_json(client), lambda: check_enforcement(client),
               lambda: check_effort(client)):
        t0 = time.perf_counter()
        try:
            c = fn()
        except Exception as exc:  # noqa: BLE001
            c = Check(getattr(fn, "__name__", "check"), detail=f"{type(exc).__name__}: {str(exc)[:300]}")
            c.extra["traceback"] = traceback.format_exc()[-1500:]
        c.seconds = round(time.perf_counter() - t0, 2)
        checks.append(c)
        print(f"[{c.status}] {c.name}  ({c.seconds}s, in {c.input_tokens} / out {c.output_tokens} "
              f"/ reasoning {c.reasoning_tokens}, ${c.cost_usd:.4f})\n        {c.detail}")
    total = sum(c.cost_usd for c in checks)
    print(f"Total nominal cost ${total:.4f} (Baseten credits)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"base_url": BASE_URL, "model": MODEL, "price_per_m": PRICE, "total_usd": total,
                               "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                               "checks": [asdict(c) for c in checks]}, indent=1), encoding="utf-8")
    return 0 if all(c.status == "PASS" for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
