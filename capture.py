"""curl2spec PRO — auto-capture a login by driving a headless browser.

The free mode asks the operator to open DevTools and "Copy as cURL". Pro mode does that job for them: given a
URL + credentials, it drives a real Chromium (Playwright), finds the login form, submits the credentials,
captures the exact authentication request the app makes (URL, method, body, headers), locates the identity
endpoint, and emits the same login/register specs.

⚠ ACTIVE + AUTHORISED-ONLY. This submits real credentials to the target and therefore makes real requests to
it. Use it only against sites you own or are explicitly authorised to test. It never exploits anything — it
performs one honest login, the same you would do by hand — but it IS live traffic, unlike the free client-side
mode which only reshapes a request you already captured.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

_EMAIL_KEYS = ("email", "e-mail", "user", "username", "login", "userid", "user_name", "account")
_PW_KEYS = ("password", "pass", "pwd", "passwd", "secret")
_DROP_HEADERS = {"content-length", "host", "cookie", "connection", "accept-encoding", "origin", "referer",
                 "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest", "user-agent", "accept",
                 "accept-language", "priority", "te", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"}
# identity endpoints, matched as whole PATH SEGMENTS (not substrings) so "/media/…" never matches "/me".
# Strong terms are unambiguous; weak terms only count as the LAST segment of the path.
_IDENTITY_STRONG = {"whoami", "userinfo", "current_user", "me"}
_IDENTITY_WEAK = {"user", "account", "profile", "session"}
_LOGIN_PATHS = ("/login", "/signin", "/sign-in", "/api/login", "/api/auth/login", "/auth/login",
                "/rest/user/login", "/users/sign_in", "/session", "/account/login")


def _is_email_key(k: str) -> bool:
    kl = k.lower()
    return any(e in kl for e in _EMAIL_KEYS) and not _is_pw_key(k)


def _is_pw_key(k: str) -> bool:
    return any(pw in k.lower() for pw in _PW_KEYS)


def _clean_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for k, v in (headers or {}).items():
        kl = k.lower()
        if kl in _DROP_HEADERS or kl == "content-type" or kl.startswith(":"):
            continue
        out[k] = v
    return out


def _parse_body(body: str, content_type: str):
    b = (body or "").strip()
    ct = (content_type or "").lower()
    if "json" in ct or (b.startswith("{") and b.endswith("}")):
        try:
            data = json.loads(b)
            if isinstance(data, dict):
                return "json", {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
    return "form", {k: v for k, v in parse_qsl(b, keep_blank_values=True)}


def _login_spec_from_request(req: dict, *, name: str, check_url: str = "") -> dict:
    ct = next((v for k, v in req.get("headers", {}).items() if k.lower() == "content-type"), "")
    where, fields = _parse_body(req.get("post_data") or "", ct)
    spec = {"name": name, "login_url": req["url"].split("?", 1)[0], "method": req.get("method", "POST"),
            "where": where, "fields": fields}
    h = _clean_headers(req.get("headers", {}))
    if h:
        spec["headers"] = h
    if check_url:
        spec["check_url"] = check_url
    return spec


def _two_account_specs(base: dict, a: Dict[str, str], b: Dict[str, str]) -> List[dict]:
    def _fill(nm, creds):
        s = json.loads(json.dumps(base)); s["name"] = nm
        for k in list(s["fields"]):
            if _is_email_key(k):
                s["fields"][k] = creds.get("username") or s["fields"][k]
            elif _is_pw_key(k):
                s["fields"][k] = creds.get("password") or s["fields"][k]
        return s
    return [_fill("accountA", a), _fill("accountB", b)]


class CaptureError(RuntimeError):
    pass


def capture_login(url: str, username: str, password: str, *, login_url_hint: str = "",
                  account_b: Optional[Dict[str, str]] = None, headless: bool = True,
                  timeout_ms: int = 30000) -> dict:
    """Drive a headless Chromium to log in at ``url`` with ``username``/``password`` and capture the auth
    request → login spec. Returns {login_spec, check_url, session, captured, notes}. Raises CaptureError with
    a clear reason when no login form / no auth request could be found."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise CaptureError("Playwright is not installed. Run: pip install playwright && playwright install "
                           f"chromium  ({type(e).__name__}: {e})") from e

    seen: List[dict] = []
    notes: List[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def _on_request(req):
            try:
                seen.append({"url": req.url, "method": req.method, "headers": dict(req.headers),
                             "post_data": req.post_data or ""})
            except Exception:  # noqa: BLE001
                pass
        page.on("request", _on_request)

        target = login_url_hint or url
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            browser.close()
            raise CaptureError(f"could not load {target}: {type(e).__name__}: {e}") from e

        pw_field = _find_password_field(page)
        if pw_field is None:
            # try common login routes off the base origin before giving up
            origin = "{u.scheme}://{u.netloc}".format(u=urlsplit(url))
            for p in _LOGIN_PATHS:
                try:
                    page.goto(origin + p, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:  # noqa: BLE001
                    continue
                pw_field = _find_password_field(page)
                if pw_field is not None:
                    notes.append(f"login form found at {p}")
                    break
        if pw_field is None:
            browser.close()
            raise CaptureError("no password field found — the login may be behind an SSO/redirect or a "
                               "captcha, or on a page this tool didn't reach. Pass the exact login page URL.")

        user_field = _find_username_field(page, pw_field)
        if user_field is not None:
            user_field.fill(username, timeout=timeout_ms)
        else:
            notes.append("no username field detected — filled password only (token/PIN login?)")
        pw_field.fill(password, timeout=timeout_ms)

        before = len(seen)
        _submit(page, pw_field)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 — SPAs may never go fully idle; the request is already captured
            pass

        # the AUTH request = a POST after submit whose body carries the credentials we typed
        auth = _pick_auth_request(seen[before:] or seen, username, password)
        if auth is None:
            browser.close()
            raise CaptureError("submitted the form but couldn't identify the login request in the captured "
                               "traffic — the app may send credentials in an unusual way. Try the free "
                               "manual (Copy-as-cURL) mode for this target.")

        check_url = _find_identity_endpoint(seen, ctx)
        session = _extract_session(ctx, page)
        browser.close()

    base = _login_spec_from_request(auth, name="accountA", check_url=check_url)
    # slot the operator's own creds onto the detected field names (the captured values were the real login)
    login_spec: List[dict]
    if account_b and account_b.get("username"):
        login_spec = _two_account_specs(base, {"username": username, "password": password}, account_b)
    else:
        login_spec = [base]
    return {"login_spec": login_spec, "check_url": check_url, "session": session,
            "captured": {"url": auth["url"], "method": auth["method"]}, "notes": notes}


# ── Playwright DOM heuristics (best-effort, defensive) ──────────────────────────────────────────────
def _find_password_field(page):
    try:
        loc = page.locator("input[type=password]:visible")
        if loc.count() > 0:
            return loc.first
        loc = page.locator("input[type=password]")
        return loc.first if loc.count() > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _find_username_field(page, pw_field):
    # prefer an explicit email/text field with a credential-ish name/type; fall back to the first text input
    for sel in ("input[type=email]:visible", "input[name*=email i]:visible", "input[name*=user i]:visible",
                "input[name*=login i]:visible", "input[id*=email i]:visible", "input[id*=user i]:visible",
                "input[type=text]:visible", "input:not([type=password]):not([type=hidden]):not([type=submit]):visible"):
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


def _submit(page, pw_field):
    for sel in ("button[type=submit]:visible", "input[type=submit]:visible",
                "button:has-text('Log in'):visible", "button:has-text('Login'):visible",
                "button:has-text('Sign in'):visible", "button:has-text('Sign In'):visible",
                "button:has-text('Continue'):visible", "*[id*=login i][role=button]:visible"):
        try:
            b = page.locator(sel)
            if b.count() > 0:
                b.first.click(timeout=8000)
                return
        except Exception:  # noqa: BLE001
            continue
    try:
        pw_field.press("Enter")     # fallback: submit the form from the password field
    except Exception:  # noqa: BLE001
        pass


def _pick_auth_request(requests, username, password):
    cands = [r for r in requests if (r.get("post_data") or "") and
             (password in r["post_data"] or (username and username in r["post_data"]))]
    # prefer a POST that carries the password specifically
    pw_posts = [r for r in cands if r.get("method", "").upper() == "POST" and password in r.get("post_data", "")]
    if pw_posts:
        return pw_posts[-1]
    return cands[-1] if cands else None


def _find_identity_endpoint(requests, ctx):
    # prefer a strong identity term anywhere in the path; else a weak term as the LAST segment. Segment-exact
    # so "/media/…" (contains "me") and "/rest/user/login" (login, not identity) are not misread.
    gets = [r for r in requests if r.get("method", "GET").upper() == "GET"]
    for strong in (True, False):
        for r in gets:
            segs = [s.lower() for s in urlsplit(r.get("url", "")).path.split("/") if s]
            if not segs:
                continue
            if strong and any(s in _IDENTITY_STRONG for s in segs):
                return r["url"].split("?", 1)[0]
            if not strong and segs[-1] in _IDENTITY_WEAK:
                return r["url"].split("?", 1)[0]
    return ""


def _extract_session(ctx, page):
    """A best-effort hint of the authenticated session (cookies + any bearer-ish token in localStorage), so
    the operator can see what the login yielded. NOT put into the login spec (which re-logs in), just reported."""
    out: Dict[str, str] = {}
    try:
        cookies = ctx.cookies()
        if cookies:
            out["Cookie"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies[:8])
    except Exception:  # noqa: BLE001
        pass
    try:
        tok = page.evaluate(
            "() => { for (const k of Object.keys(localStorage)) { const v = localStorage.getItem(k);"
            " if (v && (/token|jwt|auth/i.test(k) || /^ey[A-Za-z0-9_-]+\\./.test(v))) return v; } return ''; }")
        if tok:
            out["Authorization"] = "Bearer " + tok if not tok.lower().startswith("bearer") else tok
    except Exception:  # noqa: BLE001
        pass
    return out
