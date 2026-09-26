"""Model-agnostic structured extraction.

One method everywhere: LLMClient.extract(schema, system, user, images=None) -> schema instance.
Models are "provider/model" strings; switching provider is a config change (LLM_MODELS).
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


# ---------------- provider backends: (model, system, user, json_schema, images) -> json text -------------
def _gemini(model: str, system: str, user: str, schema: dict, images: list[bytes] | None) -> str:
    from google import genai
    from google.genai import types

    client = _gemini.client = getattr(_gemini, "client", None) or genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images or []]
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


def _openai(model: str, system: str, user: str, schema: dict, images: list[bytes] | None) -> str:
    import base64
    from openai import OpenAI

    content = [{"type": "text", "text": user}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()}}
        for i in images or []]
    resp = OpenAI().chat.completions.create(
        model=model, temperature=0.1,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        response_format={"type": "json_schema", "json_schema": {"name": "out", "schema": schema}},
    )
    return resp.choices[0].message.content


def _anthropic(model: str, system: str, user: str, schema: dict, images: list[bytes] | None) -> str:
    import base64
    import anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(i).decode()}} for i in images or []]
    content.append({"type": "text", "text": user})
    resp = anthropic.Anthropic().messages.create(
        model=model, max_tokens=4096, system=system, messages=[{"role": "user", "content": content}],
        tools=[{"name": "emit", "description": "Return the result", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "emit"},
    )
    return json.dumps(next(b.input for b in resp.content if b.type == "tool_use"))


BACKENDS = {"gemini": _gemini, "openai": _openai, "anthropic": _anthropic}


class _RateLimiter:
    """Per-model sliding-window RPM limit plus cooldowns after 429/503. Shared across threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: dict[str, deque] = defaultdict(deque)
        self.cooldown: dict[str, float] = {}

    def try_acquire(self, spec: str) -> float:
        """Reserve a slot now and return 0, or return seconds until this model may be available."""
        with self.lock:
            now = time.monotonic()
            if (until := self.cooldown.get(spec, 0)) > now:
                return until - now
            q = self.calls[spec]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= config.LLM_RPM:
                return 60 - (now - q[0]) + 0.1
            q.append(now)
            return 0.0

    def cool(self, spec: str, seconds: float) -> None:
        with self.lock:
            self.cooldown[spec] = max(self.cooldown.get(spec, 0), time.monotonic() + seconds)


_limiter = _RateLimiter()


def _retry_delay(msg: str, default: float) -> float:
    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"retryDelay'?:\s*'?(\d+)s", msg)
    return float(m.group(1)) + 1 if m else default


class LLMClient:
    def __init__(self, models: list[str] | None = None):
        self.models = models or config.LLM_MODELS
        self.calls = 0
        self.cache_hits = 0

    def extract(self, schema: type[T], system: str, user: str, images: list[bytes] | None = None,
                max_retries: int = 2, deadline_s: float = 600) -> T:
        img_hash = [hashlib.sha256(i).hexdigest() for i in images or []]
        key = "llm:" + hashlib.sha256(json.dumps(
            [schema.__name__, schema.model_json_schema(), system, user, img_hash]).encode()).hexdigest()
        if (hit := db.cache_get(key)) is not None:
            self.cache_hits += 1
            return schema.model_validate(hit)

        json_schema = schema.model_json_schema()
        failures = 0                       # real failures (validation / unknown errors), not rate limits
        last_err: Exception | None = None
        start = time.monotonic()
        while time.monotonic() - start < deadline_s and failures <= max_retries * len(self.models):
            waits = []
            for spec in self.models:
                if (w := _limiter.try_acquire(spec)) > 0:
                    waits.append(w)
                    continue
                provider, model = spec.split("/", 1)
                prompt = user if not isinstance(last_err, ValidationError) else (
                    f"{user}\n\nYour previous answer failed validation: {str(last_err)[:500]}. Return valid JSON.")
                try:
                    self.calls += 1
                    text = BACKENDS[provider](model, system, prompt, json_schema, images)
                    out = schema.model_validate_json(text)
                    db.cache_put(key, out.model_dump())
                    return out
                except ValidationError as e:
                    last_err, failures = e, failures + 1
                    log.warning("validation failed on %s: %s", spec, str(e)[:200])
                except Exception as e:
                    last_err, msg = e, str(e)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        _limiter.cool(spec, _retry_delay(msg, 30))
                    elif "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg:
                        _limiter.cool(spec, 20)
                    elif "404" in msg or "NOT_FOUND" in msg:
                        _limiter.cool(spec, 3600)
                    else:
                        failures += 1
                        _limiter.cool(spec, 3)
                    log.info("LLM %s unavailable: %s", spec, msg[:120].replace("\n", " "))
                    waits.append(1.0)
            if waits:
                time.sleep(min(min(waits), 15))
        raise LLMError(f"All models failed for {schema.__name__}: {last_err}")
