"""Model-agnostic structured extraction.

One method everywhere: LLMClient.extract(schema, system, user, images=None) -> schema instance.
Models are "provider/model" strings; switching provider is a config change (LLM_MODELS / VLM_MODELS).
Images may be URLs (preferred: the provider fetches them, e.g. official YouTube thumbnails) or raw bytes.
Responses are cached in SQLite by a hash of (model chain, schema, prompts) so demo re-runs are free.
"""
import hashlib
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import TypeVar

from pydantic import BaseModel, ValidationError

import config
import db

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class MissingKeyError(LLMError):
    pass


KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
           "anthropic": "ANTHROPIC_API_KEY"}


def _require_key(provider: str) -> str:
    import os
    env = KEY_ENV[provider]
    key = getattr(config, env, None) or os.getenv(env, "")
    if not key:
        raise MissingKeyError(f"environment variable {env} is not set")
    return key


def missing_keys(models: list[str]) -> list[str]:
    """Env vars that are missing for the given model chain (for UI / reporting)."""
    import os
    envs = {KEY_ENV[m.split("/", 1)[0]] for m in models}
    return sorted(e for e in envs if not (getattr(config, e, None) or os.getenv(e)))


Image = str | bytes  # URL or raw JPEG/PNG bytes


# ---------------- provider backends: (model, system, user, json_schema, images) -> json text -------------
def _image_bytes(img: Image) -> bytes:
    if isinstance(img, bytes):
        return img
    import requests
    r = requests.get(img, timeout=20)
    r.raise_for_status()
    return r.content


def _gemini(model: str, system: str, user: str, schema: dict, images: list[Image] | None) -> str:
    from google import genai
    from google.genai import types

    key = _require_key("gemini")
    client = _gemini.client = getattr(_gemini, "client", None) or genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    parts = [types.Part.from_bytes(data=_image_bytes(img), mime_type="image/jpeg") for img in images or []]
    parts.append(types.Part.from_text(text=user))
    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0.1,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    return resp.text


def _openai_content(user: str, images: list[Image] | None) -> list[dict]:
    import base64
    content: list[dict] = [{"type": "text", "text": user}]
    for img in images or []:
        url = img if isinstance(img, str) else "data:image/jpeg;base64," + base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
    return content


def _openai_compatible(client, model: str, system: str, user: str, schema: dict, images, **extra) -> str:
    """Structured output via json_schema; falls back to json_object + schema-in-prompt if unsupported."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": _openai_content(user, images)}]
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}, **extra)
    except Exception as e:
        msg = str(e).lower()
        if "400" not in msg or not any(s in msg for s in ("response_format", "json_schema", "schema")):
            raise
        messages[0]["content"] = (f"{system}\n\nReturn ONLY a JSON object matching this JSON Schema:\n"
                                  f"{json.dumps(schema)}")
        resp = client.chat.completions.create(model=model, messages=messages,
                                              response_format={"type": "json_object"}, **extra)
    return resp.choices[0].message.content


def _groq(model: str, system: str, user: str, schema: dict, images: list[Image] | None) -> str:
    from openai import OpenAI
    client = _groq.client = getattr(_groq, "client", None) or OpenAI(
        api_key=_require_key("groq"), base_url="https://api.groq.com/openai/v1", max_retries=0, timeout=90)
    return _openai_compatible(client, model, system, user, schema, images, temperature=0.1)


def _openai(model: str, system: str, user: str, schema: dict, images: list[Image] | None) -> str:
    from openai import OpenAI
    client = _openai.client = getattr(_openai, "client", None) or OpenAI(
        api_key=_require_key("openai"), max_retries=0, timeout=90)
    # reasoning models (gpt-5*, o*) reject temperature; keep their reasoning minimal for coarse tagging
    extra = {"reasoning_effort": "low"} if model.startswith(("gpt-5", "o")) else {"temperature": 0.1}
    return _openai_compatible(client, model, system, user, schema, images, **extra)


def _anthropic(model: str, system: str, user: str, schema: dict, images: list[Image] | None) -> str:
    import base64
    import anthropic

    content = []
    for img in images or []:
        src = ({"type": "url", "url": img} if isinstance(img, str) else
               {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(img).decode()})
        content.append({"type": "image", "source": src})
    content.append({"type": "text", "text": user})
    resp = anthropic.Anthropic(api_key=_require_key("anthropic")).messages.create(
        model=model, max_tokens=4096, system=system, messages=[{"role": "user", "content": content}],
        tools=[{"name": "emit", "description": "Return the result", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "emit"},
    )
    return json.dumps(next(b.input for b in resp.content if b.type == "tool_use"))


BACKENDS = {"gemini": _gemini, "groq": _groq, "openai": _openai, "anthropic": _anthropic}


class _RateLimiter:
    """Per-model sliding-window RPM limit plus cooldowns after 429/503. Shared across threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: dict[str, deque] = defaultdict(deque)
        self.cooldown: dict[str, float] = {}
        self.dead: dict[str, str] = {}   # spec -> reason (missing key / model not found); skipped for the process
        self.strikes: dict[str, int] = {}

    def try_acquire(self, spec: str) -> float:
        """Reserve a slot now and return 0, or return seconds until this model may be available."""
        with self.lock:
            now = time.monotonic()
            if (until := self.cooldown.get(spec, 0)) > now:
                return until - now
            q = self.calls[spec]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= config.PROVIDER_RPM.get(spec.split("/", 1)[0], 10):
                return 60 - (now - q[0]) + 0.1
            q.append(now)
            return 0.0

    def cool(self, spec: str, seconds: float) -> None:
        with self.lock:
            self.cooldown[spec] = max(self.cooldown.get(spec, 0), time.monotonic() + seconds)

    def strike(self, spec: str) -> int:
        with self.lock:
            self.strikes[spec] = self.strikes.get(spec, 0) + 1
            return self.strikes[spec]

    def kill(self, spec: str, reason: str) -> None:
        with self.lock:
            if spec not in self.dead:
                log.warning("disabling %s: %s", spec, reason)
            self.dead[spec] = reason


