"""Claude as a built-in part: reaching it, asking it, and counting what it cost.

Run against a local stand-in for the API; what a real model answers is not tested here.
"""

import json

import pytest

from clipper import claude, settings
from standins import AWS_KEY, GOOD_KEY

SHAPE = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
HELLO = [{"type": "text", "text": "hello"}]


def test_what_a_request_carries(cfg, claude_api, tmp_path):
    claude_api.replies.append({"ok": True})
    picture = tmp_path / "frame.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0 pretend jpeg")
    answer = claude.ask(cfg, "You sort things.", [claude.image(picture), *HELLO], SHAPE, purpose="check")
    assert answer == {"ok": True}
    sent = claude_api.seen[0]
    body = sent["body"]
    assert sent["path"].startswith("/v1/messages") and "server-side-fallback-2026-07-01" in sent["beta"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default" and body["max_tokens"] == 16000
    assert "thinking" not in body and "temperature" not in body  # the model decides how much to think; depth is set by effort
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SHAPE}}
    assert body["system"] == "You sort things."
    image, text = body["messages"][0]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/4CBwcmV0ZW5kIGpwZWc="}}
    assert text == {"type": "text", "text": "hello"}

    cfg.ai.model = "claude-haiku-5-5"  # a model the API cannot re-run elsewhere is asked plainly
    claude_api.replies.append({"ok": False})
    claude.ask(cfg, "s", HELLO, SHAPE, effort="high")
    later = claude_api.seen[1]
    assert "fallbacks" not in later["body"] and "server-side-fallback" not in later["beta"] and later["body"]["output_config"]["effort"] == "high"


@pytest.mark.parametrize("reply, said, fatal", [
    ({"_stop": "refusal"}, "declined", False),
    ({"ok": True, "_stop": "max_tokens"}, "cut off", False),
    ("this is not json", "expected form", False),
    ("[1, 2]", "expected form", False),
    (400, "did not accept the request", False),
    (500, r"error \(500\)", False),
    (403, "may not use the model", True),
    (404, "was not found", True),
    (429, "rate-limiting", True),
])
def test_answers_that_cannot_be_used(cfg, claude_api, reply, said, fatal):
    claude_api.replies.append(reply)
    with pytest.raises(claude.ClaudeError, match=said) as caught:
        claude.ask(cfg, "s", HELLO, SHAPE)
    assert caught.value.fatal is fatal


