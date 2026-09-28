"""Shared utilities for Evey plugins — LLM calls, HTTP helpers, retry logic.

All plugins should use these instead of rolling their own urllib code.
- call_llm(): Quick LLM call, returns string or None
- call_model(): Full LLM call with usage info, retries, reasoning recovery
- http_get(): GET with error handling
- http_post_json(): POST JSON with error handling
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error
import subprocess

logger = logging.getLogger("evey.utils")

LITELLM_URL = os.environ.get("OPENAI_BASE_URL", "")
LITELLM_KEY = os.environ.get("OPENAI_API_KEY", "")


def _hermes_config():
    """Read this Hermes profile's configured model and provider."""
    result = {}
    try:
        out = subprocess.run(
            ["hermes", "config", "get", "model.default"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            result["model"] = out.stdout.strip()
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["hermes", "config", "get", "model.provider"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            result["provider"] = out.stdout.strip()
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["hermes", "config", "get", "model.base_url"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            result["base_url"] = out.stdout.strip()
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["hermes", "config", "get", "model.api_key"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            result["api_key"] = out.stdout.strip()
    except Exception:
        pass
    return result


def _resolve_model(model):
    """Return a model string to use for an LLM call.

    Falls back from the requested model to this profile's configured model,
    then to OPENAI_BASE_URL / OPENAI_API_KEY if set, then to None.
    """
    if model and model != "auto":
        return model
    cfg = _hermes_config()
    if cfg.get("model"):
        return cfg["model"]
    if LITELLM_URL or LITELLM_KEY:
        return os.environ.get("LITELLM_MODEL", "default")
    return None


def _resolve_endpoint():
    """Return the base URL for LLM calls."""
    cfg = _hermes_config()
    if cfg.get("base_url"):
        return cfg["base_url"]
    if cfg.get("provider"):
        return os.environ.get("OPENAI_BASE_URL", "")
    return LITELLM_URL


def _resolve_key():
    """Return the API key to use for LLM calls."""
    cfg = _hermes_config()
    if cfg.get("api_key"):
        return cfg["api_key"]
    if LITELLM_KEY:
        return LITELLM_KEY
    return os.environ.get("HERMES_API_KEY", "")


def call_llm(model, prompt, max_tokens=200, temperature=0.3, retries=2):
    """Quick LLM call. Returns content string or None on failure."""
    result = call_model(model, prompt, max_tokens=max_tokens, temperature=temperature, retries=retries)
    return result.get("content") if result else None


def call_model(model, prompt, max_tokens=2000, temperature=0.7, retries=2, timeout=60):
    """Full LLM call via LiteLLM or this profile's configured model.

    Returns dict: {"content": str, "tokens": int, "model": str, "attempts": int}
    Returns None on total failure.
    """
    resolved_model = _resolve_model(model)
    resolved_url = _resolve_endpoint()
    resolved_key = _resolve_key()

    if not resolved_url:
        logger.error("No LLM endpoint configured")
        return None
    if not resolved_key:
        logger.error("No LLM API key configured")
        return None

    data = json.dumps({
        "model": resolved_model or "default",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    for attempt in range(1, retries + 2):
        try:
            req = urllib.request.Request(
                f"{resolved_url}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {resolved_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())

            msg = result["choices"][0]["message"]
            content = msg.get("content", "") or ""
            usage = result.get("usage", {})

            if not content.strip():
                content = result.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")

            return {
                "content": content,
                "tokens": usage.get("total_tokens", 0),
                "model": resolved_model or "default",
                "attempts": attempt,
            }
        except Exception as e:
            logger.debug(f"LLM call attempt {attempt} failed: {e}")
            if attempt == retries + 1:
                return None
    return None


def http_get(url, timeout=10, headers=None):
    """GET request with timeout. Returns parsed JSON or None."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def http_post_json(url, body, timeout=10, headers=None):
    """POST JSON with timeout. Returns parsed JSON or None."""
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None