_limiter = _RateLimiter()


def _retry_delay(msg: str, default: float) -> float:
    """Parses 'retry in 27.6s' (Gemini) and 'try again in 1m2.5s' / '1h3m' (Groq)."""
    m = re.search(r"(?:retry|try again) in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", msg)
    if m and any(m.groups()):
        h, mi, s = (float(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s + 1
    m = re.search(r"retryDelay'?:\s*'?(\d+)s", msg)
    return float(m.group(1)) + 1 if m else default


class LLMClient:
    def __init__(self, models: list[str] | None = None):
        self.models = models or config.LLM_MODELS
        self.calls = 0
        self.cache_hits = 0
        self.calls_by_model: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def available_models(self) -> list[str]:
        return [m for m in self.models if m not in _limiter.dead]

    def extract(self, schema: type[T], system: str, user: str, images: list[Image] | None = None,
                max_retries: int = 2, deadline_s: float = 600) -> T:
        img_ids = [i if isinstance(i, str) else hashlib.sha256(i).hexdigest() for i in images or []]
        key = "llm:" + hashlib.sha256(json.dumps(
            [schema.__name__, schema.model_json_schema(), system, user, img_ids]).encode()).hexdigest()
        if (hit := db.cache_get(key)) is not None:
            with self.lock:
                self.cache_hits += 1
            return schema.model_validate(hit)

        json_schema = schema.model_json_schema()
        failures = 0                       # real failures (validation / unknown errors), not rate limits
        skip: set[str] = set()             # models that cannot serve THIS request (e.g. request too large)
        last_err: Exception | None = None
        start = time.monotonic()
        while time.monotonic() - start < deadline_s and failures <= max_retries * len(self.models):
            usable = [m for m in self.models if m not in skip and m not in _limiter.dead]
            if not usable:
                break
            waits = []
            for spec in usable:
                if (w := _limiter.try_acquire(spec)) > 0:
                    waits.append(w)
                    continue
                provider, model = spec.split("/", 1)
                prompt = user if not isinstance(last_err, ValidationError) else (
                    f"{user}\n\nYour previous answer failed validation: {str(last_err)[:500]}. Return valid JSON.")
                try:
                    with self.lock:
                        self.calls += 1
                        self.calls_by_model[spec] += 1
                    text = BACKENDS[provider](model, system, prompt, json_schema, images)
                    out = schema.model_validate_json(text)
                    db.cache_put(key, out.model_dump())
                    _limiter.strikes.pop(spec, None)
                    return out
                except ValidationError as e:
                    last_err, failures = e, failures + 1
                    log.warning("validation failed on %s: %s", spec, str(e)[:200])
                except MissingKeyError as e:
                    last_err = e
                    _limiter.kill(spec, str(e))
                except Exception as e:
                    last_err, msg = e, str(e)
                    low = msg.lower()
                    if "413" in msg or "too large" in low or "context_length" in low or "maximum context" in low:
                        skip.add(spec)
                    elif "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate limit" in low:
                        _limiter.cool(spec, _retry_delay(msg, 30))
                    elif "503" in msg or "UNAVAILABLE" in msg or "overloaded" in low:
                        _limiter.cool(spec, 20)
                    elif "404" in msg or "NOT_FOUND" in msg or "model_not_found" in low or "does not exist" in low \
                            or "decommissioned" in low:
                        _limiter.kill(spec, "model not available: " + msg[:120])
                    elif "connection" in low or "timed out" in low or "timeout" in low or "proxy" in low:
                        # unreachable host (network policy, DNS, outage): give up on it after 2 strikes
                        if _limiter.strike(spec) >= 2:
                            _limiter.kill(spec, "host unreachable: " + msg[:100])
                        else:
                            _limiter.cool(spec, 5)
                    elif "401" in msg or "invalid_api_key" in low or "incorrect api key" in low:
                        _limiter.kill(spec, "API key rejected (401)")
                    elif "insufficient_quota" in low:
                        _limiter.kill(spec, "provider quota/billing exhausted")
                    else:
                        failures += 1
                        _limiter.cool(spec, 3)
                    log.info("LLM %s unavailable: %s", spec, msg[:160].replace("\n", " "))
                    waits.append(1.0)
            if waits:
                time.sleep(min(min(waits), 15))
        raise LLMError(f"All models failed for {schema.__name__}: {last_err}")


def dead_models() -> dict[str, str]:
    return dict(_limiter.dead)