def test_a_wrong_key_and_no_connection_end_the_run_of_requests(cfg, claude_api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")
    with pytest.raises(claude.ClaudeError, match="key was not accepted") as caught:
        claude.ask(cfg, "s", HELLO, SHAPE)
    assert caught.value.fatal
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setattr(claude, "TIMEOUT", 2.0)
    with pytest.raises(claude.ClaudeError, match="could not reach Claude") as caught:
        claude.ask(cfg, "s", HELLO, SHAPE)
    assert caught.value.fatal


def test_what_it_cost_is_counted(cfg, claude_api):
    assert claude.usage(cfg) == {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "by": {}}
    claude_api.replies += [{"ok": True}, {"ok": True}, {"_stop": "refusal"}]
    claude.ask(cfg, "s", HELLO, SHAPE, purpose="scout")
    claude.ask(cfg, "s", HELLO, SHAPE, purpose="edit")
    with pytest.raises(claude.ClaudeError):
        claude.ask(cfg, "s", HELLO, SHAPE, purpose="edit")  # a declined request is still a request
    used = claude.usage(cfg)
    # the stand-in reports 100 tokens in and 50 out per request; Opus 5.5 is $4 and $20 per million
    assert used == {"requests": 3, "input_tokens": 300, "output_tokens": 150, "cost": 0.0042, "by": {"scout": 1, "edit": 2}}
    (cfg.path("state") / claude.USAGE).write_text("not json", encoding="utf-8")
    assert claude.usage(cfg)["requests"] == 0  # a damaged count never gets in the way
    claude_api.replies.append({"ok": True})
    assert claude.ask(cfg, "s", HELLO, SHAPE) == {"ok": True}


def test_why_claude_is_off_and_checking_a_key(cfg, claude_api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "no Claude API key" in claude.unavailable(cfg) and not claude.available(cfg)
    settings.set_secret(cfg.data_root, "anthropic_api_key", GOOD_KEY)
    assert claude.unavailable(cfg) == "" and claude.api_key(cfg) == GOOD_KEY
    assert claude.test(cfg) == "" and claude.test(cfg, GOOD_KEY) == ""  # a free look-up of the model, not a request
    assert "did not accept" in claude.test(cfg, "sk-ant-wrong")
    assert all(seen["path"] != "/v1/messages" for seen in claude_api.seen)
    cfg.ai.enabled = False
    assert "switched off" in claude.unavailable(cfg)
    cfg.ai.enabled = True
    monkeypatch.setattr(claude.importlib.util, "find_spec", lambda name: None)
    assert "anthropic package is not installed" in claude.unavailable(cfg)


@pytest.fixture
def bedrock(cfg, claude_api, monkeypatch, tmp_path):
    """Claude through Amazon Bedrock: requests signed with AWS credentials, sent to the stand-in."""
    cfg.ai.provider = "bedrock"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AWS_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("CLIPPER_BEDROCK_URL", claude_api.url)
    return cfg


def test_through_bedrock_the_request_is_signed_and_the_model_renamed(bedrock, claude_api):
    assert claude.unavailable(bedrock) == "" and claude.model_id(bedrock) == "anthropic.claude-opus-5-5"
    claude_api.replies.append({"ok": True})
    assert claude.ask(bedrock, "s", HELLO, SHAPE) == {"ok": True}
    sent = claude_api.seen[0]
    assert sent["path"] == "/v1/messages" and sent["auth"].startswith(f"AWS4-HMAC-SHA256 Credential={AWS_KEY}/") and "/us-east-1/" in sent["auth"]
    assert sent["body"]["model"] == "anthropic.claude-opus-5-5" and "fallbacks" not in sent["body"]
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"

    # Bedrock has no model look-up: the free check counts the tokens of one word instead.
    assert claude.test(bedrock) == ""
    assert claude_api.seen[-1]["path"].endswith("/count_tokens") and claude_api.seen[-1]["body"]["model"] == "anthropic.claude-opus-5-5"


def test_through_bedrock_a_declined_request_is_run_again_on_the_fallback_model(bedrock, claude_api):
    claude_api.replies += [{"_stop": "refusal"}, {"ok": True}]
    assert claude.ask(bedrock, "s", HELLO, SHAPE) == {"ok": True}
    assert [seen["body"]["model"] for seen in claude_api.seen] == ["anthropic.claude-opus-5-5", "anthropic.claude-opus-4-8"]
    bedrock.ai.fallback_model = ""  # without one, the decline is the answer
    claude_api.replies.append({"_stop": "refusal"})
    with pytest.raises(claude.ClaudeError, match="declined"):
        claude.ask(bedrock, "s", HELLO, SHAPE)


def test_an_expired_aws_sign_in_says_what_to_run(bedrock, monkeypatch):
    import anthropic

    bedrock.ai.aws_profile = "bedrock-sso"

    class UnauthorizedSSOTokenError(Exception):
        pass

    def expired(**kwargs):
        raise UnauthorizedSSOTokenError("The SSO session associated with this profile has expired or is otherwise invalid.")

    monkeypatch.setattr(anthropic, "AnthropicBedrockMantle", expired)
    hint = 'the AWS sign-in for the profile "bedrock-sso" is missing or has expired'
    with pytest.raises(claude.ClaudeError, match=hint) as caught:
        claude.ask(bedrock, "s", HELLO, SHAPE)
    assert caught.value.fatal and "aws sso login --profile bedrock-sso" in str(caught.value)
    assert claude.test(bedrock).startswith("The AWS sign-in") and "aws sso login --profile bedrock-sso" in claude.test(bedrock)


def test_bedrock_needs_a_profile_and_the_profiles_on_this_computer_are_listed(cfg, tmp_path, monkeypatch):
    cfg.ai.provider = "bedrock"
    assert "no AWS profile is chosen" in claude.unavailable(cfg)
    cfg.ai.aws_profile = "bedrock-sso"
    assert claude.unavailable(cfg) == ""
    monkeypatch.setattr(claude.importlib.util, "find_spec", lambda name: None if name == "botocore" else object())
    assert "AWS packages are not installed" in claude.unavailable(cfg)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("AWS_CONFIG_FILE", raising=False)
    assert claude.aws_profiles() == []
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "config").write_text(
        "[default]\nregion = eu-west-1\n\n[profile bedrock-sso]\nsso_session = work\nregion = us-east-1\n\n[sso-session work]\nsso_region = us-east-1\n",
        encoding="utf-8")
    (tmp_path / ".aws" / "credentials").write_text("[keys]\naws_access_key_id = x\n", encoding="utf-8")
    assert claude.aws_profiles() == [{"name": "bedrock-sso", "region": "us-east-1"}, {"name": "default", "region": "eu-west-1"}, {"name": "keys", "region": ""}]


def test_the_usage_file_is_plain_json(cfg, claude_api):
    claude_api.replies.append({"ok": True})
    claude.ask(cfg, "s", HELLO, SHAPE, purpose="scout")
    data = json.loads((cfg.path("state") / claude.USAGE).read_text(encoding="utf-8"))
    assert list(data.values())[0]["by"] == {"scout": 1}
