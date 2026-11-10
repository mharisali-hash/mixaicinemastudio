"""A thinking model that runs out of time hands the step to the writer model instead of failing the job."""
from types import SimpleNamespace

import pytest

from app import ai_jobs
from app.llm import LLMError


class _Res:
    thinking = ""
    model = "writer"
    seconds = 1.0

    def call_info(self, role):
        return {"role": role}


def _run(monkeypatch, fail_with: str):
    asked = []

    def fake(role, messages, tick=None, **kw):
        asked.append((role, kw.get("think")))
        if role == "reasoning":
            raise LLMError(fail_with)
        return _Res()

    monkeypatch.setattr(ai_jobs, "chat_sync", fake)
    monkeypatch.setattr(ai_jobs, "is_warm", lambda role: True)
    ctx = SimpleNamespace(job=SimpleNamespace(payload={}), progress=lambda *a, **k: None)
    return ai_jobs.AiRun(ctx), asked


def test_a_timeout_retries_on_the_creative_model(monkeypatch):
    run, asked = _run(monkeypatch, "deepseek-r1:14b didn't answer within 600 s")
    res = run.call("Outlining", 0.1, "reasoning", [{"role": "user", "content": "hi"}], think=True)
    assert isinstance(res, _Res) and asked == [("reasoning", True), ("creative", None)]
    assert run.calls[-1]["step"] == "Outlining (quicker model)"


def test_other_errors_still_fail(monkeypatch):
    run, asked = _run(monkeypatch, "Ollama isn't reachable")
    with pytest.raises(LLMError):
        run.call("Outlining", 0.1, "reasoning", [])
    assert len(asked) == 1
