"""The writer that can't load while a video render fills the GPU hands the call to the small model."""
import pytest

from app.config import get_settings
from app.llm import LLMError
from app.llm import client as llm


@pytest.fixture
def fake_chat(monkeypatch):
    asked = []

    async def chat(role, messages, **kw):
        asked.append(kw.get("model"))
        if kw.get("model") is None:
            raise LLMError("LLM server error (500): llama-server process has terminated: unable to allocate CUDA1 buffer")
        return "ok"

    async def installed():
        return {"gemma4:e4b", "gemma4:26b"}

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(llm, "installed_models", installed)
    monkeypatch.setattr(get_settings(), "llm_model_creative", "gemma4:26b")
    monkeypatch.setattr(get_settings(), "llm_model_small", "gemma4:e4b")
    return asked


def test_no_room_on_the_gpu_falls_back_to_the_small_model(fake_chat):
    assert llm.chat_sync("creative", [{"role": "user", "content": "hi"}]) == "ok"
    assert fake_chat == [None, "gemma4:e4b"]


def test_vision_and_other_errors_still_raise(fake_chat, monkeypatch):
    with pytest.raises(LLMError):
        llm.chat_sync("vision", [])

    async def offline(role, messages, **kw):
        raise LLMError("Ollama isn't reachable at http://x. Is it running?")

    monkeypatch.setattr(llm, "chat", offline)
    with pytest.raises(LLMError):
        llm.chat_sync("creative", [])


def test_switched_off_when_empty(fake_chat, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model_small", "")
    with pytest.raises(LLMError):
        llm.chat_sync("creative", [])
