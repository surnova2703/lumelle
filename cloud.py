"""Online models by the owner's own API keys: Claude (Anthropic), ChatGPT (OpenAI), Perplexity.

They are optional: Lumelle works offline without them. When one is chosen, the question, the chat
history and the excerpts from the documents are sent to that company over the internet.
Model names change often, so they are not written here: the list comes from the service itself.
"""
from typing import List, Optional

import httpx

PROVIDERS = {
    "anthropic": {"label": "Claude", "keys_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "ChatGPT", "keys_url": "https://platform.openai.com/api-keys"},
    "perplexity": {"label": "Perplexity", "keys_url": "https://www.perplexity.ai/account/api"},
}
# Perplexity's Agent API has no model list: the "effort" decides how deep it searches
PERPLEXITY_EFFORTS = ["fast", "low", "medium", "high"]
TIMEOUT = httpx.Timeout(300, connect=10)


class CloudError(Exception):
    """No internet, a wrong key, no money on the account and so on — the text is shown to the user."""


def _check(r: httpx.Response, label: str):
    if r.status_code in (401, 403):
        raise CloudError(f"{label}: the API key was not accepted")
    if r.status_code == 402 or (r.status_code == 429 and "quota" in r.text.lower()):
        raise CloudError(f"{label}: no credit left on the account")
    if r.status_code == 429:
        raise CloudError(f"{label}: too many requests, try again in a minute")
    if r.status_code >= 400:
        raise CloudError(f"{label}: error {r.status_code}: {r.text[:200]}")


async def list_models(provider: str, key: str) -> List[str]:
    """Models this key can use, newest/most suitable first. Also checks that the key works."""
    label = PROVIDERS[provider]["label"]
    if provider == "perplexity":
        return PERPLEXITY_EFFORTS
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if provider == "anthropic":
                r = await client.get("https://api.anthropic.com/v1/models?limit=100",
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
                _check(r, label)
                return [m["id"] for m in r.json().get("data", [])]  # the API lists the newest first
            r = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
            _check(r, label)
            data = sorted(r.json().get("data", []), key=lambda m: -m.get("created", 0))
            skip = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "moderation", "search", "whisper", "dall")
            return [m["id"] for m in data if m["id"].startswith(("gpt", "o")) and not any(s in m["id"] for s in skip)]
    except httpx.HTTPError:
        raise CloudError(f"{label}: no connection — check the internet")


def default_model(provider: str, models: List[str]) -> Optional[str]:
    """A sensible first choice: a balanced model, not the most expensive one."""
    if not models:
        return None
    prefer = {"anthropic": ("sonnet",), "openai": ("mini",), "perplexity": ("fast",)}[provider]
    for m in models:
        if any(p in m for p in prefer):
            return m
    return models[0]


def _output_text(data: dict) -> str:
    """Text from a Responses-style answer (OpenAI Responses API, Perplexity Agent API)."""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    parts.append(c["text"])
    return "\n".join(parts)


def _sources(data: dict) -> str:
    seen, lines = set(), []
    for item in data.get("output", []):
        if item.get("type") == "search_results":
            for res in item.get("results", []):
                url = res.get("url")
                if url and url not in seen:
                    seen.add(url)
                    n = res.get("id", len(lines) + 1)
                    lines.append(f"[{n}] {res.get('title') or url} — {url}")
    return ("\n\n" + "\n".join(lines)) if lines else ""


async def ask(provider: str, key: str, model: str, system: str, messages: List[dict], temperature: float) -> str:
    """One answer from an online model. messages: [{role: user|assistant, content: str}]."""
    label = PROVIDERS[provider]["label"]
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("content")]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if provider == "anthropic":
                body = {"model": model, "max_tokens": 8192, "system": system, "messages": msgs, "temperature": temperature}
                headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
                r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
                if r.status_code == 400 and "temperature" in r.text:
                    body.pop("temperature")
                    r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
                _check(r, label)
                return "".join(c.get("text", "") for c in r.json().get("content", []) if c.get("type") == "text").strip()
            if provider == "openai":
                r = await client.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {key}"},
                                      json={"model": model, "instructions": system, "input": msgs})
                _check(r, label)
                return _output_text(r.json()).strip()
            # Perplexity Agent API: searches the web and returns its sources
            headers = {"Authorization": f"Bearer {key}"}
            body = {"effort": model or "fast", "instructions": system, "input": msgs, "tools": [{"type": "web_search"}]}
            r = await client.post("https://api.perplexity.ai/v1/agent", json=body, headers=headers)
            if r.status_code in (400, 422):  # older accounts accept only one text as input
                history = "\n\n".join(f"{m['role']}: {m['content']}" for m in msgs[:-1])
                body["input"] = (f"Conversation so far:\n{history}\n\nQuestion: " if history else "") + msgs[-1]["content"]
                r = await client.post("https://api.perplexity.ai/v1/agent", json=body, headers=headers)
            _check(r, label)
            data = r.json()
            return (_output_text(data).strip() + _sources(data)).strip()
    except httpx.HTTPError:
        raise CloudError(f"{label}: no connection — check the internet")
