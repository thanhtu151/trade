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


def _router(monkeypatch, tmp_path, creates):
    """creates: dict provider-model -> list of callables/values per call."""
    monkeypatch.setattr(llm_router, "_load_usage", lambda: {})
    monkeypatch.setattr(llm_router, "_save_usage", lambda u: None)
    monkeypatch.setattr(llm_router, "_provider_templates", lambda preferred_model=None: [
        {"provider": "groq", "name": "groq", "base_url": "g", "api_key": "k", "model": "openai/gpt-oss-120b", "timeout": 1, "supports_json": False},
        {"provider": "cerebras", "name": "cerebras", "base_url": "c", "api_key": "k", "model": "other", "timeout": 1, "supports_json": False},
    ])

    class FakeClient:
        def __init__(self, **kw):
            self.base = kw["base_url"]
            self.chat = type("C", (), {"completions": self})()

        def create(self, **kw):
            creates.setdefault(self.base, []).append(kw)
            r = creates["_plan"][self.base].pop(0)
            if isinstance(r, Exception):
                raise r
            return type("R", (), {"choices": [type("Ch", (), {"message": type("M", (), {"content": r})()})()]})()

    monkeypatch.setattr(llm_router, "OpenAI", FakeClient)


def test_empty_content_falls_back(monkeypatch, tmp_path, caplog):
    calls = {"_plan": {"g": ["   "], "c": ["hello"]}}
    _router(monkeypatch, tmp_path, calls)
    out = llm_router.call_llm("p")
    assert out["success"] and out["provider"] == "cerebras" and out["content"] == "hello"
    assert "empty_content" in caplog.text


def test_reasoning_effort_400_retries_without_extra_body(monkeypatch, tmp_path):
    class Bad(Exception):
        status_code = 400

    calls = {"_plan": {"g": [Bad("400 unknown param reasoning_effort"), "ok"], "c": []}}
    _router(monkeypatch, tmp_path, calls)
    out = llm_router.call_llm("p")
    assert out["success"] and out["provider"] == "groq"
    assert "extra_body" in calls["g"][0] and "extra_body" not in calls["g"][1]
