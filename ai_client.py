import json
import re
import threading
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

import settings

_client = OpenAI(base_url=settings.AI_BASE_URL, api_key=settings.AI_API_KEY, timeout=settings.AI_TIMEOUT)
# The proxy fronts a single Codex CLI subscription; keep concurrent calls low.
_gate = threading.BoundedSemaphore(settings.AI_MAX_CONCURRENCY)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AIResponseError(RuntimeError):
    pass


def _retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    return False


def chat(system: str, user: str) -> str:
    last_err: Exception | None = None
    attempts = settings.AI_HTTP_RETRIES + 1
    for attempt in range(attempts):
        try:
            with _gate:
                resp = _client.chat.completions.create(
                    model=settings.AI_MODEL,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last_err = exc
            if not _retryable_http_error(exc) or attempt >= attempts - 1:
                raise
            time.sleep(settings.AI_RETRY_BASE_SECONDS * (2 ** attempt))
    raise last_err or AIResponseError("AI request failed without an exception")


def parse_json(text: str):
    # Proxy may not honour response_format, so tolerate fenced or prose-wrapped JSON.
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise AIResponseError(f"AI response is not valid JSON: {text[:300]!r}")


def chat_json(system: str, user: str, retries: int = 1):
    last_err: Exception | None = None
    for _ in range(retries + 1):
        raw = chat(system + "\nRespond with JSON only, no prose.", user)
        try:
            return parse_json(raw)
        except AIResponseError as e:
            last_err = e
    raise last_err
