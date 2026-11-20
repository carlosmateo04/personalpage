"""A stand-in for Anthropic's `ant` sign-in tool, for the tests.

It does what the documentation says the real one does for the commands the app uses:
`auth login --profile NAME --no-browser --callback-port PORT` prints the page to approve on,
waits for the browser to come back to that port, then writes the profile's two files and
reports the organisation, email and workspace; `--profile NAME auth logout` removes them.
What it is told to do comes from the FAKE_ANT environment variable: "ok" (default),
"timeout" (nobody approves) or "apache" (it is the Java build tool of the same name).
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

mode = os.environ.get("FAKE_ANT", "ok")
args = sys.argv[1:]
root = Path(os.environ["ANTHROPIC_CONFIG_DIR"])


def value(flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args else ""


if "--version" in args:
    print("Apache Ant(TM) version 1.10.14" if mode == "apache" else "ant version 2.9.0")
elif args[:2] == ["auth", "login"]:
    profile, port = value("--profile"), int(value("--callback-port"))
    print("To sign in, open this page in your browser:", flush=True)
    print(f"https://platform.claude.com/oauth/authorize?client_id=ant&redirect_uri=http%3A%2F%2Flocalhost%3A{port}%2Fcallback&state=xyz", flush=True)
    if mode == "timeout":
        time.sleep(float(os.environ.get("FAKE_ANT_WAIT", "1")))
        print("Error: timed out waiting for authorization", flush=True)
        sys.exit(1)

    class Back(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"You are signed in. You can close this tab.")
            self.server.approved = True

    server = HTTPServer(("127.0.0.1", port), Back)
    server.approved = False
    server.timeout = 20
    while not server.approved:
        server.handle_request()
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "credentials").mkdir(parents=True, exist_ok=True)
    (root / "configs" / f"{profile}.json").write_text(json.dumps({
        "version": "1.0", "authentication": {"type": "user_oauth"}, "organization_id": "org_" + profile, "workspace_id": "wrkspc_default"}))
    secret = root / "credentials" / f"{profile}.json"
    secret.write_text(json.dumps({"type": "oauth_token", "access_token": os.environ.get("FAKE_ANT_TOKEN", "sk-ant-test-good"),
                                  "expires_at": int(time.time()) + 3600}))
    secret.chmod(0o600)
    print("Signed in.")
    print("Organization: Leonida Clips LLC")
    print(f"Email: {os.environ.get('FAKE_ANT_EMAIL', 'owner@example.com')}")
    print("Workspace: Default")
    print(f"Config: {root / 'configs' / (profile + '.json')}")
elif "logout" in args:
    profile = value("--profile")
    for folder in ("configs", "credentials"):
        (root / folder / f"{profile}.json").unlink(missing_ok=True)
    print("Signed out.")
else:
    print("unknown command", file=sys.stderr)
    sys.exit(2)
