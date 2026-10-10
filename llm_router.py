"""
LLM Router - multi-provider fallback for VN Stock Dashboard.

Keys are loaded from .env / environment variables owned by the user:
- GATEWAY_KEYS=sk-...,sk-...
- or GATEWAY_KEY1, GATEWAY_KEY2, ... GATEWAY_KEY10
- optional GEMINI_KEY1..3 and DEEPSEEK_KEY for direct providers

Fallback order:
1. Groq
2. Cerebras
3. Cloudflare Workers AI
4. OpenAI-compatible gateway keys
5. Gemini direct keys
6. DeepSeek direct key
7. Local Ollama
"""

import json
import logging
import os
import re
import time
from datetime import datetime

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Override with GROQ_MODEL / CEREBRAS_MODEL when a provider retires a model (404 model_not_found).
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_CEREBRAS_MODEL = "gpt-oss-120b"
# gpt-oss is a reasoning model: reasoning tokens count against max_tokens, so keep it short
# and give completions a floor, otherwise `message.content` can come back empty.
REASONING_EFFORT = "low"
MIN_REASONING_MAX_TOKENS = 1500
USAGE_FILE = os.path.join(BASE_DIR, "llm_router_usage.json")

GATEWAY_URL = os.getenv("GATEWAY_URL", "https://aiapiv2.pekpik.com/v1")
GATEWAY_MODELS = [
    item.strip()
    for item in os.getenv("GATEWAY_MODELS", "deepseek-chat,gemini-2.0-flash,gpt-4o-mini").split(",")
    if item.strip()
]
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("llm_router")


def _valid_key(value):
    value = str(value or "").strip()
    if not value:
        return False
    placeholders = {
        "AIza_KEY_CUA_PROJECT1",
        "AIza_KEY_CUA_PROJECT2",
        "AIza_KEY_CUA_PROJECT3",
        "YOUR_KEY_HERE",
        "PASTE_KEY_HERE",
        "sk-YOUR_KEY_HERE",
    }
    if value in placeholders:
        return False
    if "KEY_CUA_PROJECT" in value:
        return False
    return True


def _split_keys(value):
    return [item.strip() for item in re.split(r"[\s,;]+", str(value or "")) if _valid_key(item)]


def fetch_keys():
    keys = []
    keys.extend(_split_keys(os.getenv("GATEWAY_KEYS", "")))
    for idx in range(1, 11):
        key = os.getenv(f"GATEWAY_KEY{idx}", "")
        if _valid_key(key):
            keys.append(key.strip())

    keys = list(dict.fromkeys(keys))
    return keys


def _get_cloudflare_base_url():
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account_id:
        return None
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


def _provider_templates(preferred_model=None):
    providers = []

    groq_key = os.getenv("GROQ_KEY", "").strip()
    if _valid_key(groq_key):
        providers.append({
            "provider": "groq",
            "name": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": groq_key,
            "model": os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL,
            "timeout": 10,
            "supports_json": True,
        })

    cerebras_key = os.getenv("CEREBRAS_KEY", "").strip()
    if _valid_key(cerebras_key):
        providers.append({
            "provider": "cerebras",
            "name": "cerebras",
            "base_url": "https://api.cerebras.ai/v1",
            "api_key": cerebras_key,
            "model": os.getenv("CEREBRAS_MODEL", "").strip() or DEFAULT_CEREBRAS_MODEL,
            "timeout": 10,
            "supports_json": True,
        })

    cloudflare_key = os.getenv("CLOUDFLARE_KEY", "").strip()
    cloudflare_base_url = _get_cloudflare_base_url()
    if _valid_key(cloudflare_key) and cloudflare_base_url:
        providers.append({
            "provider": "cloudflare",
            "name": "cloudflare",
            "base_url": cloudflare_base_url,
            "api_key": cloudflare_key,
            "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "timeout": 15,
            "supports_json": True,
        })

    model = preferred_model or (GATEWAY_MODELS[0] if GATEWAY_MODELS else "google/gemma-4-31b-it:free")
    for idx, key in enumerate(fetch_keys(), start=1):
        providers.append({
            "provider": "gateway",
            "name": f"gateway_key{idx}",
            "base_url": GATEWAY_URL,
            "api_key": key,
            "model": model,
            "timeout": 15,
            "supports_json": True,
        })

    for idx in range(1, 4):
        key = os.getenv(f"GEMINI_KEY{idx}", "")
        if _valid_key(key):
            providers.append({
                "provider": "gemini",
                "name": f"gemini_p{idx}",
                "base_url": GEMINI_BASE_URL,
                "api_key": key.strip(),
                "model": GEMINI_MODEL,
                "timeout": 15,
                "supports_json": True,
            })

    deepseek_key = os.getenv("DEEPSEEK_KEY", "")
    if _valid_key(deepseek_key):
        providers.append({
            "provider": "deepseek",
            "name": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": deepseek_key.strip(),
            "model": DEEPSEEK_MODEL,
            "timeout": 20,
            "supports_json": True,
        })

    providers.append({
        "provider": "ollama",
        "name": "ollama",
        "base_url": OLLAMA_URL,
        "api_key": "ollama",
        "model": OLLAMA_MODEL,
        "timeout": 120,
        "supports_json": False,
    })
    return providers


