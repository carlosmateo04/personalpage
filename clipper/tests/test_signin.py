"""Signing in to Claude from the app and switching between saved accounts.

The sign-in tool is stood in for by tests/fake_ant.py, which follows the documented
behaviour of Anthropic's `ant`; the profile it writes is read by the real SDK.
"""

import os
import stat
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from clipper import claude, config, settings, signin
from standins import GOOD_KEY

SHAPE = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
HELLO = [{"type": "text", "text": "hello"}]


@pytest.fixture
def ant(tmp_path, monkeypatch):
    """Anthropic's sign-in tool on the PATH (the stand-in), and an empty folder for its profiles."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = Path(__file__).with_name("fake_ant.py")
    if os.name == "nt":
        (bin_dir / "ant.cmd").write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
    else:
        script = bin_dir / "ant"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("FAKE_ANT", raising=False)
    return tmp_path


def approve(login, timeout=20.0):
    """What the user does: open the page the tool printed and approve; the browser then comes back to the tool."""
    deadline = time.time() + timeout
    while not login.url and time.time() < deadline:
        time.sleep(0.05)
    assert login.url.startswith("https://platform.claude.com/"), login.lines
    port = login.url.split("localhost%3A")[1].split("%2F")[0]
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc", timeout=10).read()
    while not login.done and time.time() < deadline:
        time.sleep(0.05)
    return login


def test_signing_in_stores_a_profile_that_the_sdk_then_uses(ant, claude_api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert signin.tool().endswith(("ant", "ant.cmd", "ant.CMD")) and signin.profiles() == []
    login = approve(signin.Login("clipper-personal").start())
    assert login.ok and login.error == "" and signin.profiles() == ["clipper-personal"] and signin.exists("clipper-personal")
    assert login.details == {"organization": "Leonida Clips LLC", "email": "owner@example.com", "workspace": "Default"}
    assert signin.facts("clipper-personal") == {"organization_id": "org_clipper-personal", "workspace_id": "wrkspc_default"}

    cfg = config.load()
    cfg.ai.provider, cfg.ai.login_profile = "login", "clipper-personal"
    assert claude.unavailable(cfg) == "" and claude.test(cfg) == ""
    claude_api.replies.append({"ok": True})
    assert claude.ask(cfg, "s", HELLO, SHAPE) == {"ok": True}
    assert claude_api.seen[-1]["auth"] == f"Bearer {GOOD_KEY}"  # the token of the sign-in, read by the SDK from the profile

    signin.logout("clipper-personal")
    assert signin.profiles() == [] and "no longer on this computer" in claude.unavailable(cfg)


def test_a_sign_in_nobody_approves_and_a_missing_or_wrong_tool(ant, monkeypatch):
    monkeypatch.setenv("FAKE_ANT", "timeout")
    login = signin.Login("clipper-x").start()
    for _ in range(200):
        if login.done:
            break
        time.sleep(0.05)
    assert login.done and not login.ok and "Nobody approved the sign-in in time" in login.error and signin.profiles() == []

    monkeypatch.setenv("FAKE_ANT", "apache")  # the Java build tool has the same name
    assert signin.tool() == ""
    with pytest.raises(RuntimeError, match="sign-in tool \\(ant\\) is not installed"):
        signin.Login("clipper-x").start()
    monkeypatch.setenv("PATH", str(ant / "nowhere"))
    assert signin.tool() == ""


def test_accounts_are_saved_side_by_side_and_switched_at_will(ant, claude_api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    data = ant / "data"
    assert claude.accounts(config.load()) == []

    # A key saved before there were accounts shows up as one, and keeps working.
    settings.set_secret(data, "anthropic_api_key", GOOD_KEY)
    assert [(a["name"], a["kind"], a["active"]) for a in claude.accounts(config.load())] == [("API key", "key", True)]

    login = approve(signin.Login("clipper-a1").start())
    work = claude.add_account(config.load(), "  Work   sign-in ", "login", profile="clipper-a1", detail="owner@example.com, Leonida Clips LLC")
    cfg = config.load()
    assert [(a["id"], a["name"], a["label"], a["active"]) for a in claude.accounts(cfg)] == [
        ("first", "API key", "API key", False), ("a1", "Work sign-in", "Signed in with Claude", True)]
    assert (cfg.ai.provider, cfg.ai.login_profile, cfg.ai.account) == ("login", "clipper-a1", "a1") and work["id"] == "a1"
    assert claude.test(cfg) == ""

    spare = claude.add_account(config.load(), "Spare key", "key", key="sk-ant-other")
    cloud = claude.add_account(config.load(), "", "bedrock", aws_profile="bedrock-sso", aws_region="us-west-2")
    cfg = config.load()
    assert (cfg.ai.provider, cfg.ai.aws_profile, cfg.ai.aws_region, cfg.ai.account) == ("bedrock", "bedrock-sso", "us-west-2", cloud["id"])
    assert [a["name"] for a in claude.accounts(cfg)] == ["API key", "Work sign-in", "Spare key", "Amazon Bedrock"]
    kept = (data / "settings.json").read_text(encoding="utf-8")
    assert "sk-ant" not in kept  # no key is ever written to the settings

    claude.use_account(config.load(), "first")  # back to the first key, at will
    cfg = config.load()
    assert cfg.ai.provider == "anthropic" and claude.api_key(cfg) == GOOD_KEY and claude.test(cfg) == ""
    claude.use_account(config.load(), spare["id"])
    assert claude.api_key(config.load()) == "sk-ant-other" and "did not accept" in claude.test(config.load())
    claude.use_account(config.load(), "a1")
    claude_api.replies.append({"ok": True})
    assert claude.ask(config.load(), "s", HELLO, SHAPE) == {"ok": True} and claude_api.seen[-1]["auth"] == f"Bearer {GOOD_KEY}"
    with pytest.raises(KeyError):
        claude.use_account(config.load(), "nope")

    # Removing the account in use hands over to the next one; its sign-in leaves this computer.
    claude.remove_account(config.load(), "a1")
    cfg = config.load()
    assert signin.profiles() == [] and [a["id"] for a in claude.accounts(cfg)] == ["first", spare["id"], cloud["id"]] and cfg.ai.account == "first"
    claude.remove_account(config.load(), spare["id"])
    assert not settings.get_secret(data, f"anthropic_api_key_{spare['id']}")
    for ident in ("first", cloud["id"]):
        claude.remove_account(config.load(), ident)
    cfg = config.load()
    assert claude.accounts(cfg) == [] and cfg.ai.account == "" and "no Claude API key" in claude.unavailable(cfg)


def test_a_sign_in_that_was_removed_elsewhere_is_shown_as_such(ant, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    approve(signin.Login("clipper-a1").start())
    claude.add_account(config.load(), "Personal", "login", profile="clipper-a1")
    signin.logout("clipper-a1")  # signed out with the tool itself, outside the app
    cfg = config.load()
    assert claude.accounts(cfg)[0]["problem"] == "the sign-in is no longer on this computer"
    assert "sign in again" in claude.unavailable(cfg)
