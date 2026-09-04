"""The optional bring-your-own model endpoint, and what it is allowed to send.

This module had no tests. It is 160 lines that open a socket and post text, and
the promise in its own docstring - "It never receives a scraped posting" - is
the kind of claim that is true until somebody adds a convenient parameter.

WHAT IS WORTH ASSERTING HERE is therefore not the HTTP mechanics but the
boundary: nothing in the scoring path may depend on this, and nothing the
employer wrote may leave through it.
"""
from __future__ import annotations

import urllib.error

import pytest

from unlatched import agent_api


def _sent() -> tuple[list[dict[str, str]], dict[str, object]]:
    """A fake chat function that records what it was handed."""
    captured: dict[str, object] = {}

    def chat_fn(base_url, api_key, model, messages, timeout=30.0):
        captured.update(base_url=base_url, api_key=api_key, model=model,
                        messages=messages, timeout=timeout)
        return "support analyst\nservice desk"

    return chat_fn, captured


# ---- the boundary -----------------------------------------------------------

def test_it_sends_only_the_persons_own_words():
    """THE PROMISE THE MODULE MAKES. A posting is text a stranger wrote, and
    handing one to a model is the prompt-injection surface the whole
    attachment trust split exists to avoid. Only skills and resume text go."""
    chat_fn, captured = _sent()
    cfg = {"agent_api": {"base_url": "http://localhost:11434/v1"}}

    agent_api.suggest_terms(cfg, ["Customer Service", "Active Directory"],
                            resume_text="Ten years supporting end users.",
                            chat_fn=chat_fn)

    body = "\n".join(m["content"] for m in captured["messages"])
    assert "Customer Service" in body
    assert "Ten years supporting end users." in body
    # Nothing else reached it: the only inputs are the two arguments above.
    assert set(captured["messages"][0]) == {"role", "content"}
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


def test_nothing_in_the_scoring_path_imports_this():
    """Search results have to mean the same thing for everybody, which is only
    true while scoring cannot depend on a model. Asserted against the source
    rather than trusted, because an import is one line."""
    from pathlib import Path

    engine = Path(__file__).resolve().parent.parent / "unlatched"
    for name in ("screen.py", "coverage.py", "enrich.py", "keywords.py"):
        text = (engine / name).read_text(encoding="utf-8")
        assert "agent_api" not in text, (
            f"{name} reaches the model client - scoring would stop being "
            f"reproducible for anybody without an endpoint")


# ---- refusing rather than doing nothing --------------------------------------

def test_an_unconfigured_endpoint_refuses_out_loud():
    """Silently doing nothing, or falling back to some default endpoint, are
    both worse: one looks broken and the other sends the person's resume
    somewhere they never named."""
    chat_fn, _ = _sent()
    with pytest.raises(agent_api.AgentNotConfiguredError):
        agent_api.suggest_terms({}, ["Skill"], chat_fn=chat_fn)


def test_nothing_of_theirs_to_send_is_also_a_refusal():
    chat_fn, captured = _sent()
    cfg = {"agent_api": {"base_url": "http://localhost:11434/v1"}}
    with pytest.raises(agent_api.AgentNotConfiguredError):
        agent_api.suggest_terms(cfg, [], resume_text="", chat_fn=chat_fn)
    assert not captured, "an empty request was still sent"


def test_is_configured_reads_the_endpoint_and_not_the_key():
    assert not agent_api.is_configured({})
    assert not agent_api.is_configured({"agent_api": {"api_key": "k"}})
    assert agent_api.is_configured({"agent_api": {"base_url": "http://x/v1"}})


# ---- what it will talk to ----------------------------------------------------

@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://x/v1",
                                  "javascript:alert(1)"])
def test_only_http_endpoints_are_contacted(bad):
    """The same scheme rule the rest of the app applies to links. A base_url
    is a place this posts the person's resume to."""
    with pytest.raises(RuntimeError, match="http"):
        agent_api._chat(bad, None, None, [{"role": "user", "content": "x"}])  # noqa: SLF001

    assert agent_api.check({"agent_api": {"base_url": bad}})["ok"] is False


def test_check_says_no_endpoint_rather_than_failing():
    result = agent_api.check({})
    assert result["ok"] is False
    assert "no endpoint" in result["detail"]


# ---- the failure a local model actually produces -----------------------------

def test_a_stalled_endpoint_is_reported_not_raised(monkeypatch):
    """MEASURED, not assumed: a server that completes the handshake and then
    says nothing raises a bare TimeoutError, which is an OSError and NOT a
    URLError. Catching only URLError let it escape as a traceback.

    This is the likeliest failure here, because the endpoint is usually a
    model on the same machine - connecting always succeeds, and thinking is
    what takes time.
    """
    def stall(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(agent_api.urllib.request, "urlopen", stall)

    with pytest.raises(RuntimeError, match="unreachable"):
        agent_api._chat("http://localhost:11434/v1", None, None,  # noqa: SLF001
                        [{"role": "user", "content": "x"}])


def test_an_unreachable_endpoint_is_reported_too(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(agent_api.urllib.request, "urlopen", refuse)

    with pytest.raises(RuntimeError, match="unreachable"):
        agent_api._chat("http://localhost:11434/v1", None, None,  # noqa: SLF001
                        [{"role": "user", "content": "x"}])


def test_the_desktop_offers_the_same_endpoints_this_module_does():
    """agent.rs says it mirrors KNOWN_LOCAL_ENDPOINTS and nothing held it to
    that - the two had already drifted, one calling the same server
    "llama.cpp" and the other "llama.cpp server".

    The label is cosmetic; the ADDRESS is not. A port that moved on one side
    only would have somebody typing a suggestion from one screen that the
    other half cannot reach, with nothing to explain it. Both are compared,
    because two screens naming one program differently is its own small lie.
    """
    import re
    from pathlib import Path

    rust = (Path(__file__).resolve().parent.parent
            / "desktop" / "src" / "views" / "agent.rs").read_text(encoding="utf-8")
    block = re.search(r"LOCAL_ENDPOINTS[^=]*=\s*\[(.*?)\];", rust, re.DOTALL)
    assert block, "agent.rs no longer declares LOCAL_ENDPOINTS"
    theirs = re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', block.group(1))

    assert theirs, "the parse of agent.rs found nothing - this check is vacuous"
    assert theirs == list(agent_api.KNOWN_LOCAL_ENDPOINTS), (
        "the two halves offer different endpoints\n"
        f"  rust:   {theirs}\n"
        f"  python: {list(agent_api.KNOWN_LOCAL_ENDPOINTS)}")


def test_the_suggested_local_endpoints_are_offered_as_examples():
    """Not as one universal address. 11434 is Ollama's default and nobody
    else's, and presenting it as THE address strands anyone on a different
    runner or a remapped port."""
    assert len(agent_api.KNOWN_LOCAL_ENDPOINTS) >= 3
    names = {name for name, _ in agent_api.KNOWN_LOCAL_ENDPOINTS}
    assert len(names) == len(agent_api.KNOWN_LOCAL_ENDPOINTS), "a duplicate name"
    for _name, url in agent_api.KNOWN_LOCAL_ENDPOINTS:
        assert url.startswith("http://localhost:"), url
