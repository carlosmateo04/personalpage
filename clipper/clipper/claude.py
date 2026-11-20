"""Claude, built into the clipper.

Every AI step goes through `ask`: picking the moments in a video (scout.py),
checking and editing a clip (review.py), and a re-edit asked for in the app.
One request, one answer in a fixed shape.

Three ways to reach Claude. Any number of accounts can be saved in the app under
Accounts, and the one in use is switched at will:

  login       a sign-in in the browser with Anthropic's own tool (see signin.py); no key to handle
  anthropic   an API key from console.anthropic.com (ANTHROPIC_API_KEY, or one saved in the app)
  bedrock     Claude through Amazon Bedrock, signed in with an AWS profile on this computer

Without either the clipper carries on with its loudness-based finder and rule-based titles.
What is sent: frames of the video, what is said in it, and its title. Nothing else leaves
the computer.
"""

from __future__ import annotations

import base64
import configparser
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import settings, signin
from .config import Config

# Models offered in the app, with the first-party list price per million tokens (input, output) used
# for the cost estimate. Bedrock is billed by AWS at its own rates.
MODELS: dict[str, tuple[str, float, float]] = {
    "claude-opus-5-5": ("Claude Opus 5.5", 4.0, 20.0),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5", 2.0, 10.0),
    "claude-haiku-5-5": ("Claude Haiku 5.5", 0.10, 0.50),
    "claude-fable-5-2": ("Claude Fable 5.2", 8.0, 40.0),
}
# Models whose requests can be re-run on another model by the API itself when one declines.
SERVER_FALLBACK = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
USAGE = "ai_usage.json"
TIMEOUT = 240.0  # seconds one request may take


class ClaudeError(RuntimeError):
    """A request to Claude that did not give an answer. `fatal` means there is no point trying the next one."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


# --- reaching Claude ------------------------------------------------------------

def api_key(cfg: Config) -> str:
    """The key in use: the environment's, else the chosen account's, else the one saved before there were accounts."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    own = settings.get_secret(cfg.data_root, f"anthropic_api_key_{cfg.ai.account}") if cfg.ai.account else ""
    return own or settings.get_secret(cfg.data_root, "anthropic_api_key")


def model_id(cfg: Config, model: str | None = None) -> str:
    """The model name as the chosen provider spells it: Bedrock puts "anthropic." in front."""
    model = model or cfg.ai.model
    if cfg.ai.provider == "bedrock" and "." not in model.split("-")[0]:
        return f"anthropic.{model}"
    return model


