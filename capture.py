"""curl2spec PRO: auto-capture a login by driving a headless browser.

The free mode asks the operator to open DevTools and "Copy as cURL". Pro mode does that job for them: given a
URL + credentials, it drives a real Chromium (Playwright), finds the login form, submits the credentials,
captures the exact authentication request the app makes (URL, method, body, headers), locates the identity
endpoint, and emits the same login/register specs.

⚠ ACTIVE + AUTHORISED-ONLY. This submits real credentials to the target and therefore makes real requests to
it. Use it only against sites you own or are explicitly authorised to test. It never exploits anything. It
performs one honest login, the same you would do by hand. It IS live traffic, unlike the free client-side
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
_LOGIN_PATHS = ("/#/login", "/#/signin", "/#/sign-in", "/#/account/login", "/login", "/signin", "/sign-in",
                "/api/login", "/api/auth/login", "/auth/login", "/rest/user/login", "/users/sign_in",
                "/session", "/account/login")
# register/signup pages to try when the caller gives no hint. SPA fragment routes (#/register) included.
_REGISTER_PATHS = ("/register", "/signup", "/sign-up", "/#/register", "/#/signup", "/#/register/",
                   "/api/register", "/auth/register", "/users/sign_up", "/account/register", "/create-account")
# tokens that mark a request URL as the registration call (prefer these over any other password-bearing POST).
_REGISTER_URL_HINTS = ("register", "signup", "sign-up", "sign_up", "users", "account", "create")


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

        # SPAs render the form after load, and a consent/welcome dialog can sit over it, so wait and dismiss.
        pw_field = _await_password_field(page, timeout_ms)
        if pw_field is None:
            # try common login routes off the base origin, SPA fragment routes (#/login) first
            origin = "{u.scheme}://{u.netloc}".format(u=urlsplit(url))
            for p in _LOGIN_PATHS:
                try:
                    page.goto(origin + p, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:  # noqa: BLE001
                    continue
                pw_field = _await_password_field(page, timeout_ms)
                if pw_field is not None:
                    notes.append(f"login form found at {p}")
                    break
        if pw_field is None:
            browser.close()
            raise CaptureError("no password field found. The login may be behind an SSO/redirect or a "
                               "captcha, or on a page this tool didn't reach. Pass the exact login page URL "
                               "(for a single-page app, include the #/… route, e.g. https://app/#/login).")

        user_field = _find_username_field(page, pw_field)
        if user_field is not None:
            user_field.fill(username, timeout=timeout_ms)
        else:
            notes.append("no username field detected, filled password only (token/PIN login?)")
        pw_field.fill(password, timeout=timeout_ms)

        before = len(seen)
        _submit(page, pw_field)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001. SPAs may never go fully idle; the request is already captured
            pass

        # the AUTH request = a POST after submit whose body carries the credentials we typed
        auth = _pick_auth_request(seen[before:] or seen, username, password)
        if auth is None:
            browser.close()
            raise CaptureError("submitted the form but couldn't identify the login request in the captured "
                               "traffic. The app may send credentials in an unusual way. Try the free "
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


def _register_spec_from_request(req: dict, *, login_url: str = "", check_url: str = "",
                                email_field: str = "email", pw_field: str = "password") -> dict:
    """Reshape a captured signup request into a register spec. Credential fields templated to
    {email}/{password}, every other required field (security answer, terms flag, …) kept verbatim so the
    signup still validates. Same shape the free manual mode's registerSpecFromCurl produces."""
    ct = next((v for k, v in req.get("headers", {}).items() if k.lower() == "content-type"), "")
    where, fields = _parse_body(req.get("post_data") or "", ct)
    templated = {k: ("{email}" if _is_email_key(k) else "{password}" if _is_pw_key(k) else v)
                 for k, v in fields.items()}
    spec = {"register_url": req["url"].split("?", 1)[0], "method": req.get("method", "POST"),
            "where": where, "fields": templated, "count": 2, "email_domain": "example.test",
            "login_url": login_url, "login_email_field": email_field, "login_password_field": pw_field}
    if check_url:
        spec["check_url"] = check_url
    h = _clean_headers(req.get("headers", {}))
    if h:
        spec["headers"] = h
    return spec


