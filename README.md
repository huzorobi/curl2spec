# curl2spec

Turn a browser **"Copy as cURL"** of a login (and optionally a signup) request into a ready-to-use,
two-account **authentication test spec** — the kind an authenticated web-security scan needs to test for
broken object-level authorization (BOLA/IDOR) and broken access control.

It automates the fiddly, error-prone step of hand-writing that JSON: reading a login request to find its URL,
whether the body is JSON or form-encoded, the exact field names, and the identity endpoint.

## 🔒 Privacy — this is the whole point

**curl2spec runs 100% in your browser.** The cURL you paste — including any credentials in it — is parsed
locally in JavaScript. **Nothing is uploaded, logged, transmitted, or stored anywhere.** There is no backend,
no server, no analytics, no network request of any kind. Open the file offline and it still works. That is by
design: a tool that handles login requests has no business phoning home.

## Use

1. Open `index.html` in any browser (double-click it — no install, no server).
2. In your browser's DevTools → **Network**, log in to the target app once by hand.
3. Right-click the login request → **Copy → Copy as cURL** → paste it into curl2spec.
4. (Optional) Add the app's identity endpoint (`/me`, `/whoami`, `/api/user`) as `check_url` — it's the
   positive control that proves a session is live.
5. (Optional, for BOLA) Enter two accounts **you are authorised to use** — A owns the data, B is the attacker.
6. (Optional) Paste the **signup** request's cURL to also get a register spec (auto-mint / reset-recovery).
7. Click **Generate** and copy the JSON blocks into your test harness.

## Output

- **Login spec** — a JSON array, one object per account: `login_url`, `method`, `where` (`json`|`form`),
  `fields` (the real body keys), `headers` (volatile browser noise stripped), and `check_url`.
- **Register spec** — a JSON object for auto-minting accounts: credential fields templated to
  `{email}`/`{password}`, other required fields (e.g. a security answer) kept verbatim.

The output format is compatible with [NullCadre](https://huzosecurity.com)'s two-account authorization testing,
and is a clean, generic shape any harness can consume.

## Scope & ethics

Only generate specs for **test accounts you own or are explicitly authorised to test.** This tool creates no
traffic itself — it only reshapes a request you already captured — but what you do with the output is your
responsibility. Authorised security testing only.

## License

MIT — see [LICENSE](LICENSE). Built by HuzoSecurity Ltd.