def aws_profiles() -> list[dict[str, str]]:
    """The AWS profiles set up on this computer, each with its region when it names one."""
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~").expanduser()
    found: dict[str, str] = {}
    for name in (os.environ.get("AWS_CONFIG_FILE") or str(home / ".aws" / "config"), str(home / ".aws" / "credentials")):
        parser = configparser.RawConfigParser()
        try:
            parser.read(name, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        for section in parser.sections():
            if section.startswith("sso-session"):
                continue
            profile = section.removeprefix("profile ").strip()
            found[profile] = found.get(profile) or parser.get(section, "region", fallback="")
    return [{"name": name, "region": region} for name, region in sorted(found.items())]


def unavailable(cfg: Config) -> str:
    """Why Claude cannot be used right now, or empty when it can."""
    if not cfg.ai.enabled:
        return "it is switched off in the settings"
    if importlib.util.find_spec("anthropic") is None:
        return "the anthropic package is not installed (run the installer again)"
    if cfg.ai.provider == "bedrock":
        if importlib.util.find_spec("botocore") is None:
            return "the AWS packages are not installed (run the installer again)"
        if not cfg.ai.aws_profile and not (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")):
            return "no AWS profile is chosen (pick one in the app under Accounts)"
        return ""
    if cfg.ai.provider == "login":
        if not signin.exists(cfg.ai.login_profile):
            return "the Claude sign-in is no longer on this computer (sign in again in the app under Accounts)"
        return ""
    if not api_key(cfg):
        return "no Claude API key is saved (add one in the app under Accounts)"
    return ""


def available(cfg: Config) -> bool:
    return not unavailable(cfg)


def _aws_hint(cfg: Config, exc: Exception) -> str:
    """AWS sign-in problems in plain words, with what to run."""
    text = f"{type(exc).__name__} {exc}".lower()
    profile = cfg.ai.aws_profile or "default"
    if any(word in text for word in ("sso", "token", "expired", "credential", "profile", "unauthorized")):
        return (f'the AWS sign-in for the profile "{profile}" is missing or has expired. '
                f"Open a terminal, run:  aws sso login --profile {profile}  and try again")
    return f"AWS did not accept the request ({type(exc).__name__}: {str(exc)[:160]})"


def client(cfg: Config, key: str | None = None):
    """A client for the chosen provider."""
    import anthropic

    if cfg.ai.provider == "bedrock":
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

        # Bedrock has no server-side fallback, so a declined request is re-run on another model from here.
        fallback = BetaRefusalFallbackMiddleware([{"model": model_id(cfg, cfg.ai.fallback_model)}]) if cfg.ai.fallback_model else None
        try:
            return AnthropicBedrockMantle(aws_region=cfg.ai.aws_region, aws_profile=cfg.ai.aws_profile or None, timeout=TIMEOUT,
                                          middleware=[fallback] if fallback else None,
                                          base_url=os.environ.get("CLIPPER_BEDROCK_URL") or None)
        except Exception as exc:  # noqa: BLE001 - AWS raises many kinds of sign-in error
            raise ClaudeError(_aws_hint(cfg, exc), fatal=True) from exc
    if cfg.ai.provider == "login" and not key:
        # The SDK reads the named sign-in itself and renews its token when it runs out.
        return anthropic.Anthropic(profile=cfg.ai.login_profile, timeout=TIMEOUT)
    return anthropic.Anthropic(api_key=key or api_key(cfg), timeout=TIMEOUT)


def test(cfg: Config, key: str | None = None) -> str:
    """Empty if Claude can be reached with these settings, otherwise why not. Costs nothing."""
    import anthropic

    try:
        api = client(cfg, key)
        if cfg.ai.provider == "bedrock":  # Bedrock has no model lookup; counting the tokens of one word is free
            api.messages.count_tokens(model=model_id(cfg), messages=[{"role": "user", "content": "hello"}])
        else:
            api.models.retrieve(cfg.ai.model)
    except ClaudeError as exc:
        return str(exc)[0].upper() + str(exc)[1:] + "."
    except anthropic.AuthenticationError:
        return {"bedrock": "AWS did not accept the sign-in.", "login": "The Claude sign-in has run out. Sign in again."}.get(
            cfg.ai.provider, "Anthropic did not accept this key.")
    except anthropic.PermissionDeniedError:
        return f"This account is not allowed to use the model {model_id(cfg)}."
    except anthropic.NotFoundError:
        return f"The model {model_id(cfg)} is not available to this account."
    except anthropic.APIStatusError as exc:
        return f"Claude answered with an error ({exc.status_code})."
    except anthropic.APIConnectionError:
        return "Could not reach Claude. Check the internet connection."
    except Exception as exc:  # noqa: BLE001 - AWS sign-in errors surface when the first request is signed
        if cfg.ai.provider == "bedrock":
            hint = _aws_hint(cfg, exc)
            return hint[0].upper() + hint[1:] + "."
        raise
    return ""


# --- the saved accounts -------------------------------------------------------------

KINDS = {"login": "Signed in with Claude", "key": "API key", "bedrock": "Amazon Bedrock"}


def accounts(cfg: Config) -> list[dict[str, Any]]:
    """The Claude accounts saved on this computer, each as the app shows it; the one in use has `active`.

    A key or an AWS profile set up before there were accounts shows as an account too.
    """
    saved = [dict(a) for a in cfg.ai.accounts if isinstance(a, dict) and a.get("id")]
    if not saved:
        if cfg.ai.provider == "bedrock" and cfg.ai.aws_profile:
            saved.append({"id": "", "name": f"Amazon Bedrock ({cfg.ai.aws_profile})", "kind": "bedrock",
                          "aws_profile": cfg.ai.aws_profile, "aws_region": cfg.ai.aws_region})
        elif settings.get_secret(cfg.data_root, "anthropic_api_key"):
            saved.append({"id": "", "name": "API key", "kind": "key"})
    for account in saved:
        account["active"] = account["id"] == cfg.ai.account
        account["label"] = KINDS.get(account.get("kind", ""), "")
        if account.get("kind") == "login" and not signin.exists(account.get("profile", "")):
            account["problem"] = "the sign-in is no longer on this computer"
    return saved


def _store(cfg: Config, saved: list[dict[str, Any]], use: dict[str, Any] | None) -> None:
    """Write the account list and, when `use` is given, make that account the one in use."""
    keep = ("id", "name", "kind", "profile", "aws_profile", "aws_region", "detail", "added")
    changes: dict[str, Any] = {"accounts": [{k: a[k] for k in keep if k in a} for a in saved if a.get("id")]}
    if use is not None:
        changes.update(account=use["id"], enabled=True, provider={"key": "anthropic"}.get(use["kind"], use["kind"]))
        if use["kind"] == "login":
            changes["login_profile"] = use["profile"]
        if use["kind"] == "bedrock":
            changes.update(aws_profile=use["aws_profile"], aws_region=use.get("aws_region") or "us-east-1")
    settings.update(cfg.data_root, {"ai": changes})


def add_account(cfg: Config, name: str, kind: str, key: str = "", **fields: str) -> dict[str, Any]:
    """Save a Claude account and start using it. A key goes to the credential store, never into the settings."""
    saved = accounts(cfg)
    if any(not a["id"] for a in saved):  # the one from before there were accounts gets a place in the list
        old = next(a for a in saved if not a["id"])
        old["id"] = "first"
        if old["kind"] == "key":
            settings.set_secret(cfg.data_root, "anthropic_api_key_first", settings.get_secret(cfg.data_root, "anthropic_api_key"))
            settings.set_secret(cfg.data_root, "anthropic_api_key", "")
    taken = {a["id"] for a in saved}
    ident = next(f"a{n}" for n in range(1, 1000) if f"a{n}" not in taken)
    label = " ".join(name.split())[:40] or {"login": "Claude account", "key": "API key", "bedrock": "Amazon Bedrock"}[kind]
    account = {"id": ident, "name": label, "kind": kind, "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"), **{k: v for k, v in fields.items() if v}}
    if kind == "key":
        settings.set_secret(cfg.data_root, f"anthropic_api_key_{ident}", key)
    if cfg.ai.account == "" and any(a["id"] == "first" for a in saved):
        settings.update(cfg.data_root, {"ai": {"account": "first"}})
    _store(cfg, [*saved, account], account)
    return account


def use_account(cfg: Config, ident: str) -> dict[str, Any]:
    saved = accounts(cfg)
    chosen = next((a for a in saved if a["id"] == ident), None)
    if chosen is None:
        raise KeyError(ident)
    _store(cfg, saved, chosen)
    return chosen


def remove_account(cfg: Config, ident: str) -> None:
    """Forget an account: its key is deleted, its sign-in is removed from this computer."""
    saved = accounts(cfg)
    gone = next((a for a in saved if a["id"] == ident), None)
    if gone is None:
        raise KeyError(ident)
    if gone["kind"] == "key":
        settings.set_secret(cfg.data_root, f"anthropic_api_key_{ident}" if ident else "anthropic_api_key", "")
    if gone["kind"] == "login":
        signin.logout(gone.get("profile", ""))
    left = [a for a in saved if a["id"] != ident]
    if gone.get("active") and left and left[0]["id"]:
        _store(cfg, left, left[0])  # the next one takes over
    else:
        _store(cfg, left, None)
        if gone.get("active"):
            settings.update(cfg.data_root, {"ai": {"account": "", "provider": "anthropic", "login_profile": "", "aws_profile": ""}})


# --- asking ---------------------------------------------------------------------

def image(path: Path) -> dict[str, Any]:
    """A JPEG as a content block."""
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")}}


def ask(cfg: Config, system: str, content: list[dict], schema: dict, effort: str | None = None, purpose: str = "") -> dict:
    """One request to Claude; returns its answer as a dict in the shape of `schema`.

    `content` is the user turn: text and image blocks. `purpose` names the step for the
    usage record ("scout", "edit", "check"...).
    """
    import anthropic
    from anthropic import BetaFallbackState

    request: dict[str, Any] = {
        "model": model_id(cfg),
        "max_tokens": 16000,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort or cfg.ai.effort, "format": {"type": "json_schema", "schema": schema}},
    }
    if cfg.ai.provider != "bedrock" and cfg.ai.model in SERVER_FALLBACK:
        # A request the model declines is re-run on another model inside the same call.
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"

    api = client(cfg)
    try:
        with BetaFallbackState():  # one request is one conversation; nothing to pin afterwards
            response = api.beta.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        said = {"bedrock": "the Claude sign-in was not accepted", "login": "the Claude sign-in has run out: sign in again in the app under Accounts"}
        raise ClaudeError(said.get(cfg.ai.provider, "the Claude API key was not accepted"), fatal=True) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ClaudeError(f"this account may not use the model {request['model']}", fatal=True) from exc
    except anthropic.NotFoundError as exc:
        raise ClaudeError(f"the model {request['model']} was not found", fatal=True) from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeError("Claude is rate-limiting this account; the remaining steps fall back to the rules", fatal=True) from exc
    except anthropic.BadRequestError as exc:
        raise ClaudeError(f"Claude did not accept the request: {str(getattr(exc, 'message', exc))[:200]}") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"Claude answered with an error ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError("could not reach Claude", fatal=True) from exc
    except ClaudeError:
        raise
    except Exception as exc:  # noqa: BLE001 - AWS sign-in errors surface when the request is signed
        if cfg.ai.provider == "bedrock":
            raise ClaudeError(_aws_hint(cfg, exc), fatal=True) from exc
        raise

    _record(cfg, response, purpose)
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("the answer was cut off before it finished")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeError("the answer did not come back in the expected form") from exc
    if not isinstance(answer, dict):
        raise ClaudeError("the answer did not come back in the expected form")
    return answer


