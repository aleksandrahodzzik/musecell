"""LLMClient against a mocked HTTP transport: model check, retries, cost, caps, empty answers."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm import CallRecord, CostCapExceeded, CostMeter, DailyLedger, LLMClient, LLMError, ModelCheckError, cost_of

MODELS = ["muse-spark-1.3", "muse-wonder-1.0", "sam-3.1"]


def completion(content="ok", prompt=1000, completion_tokens=2000, reasoning=500, finish="stop", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens,
                      "total_tokens": prompt + completion_tokens,
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}


class Script:
    """Replies in order; records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "object": "model", "created": 0,
                                                                        "owned_by": "meta"} for m in MODELS]})
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)


def make(tmp_path, script, **overrides):
    settings = Settings(_env_file=None, model_api_key=SecretStr("test-key"), data_dir=str(tmp_path), **overrides)
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = LLMClient(settings, ledger=DailyLedger(tmp_path / "costs", settings.daily_cost_cap_usd),
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(script)), sleep=fake_sleep)
    return client, sleeps


def run(coro):
    return asyncio.run(coro)


def chat(client, meter, **kw):
    return run(client.chat(model="muse-spark-1.3", messages=[{"role": "user", "content": "hi"}], meter=meter,
                           purpose="test", **kw))


def test_cost_from_price_table():
    assert cost_of("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(1.25 + 4.25)
    assert cost_of("muse-wonder-1.0", 1000, 2000) == pytest.approx(0.004 + 0.04)
    with pytest.raises(LLMError, match="No price"):
        cost_of("muse-spark-9", 1, 1)


def test_model_check_passes_and_fails(tmp_path):
    client, _ = make(tmp_path, Script())
    assert run(client.check_models()) == sorted(MODELS)
    client, _ = make(tmp_path, Script(), reviewer_model="muse-wonder")  # an id that does not exist
    with pytest.raises(ModelCheckError, match="muse-wonder"):
        run(client.check_models())


def test_usage_cost_recorded_on_meter_and_ledger(tmp_path):
    client, _ = make(tmp_path, Script((200, completion())))
    meter = CostMeter(run_id="r1", cap_usd=1.0)
    res = chat(client, meter)
    assert res.content == "ok"
    rec = res.record
    assert (rec.input_tokens, rec.output_tokens, rec.reasoning_tokens) == (1000, 2000, 500)
    assert rec.cost_usd == pytest.approx(1000 * 1.25e-6 + 2000 * 4.25e-6)
    assert meter.total_usd == pytest.approx(rec.cost_usd)
    assert client.ledger.total_today() == pytest.approx(rec.cost_usd)


def test_retries_429_and_5xx_with_backoff(tmp_path):
    script = Script((429, {"error": {"message": "slow down"}}), (503, {"error": {"message": "busy"}}),
                    (200, completion()))
    client, sleeps = make(tmp_path, script)
    res = chat(client, CostMeter(run_id="r", cap_usd=1))
    assert res.record.attempts == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]
    assert len([r for r in script.requests if r.url.path.endswith("/chat/completions")]) == 3


def test_no_retry_on_400_and_readable_401(tmp_path):
    client, sleeps = make(tmp_path, Script((400, {"error": {"message": "bad schema"}})))
    with pytest.raises(LLMError, match="HTTP 400"):
        chat(client, CostMeter(run_id="r", cap_usd=1))
    assert sleeps == []
    client, _ = make(tmp_path, Script((401, {"error": {"message": "no"}})))
    with pytest.raises(LLMError, match="MODEL_API_KEY"):
        chat(client, CostMeter(run_id="r", cap_usd=1))


def test_gives_up_after_max_retries(tmp_path):
    client, sleeps = make(tmp_path, Script(*[(500, {"error": {"message": "x"}})] * 3), model_max_retries=2)
    with pytest.raises(LLMError, match="HTTP 500"):
        chat(client, CostMeter(run_id="r", cap_usd=1))
    assert len(sleeps) == 2


