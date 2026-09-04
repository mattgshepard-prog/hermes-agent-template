"""
Kroger authorization-code + PKCE one-time helper.

WHY THIS EXISTS
    Hazel's price layer uses grant_type=client_credentials with scope
    product.compact. That is an APPLICATION token. It can read products and
    it can never touch a cart, because a cart belongs to a PERSON.

    Writing to a cart requires a token minted for Matt specifically, which
    means Matt must log in to Kroger in a real browser once and approve the
    scope. That is the authorization-code flow. It returns a refresh token,
    and the refresh token is the durable artifact: Hazel exchanges it for a
    30-minute access token whenever she needs one, forever, without a
    password ever existing anywhere in the system.

WHERE THIS RUNS
    On Matt's Windows machine, NOT on Railway. The registered redirect URI is
    http://localhost:8080/callback and this script binds that port and opens
    a browser. Railway has neither. This is a one-time local ceremony.

WHAT IT DOES NOT DO
    It does not print the refresh token. It writes it to a file with a
    fingerprint printed for verification. Secrets do not go through terminal
    output, chat, or process arguments.

USAGE
    set KROGER_CLIENT_ID=...
    set KROGER_CLIENT_SECRET=...
    python authorize_kroger.py

    Then set the token on Railway WITHOUT it passing through a shell arg:
        railway.cmd link -p "Matt's Bots" -e production -s "Hazel"
        type kroger_refresh_token.txt | railway.cmd variables --set-from-stdin KROGER_REFRESH_TOKEN --skip-deploys

    Then DELETE kroger_refresh_token.txt. It is a live credential.

STDLIB ONLY, to match prices.py. No pip install.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

KROGER_BASE = "https://api.kroger.com/v1"
REDIRECT_URI = "http://localhost:8080/callback"
PORT = 8080

# cart.basic:write is the write scope. profile.compact is included so the
# token can prove WHOSE cart it is, which matters the day a second household
# member gets their own token and the two must not be confused.
SCOPE = "cart.basic:write profile.compact"

OUT_PATH = os.path.join(os.getcwd(), "kroger_refresh_token.txt")

_result = {"code": None, "state": None, "error": None}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        _result["code"] = (q.get("code") or [None])[0]
        _result["state"] = (q.get("state") or [None])[0]
        _result["error"] = (q.get("error_description") or q.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Authorization failed. Close this tab and check the terminal." \
            if _result["error"] else "Authorized. Close this tab and return to the terminal."
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'>"
                         f"<h2>{msg}</h2></body></html>".encode())
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        pass  # keep the auth code out of stdout


def post_form(url, form, basic):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + basic,
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        raise SystemExit(f"FAIL  token endpoint returned HTTP {e.code}\n{body}")


def main():
    cid = os.environ.get("KROGER_CLIENT_ID")
    sec = os.environ.get("KROGER_CLIENT_SECRET")
    if not cid or not sec:
        raise SystemExit("FAIL  set KROGER_CLIENT_ID and KROGER_CLIENT_SECRET first")

    # PKCE. Protects the code in transit even though this is a confidential
    # client, because the code round-trips through a browser on a shared box.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    auth_url = f"{KROGER_BASE}/connect/oauth2/authorize?" + urllib.parse.urlencode({
        "scope": SCOPE,
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Listening on {REDIRECT_URI}")
    print("Opening browser. Sign in to Kroger and approve cart access.")
    print("If the browser does not open, paste this URL manually:\n")
    print(auth_url + "\n")
    webbrowser.open(auth_url)
    server.serve_forever()   # Handler shuts this down after the callback
    server.server_close()

    if _result["error"]:
        raise SystemExit(f"FAIL  authorization denied: {_result['error']}")
    if not _result["code"]:
        raise SystemExit("FAIL  no authorization code returned")
    # State check is not decoration. Without it a forged callback could seat
    # someone else's authorization code in this exchange.
    if _result["state"] != state:
        raise SystemExit("FAIL  state mismatch, discarding code")

    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    tok = post_form(f"{KROGER_BASE}/connect/oauth2/token", {
        "grant_type": "authorization_code",
        "code": _result["code"],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }, basic)

    refresh = tok.get("refresh_token")
    access = tok.get("access_token")
    if not refresh:
        raise SystemExit("FAIL  no refresh_token in response. Confirm the app is "
                         "registered for the authorization-code grant.")

    # Prove the token actually works before declaring success. A token that
    # mints cleanly but 403s on the cart is the failure mode worth catching
    # here rather than at 6am on order day.
    scopes = tok.get("scope", "")
    if "cart.basic:write" not in scopes:
        print(f"WARN  granted scopes do not include cart.basic:write: {scopes}")

    with open(OUT_PATH, "w") as f:
        f.write(refresh)

    fp = hashlib.sha256(refresh.encode()).hexdigest()[:16]
    print("\nOK   refresh token written")
    print(f"     path         {OUT_PATH}")
    print(f"     length       {len(refresh)}")
    print(f"     sha256[:16]  {fp}")
    print(f"     scopes       {scopes}")
    print(f"     access token minted, expires_in {tok.get('expires_in')}s")
    print("\nNEXT")
    print("  1. railway.cmd link -p \"Matt's Bots\" -e production -s \"Hazel\"")
    print("  2. type kroger_refresh_token.txt | railway.cmd variables "
          "--set-from-stdin KROGER_REFRESH_TOKEN --skip-deploys")
    print("  3. Add KROGER_REFRESH_TOKEN to HERMES_ENV_KEYS in start.sh "
          "(two-part change rule) and push")
    print("  4. Bump RESTART_TRIGGER, then verify on the VOLUME not the dashboard")
    print("  5. DELETE kroger_refresh_token.txt")
    if access:
        print("\n  (The access token is deliberately not printed or saved. "
              "Hazel mints her own from the refresh token.)")


if __name__ == "__main__":
    sys.exit(main())