# --- what it has cost -------------------------------------------------------------

def _record(cfg: Config, response, purpose: str) -> None:
    """Add one answered request to this month's count. Estimates only; the provider's bill is what counts."""
    try:
        used = response.usage
        tokens_in = int(used.input_tokens or 0) + int(getattr(used, "cache_creation_input_tokens", 0) or 0) + int(getattr(used, "cache_read_input_tokens", 0) or 0)
        tokens_out = int(used.output_tokens or 0)
        served = str(getattr(response, "model", "") or cfg.ai.model).removeprefix("anthropic.")
        price = MODELS.get(served) or MODELS.get(cfg.ai.model) or ("", 0.0, 0.0)
        path = cfg.path("state") / USAGE
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        month = data.setdefault(datetime.now(timezone.utc).strftime("%Y-%m"), {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "by": {}})
        month["requests"] += 1
        month["input_tokens"] += tokens_in
        month["output_tokens"] += tokens_out
        month["cost"] = round(month["cost"] + (tokens_in * price[1] + tokens_out * price[2]) / 1e6, 4)
        month["by"][purpose or "other"] = month["by"].get(purpose or "other", 0) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # the count is a convenience; it never gets in the way of the work


def usage(cfg: Config) -> dict[str, Any]:
    """This month's requests, tokens and estimated cost."""
    path = cfg.path("state") / USAGE
    empty = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "by": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return {**empty, **data.get(datetime.now(timezone.utc).strftime("%Y-%m"), {})}
