"""Claude accounts in the app: signing in, adding a key or an AWS profile, switching and removing.

The sign-in tool is stood in for by tests/fake_ant.py and the Claude API by a local server.
"""

import json
import os
import time
import urllib.request

import pytest

from clipper import settings, signin
from standins import GOOD_KEY
from test_signin import ant  # noqa: F401 - a fixture
from test_ui import app, call  # noqa: F401 - `app` is a fixture


def approve_in_the_browser(url: str) -> None:
    """What the user does in the tab that opened: approve, and the browser goes back to the sign-in tool."""
    port = url.split("localhost%3A")[1].split("%2F")[0]
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc", timeout=10).read()


def ai(app) -> dict:  # noqa: F811
    return call(app, "/api/state")[1]["ai"]


def wait_for(app, done, seconds: float = 20.0) -> dict:  # noqa: F811
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = ai(app)
        if done(state):
            return state
        time.sleep(0.1)
    raise AssertionError(f"still waiting: {state['signin']} {state['accounts']}")


def test_sign_in_add_a_key_switch_and_remove(ant, app, claude_api, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    state = ai(app)
    assert state["accounts"] == [] and state["can_sign_in"] and state["signin"] == {"waiting": False, "url": "", "note": ""}
    assert state["off"] == "no Claude API key is saved (add one in the app under Accounts)"

    # --- sign in with Claude: the page gets the address to open, and the account appears once it is approved ---
    status, body, _ = call(app, "/api/claude-signin", {"name": "Personal"})
    assert status == 200 and body["url"].startswith("https://platform.claude.com/")
    assert ai(app)["signin"]["waiting"] and ai(app)["signin"]["url"] == body["url"] and ai(app)["accounts"] == []
    approve_in_the_browser(body["url"])
    state = wait_for(app, lambda s: s["accounts"])
    first = state["accounts"][0]
    assert (first["name"], first["kind"], first["label"], first["active"]) == ("Personal", "login", "Signed in with Claude", True)
    assert first["detail"] == "Leonida Clips LLC · owner@example.com" and first["profile"] == "clipper-1" and "problem" not in first
    state = wait_for(app, lambda s: s["signin"]["note"])
    assert state["signin"] == {"waiting": False, "url": body["url"], "note": "Signed in: Personal."}  # and Claude answered with it
    assert state["off"] == "" and state["provider"] == "login" and signin.profiles() == ["clipper-1"]
    assert call(app, "/api/claude-test", {})[1] == {"ok": True, "problem": ""}

    # --- a second account: an API key, tested before it is kept ---
    status, body, _ = call(app, "/api/claude-add", {"kind": "key", "name": "Work key", "api_key": "sk-ant-wrong"})
    assert status == 400 and "did not accept this key" in body["error"] and len(ai(app)["accounts"]) == 1 and ai(app)["provider"] == "login"
    assert call(app, "/api/claude-add", {"kind": "key", "name": "Work key", "api_key": " "})[0] == 400
    assert call(app, "/api/claude-add", {"kind": "key", "name": "Work key", "api_key": f" {GOOD_KEY}\n"})[1] == {"ok": True, "problem": ""}
    state = call(app, "/api/state")[1]
    assert [(a["name"], a["kind"], a["active"]) for a in state["ai"]["accounts"]] == [("Personal", "login", False), ("Work key", "key", True)]
    assert state["ai"]["provider"] == "anthropic" and state["ai"]["off"] == "" and GOOD_KEY not in json.dumps(state)  # the key never goes back to the page
    second = state["ai"]["accounts"][1]["id"]
    assert settings.get_secret(app.data, f"anthropic_api_key_{second}") == GOOD_KEY
    assert GOOD_KEY not in (app.data / "settings.json").read_text(encoding="utf-8")  # nor into the settings file

    # --- switch back and forth at will ---
    assert call(app, "/api/claude-use", {"id": first["id"]})[1] == {"ok": True, "problem": ""}
    assert [a["active"] for a in ai(app)["accounts"]] == [True, False] and ai(app)["provider"] == "login"
    assert call(app, "/api/claude-use", {"id": second})[1] == {"ok": True, "problem": ""}
    assert [a["active"] for a in ai(app)["accounts"]] == [False, True] and app.cfg().ai.account == second
    assert call(app, "/api/claude-use", {"id": "nobody"})[0] == 400

    # --- an AWS profile as a third; its sign-in may have run out, and the app says what to do instead of refusing it ---
    assert call(app, "/api/claude-add", {"kind": "bedrock", "name": ""})[0] == 400
    status, body, _ = call(app, "/api/claude-add", {"kind": "bedrock", "name": "", "aws_profile": "bedrock-sso", "aws_region": "us-west-2"})
    assert status == 200 and body["problem"]  # no such AWS sign-in on this computer
    third = ai(app)["accounts"][2]
    assert (third["name"], third["kind"], third["aws_profile"], third["aws_region"], third["active"]) == ("Amazon Bedrock (bedrock-sso)", "bedrock", "bedrock-sso", "us-west-2", True)
    assert call(app, "/api/claude-add", {"kind": "sso"})[0] == 400

    # --- removing the one in use hands over to the next; removing a sign-in takes it off the computer ---
    assert call(app, "/api/claude-remove", {"id": third["id"]})[0] == 200
    assert [(a["name"], a["active"]) for a in ai(app)["accounts"]] == [("Personal", True), ("Work key", False)]
    assert call(app, "/api/claude-remove", {"id": first["id"]})[0] == 200
    assert [(a["name"], a["active"]) for a in ai(app)["accounts"]] == [("Work key", True)] and signin.profiles() == []
    assert call(app, "/api/claude-remove", {"id": second})[0] == 200
    assert ai(app)["accounts"] == [] and settings.get_secret(app.data, f"anthropic_api_key_{second}") == ""
    assert ai(app)["off"] == "no Claude API key is saved (add one in the app under Accounts)"


def test_a_sign_in_nobody_approves_can_be_cancelled_and_started_again(ant, app, claude_api, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    first = call(app, "/api/claude-signin", {"name": ""})[1]["url"]
    assert ai(app)["signin"]["waiting"]
    assert call(app, "/api/claude-signin-cancel", {})[0] == 200
    assert ai(app)["signin"] == {"waiting": False, "url": "", "note": ""} and ai(app)["accounts"] == []
    second = call(app, "/api/claude-signin", {"name": ""})[1]["url"]  # pressing the button again starts a fresh one
    assert second != first
    approve_in_the_browser(second)
    state = wait_for(app, lambda s: s["accounts"])
    assert state["accounts"][0]["name"] == "Leonida Clips LLC"  # no name given: the organisation's


def test_without_the_sign_in_tool_the_app_says_what_to_do(app, monkeypatch, tmp_path):  # noqa: F811
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert not ai(app)["can_sign_in"]
    status, body, _ = call(app, "/api/claude-signin", {"name": "Personal"})
    assert status == 400 and "is not installed on this computer" in body["error"] and "use an API key" in body["error"]
    assert not ai(app)["signin"]["waiting"]


def test_a_key_saved_before_there_were_accounts_shows_as_one_and_stays_in_use(app, claude_api, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert call(app, "/api/ai-key", {"api_key": GOOD_KEY})[0] == 200  # how the app saved a key before
    assert [(a["id"], a["name"], a["kind"], a["active"]) for a in ai(app)["accounts"]] == [("", "API key", "key", True)]
    assert call(app, "/api/claude-add", {"kind": "key", "name": "Second", "api_key": GOOD_KEY})[0] == 200
    listed = ai(app)["accounts"]
    assert [(a["id"], a["name"], a["active"]) for a in listed] == [("first", "API key", False), (listed[1]["id"], "Second", True)]
    assert call(app, "/api/claude-use", {"id": "first"})[1]["problem"] == "" and ai(app)["off"] == ""