def _pick_register_request(requests, email, password):
    """The signup request = a POST carrying the password; prefer one whose URL looks like a register call
    (register/signup/users/account) over any other password-bearing POST (e.g. an unrelated login)."""
    cands = [r for r in requests if r.get("method", "").upper() == "POST" and password
             and password in (r.get("post_data") or "")]
    reggy = [r for r in cands if any(h in r["url"].lower() for h in _REGISTER_URL_HINTS)]
    if reggy:
        return reggy[-1]
    return cands[-1] if cands else None


def capture_register(url: str, email: str, password: str, *, register_url_hint: str = "",
                     security_answer: str = "", login_url: str = "", check_url: str = "",
                     email_field: str = "email", pw_field: str = "password",
                     headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Drive a headless Chromium to the signup form, fill it (email + every password field + best-effort
    security question/answer + terms), submit, and capture the register request → register spec. Returns
    {register_spec, captured, notes} or raises CaptureError with a clear reason. Best-effort by nature:
    signup forms vary far more than logins; on failure the caller should fall back to the manual mode."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise CaptureError("Playwright is not installed. Run: pip install playwright && playwright install "
                           f"chromium  ({type(e).__name__}: {e})") from e

    seen: List[dict] = []
    notes: List[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(ignore_https_errors=True)   # fresh, logged-out context
        page = ctx.new_page()
        page.on("request", lambda req: seen.append(
            {"url": req.url, "method": req.method, "headers": dict(req.headers), "post_data": req.post_data or ""})
            if _safe(req) else None)

        # reach a page that has a signup form (2+ password fields, or a password field on a register route)
        origin = "{u.scheme}://{u.netloc}".format(u=urlsplit(url))
        targets = ([register_url_hint] if register_url_hint else []) + [origin + p for p in _REGISTER_PATHS]
        reached = None
        for t in targets:
            try:
                page.goto(t, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                continue
            page.wait_for_timeout(400)
            if _has_register_form(page):
                reached = t
                break
        if reached is None:
            browser.close()
            raise CaptureError("couldn't find a signup form. The registration page may be behind a link this "
                               "tool didn't follow, an SSO, or a captcha. Pass the exact register-page URL, or "
                               "use the free manual (Copy-as-cURL) mode for the signup request.")
        notes.append(f"signup form found at {reached}")

        _fill_register_form(page, email, password, security_answer, timeout_ms, notes)
        before = len(seen)
        _submit(page, _find_password_field(page))
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        reg = _pick_register_request(seen[before:] or seen, email, password)
        browser.close()

    if reg is None:
        raise CaptureError("filled the signup form but no registration request fired. A required field may "
                           "not have been auto-filled (custom captcha/validation). Use the manual mode for "
                           "this target's signup.")
    spec = _register_spec_from_request(reg, login_url=login_url, check_url=check_url,
                                       email_field=email_field, pw_field=pw_field)
    return {"register_spec": spec, "captured": {"url": reg["url"], "method": reg["method"]}, "notes": notes}


# ── Playwright DOM heuristics (best-effort, defensive) ──────────────────────────────────────────────
def _safe(req):
    try:
        _ = req.url; return True
    except Exception:  # noqa: BLE001
        return False


def _has_register_form(page):
    try:
        if page.locator("input[type=password]:visible").count() >= 2:
            return True   # password + confirm-password is the strongest signup signal
        # a single password field on a register-looking URL also counts
        u = (page.url or "").lower()
        if page.locator("input[type=password]:visible").count() >= 1 and \
                any(h in u for h in ("register", "signup", "sign-up", "sign_up")):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _dismiss_overlays(page):
    """Close welcome banners / cookie-consent dialogs whose backdrops intercept clicks (Juice Shop shows
    both). Best-effort and generic: known dismiss controls, then Escape."""
    for sel in ("button[aria-label*='Close Welcome' i]", "button[aria-label*=dismiss i]",
                "button[aria-label*=close i]", "a.cc-dismiss", ".cc-btn", "a[aria-label*=dismiss i]",
                "button:has-text('Dismiss')", "button:has-text('Me want it')", "button:has-text('Got it')",
                "button:has-text('Accept')", "button:has-text('OK')", "button:has-text('Allow')"):
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=1500)
                page.wait_for_timeout(150)
        except Exception:  # noqa: BLE001
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


def _fill_register_form(page, email, password, security_answer, timeout_ms, notes):
    _dismiss_overlays(page)   # clear consent/welcome backdrops that would swallow the dropdown click
    # email / username
    uf = _find_username_field(page, None)
    if uf is not None:
        try:
            uf.fill(email, timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            pass
    # every visible password field (covers password + repeat/confirm)
    try:
        pw = page.locator("input[type=password]:visible")
        for i in range(min(pw.count(), 4)):
            try:
                pw.nth(i).fill(password, timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # security-answer-ish text fields
    for sel in ("input[name*=answer i]:visible", "input[id*=answer i]:visible",
                "input[name*=security i]:visible", "input[formcontrolname*=answer i]:visible"):
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.fill(security_answer or "curl2spec", timeout=timeout_ms)
                break
        except Exception:  # noqa: BLE001
            continue
    # required dropdowns, native <select>: pick the first non-empty option
    try:
        sels = page.locator("select:visible")
        for i in range(min(sels.count(), 3)):
            try:
                opts = sels.nth(i).locator("option")
                for j in range(opts.count()):
                    val = opts.nth(j).get_attribute("value") or ""
                    if val and val not in ("", "null", "undefined"):
                        sels.nth(i).select_option(index=j); break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    # Angular Material mat-select (Juice Shop's security question): click it, pick the first option.
    # A stray consent backdrop can still intercept, so dismiss again and fall back to a forced click.
    try:
        ms = page.locator("mat-select:visible")
        for i in range(min(ms.count(), 3)):
            try:
                _dismiss_overlays(page)
                try:
                    ms.nth(i).click(timeout=3000)
                except Exception:  # noqa: BLE001
                    ms.nth(i).click(timeout=3000, force=True)   # backdrop still there → force through
                page.wait_for_timeout(350)
                opt = page.locator("mat-option:visible")
                if opt.count() > 0:
                    try:
                        opt.first.click(timeout=3000)
                    except Exception:  # noqa: BLE001
                        opt.first.click(timeout=3000, force=True)
                    notes.append("selected a security question (mat-select)")
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    # accept terms/consent checkboxes
    for sel in ("input[type=checkbox]:visible", "mat-checkbox:visible"):
        try:
            cbs = page.locator(sel)
            for i in range(min(cbs.count(), 3)):
                try:
                    cbs.nth(i).click(timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass



def _await_password_field(page, timeout_ms):
    """Wait for a password field to render (SPAs draw the form after load), dismissing any welcome/cookie
    overlay that sits over it. Returns the field or None. Short, bounded wait so a wrong page fails fast."""
    _dismiss_overlays(page)
    try:
        page.wait_for_selector("input[type=password]", state="visible", timeout=min(int(timeout_ms), 7000))
    except Exception:  # noqa: BLE001
        pass
    _dismiss_overlays(page)   # a dialog can appear once the route has rendered
    return _find_password_field(page)


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
                "button:has-text('Register'):visible", "button:has-text('Sign up'):visible",
                "button:has-text('Sign Up'):visible", "button:has-text('Create account'):visible",
                "*[id*=register i][role=button]:visible", "button:has-text('Continue'):visible",
                "*[id*=login i][role=button]:visible"):
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
