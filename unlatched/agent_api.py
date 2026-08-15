"""agent_api.py - Optional, bring-your-own OpenAI-compatible endpoint client.

Nothing in the scoring path (screen.py, coverage.py, enrich.py) imports this
module or anything it touches. That is deliberate: search results have to
mean the same thing for every user regardless of whether they have a model
subscription, which is only true if scoring never depends on one.

What a model IS useful for is judgement where reproducibility does not
matter - drafting search terms for an unfamiliar field, for instance. This
client sends exactly that: text the user typed or their own resume content.
It never receives a scraped posting, and it refuses to run at all unless
`config.agent_api.base_url` is set - there is no default endpoint, paid or
otherwise, baked into the product.

An OpenAI-compatible `/chat/completions` shape is deliberately the only one
supported: a local Ollama server and a frontier API answer to the same
request body at that path, so this is one code path for both, not a
provider integration to maintain per backend.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


class AgentNotConfiguredError(RuntimeError):
    pass


# This module does not go through fetch() - it posts to an endpoint the user
# configured, not to a job board - so it has to bring fetch()'s size cap with
# it. Without one, r.read() is unbounded and a misbehaving endpoint (or a
# mistyped address that lands on something enormous) exhausts memory.
MAX_RESPONSE_BYTES = 2_000_000


class ChatFn(Protocol):
    def __call__(self, base_url: str, api_key: str | None, model: str | None,
                  messages: list[dict[str, str]], timeout: float = ...) -> str: ...


def is_configured(cfg: dict[str, Any]) -> bool:
    return bool((cfg.get("agent_api") or {}).get("base_url"))


def _chat(base_url: str, api_key: str | None, model: str | None,
          messages: list[dict[str, str]], timeout: float = 30.0) -> str:
    scheme = urllib.parse.urlsplit(base_url).scheme
    if scheme not in ("http", "https"):
        raise RuntimeError(
            f"agent_api.base_url must be http or https, got {scheme!r}")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model or "default", "messages": messages}
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            body = json.loads(r.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"agent endpoint unreachable: {e}") from e
    try:
        return str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected response shape from agent endpoint: {body!r}") from e


# Where the common local model runners listen by default. Offered as
# starting points, NOT as one universal address: 11434 is Ollama's default
# and nobody else's, and presenting it as "the" address strands anyone using
# a different runner, a container with a remapped port, OLLAMA_HOST set, or a
# machine across the room.
KNOWN_LOCAL_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
    ("llama.cpp server", "http://localhost:8080/v1"),
    ("Jan", "http://localhost:1337/v1"),
)


def check(cfg: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Is the configured endpoint actually answering?

    Guessing an address and finding out later that suggestions silently fail
    is the worst of both worlds, so this asks the endpoint directly. It lists
    models rather than starting a conversation: every OpenAI-shaped server
    exposes /models, it costs nothing, it needs no model name to be right
    yet, and it sends none of the user's data anywhere.
    """
    agent = cfg.get("agent_api") or {}
    base_url = str(agent.get("base_url") or "").strip()
    if not base_url:
        return {"ok": False, "detail": "no endpoint configured"}
    scheme = urllib.parse.urlsplit(base_url).scheme
    if scheme not in ("http", "https"):
        return {"ok": False, "detail": f"base_url must be http or https, got {scheme!r}"}

    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)  # noqa: S310 - scheme checked above
    if agent.get("api_key"):
        req.add_header("Authorization", f"Bearer {agent['api_key']}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            body = json.loads(r.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # A 401 means something IS there and it wants a key - a different and
        # much more useful answer than "unreachable".
        if e.code in (401, 403):
            return {"ok": False, "detail": "reachable, but it refused without a valid API key"}
        return {"ok": False, "detail": f"reachable, but returned HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "detail": f"nothing answered at {url} ({e.reason})"}
    except (TimeoutError, OSError) as e:
        return {"ok": False, "detail": f"no answer from {url} ({e})"}

    models = [str(m.get("id")) for m in (body.get("data") or []) if isinstance(m, dict)]
    return {
        "ok": True,
        "detail": (f"answered with {len(models)} model(s)" if models
                    else "answered, but listed no models"),
        "models": models[:20],
    }


def suggest_terms(cfg: dict[str, Any], skills: list[str], resume_text: str = "",
                   chat_fn: ChatFn = _chat) -> str:
    """Ask the configured endpoint for search-term ideas.

    Only ever sends the user's OWN skills list and resume text - never a
    posting, never anything fetched from the network. Refuses politely
    (raises AgentNotConfiguredError, which the CLI turns into a plain message and
    exit code 1) when no endpoint is configured, rather than silently doing
    nothing or falling back to a hardcoded default.
    """
    agent = cfg.get("agent_api") or {}
    if not agent.get("base_url"):
        raise AgentNotConfiguredError(
            "no agent endpoint configured - set agent_api.base_url first "
            "(config set agent_api.base_url http://localhost:11434/v1)")

    parts = []
    if skills:
        parts.append("Skills: " + ", ".join(skills))
    if resume_text:
        parts.append("Resume:\n" + resume_text)
    if not parts:
        raise AgentNotConfiguredError(
            "no skills or resume_path configured - nothing of yours to send")
    user_text = "\n\n".join(parts)

    messages = [
        {"role": "system",
         "content": ("Suggest 10-15 job-search title and keyword terms based "
                     "only on the skills and resume text provided. One term "
                     "per line, no numbering, no commentary.")},
        {"role": "user", "content": user_text},
    ]
    return chat_fn(agent["base_url"], agent.get("api_key"), agent.get("model"), messages)