def _empty_usage():
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "groq_calls": 0,
        "cerebras_calls": 0,
        "cloudflare_calls": 0,
        "gateway_calls": 0,
        "gemini_calls": 0,
        "deepseek_calls": 0,
        "ollama_calls": 0,
        "fail_calls": 0,
        "keys_tried_today": 0,
        "errors": {},
        "last_provider": None,
        "last_model": None,
    }


def _load_usage():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today:
            return _empty_usage()
        base = _empty_usage()
        base.update(data if isinstance(data, dict) else {})
        base.setdefault("errors", {})
        return base
    except Exception:
        return _empty_usage()


def _save_usage(usage):
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(usage, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _messages(prompt, system):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _parse_json_text(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _call_provider(provider, prompt, system, max_tokens, require_json):
    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider["base_url"],
        timeout=provider["timeout"],
    )
    kwargs = {
        "model": provider["model"],
        "messages": _messages(prompt, system),
        "max_tokens": max_tokens,
    }
    if "gpt-oss" in provider["model"].lower():
        kwargs["max_tokens"] = max(int(max_tokens), MIN_REASONING_MAX_TOKENS)
        kwargs["extra_body"] = {"reasoning_effort": REASONING_EFFORT}
    if require_json and provider.get("supports_json"):
        kwargs["response_format"] = {"type": "json_object"}

    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        if require_json and "response_format" in kwargs:
            kwargs.pop("response_format", None)
            return client.chat.completions.create(**kwargs)
        raise exc


def call_llm(
    prompt: str,
    system: str = "Bạn là chuyên gia phân tích cổ phiếu Việt Nam. Trả lời ngắn gọn, chính xác bằng tiếng Việt.",
    max_tokens: int = 800,
    require_json: bool = False,
    preferred_model: str = None,
    skip_providers: list = None,
) -> dict:
    """
    Call LLM with provider fallback.

    Returns:
        {content, provider, model, success, latency_ms, error}
    """
    usage = _load_usage()
    skip = set(skip_providers or [])

    if OpenAI is None:
        log.warning(
            "[LLMRouter] LLM unavailable: package 'openai' is not installed "
            "(pip install -r requirements.txt) — every LLM call fails"
        )
        return {
            "content": None,
            "provider": None,
            "model": None,
            "success": False,
            "latency_ms": 0,
            "error": "Package openai chưa được cài",
        }

    providers = [p for p in _provider_templates(preferred_model) if p["provider"] not in skip and p["name"] not in skip]
    from runtime_reliability import is_transient_network_error, llm_circuit, retry_transient
    last_error = None

    for provider in providers:
        if not llm_circuit.allow(provider["name"]):
            log.warning("[LLMRouter] circuit open for %s", provider["name"])
            continue
        start = time.time()
        try:
            response = retry_transient(
                lambda: _call_provider(provider, prompt, system, max_tokens, require_json),
                attempts=2,
                base_delay=1.0,
                max_delay=4.0,
                is_transient=is_transient_network_error,
            )
            content = response.choices[0].message.content
            latency = int((time.time() - start) * 1000)

            call_key = f"{provider['provider']}_calls"
            usage[call_key] = int(usage.get(call_key, 0)) + 1
            if provider["provider"] == "gateway":
                usage["keys_tried_today"] = int(usage.get("keys_tried_today", 0)) + 1
            usage["last_provider"] = provider["provider"]
            usage["last_model"] = provider["model"]
            _save_usage(usage)
            llm_circuit.success(provider["name"])

            return {
                "content": content,
                "provider": provider["provider"],
                "model": provider["model"],
                "success": True,
                "latency_ms": latency,
                "error": None,
            }
        except Exception as exc:
            latency = int((time.time() - start) * 1000)
            last_error = str(exc)[:200]
            if provider["provider"] == "gateway":
                usage["keys_tried_today"] = int(usage.get("keys_tried_today", 0)) + 1
            errors = usage.setdefault("errors", {})
            errors[provider["name"]] = int(errors.get(provider["name"], 0)) + 1
            _save_usage(usage)
            llm_circuit.failure(provider["name"])
            log.warning("[LLMRouter] %s failed (%sms): %s", provider["name"], latency, last_error)
            continue

    usage["fail_calls"] = int(usage.get("fail_calls", 0)) + 1
    _save_usage(usage)
    log.warning(
        "[LLMRouter] LLM unavailable: %s",
        last_error or f"no usable provider ({len(providers)} configured)",
    )
    return {
        "content": None,
        "provider": None,
        "model": None,
        "success": False,
        "latency_ms": 0,
        "error": last_error or "Tất cả provider đều thất bại",
    }


def call_llm_json(
    prompt: str,
    system: str = "Chỉ trả về JSON object thuần túy, không có text thừa, không có markdown.",
    max_tokens: int = 600,
) -> dict | None:
    json_prompt = prompt + "\n\nQuan trọng: Chỉ trả về JSON object, không giải thích thêm."
    result = call_llm(json_prompt, system=system, max_tokens=max_tokens, require_json=True)
    if not result.get("success") or not result.get("content"):
        return {}
    try:
        parsed = _parse_json_text(result["content"])
    except Exception as exc:
        log.warning("[LLMRouter] JSON parse failed: %s", str(exc)[:120])
        return {}
    if not isinstance(parsed, dict):
        log.warning("[LLMRouter] LLM returned non-dict JSON: %s", type(parsed).__name__)
        return {}
    if not parsed:
        log.warning("[LLMRouter] LLM returned empty JSON object")
        return {}
    return parsed


def get_router_status() -> dict:
    usage = _load_usage()
    keys = fetch_keys()
    providers = _provider_templates()
    errors = usage.get("errors", {})

    provider_rows = []
    seen = set()
    for provider in providers:
        key = provider["provider"]
        if key in seen:
            continue
        seen.add(key)
        provider_rows.append({
            "provider": key,
            "has_key": key == "ollama" or any(p["provider"] == key and _valid_key(p["api_key"]) for p in providers),
            "calls_today": int(usage.get(f"{key}_calls", 0)),
            "errors_today": sum(int(v) for name, v in errors.items() if str(name).startswith(key)),
            "is_last_used": usage.get("last_provider") == key,
        })

    return {
        "date": usage["date"],
        "groq_calls": int(usage.get("groq_calls", 0)),
        "cerebras_calls": int(usage.get("cerebras_calls", 0)),
        "cloudflare_calls": int(usage.get("cloudflare_calls", 0)),
        "gateway_calls": int(usage.get("gateway_calls", 0)),
        "gemini_calls": int(usage.get("gemini_calls", 0)),
        "deepseek_calls": int(usage.get("deepseek_calls", 0)),
        "ollama_calls": int(usage.get("ollama_calls", 0)),
        "fail_calls": int(usage.get("fail_calls", 0)),
        "keys_available": len(keys),
        "cache_active": False,
        "last_provider": usage.get("last_provider"),
        "last_model": usage.get("last_model"),
        "providers": provider_rows,
        "total_calls": (
            int(usage.get("groq_calls", 0))
            + int(usage.get("cerebras_calls", 0))
            + int(usage.get("cloudflare_calls", 0))
            + int(usage.get("gateway_calls", 0))
            + int(usage.get("gemini_calls", 0))
            + int(usage.get("deepseek_calls", 0))
            + int(usage.get("ollama_calls", 0))
        ),
    }
