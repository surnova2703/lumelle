"""Online models by the owner's own API keys: GigaChat (Sber), YandexGPT (Yandex AI Studio), Claude (Anthropic), ChatGPT (OpenAI), Perplexity.

They are optional: Lumelle works offline without them. When one is chosen, the question, the chat
history and the excerpts from the documents are sent to that company over the internet.
Model names change often, so they are not written here: the list comes from the service itself.
"""
import ssl
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx

PROVIDERS = {
    "gigachat": {"label": "GigaChat", "keys_url": "https://developers.sber.ru/docs/ru/gigachat/individuals-quickstart"},
    "yandex": {"label": "Alice / YandexGPT", "keys_url": "https://aistudio.yandex.ru/docs/ru/ai-studio/quickstart/"},
    "anthropic": {"label": "Claude", "keys_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "ChatGPT", "keys_url": "https://platform.openai.com/api-keys"},
    "perplexity": {"label": "Perplexity", "keys_url": "https://www.perplexity.ai/account/api"},
}
# Perplexity's Agent API has no model list: the "effort" decides how deep it searches
PERPLEXITY_EFFORTS = ["fast", "low", "medium", "high"]
TIMEOUT = httpx.Timeout(300, connect=10)

# GigaChat: the key ("Authorization key") is exchanged for a token that lives 30 minutes.
# Its servers use the Russian Trusted Root CA, which Lumelle trusts only for these requests.
GIGACHAT_AUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_HOSTS = ["https://gigachat.devices.sberbank.ru/api/v1", "https://api.giga.chat/v1"]
GIGACHAT_CERT = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"
_giga_tokens = {}  # key -> (token, expires at, unix seconds)


def _giga_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    if GIGACHAT_CERT.exists():
        ctx.load_verify_locations(str(GIGACHAT_CERT))
    return ctx


async def _giga_token(client: httpx.AsyncClient, key: str) -> str:
    tok = _giga_tokens.get(key)
    if tok and tok[1] > time.time() + 60:
        return tok[0]
    r = await client.post(GIGACHAT_AUTH, data={"scope": "GIGACHAT_API_PERS"},
                          headers={"Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()), "Accept": "application/json"})
    if r.status_code in (400, 401, 403):
        raise CloudError("GigaChat: the authorization key was not accepted (copy the «Authorization key» of the project)")
    _check(r, "GigaChat")
    data = r.json()
    token = data.get("access_token") or data.get("tok")
    exp = data.get("expires_at") or data.get("exp") or 0
    exp = exp / 1000 if exp > 1e11 else (exp or time.time() + 1500)
    _giga_tokens[key] = (token, exp)
    return token


# YandexGPT: the stored key is "<folder ID>|<API key>" (both come from Yandex AI Studio)
YANDEX_MODELS = "https://ai.api.cloud.yandex.net/v1/models"
YANDEX_CHAT = "https://llm.api.cloud.yandex.net/v1/chat/completions"
# Used only if the service doesn't return its list; «aliceai-llm» is the model of Alice (Алиса AI)
YANDEX_FALLBACK = ("aliceai-llm", "aliceai-llm-flash", "yandexgpt-5.1", "yandexgpt-5-pro", "deepseek-v4.1-flash",
                   "qwen3.6-35b-a3b")


def _yandex_parts(key: str):
    folder, _, api_key = key.partition("|")
    if not folder or not api_key:
        raise CloudError("YandexGPT: both the folder ID and the API key are needed")
    return folder.strip(), api_key.strip()


async def _yandex(method: str, url: str, key: str, json_body=None) -> dict:
    folder, api_key = _yandex_parts(key)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = None
            for auth in (f"Api-Key {api_key}", f"Bearer {api_key}"):
                r = await client.request(method, url, json=json_body, headers={
                    "Authorization": auth, "x-project": folder, "x-folder-id": folder, "OpenAI-Project": folder})
                if r.status_code != 401:
                    break
            _check(r, "YandexGPT")
            return r.json()
    except httpx.HTTPError:
        raise CloudError("YandexGPT: no connection — check the internet")


def yandex_short(model_id: str) -> str:
    """gpt://b1g…/yandexgpt/latest → yandexgpt/latest"""
    return model_id.split("/", 3)[-1] if model_id.startswith("gpt://") else model_id


async def _giga(method: str, path: str, key: str, json_body=None) -> dict:
    """A GigaChat request: token, then the first host that answers."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, verify=_giga_ssl()) as client:
            token = await _giga_token(client, key)
            last = None
            for base in GIGACHAT_HOSTS:
                try:
                    r = await client.request(method, base + path, json=json_body,
                                             headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
                except httpx.ConnectError as e:
                    last = e
                    continue
                if r.status_code == 404 and base != GIGACHAT_HOSTS[-1]:
                    continue
                if r.status_code == 401:
                    _giga_tokens.pop(key, None)
                _check(r, "GigaChat")
                return r.json()
            raise last or CloudError("GigaChat: no answer")
    except httpx.HTTPError as e:
        if "CERTIFICATE" in str(e).upper():
            raise CloudError("GigaChat: the Russian Trusted Root CA certificate is missing (certs folder)")
        raise CloudError("GigaChat: no connection — check the internet")


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
    if provider == "yandex":
        folder, _ = _yandex_parts(key)
        try:
            data = await _yandex("GET", YANDEX_MODELS, key)
            ids = [m.get("id", "") for m in data.get("data", [])]
            ids = [i for i in ids if i.startswith("gpt://") and "speech" not in i]
        except CloudError as e:
            if "error 404" not in str(e):
                raise
            ids = []
        return ids or [f"gpt://{folder}/{m}" for m in YANDEX_FALLBACK]
    if provider == "gigachat":
        data = await _giga("GET", "/models", key)
        ids = [m.get("id", "") for m in data.get("data", [])]
        return [i for i in ids if i and "embed" not in i.lower()]
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
    prefer = {"anthropic": ("sonnet",), "openai": ("mini",), "perplexity": ("fast",), "gigachat": ("2-Max", "Max", "Pro"),
              "yandex": ("yandexgpt-5.1", "yandexgpt-5-pro", "/yandexgpt/latest", "yandexgpt")}[provider]
    if provider == "yandex":  # Alice AI LLM first: the newest and largest-context Yandex model
        for m in models:
            if m.rstrip("/").endswith(("/aliceai-llm", "/aliceai-llm/latest")):
                return m
    for p in prefer:  # in the order of preference
        for m in models:
            if p in m:
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
    if provider == "yandex":
        folder, _ = _yandex_parts(key)
        if not model.startswith("gpt://"):
            model = f"gpt://{folder}/{model}"
        data = await _yandex("POST", YANDEX_CHAT, key,
                             {"model": model, "messages": [{"role": "system", "content": system}] + msgs,
                              "temperature": temperature, "max_tokens": 8000})
        choices = data.get("choices") or [{}]
        return (choices[0].get("message", {}).get("content") or "").strip()
    if provider == "gigachat":
        data = await _giga("POST", "/chat/completions", key,
                           {"model": model, "messages": [{"role": "system", "content": system}] + msgs,
                            "temperature": max(temperature, 0.01)})
        choices = data.get("choices") or [{}]
        return (choices[0].get("message", {}).get("content") or "").strip()
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
