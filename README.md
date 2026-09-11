# curl2spec

**Turn a browser "Copy as cURL" of a login (and optionally a signup) request into a ready-to-use,
two-account authentication test spec** — the kind an authenticated web-security scan needs to test for
broken object-level authorization (BOLA / IDOR) and broken access control.

It automates the fiddly, error-prone step of hand-writing that JSON: reading a login request to find its URL,
whether the body is JSON or form-encoded, the exact field names, and the identity endpoint.

![curl2spec screenshot](screenshot.png)

## 🔒 Privacy — this is the whole point

**curl2spec runs 100% in your browser.** The cURL you paste — including any credentials in it — is parsed
locally in JavaScript. **Nothing is uploaded, logged, transmitted, or stored anywhere.** There is no backend,
no server, no analytics, no network request of any kind. Open the file with your Wi-Fi off and it still works.
That is by design: a tool that handles login requests has no business phoning home.

## Install

There is nothing to install. It's a single static HTML file.

```bash
git clone https://github.com/huzorobi/curl2spec.git
# then just open the file:
xdg-open curl2spec/index.html      # Linux
open curl2spec/index.html          # macOS
# or double-click index.html in your file manager
```

Or download `index.html` on its own and open it — that's the whole app.

## Step-by-step

### 1. Capture the login request
1. Open the target app in your browser and press **F12** → **Network** tab.
2. **Log in once by hand** with a test account.
3. Find the login request in the Network list (usually a `POST` to `.../login`, `.../signin`, `.../auth`, or
   `.../session`; status 200).
4. **Right-click it → Copy → Copy as cURL** (Chrome/Edge: "Copy as cURL"; Firefox: "Copy Value → Copy as cURL").

### 2. Generate the login spec
1. Open `index.html`.
2. Paste the cURL into **Login request (cURL)**.
3. *(Recommended)* Find the app's **identity endpoint** — the request the app makes to answer "who am I?"
   (commonly `/me`, `/whoami`, `/api/user`, `/account`) — and put its URL in **check_url**. This is the
   positive control that proves a session is live and correctly attributed.
4. Click **Generate specs** → copy the **Login spec** JSON.

### 3. (For BOLA/IDOR) make it two accounts
Broken object-level authorization needs **two accounts you are authorised to use**: **A owns the data,
B is the attacker who shouldn't be able to read it.**
- Fill in **Account A** and **Account B** identifiers + passwords.
- curl2spec keeps the request *shape* from your cURL and slots each account's credentials onto the real field
  names — so you get a two-object login spec, no manual editing.
- Leave A/B blank to emit a single-account spec from the cURL exactly as captured.

### 4. (Optional) generate a register spec
If the app has open self-signup, capture the **signup** request the same way (step 1) and paste it into
**Signup request (cURL)**. curl2spec emits a **register spec** that auto-mints fresh accounts — useful for
harnesses that re-create test accounts if the target resets mid-run. Credential fields are templated to
`{email}`/`{password}`; other required fields (e.g. a security answer, a "terms" flag) are kept as-is so the
signup still validates.

## Worked example

**Paste this login cURL:**
```bash
curl 'https://shop.example.com/rest/user/login' -X POST \
  -H 'Content-Type: application/json' -H 'Origin: https://shop.example.com' \
  --data-raw '{"email":"a@example.com","password":"pw"}'
```
**With** `check_url` = `https://shop.example.com/rest/user/whoami`, Account A = `a@example.com` / `pwA`,
Account B = `b@example.com` / `pwB` — **you get:**
```json
[
  {"name":"accountA","login_url":"https://shop.example.com/rest/user/login","method":"POST","where":"json",
   "fields":{"email":"a@example.com","password":"pwA"},"check_url":"https://shop.example.com/rest/user/whoami"},
  {"name":"accountB","login_url":"https://shop.example.com/rest/user/login","method":"POST","where":"json",
   "fields":{"email":"b@example.com","password":"pwB"},"check_url":"https://shop.example.com/rest/user/whoami"}
]
```
(The volatile `Origin` header is dropped automatically; JSON vs form is detected from the body/Content-Type.)

## Output reference

**Login spec** — a JSON array, one object per account:

| field | meaning |
|---|---|
| `name` | label for the account (`accountA` / `accountB`) |
| `login_url` | the POST target for login |
| `method` | HTTP method (usually `POST`) |
| `where` | `json` or `form` — how the credential fields are sent |
| `fields` | the actual request-body keys with each account's credentials |
| `headers` | static headers worth keeping (auth/API-key); volatile browser noise stripped |
| `check_url` | the identity endpoint used to verify the session is live |

**Register spec** — a JSON object: `register_url`, `method`, `where`, `fields` (credentials templated to
`{email}`/`{password}`, other required fields kept verbatim), `count`, `email_domain`, and the `login_*`
fields for logging the minted account in.

The output format is compatible with [NullCadre](https://huzosecurity.com)'s two-account authorization
testing, and is a clean, generic shape any harness can consume.

## Scope & ethics

Only generate specs for **test accounts you own or are explicitly authorised to test.** curl2spec creates no
traffic itself — it only reshapes a request you already captured — but what you do with the output is your
responsibility. **Authorised security testing only.**

## License

MIT — see [LICENSE](LICENSE). Built by [HuzoSecurity Ltd](https://huzosecurity.com).
