"""LLM access behind ONE function signature:  llm(system_prompt, user_prompt) -> raw text.

To add a provider, add a `_make_<name>` factory and register it in `get_llm`.
Plain HTTPS calls (requests) are used so there is no SDK version to manage.
API keys are only ever held in memory / Streamlit secrets - never written to disk or the database.
"""
from __future__ import annotations

import os
import time
from typing import Callable

import requests

PROVIDERS = {
    "offline-demo": "Offline demo scorer (no API key, keyword heuristic)",
    "offline-demo-messy": "Offline demo - deliberately messy output (shows validation)",
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
}
# Model names change often - these are only defaults; they can be edited in the app sidebar.
DEFAULT_MODELS = {
    "offline-demo": "keyword-heuristic-v1",
    "offline-demo-messy": "keyword-heuristic-v1-messy",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}
ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3

LLM = Callable[[str, str], str]


class LLMError(Exception):
    """The provider call failed (network, quota, bad key, bad model ...)."""


class LLMConfigError(Exception):
    """The provider is not configured (for example, no API key)."""


def needs_key(provider: str) -> bool:
    return provider in ENV_KEYS


def _error_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except ValueError:
        msg = resp.text
    return (msg or "no details")[:300]


def _post(url: str, headers: dict, build_payload: Callable[[bool], dict], timeout: int) -> dict:
    include_temperature, last = True, "unknown error"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=build_payload(include_temperature), timeout=timeout)
        except requests.RequestException as exc:
            last = f"network error ({exc.__class__.__name__})"
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last = "provider response was not JSON"
            else:
                last = f"HTTP {resp.status_code}: {_error_text(resp)}"
                if resp.status_code == 400 and include_temperature and "temperature" in last.lower():
                    include_temperature = False   # some models reject the parameter
                    continue
                if resp.status_code not in RETRYABLE:
                    break                         # bad key / bad model: retrying will not help
        time.sleep(2 ** attempt)
    raise LLMError(last)


def _make_anthropic(model: str, key: str, timeout: int) -> LLM:
    def call(system: str, user: str) -> str:
        def payload(temp: bool) -> dict:
            p = {"model": model, "max_tokens": 4096, "system": system,
                 "messages": [{"role": "user", "content": user}]}
            if temp:
                p["temperature"] = 0
            return p
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                     payload, timeout)
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError("unexpected response format from Anthropic") from exc
    return call


def _make_openai(model: str, key: str, timeout: int) -> LLM:
    def call(system: str, user: str) -> str:
        def payload(temp: bool) -> dict:
            p = {"model": model, "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if temp:
                p["temperature"] = 0
            return p
        data = _post("https://api.openai.com/v1/chat/completions",
                     {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, payload, timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response format from OpenAI") from exc
    return call


def _make_gemini(model: str, key: str, timeout: int) -> LLM:
    def call(system: str, user: str) -> str:
        def payload(temp: bool) -> dict:
            cfg = {"responseMimeType": "application/json"}
            if temp:
                cfg["temperature"] = 0
            return {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": cfg}
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     {"x-goog-api-key": key, "Content-Type": "application/json"}, payload, timeout)
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response format from Gemini (possibly blocked)") from exc
    return call


def get_llm(provider: str, model: str | None = None, api_key: str | None = None, timeout: int = 90) -> LLM:
    if provider not in PROVIDERS:
        raise LLMConfigError(f"Unknown provider '{provider}'.")
    model = (model or DEFAULT_MODELS[provider]).strip()
    if provider.startswith("offline-demo"):
        from tools.demo_llm import make_demo_llm
        return make_demo_llm(messy=provider.endswith("messy"))
    key = (api_key or os.environ.get(ENV_KEYS[provider], "")).strip()
    if not key:
        raise LLMConfigError(f"No API key for {PROVIDERS[provider]}. Add {ENV_KEYS[provider]} to secrets or the sidebar.")
    return {"anthropic": _make_anthropic, "openai": _make_openai, "gemini": _make_gemini}[provider](model, key, timeout)
