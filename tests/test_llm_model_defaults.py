import llm_router


def _providers(monkeypatch, **env):
    for k in ("GROQ_MODEL", "CEREBRAS_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_KEY", "gsk_" + "a" * 40)
    monkeypatch.setenv("CEREBRAS_KEY", "csk-" + "a" * 40)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return {p["provider"]: p for p in llm_router._provider_templates()}


def test_default_models(monkeypatch):
    p = _providers(monkeypatch)
    assert p["groq"]["model"] == "openai/gpt-oss-120b"
    assert p["cerebras"]["model"] == "gpt-oss-120b"


def test_env_override(monkeypatch):
    p = _providers(monkeypatch, GROQ_MODEL="x/y", CEREBRAS_MODEL="z")
    assert p["groq"]["model"] == "x/y"
    assert p["cerebras"]["model"] == "z"


def test_reasoning_params_for_gpt_oss(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": self})()

        def create(self, **kw):
            seen.update(kw)
            return "ok"

    monkeypatch.setattr(llm_router, "OpenAI", FakeClient)
    prov = {"api_key": "k", "base_url": "u", "timeout": 1, "model": "gpt-oss-120b", "supports_json": True}
    llm_router._call_provider(prov, "p", "s", 600, True)
    assert seen["max_tokens"] >= llm_router.MIN_REASONING_MAX_TOKENS
    assert seen["extra_body"] == {"reasoning_effort": "low"}
    seen.clear()
    llm_router._call_provider({**prov, "model": "other"}, "p", "s", 600, False)
    assert seen["max_tokens"] == 600 and "extra_body" not in seen