def test_empty_answer_at_length_is_an_error_but_still_billed(tmp_path):
    client, _ = make(tmp_path, Script((200, completion(content="", finish="length"))))
    meter = CostMeter(run_id="r", cap_usd=1)
    with pytest.raises(LLMError, match="without answering"):
        chat(client, meter, max_tokens=400)
    assert meter.total_usd > 0


def test_tool_call_without_content_is_fine(tmp_path):
    call = [{"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    client, _ = make(tmp_path, Script((200, completion(content=None, finish="tool_calls", tool_calls=call))))
    res = chat(client, CostMeter(run_id="r", cap_usd=1), tools=[{"type": "function", "function": {"name": "f"}}])
    assert res.message.tool_calls[0].function.name == "f"


def test_request_shape(tmp_path):
    script = Script((200, completion(content='{"a": 1}')))
    client, _ = make(tmp_path, script)
    out, _ = run(client.chat_json(model="muse-spark-1.3", messages=[{"role": "user", "content": "x"}],
                                  meter=CostMeter(run_id="r", cap_usd=1), purpose="t", schema_name="s",
                                  schema={"type": "object"}, reasoning_effort="low"))
    assert out == {"a": 1}
    body = json.loads(script.requests[-1].content)
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "low" and body["max_tokens"] >= 2000
    assert script.requests[-1].headers["authorization"] == "Bearer test-key"


def test_run_cap_blocks_next_call(tmp_path):
    client, _ = make(tmp_path, Script((200, completion(prompt=10**6, completion_tokens=0))))
    meter = CostMeter(run_id="r", cap_usd=1.0)
    chat(client, meter)  # $1.25 — allowed because the cap is checked before the call
    with pytest.raises(CostCapExceeded, match="Run cost cap"):
        chat(client, meter)


def test_daily_cap_survives_restart(tmp_path):
    client, _ = make(tmp_path, Script((200, completion(prompt=10**6, completion_tokens=0))), daily_cost_cap_usd=1.0)
    chat(client, CostMeter(run_id="a", cap_usd=5))
    fresh, _ = make(tmp_path, Script(), daily_cost_cap_usd=1.0)  # new client reads the same ledger file
    with pytest.raises(CostCapExceeded, match="Daily cost cap"):
        chat(fresh, CostMeter(run_id="b", cap_usd=5))


def _append_many(directory: str, n: int) -> None:
    ledger = DailyLedger(Path(directory), 100.0)
    for _ in range(n):
        ledger.add("r", CallRecord(model="m", purpose="p" * 300, cost_usd=0.01))


def test_ledger_appends_from_several_processes_stay_whole(tmp_path):
    """Phase 6: the eval and the demo recorder appended at once; on Windows a line was overwritten."""
    procs = [multiprocessing.Process(target=_append_many, args=(str(tmp_path), 200)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    ledger = DailyLedger(tmp_path, 100.0)
    lines = ledger._path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 800 and all(json.loads(line)["cost_usd"] == 0.01 for line in lines)
    assert ledger.total_today() == pytest.approx(8.0)


def test_ledger_skips_a_torn_line(tmp_path):
    ledger = DailyLedger(tmp_path, 100.0)
    ledger.add("r", CallRecord(model="m", purpose="p", cost_usd=0.5))
    with ledger._path().open("a", encoding="utf-8") as f:
        f.write('on": "tool_calls"}\n')
    assert ledger.total_today() == pytest.approx(0.5)


def test_contributor_model_needs_flag(tmp_path):
    client, _ = make(tmp_path, Script((200, completion())))
    with pytest.raises(LLMError, match="ALLOW_CONTRIBUTOR"):
        run(client.chat(model="muse-spark-1.3-contributor", messages=[], meter=CostMeter(run_id="r", cap_usd=1),
                        purpose="t"))


def test_missing_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)  # other tests may load backend/.env into os.environ
    with pytest.raises(LLMError, match="MODEL_API_KEY is empty"):
        LLMClient(Settings(_env_file=None, data_dir=str(tmp_path)))
