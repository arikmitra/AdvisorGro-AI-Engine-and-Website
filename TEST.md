# TEST.md: testing AdvisorGro locally in VS Code

This guide takes you from a fresh checkout to a fully exercised local system. Everything here runs on your machine with **fake providers**: no API keys, no ad accounts, no Postgres, no Docker. Real providers are covered separately in [section 12](#12-optional-testing-with-real-providers).

Commands are written for a **bash-style terminal** (macOS, Linux, or *Git Bash* on Windows). Where Windows PowerShell differs, a PowerShell version is shown. The VS Code integrated terminal can be either.

> **Conventions.** `backend/` means the folder `advisorgro/backend`. "Run in `backend/`" means `cd backend` first. Token and ID values in examples are illustrative; yours will differ.

---

## Contents
1. [What you are testing, and how](#1-what-you-are-testing-and-how)
2. [Prerequisites](#2-prerequisites)
3. [VS Code setup (one time)](#3-vs-code-setup-one-time)
4. [Level 1: automated tests](#4-level-1-automated-tests)
5. [Level 2: run the stack](#5-level-2-run-the-stack)
6. [Level 3: automated smoke test](#6-level-3-automated-smoke-test)
7. [Level 4: manual scenarios](#7-level-4-manual-scenarios)
8. [Browser walkthroughs: website and portal](#8-browser-walkthroughs-website-and-portal)
9. [Inspecting data, files and logs](#9-inspecting-data-files-and-logs)
10. [Debugging in VS Code](#10-debugging-in-vs-code)
11. [Resetting state](#11-resetting-state)
12. [Optional: testing with real providers](#12-optional-testing-with-real-providers)
13. [Troubleshooting](#13-troubleshooting)
14. [What cannot be tested locally](#14-what-cannot-be-tested-locally)
15. [Sign-off checklist](#15-sign-off-checklist)

---

## 1. What you are testing, and how

| Level | What | Time | Needs the stack running? |
|---|---|---|---|
| **1. Automated tests** | 237 tests (235 pass, 2 intentionally skipped): engines, approvals, publisher, leads, security, API | ~20 s | No |
| **2. Run the stack** | API on :8000, website on :8081, portal on :5173 | 5 min | n/a |
| **3. Smoke test** | `scripts/smoke_test.py`: 31 end-to-end checks over real HTTP | ~30 s | Yes (API) |
| **4. Manual scenarios** | The behaviours that matter most, one at a time, with expected results | 45-60 min | Yes |
| **5. Browser walkthroughs** | Website audit form; portal approval console | 15 min | Yes |

### What "fake providers" means
With `AG_USE_FAKES=true` (the default in dev):

| Real-world thing | Local stand-in | Where you see it |
|---|---|---|
| The five audit check groups (search, AI answers, directories...) | Canned results per group; **scoring, review gate, reports, tokens, events are all real** | Audit results |
| LLM (Claude/GPT/Gemini) | Deterministic fake that returns schema-valid ad copy, pages and emails | Campaign drafts |
| Google/Meta Ads | In-memory fake platform, shared between local processes through `backend/.objects/fake_google.json` | Publisher / read-back |
| Email and SMS | Written to `backend/.objects/sent_messages.jsonl` and `GET /dev/outbox` | **Your "inbox"** |
| Database | SQLite file `backend/advisorgro_dev.db` | Section 9 |

### Domain keywords that steer the fake audit
The fake check groups look at the website you type, so you can force every path without real sites:

| Put this in the domain | Effect |
|---|---|
| *(nothing special)*, e.g. `yourtest-firm.com` | Clean run, released automatically |
| `held`, e.g. `heldfirm-demo.com` | Compliance group finds a **material** flag, so the report is **held for a person** |
| `nog5` | Compliance scan **could not run**, so it is **not treated as clean** (held) |
| `boom` | One group raises an error, so it becomes **unverified** and the run still completes |
| `slow` | One group takes 30 s. With a 10 s budget (`AG_SHIELD_BUDGET_SECONDS=10`) it is cut off and becomes **unverified** |
| `lowtls` | Certificate has 9 days left, so Shield raises an **expiry alert** |

---

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer (3.12 tested) | `python --version` (Windows: `py --version`) |
| Node.js | 20 or newer (22 tested) | `node --version` |
| VS Code | recent | |
| Git | any | optional |
| curl | any | `curl --version` (Windows 10+ has `curl.exe`; in PowerShell use `curl.exe`, not `curl`) |

Not needed: Docker, Postgres, Redis, Temporal, Keycloak, any API key.

---

## 3. VS Code setup (one time)

### 3.1 Open the right folder
**File > Open Folder** and choose the repository root (the folder that contains `backend/`, `portal/`, `website/`). The `.vscode/` folder in the repo then gives you tasks, launch configurations and recommended extensions.

### 3.2 Install the recommended extensions
VS Code shows a prompt ("This workspace has extension recommendations"). Accept it, or install:
- **Python** + **Pylance** (`ms-python.python`, `ms-python.vscode-pylance`)
- **Ruff** (`charliermarsh.ruff`): lint
- **REST Client** (`humao.rest-client`): lets you click-run `.vscode/requests.http`
- **SQLite** (`alexcvzz.vscode-sqlite`): browse the local database
- **ESLint** (optional)

### 3.3 Create the virtual environment and install
Run in the VS Code terminal (**Terminal > New Terminal**):

```bash
cd backend
python -m venv .venv
# activate:
source .venv/bin/activate          # macOS / Linux / Git Bash:  source .venv/Scripts/activate
pip install --upgrade pip
pip install -e ".[dev]"
```
PowerShell:
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1       # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install --upgrade pip
pip install -e ".[dev]"
```
Expected: ends with `Successfully installed ... advisorgro-1.0.0`. (`-e` means editable: code changes take effect without reinstalling.)

### 3.4 Select the interpreter
**Ctrl+Shift+P** (Cmd+Shift+P on macOS) > **Python: Select Interpreter** > choose **`backend/.venv`**. The status bar (bottom right) should show `3.x.x ('.venv')`. The tasks and launch configurations use this interpreter automatically, which is why they work on Windows and macOS alike.

### 3.5 Open a *new* terminal after selecting
New terminals auto-activate the venv (you will see `(.venv)` in the prompt).

### 3.6 Sanity check
```bash
cd backend
python -c "import advisorgro; print('advisorgro imported OK')"
```
If you see `ModuleNotFoundError: No module named 'advisorgro'`, the venv is not active or step 3.3 did not finish.

### 3.7 Install the portal's packages
```bash
cd portal
npm install
```
(First run takes about a minute. A `npm notice` about a newer npm version is harmless.)

### 3.8 Useful VS Code commands
| Action | How |
|---|---|
| Run a task | **Terminal > Run Task...** (tasks are numbered in the order you need them) |
| Start the debugger | **Run and Debug** panel (Ctrl+Shift+D), pick a configuration, press F5 |
| Run tests with a UI | **Testing** panel (flask icon). Pytest is pre-configured. Click *Refresh Tests* if empty |
| Send an API request | Open `.vscode/requests.http`, click **Send Request** above a block |

---

## 4. Level 1: automated tests

### 4.1 Run everything
```bash
cd backend
python -m pytest -q
```
**Expected (last line):** `235 passed, 2 skipped, 1 warning in ~20s`

- The **1 warning** is a third-party deprecation notice from the test client (`httpx` with `starlette.testclient`). It is harmless.
- The **2 skipped** tests belong to the original MVP's server/email layers, which the new platform replaced (their behaviours are covered by `test_api.py`, `test_shield_service.py`). They are marked `skip` on purpose.

Or use the VS Code task **Terminal > Run Task > 2. Run all tests**, or the **Testing** panel.

### 4.2 What each test file proves

| File | Tests | What it proves |
|---|---|---|
| `tests/test_shield_service.py` | 13 | Free audit end to end; report link is the credential; **held compliance goes to review and is not emailed**; **unscanned compliance is not "clean"**; a **late or crashing group becomes unverified, never a failed run**; reviewer release; honeypot/validation; **free audit is one-time per firm forever** (also by CRD); **monitoring needs a Shield subscription**; events not duplicated on replay |
| `tests/test_gro.py` | 15 | Full path draft → approve → publish → read-back → Live; **approval refusals** (wrong role, admin, stale MFA, wrong hash, wrong version, staff); drafter cannot approve own work; deputy only inside a delegation window; **approvals are insert-only**; **edit invalidates the old approval**; **read-back mismatch pauses + raises an incident**; kill switch blocks publish; **publish retry never makes a second campaign**; **fact guard blocks invented claims**; overlong headline gets exactly one repair attempt; true-but-regulated claims reach the CCO flagged; illegal state jumps rejected; **tenant isolation**; marketing can only *request* a pause |
| `tests/test_api.py` | 12 | Free-audit contract over HTTP; 413/CORS/honeypot; **rate limit**; **idempotency keys**; 401/404 tenant isolation incl. forged tenant claim; portal approval flow and role limits; **approval publishes without a separate worker**; lead capture → **90 s follow-up** → role-based masking → signed booking webhook → conversion; SMS STOP; kill switch scopes; metrics endpoint is off by default; **402 when enrolling without a subscription** |
| `tests/test_ops.py` | 8 | ORM tenant scoping; Shield monthly + daily probe + alert dedupe; **dead-letter and replay**; metrics gap recorded as a gap (not zero); optimiser cannot launch or exceed the ceiling; weekly-report numbers must come from code; resume needs a still-valid approval |
| `tests/test_security.py` | 28 | 16-case permission matrix; staff/break-glass rules; encryption bound to tenant; **dev secrets refused in prod**; token signature/expiry/audience; webhook signing; signed links; SSRF guard; **tamper-evident activity log**; HTML normalisation; PII redaction |
| `tests/test_golden_evals.py` | 1 | Runs `evals/run_golden.py` (9 labelled cases for the compliance scanner and fact guard) |
| `tests/mvp/*` | 158 (+2 skipped) | The original MVP engine: the five check groups, scraping, search/IAPD/NAP matching, LLM providers, fix list |

### 4.3 Run subsets
```bash
python -m pytest tests/test_gro.py -q                      # one file
python -m pytest -q -k "approval"                          # by keyword
python -m pytest tests/test_gro.py::test_full_path_approve_publish_read_back_live -q   # one test
python -m pytest -q -x                                     # stop at first failure
python -m pytest -q --lf                                   # re-run only last failures (omit -p no:cacheprovider)
python -m pytest tests/mvp -q                              # just the vendored MVP engine
python -m pytest -q -vv                                    # verbose names
```
In the **Testing** panel you can run or debug any single test with the play / bug icons.

### 4.4 Lint, evals, migrations, portal build
```bash
cd backend
python -m ruff check src tests          # expected: "All checks passed!"
python ../evals/run_golden.py           # expected: 9 lines starting PASS, exit code 0
AG_DATABASE_URL=sqlite+pysqlite:///./migrate_check.db python -m alembic upgrade head   # expected: no error; then delete migrate_check.db
cd ../portal && npm run build           # expected: "tsc --noEmit" silent, then "built in ..."
```
PowerShell for the migration check: `$env:AG_DATABASE_URL="sqlite+pysqlite:///./migrate_check.db"; python -m alembic upgrade head`.
(The Postgres-only part of the migration, row-level security, is not exercised on SQLite. See [section 14](#14-what-cannot-be-tested-locally).)

### 4.5 If a test fails
1. Re-run just that test with `-vv -x` and read the assertion.
2. Make sure you did not leave a server from section 5 running with a different `AG_*` setting; tests build their own isolated database in a temp folder and do **not** touch `advisorgro_dev.db`.
3. Check [Troubleshooting](#13-troubleshooting).

---

## 5. Level 2: run the stack

You will use **four terminals**. In VS Code use the **+** in the terminal panel, or the **Run Task** entries.

### 5.1 Terminal 1: seed data (once per reset)
```bash
cd backend
python ../scripts/seed_dev.py
```
**Expected output (abridged):**
```
tenant 47386867-e004-...
firm   19893afb-8528-...
saved  .../advisorgro/.dev/seed.json  (mint fresh tokens with: python ../scripts/mint_token.py cco)

--- firm_admin
eyJhbGciOiJIUzI1NiIs...
--- cco
...
```
What got created:

| Thing | Detail |
|---|---|
| **Tenant "Merrow Lane Wealth"** (plan **gro**) | Users: `firm_admin`, `cco`, `marketing`, `advisor`, `read_only`. One firm with approved facts, two approved disclosures, and a linked (fake) Google Ads account |
| **Tenant "Audit-only Advisors"** (plan **audit**) | User `audit_admin`: a customer who only ever had the free audit, so **no Shield subscription** |
| **Staff user** | `reviewer` (AdvisorGro staff who checks held audit reports) |

> **Tokens expire and approvals need a *fresh* sign-in.** The tokens printed by the seed are good for 8 hours, but anything that approves, links an ad account, or releases a review requires a sign-in **within the last 5 minutes** (step-up MFA). Mint a fresh one right before those actions:
> ```bash
> python ../scripts/mint_token.py cco               # prints a token
> python ../scripts/mint_token.py cco --stale       # "signed in 2 hours ago": shows the step-up refusal
> python ../scripts/mint_token.py marketing --no-mfa
> python ../scripts/mint_token.py cco --other-tenant   # same person, different tenant claim: isolation test
> python ../scripts/mint_token.py audit_admin
> ```
> Roles: `firm_admin cco marketing advisor read_only reviewer audit_admin`.

Handy shell variables (bash):
```bash
cd backend
mk(){ python ../scripts/mint_token.py "$1"; }
FIRM=$(python -c "import json;print(json.load(open('../.dev/seed.json'))['firm_id'])")
J='content-type: application/json'
```
PowerShell:
```powershell
function mk($r){ python ..\scripts\mint_token.py $r }
$FIRM = (Get-Content ..\.dev\seed.json | ConvertFrom-Json).firm_id
```

### 5.2 Terminal 2: start the API
Pick one:

**A. Debugger (recommended for development):** Run and Debug panel > **API (fake providers) - debug** > F5.
**B. Task:** Run Task > **5. Start API (fake providers)** (auto-reloads on code changes).
**C. Terminal:**
```bash
cd backend
python -m uvicorn advisorgro.api.main:app_factory --factory --port 8000 --reload
```
Verify:
```bash
curl -s localhost:8000/readyz
```
**Expected:** `{"ok":true,"env":"dev","fakes":true,"llm":true,"email":true}`

Also open **http://localhost:8000/docs** for interactive API docs (read the reference there; call protected endpoints with curl or the REST Client using a minted token).

> **Single-process mode.** By default `AG_INLINE_WORKER=true`: after the HTTP response is sent, the same process delivers the follow-on events (run the audit, publish after approval, send the first lead reply). So you do **not** need a separate worker. To test the two-process shape instead, set `AG_INLINE_WORKER=false` for the API and start the **Worker (event relay + daily tick)** launch configuration; the behaviour is identical.

### 5.3 Terminal 3: the website
Task **Serve website on :8081**, or:
```bash
cd website
python -m http.server 8081
```
Open **http://localhost:8081**. `website/site.config.js` already points the page at `http://localhost:8000`, and the API's CORS allow-list includes `http://localhost:8081`.

### 5.4 Terminal 4: the portal
The portal needs the API running and a minted token; it calls `http://localhost:8000` by default (set `VITE_API_URL` before `npm run dev` to change it).

```bash
cd portal
npm run dev
```
Open **http://localhost:5173**. Sign-in in development is "paste a token" (see 8.2).

### 5.5 Make emailed links open locally (optional but recommended)
By default, links inside emails and SMS point at production hosts. For local testing start the API with two overrides:

```bash
AG_REPORT_PAGE_URL=http://localhost:8081/ AG_PAGES_BASE_URL=http://localhost:8000 \
  python -m uvicorn advisorgro.api.main:app_factory --factory --port 8000
```
PowerShell:
```powershell
$env:AG_REPORT_PAGE_URL="http://localhost:8081/"; $env:AG_PAGES_BASE_URL="http://localhost:8000"
python -m uvicorn advisorgro.api.main:app_factory --factory --port 8000
```
- `AG_REPORT_PAGE_URL`: the audit email link becomes `http://localhost:8081/#report=<token>` (opens on your local website).
- `AG_PAGES_BASE_URL`: landing-page links (in the ad, SMS and emails) become `http://localhost:8000/p/<campaign>/<version>`, served by the API.

You can put these in `backend/.env` instead (one `AG_NAME=value` per line); every process picks them up. Other handy overrides: `AG_SHIELD_BUDGET_SECONDS=10`, `AG_QUIET_HOURS=[0,0]`, `AG_RATE_LIMIT_PER_MIN=1000` (stops the limiter getting in the way while you click around), `AG_METRICS_TOKEN=local`.

---

## 6. Level 3: automated smoke test

With the API running (5.2) and seeded data (5.1):

```bash
cd backend
python ../scripts/smoke_test.py
```
It makes real HTTP calls and prints one line per check. **Expected:** 31 lines starting `PASS`, then `ALL PASSED`, exit code 0. It covers:

- API up and in fake mode
- Free audit accepted (202), completes, released; **held** audit waits for a person and the held detail is **not** in the prospect's report
- Form validation (400 with field errors), the **one-time rule** (same person gets the same audit; a different person gets *no id* and is pointed to Shield; `www.`/case/path variants do not get a second audit), honeypot
- Campaign: draft → generate → fact guard → pre-check → render → `PendingCCO`
- Approval refusals: marketing (403), stale sign-in (`step_up_required`), wrong hash (`approval_mismatch`), then CCO approves
- Publisher goes **Live** after read-back verification; landing page served with disclosures and a consent checkbox
- Leads: consent required, accepted, duplicate recognised, **first reply within 90 s**, **masking by role**
- Booking webhook: bad signature refused, good signature accepted
- Cross-tenant access gives 404; activity log shows the approval and go-live
- **Free-audit-only customer gets 402** on monitoring; subscriber can enrol
- Firm admin cannot set the global kill switch

Run it again any time: it uses fresh emails and domains. Run it from the debugger with the **Smoke test** launch configuration to step through it. If a line says `FAIL`, the bracketed text shows what the API returned.

---

## 7. Level 4: manual scenarios

Each scenario states **what to do**, **what you should see**, and **why it matters**. They go from "does it work" to "does it refuse what it must refuse". The API must be running (5.2) with seed data (5.1). Examples use bash; for Windows use Git Bash, or send the same requests from `.vscode/requests.http`.

Set up once per terminal:
```bash
cd backend
mk(){ python ../scripts/mint_token.py "$1"; }
FIRM=$(python -c "import json;print(json.load(open('../.dev/seed.json'))['firm_id'])")
J='content-type: application/json'; API=http://localhost:8000
```

### Part A: Shield (the audit)

#### A1. Free audit, clean path
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"yourtest-firm.com","email":"you@yourtest-firm.com","area":"Austin, Texas","consent":true}'
```
**Expect:** HTTP 202 immediately, body `{"ok":true,"id":"<uuid>","message":"Thanks. Your audit is being prepared..."}`. Save the id.

Poll it:
```bash
curl -s $API/api/audit/<id> | python -m json.tool | head -40
```
**Expect (after 1-3 s):** `"status":"sent"`, `report.score.total` about 50, `report.score.max` 100, a list of group results, a "what passed" section, and a fix list. Then read your "inbox":
```bash
curl -s $API/dev/outbox | python -m json.tool
```
**Expect:** one email to `you@yourtest-firm.com`, subject `Your AdvisorGro audit: Yourtest Firm`, containing `...#report=<token>`.
Copy the token and open the report as the recipient would:
```bash
curl -s $API/api/report/<token> | python -m json.tool | head -20
```
**Expect:** `"published":true` and the report. A wrong token (`curl -s -o /dev/null -w "%{http_code}" $API/api/report/wrongwrongwrong`) returns **404**, not 403: a bad link looks like it does not exist.

*Why it matters:* the link is the credential: unguessable, 30-day expiry, revocable, every view logged.

#### A2. The free audit is one time per firm, forever
Repeat A1 with the **same** website and email:
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"yourtest-firm.com","email":"you@yourtest-firm.com","area":"Austin, Texas","consent":true}'
```
**Expect:** HTTP 200 (not 202), `"duplicate":true`, the **same id** as before, and a message saying the free audit was already used and that ongoing monitoring is a Shield subscription.

Now a **different person** asking for the same site:
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"yourtest-firm.com","email":"someone.else@example.com","area":"Austin","consent":true}'
```
**Expect:** `"duplicate":true` and **no `id` field**, so nobody else can open your audit. Try the same firm written differently, which must *not* earn a new audit:
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"https://www.YourTest-Firm.com/about","email":"x@example.com","area":"Austin","consent":true}'
```
**Expect:** `"duplicate":true`. Then use the **same CRD** on a brand-new domain (the firm is identified by CRD too):
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"rebranded-firm.com","email":"a@rebranded.com","area":"Austin","crd":"123456","consent":true}'   # first: 202
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"another-new-name.com","email":"b@another.com","area":"Austin","crd":"123456","consent":true}'  # second: duplicate, no id
```
*Why it matters:* after the one free audit, ongoing monitoring requires the Shield subscription (scenario A8).

#### A3. Validation errors and the honeypot
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"nope","email":"bad","area":"","consent":false}'
```
**Expect:** HTTP 400 with `errors` for `website`, `email`, `area`, `consent`, each a human-readable message. A bot field returns a fake success without doing anything:
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"hp":"i am a bot"}'
```
**Expect:** `{"ok":true,"id":"00000000000000000000000000000000",...}`. Confirm nothing ran: `curl -s $API/dev/outbox` shows no new email.

#### A4. A held report: compliance flag goes to a person
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"heldfirm-demo.com","email":"h@heldfirm-demo.com","area":"Dallas","consent":true}'
# wait 2 s, then poll the id:
curl -s $API/api/audit/<id> | python -m json.tool | head -30
```
**Expect:** `"status":"pending_review"`, a "being reviewed by a person" message, a **preliminary** report (`"preliminary":true`), and **no email** in `/dev/outbox`. Search the whole response: the flagged wording ("12% annual returns") must **not** appear. Held compliance detail never reaches the prospect.

Now act as the AdvisorGro reviewer:
```bash
curl -s $API/v1/review-queue -H "Authorization: Bearer $(mk reviewer)"        # expect 1 item, assigned_role "reviewer"
curl -s $API/v1/review-queue -H "Authorization: Bearer $(mk firm_admin)"      # expect []  (not theirs to see)
curl -s -X POST $API/v1/review-queue/<run_id>/decision -H "Authorization: Bearer $(mk reviewer)" -H "$J" -d '{"decision":"release"}'
curl -s $API/dev/outbox                                                       # the report email has now been sent
```
**Expect:** the queue shows the flag text; release returns `{"state":"Released"}`; the email appears. Poll the audit again: `status":"sent"`.

#### A5. Compliance scan that could not run is not a clean scan
Use domain `nog5-demo.com`. **Expect:** `pending_review` (held) even though no specific flag was found. In the database (section 9) the run has `release_status = compliance_unverified`.

#### A6. A broken or late check is "unverified", never a failed run
Stop the API, then restart it with a **10-second Shield budget** (launch configuration **API (fake providers, 10 s Shield budget)**, or `AG_SHIELD_BUDGET_SECONDS=10`). Then:
```bash
curl -s -X POST $API/api/audit -H "$J" -d '{"website":"slowboom-firm.com","email":"a@slowboom.com","area":"Austin","consent":true}'
# wait ~14 s, then poll
```
**Expect:** the run still **finishes**. `report.score.complete` is `false`, and the total is shown as a **lower bound** (e.g. 46.33 of 100, the report says some checks "could not be verified" and will be completed by the team). The AI-visibility group is marked unverified rather than scored as zero.

#### A7. Live technical snapshot
```bash
curl -s -X POST $API/api/shield/snapshot -H "$J" -d '{"website":"yourtest-firm.com"}' | python -m json.tool
```
**Expect:** `"ok":true`, a few traffic-light rows (certificate, email delivery...) and `live_rows`. Repeating the same call within an hour is served from cache.

#### A8. Monitoring needs a subscription; what Shield does when enrolled
```bash
# A customer who only used the free audit (plan "audit"):
curl -s -i -X POST $API/v1/shield/enroll -H "Authorization: Bearer $(mk audit_admin)" -H "$J" -d "{\"firm_id\":\"$(python -c "import json;print(json.load(open('../.dev/seed.json'))['audit_only']['firm_id'])")\"}" | head -1
```
**Expect:** `HTTP/1.1 402` and `"error":"subscription_required"` ("Monitoring needs a Shield subscription. The free audit is a one-time check."). Portfolio batches (`POST /v1/shield/portfolio`) are refused the same way.

Now a **subscriber** (plan `gro`) with a firm whose certificate is about to expire (`lowtls` in the domain):
```bash
A=$(mk firm_admin)
F2=$(curl -s -X POST $API/v1/firms -H "Authorization: Bearer $A" -H "$J" -d '{"name":"Low Tls Advisors","domain":"lowtls-advisors.com","area":"Austin, Texas"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST $API/v1/shield/enroll -H "Authorization: Bearer $A" -H "$J" -d "{\"firm_id\":\"$F2\"}"      # {"ok":true}
python ../scripts/run_daily.py                       # what the daily schedule does
curl -s $API/v1/shield/runs   -H "Authorization: Bearer $A"
curl -s $API/v1/shield/alerts -H "Authorization: Bearer $A"
```
**Expect from `run_daily.py`:** `"shield_due":1,"probes":1`. **Runs:** one `shield` run (score ~50) **and** one `probe` run (technical group only, lower score is normal). **Alerts:** `cert_expiring`, route `client`: "Your security certificate expires in 9 days". Run `run_daily.py` again the same day: **no second run, no duplicate alert** (idempotent). If you set the tenant's plan back to `audit` (section 9 SQL), `schedule_due` returns 0: a lapsed subscription stops scheduled runs.

### Part B: Gro (campaigns)

#### B1. The golden path: draft, approve, go live
```bash
M=$(mk marketing)
CID=$(curl -s -X POST $API/v1/campaigns -H "Authorization: Bearer $M" -H "$J" -d "{\"firm_id\":\"$FIRM\",\"offer_id\":\"retirement_readiness\",\"platform\":\"google\",\"monthly_budget_cents\":300000,\"budget_ceiling_cents\":500000}" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST $API/v1/campaigns/$CID/submit -H "Authorization: Bearer $M" -H "$J" -d '{}'
```
**Expect:** `{"ok":true,"state":"PendingCCO","version":1,"content_hash":"<64 hex chars>","precheck":"clear"}`. Keep the hash.

View what the CCO would review (use any role that can view):
```bash
curl -s $API/v1/campaigns/$CID -H "Authorization: Bearer $(mk cco)" | python -m json.tool | less
```
**Expect:** `version.page_html` (the landing page), `version.creative_set` (Google headlines <= 30 chars, descriptions <= 90, a 5-email sequence and an SMS containing "Reply STOP"), `version.precheck`, and the same `content_hash`. The page contains your firm's two approved disclosures, injected by code.

Approve (fresh token!):
```bash
H=<content_hash>
curl -s -X POST $API/v1/campaigns/$CID/decision -H "Authorization: Bearer $(mk cco)" -H "$J" -d "{\"version\":1,\"content_hash\":\"$H\",\"decision\":\"approve\",\"comment\":\"OK\"}"
sleep 2; curl -s $API/v1/campaigns/$CID -H "Authorization: Bearer $(mk cco)" | python -c "import sys,json;d=json.load(sys.stdin);print(d['state'],d['live_version'])"
```
**Expect:** decision returns `"state":"Approved"` with an `expires_at` 30 days out; two seconds later `Live 1`. The publisher created the ad **paused**, **read back** what the platform holds, recomputed the content hash, found it equal, and only then activated it. See the fake platform:
```bash
cat .objects/fake_google.json | python -m json.tool | head -30     # status "ENABLED", your headlines
```
And the landing page: open **http://localhost:8000/p/<CID>/1** in a browser. It shows the page, the disclosures, and a form with a **required consent checkbox**.

#### B2. Who may and may not approve (all must be refused)
Create a new draft and submit it as in B1, then try each of these against `/v1/campaigns/$CID/decision`:

| Try | Token | Expect |
|---|---|---|
| Marketing approves (the drafter) | `mk marketing` | **403** `forbidden` ("drafter cannot approve") |
| Firm admin approves | `mk firm_admin` | **403** (admin is not the approver) |
| Read-only approves | `mk read_only` | **403** (same message as admin) |
| AdvisorGro staff approves | `mk reviewer` | **404** `not_found`: a staff token carries no tenant, so the firm's campaign is not even visible. (A staff token that *does* carry a tenant gets 403 "staff cannot approve a firm's marketing"; covered by `test_approval_refusals`) |
| CCO with an old sign-in | `mk cco --stale` | **401** `step_up_required` ("re-authenticate with MFA") |
| CCO signed in without a second factor | `mk cco --no-mfa` | **401** `step_up_required` |
| CCO, **wrong hash** | fresh `cco`, hash `000...0` (64 zeros) | **409** `approval_mismatch` ("content hash does not match what you were shown") |
| CCO, **wrong version** | fresh `cco`, `"version":2` | **409** `approval_mismatch` |
| CCO from **another tenant** | `mk cco --other-tenant` | **404** (tenant comes only from the signed token; another firm's id looks absent) |

**Expect:** every row above refused, the campaign still `PendingCCO`, then the correct approval succeeds (`"state":"Approved"`). Afterwards `GET /v1/activity` shows exactly **one** `campaign.approve`: refused attempts never become approvals.

*Separation of duties:* a person who holds **both** `marketing` and `cco` and drafted a version cannot approve it (covered by `test_drafter_cannot_approve_own_work`; reproducing it by hand needs a token with two roles, so rely on the automated test).

#### B3. Request changes, edit, and the old approval can never be reused
```bash
# new campaign + submit => v1, hash H1
curl -s -X POST $API/v1/campaigns/$CID/decision -H "Authorization: Bearer $(mk cco)" -H "$J" -d "{\"version\":1,\"content_hash\":\"$H1\",\"decision\":\"request_changes\",\"comment\":\"softer tone\"}"
```
**Expect:** `"state":"ChangesRequested"`. Now edit with a clean patch:
```bash
curl -s -X POST $API/v1/campaigns/$CID/submit -H "Authorization: Bearer $M" -H "$J" -d '{"creative_patch":{"google":{"headlines":["Retirement income planning","Fee-only fiduciary advisor","Talk to a local advisor"],"descriptions":["Book a no-obligation call with a local advisor.","Plan the income your retirement needs."]}},"note":"softer tone"}'
```
**Expect:** `{"ok":true,"state":"PendingCCO","version":2,"content_hash":"<different from H1>"}`. Try to approve the **old** version:
```bash
curl -s -X POST $API/v1/campaigns/$CID/decision -H "Authorization: Bearer $(mk cco)" -H "$J" -d "{\"version\":1,\"content_hash\":\"$H1\",\"decision\":\"approve\"}"
```
**Expect:** **409** `approval_mismatch` ("this version has been superseded; review the current version"). Approval belongs to one exact version and hash.

#### B4. The fact guard blocks invented claims
From a campaign in `ChangesRequested` (B3 before the clean patch), submit a patch with made-up claims:
```bash
curl -s -X POST $API/v1/campaigns/$CID/submit -H "Authorization: Bearer $M" -H "$J" -d '{"creative_patch":{"google":{"headlines":["Our 12% annual returns","Award-winning advisors","Call us today"],"descriptions":["We beat the market every year.","Book a call today."]}}}'
```
**Expect:** `{"ok":false,"state":"Draft","unsupported_claims":[{"text":"12%",...},{"text":"Award-winning advisors",...},{"text":"beat the market every year",...}]}`. **No new version exists** and nothing reached the CCO: `GET /v1/campaigns/$CID` shows `current_version` unchanged.

#### B5. Platform limits are enforced in code
The fake LLM obeys limits, so test this through the automated suite instead: `python -m pytest tests/test_gro.py -k overlong -q` (a 40-character headline gets exactly **one** repair retry, then fails cleanly rather than being silently truncated).

#### B6. Drift on the live ad: auto-pause (the most important safety check)
With a campaign `Live` (B1), simulate someone editing the ad inside the ad platform's own UI:
```bash
python - <<'EOF'
from advisorgro.connectors.ads import FakeAdPlatform
p = FakeAdPlatform("google", ".objects/fake_google.json"); p._load()
pid = next(iter(p.objects)); p.tamper(pid, "Guaranteed 12% returns"); print("tampered", pid)
EOF
python ../scripts/run_daily.py      # the daily verification re-reads every live ad
```
**Expect:** `"live_verified":0,"live_total":1`. Then:
```bash
curl -s $API/v1/campaigns -H "Authorization: Bearer $(mk cco)" | python -m json.tool | grep -E "state|pause_reason"
```
**Expect:** `"state":"Paused"`, `"pause_reason":"read-back mismatch: live content differs from the approved version"`. The fake platform shows `PAUSED` (`cat .objects/fake_google.json`). `GET /v1/activity` contains `readback.mismatch`, and the `incidents` table has a `readback_mismatch` row (section 9). *Resume is refused until verification passes* (B8).

#### B7. Kill switch
```bash
curl -s -X POST $API/v1/admin/kill-switch -H "Authorization: Bearer $(mk firm_admin)" -H "$J" -d '{"scope":"tenant","reason":"drill"}'      # {"ok":true}
curl -s -i -X POST $API/v1/admin/kill-switch -H "Authorization: Bearer $(mk firm_admin)" -H "$J" -d '{"scope":"global","reason":"x"}' | head -1   # 403
```
**Expect:** tenant scope accepted for the firm's admin (or CCO); **global scope refused (403)** for a firm admin: only AdvisorGro platform admins may set it. With the switch on, `python ../scripts/run_daily.py` pauses any live campaign ("approval expired or kill switch active"), and any new publish is refused (`kill_switch_active`).

#### B8. Pause, request-pause and resume
With a Live campaign:
```bash
curl -s -X POST $API/v1/campaigns/$CID/pause -H "Authorization: Bearer $(mk marketing)" -H "$J" -d '{"reason":"looks off"}'
```
**Expect:** `{"state":"unchanged","requested":true}`: marketing can only **request** a pause; the campaign is still `Live`. As admin:
```bash
curl -s -X POST $API/v1/campaigns/$CID/pause -H "Authorization: Bearer $(mk firm_admin)" -H "$J" -d '{"reason":"pausing"}'    # state Paused
curl -s -X POST $API/v1/campaigns/$CID/resume -H "Authorization: Bearer $(mk firm_admin)"                                        # {"state":"Live"}
```
Resuming re-checks that the approval is still valid and re-reads the live ad. After a drift (B6) the resume returns `{"state":"Paused"}` until the ad matches again.

### Part C: Leads

#### C1. Capture, consent, duplicates, first reply
With a Live campaign `CID` and its page version `1`:
```bash
LEAD='{"campaign_id":"'$CID'","page_version":1,"name":"Pat Jones","email":"pat@example.com","phone":"(512) 555-0100","consent":true,"gclid":"G123"}'
curl -s -i -X POST $API/v1/leads -H "$J" -d "${LEAD/\"consent\":true/\"consent\":false}" | head -1     # 400  (no consent, no lead)
curl -s -X POST $API/v1/leads -H "$J" -d "$LEAD"                                                       # {"ok":true}  (202)
curl -s -X POST $API/v1/leads -H "$J" -d "$LEAD"                                                       # {"ok":true,"duplicate":true}
curl -s $API/dev/outbox | python -m json.tool | tail -30
```
**Expect:** exactly **one** follow-up email to `pat@example.com` (tag `lead-first`) and, because a phone number was given (and it is outside quiet hours), one SMS such as `Thanks for reaching out. Reply STOP to opt out. Book here: <link>`. The responses are `{"ok":true}` (202), `{"ok":true,"duplicate":true}` (200) and a "not accepting enquiries" message (404). **SMS quiet hours:** texts are not sent between 21:00 and 08:00 (server clock); in that window only the email goes out and the SMS is deferred. To test SMS at any hour, start the API with `AG_QUIET_HOURS="[0,0]"` (PowerShell: `$env:AG_QUIET_HOURS="[0,0]"`). The outbox also contains unrelated messages such as a CCO `approval-request`; look for tag `lead-first`. A post to a campaign that is not live, or with a wrong `page_version`, returns **404** ("This page is not accepting enquiries."), so forged forms cannot attach leads to unapproved pages.

Check the speed promise:
```bash
curl -s $API/v1/leads/sla -H "Authorization: Bearer $(mk cco)"     # {"first_contacts":1,"within_sla":1}
```

#### C2. Who sees what
```bash
curl -s $API/v1/leads -H "Authorization: Bearer $(mk cco)"        # full email/phone
curl -s $API/v1/leads -H "Authorization: Bearer $(mk marketing)"  # "p***@example.com", "***-***-0100"
curl -s $API/v1/leads -H "Authorization: Bearer $(mk read_only)"  # masked
curl -s $API/v1/leads -H "Authorization: Bearer $(mk advisor)"    # []  (advisors see only leads assigned to them)
```

#### C3. Opt-out stops everything
Create the signed request with the helper, then send it:
```bash
python ../scripts/sign_webhook.py sms '{"from":"+15125550100","body":"STOP"}'
# copy the printed curl line and run it   -> {"ok":true,"opted_out":1}
```
**Expect:** `opted_out: 1`; the lead's state is `opted_out` and future check-ins stop. (Time-travel check of the 30/60/90-day sequence is in `tests/test_api.py::test_sms_stop_ends_sequences`.)

#### C4. Booking webhook and conversions
```bash
python ../scripts/sign_webhook.py calendar '{"payload":{"uid":"bk-1","attendees":[{"email":"pat@example.com"}]}}'
# run the printed curl  -> {"ok":true}
```
**Expect:** lead state `booked`, check-ins stopped, and, because the lead came with a click id and consent, **one** conversion recorded on the fake platform (`cat .objects/fake_google.json | python -m json.tool | grep -A3 conversions`). Send the *same* webhook again: still `{"ok":true}` and **still one** conversion. A tampered signature returns **401**; a timestamp older than 5 minutes returns **401** ("stale webhook").
PowerShell has no `curl -d` quoting parity; use `Invoke-RestMethod` or `.vscode/requests.http` for these.

### Part D: Edge, auth and isolation

#### D1. Security headers and correlation id
```bash
curl -sI $API/healthz
```
**Expect:** `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy`, `strict-transport-security`, and an `x-correlation-id` you can search for in the server logs.

#### D2. Request size cap and rate limit
```bash
head -c 20000 /dev/zero | tr '\0' 'x' | curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/api/audit -H "$J" --data-binary @-   # 413
for i in $(seq 1 45); do curl -s -o /dev/null -w "%{http_code} " $API/api/audit/x; done; echo
```
**Expect:** `413`; then about 30 `404`s followed by `429`s (the public limit is 30 requests/minute per client; the bucket refills at 0.5/s). Wait a minute and it recovers.

#### D3. CORS allow-list
```bash
curl -s -i -X OPTIONS $API/api/audit -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST" | grep -i access-control      # no allow-origin header
curl -s -i -X OPTIONS $API/api/audit -H "Origin: http://localhost:8081" -H "Access-Control-Request-Method: POST" | grep -i allow-origin       # allowed
```

#### D4. Idempotency keys
```bash
curl -s -X POST $API/v1/audits -H "$J" -H "Idempotency-Key: demo-1" -d '{"website":"idem-firm.com","email":"i@idem.com","area":"Austin","consent":true}'
curl -s -X POST $API/v1/audits -H "$J" -H "Idempotency-Key: demo-1" -d '{"website":"idem-firm.com","email":"i@idem.com","area":"Austin","consent":true}'
curl -s -X POST $API/v1/audits -H "$J" -H "Idempotency-Key: demo-1" -d '{"website":"idem-firm.com","email":"i@idem.com","area":"Dallas","consent":true}'
```
**Expect:** first two return **identical** bodies (one audit, one email); the third (same key, different body) returns **409** `idempotency_mismatch`.

#### D5. Authentication
```bash
curl -s -i $API/v1/campaigns | head -1                                   # 401
curl -s -i $API/v1/campaigns -H "Authorization: Bearer junk" | head -1   # 401
curl -s $API/v1/me -H "Authorization: Bearer $(mk cco)"                 # your user, tenant, roles, mfa:true
```

#### D6. Role matrix spot-checks
| Request | Token | Expect |
|---|---|---|
| `POST /v1/campaigns` | `read_only` | 403 |
| `POST /v1/campaigns` | `cco` | 403 (CCO approves; does not draft) |
| `GET /v1/activity` | `marketing` | 403 |
| `GET /v1/activity` | `cco` | 200, newest first, each row has a `hash` |
| `POST /v1/firms` | `marketing` | 403 |
| `POST /v1/ad-links` | `firm_admin` stale | 401 `step_up_required` |
| `GET /v1/campaigns/<id>` | `cco --other-tenant` | 404 |

#### D7. Tamper-evident activity log
After doing a few things above, verify the hash chain (section 9.3 has the snippet). Then edit one row directly in SQLite and re-run: verification **fails**.

#### D8. Metrics endpoint is off by default
```bash
curl -s -o /dev/null -w "%{http_code}\n" $API/internal/metrics          # 404
AG_METRICS_TOKEN=local python -m uvicorn ...                            # restart with a token
curl -s $API/internal/metrics -H "Authorization: Bearer local"          # ag_outbox_dead_letters 0 ... ag_campaigns_live 1
```

---

## 8. Browser walkthroughs: website and portal

### 8.1 Website (http://localhost:8081)
1. **Page loads** with the dark design, no console errors (F12 > Console). `site.config.js` points at the API.
2. **Simulator:** click *Simulate a check timeout*: the total changes to **49+**, the AI-visibility group shows **Unverified**, the note says "not counted as a failure", and the delta label says "No alert: run incomplete". Click again to restore.
3. **Hero URL bar:** type `yourtest-firm.com` and submit; the page scrolls to the audit form with the website field filled.
4. **Audit form, validation:** submit empty. Fields highlight with messages (website, email, area, consent). Leave the **consent checkbox** unticked with everything else valid: still refused.
5. **Audit form, success:** tick consent and submit with a fresh domain. **Expect:** a progress message ("We are checking your firm now"), then the results rendered in the page: score, what passed, ranked fixes. DevTools > Network shows `POST /api/audit` → **202** with an `Idempotency-Key` header, then polling `GET /api/audit/<id>`.
6. **Report link:** start the API with the 5.5 overrides, run an audit, copy the link from `/dev/outbox`, paste it into the browser. **Expect:** the report loads straight from the `#report=` token.
7. **One-time rule in the UI:** submit the **same site** again with the same email: your audit loads again with a "free audit already used / Shield" message. With another email: a message and **no report**.
8. **Held audit in the UI:** use a domain containing `held`. **Expect:** "reviewed by a person... within one business day" and a preliminary report without compliance detail. Release it as reviewer (A4) and check the email.
9. **API down:** stop the API and submit. **Expect:** a clear error message, not a blank page or endless spinner.
10. **No cookies or trackers:** DevTools > Application > Cookies is empty for the site.

### 8.2 Portal (http://localhost:5173)
**Sign in.** Paste a token from `python ../scripts/mint_token.py <role>` and click **Sign in**. The header shows your roles. (Production uses single sign-on; this paste box is development only.)

Walk through as each role (open a private window per role, or sign out between):

| Role | You should see |
|---|---|
| **marketing** | Nav: Campaigns, Shield, Leads. **New campaign** form. Open a campaign: **Generate and submit for CCO review** |
| **cco** | Nav adds **Review queue** and **Activity**. Open a `PendingCCO` campaign: the **approval console** |
| **firm_admin** | Campaigns (create), Shield, Leads (full details), Activity; **Pause / Resume** buttons |
| **read_only** | View-only: no create form, no buttons that change things |
| **reviewer** (staff) | Only **Review queue** (audit reports held for a person) |

**The approval console** (campaign in `PendingCCO`, signed in as `cco`):
- Left: the **exact landing page** in a sandboxed frame (scripts disabled), the Google ad headlines/descriptions, and the follow-up emails + SMS. This is the stored artefact whose hash is sent with your decision.
- Right: **Pre-check** (flags only: "Material"/"Minor", "AI review missing" if applicable), the **content hash**, and **Approve this version / Request changes / Reject** with a comment box. Wording reminds you approval needs a fresh multi-factor sign-in and that you cannot approve what you drafted.

Click **Approve this version** with a token minted more than 5 minutes ago (or `--stale`): the page shows *"Please sign in again with your second factor, then retry."* Mint a new token, sign in again, and approve: state becomes **Live**.

Other pages: **Shield** (runs and alerts; after A8 you see the certificate alert), **Leads** (masked per role, plus the SLA line "First reply within 90 seconds: n of n"), **Activity** (append-only log with hash).

---

## 9. Inspecting data, files and logs

### 9.1 Files
| Path (under `backend/`) | Contains |
|---|---|
| `advisorgro_dev.db` | SQLite database (open with the SQLite extension: **Ctrl+Shift+P > SQLite: Open Database**) |
| `.objects/sent_messages.jsonl` | **Every email and SMS "sent"**, one JSON per line, newest last |
| `.objects/fake_google.json`, `fake_meta.json` | The fake ad platforms' state |
| `.objects/<tenant-id>/<hash>/...` | Reports (`client_report.html`, `evidence_report.md`, `run_record.json`, `payload.json`), landing pages, creative JSON |
| `../.dev/seed.json` | Ids from the seed script (used by the token and smoke scripts) |

Open `client_report.html` in a browser to see the polished HTML report; `evidence_report.md` is the source-traced evidence.

### 9.2 Useful SQL (SQLite extension, or `sqlite3 advisorgro_dev.db`)
```sql
-- audit runs and their gate
SELECT domain, trigger, state, score, partial, release_status FROM runs ORDER BY created_at DESC;
-- each group: verified / partial / unverified
SELECT r.domain, g.group_key, g.status, g.points, g.max_points, g.unverified_reason
FROM group_results g JOIN runs r ON r.id = g.run_id ORDER BY r.created_at DESC;
-- the event backbone: pending, delivered, dead-lettered
SELECT type, published_at IS NOT NULL AS delivered, attempts, dead, last_error FROM outbox ORDER BY created_at DESC;
-- campaigns and their states
SELECT id, state, current_version, live_version, pause_reason FROM campaigns;
-- approvals (insert-only): who, what, which hash
SELECT campaign_id, version, decision, substr(content_hash,1,12) AS hash, approver_id, expires_at FROM approvals;
-- incidents (read-back mismatches, send failures)
SELECT kind, detail, resolved FROM incidents;
-- leads: contact fields are ENCRYPTED, only hashes and states are readable
SELECT state, substr(email_enc,1,16) AS email_enc, substr(email_hmac,1,12) AS hmac, first_contact_at FROM leads;
-- first-reply speed
SELECT json_extract(meta,'$.latency_s') AS seconds, json_extract(meta,'$.sla_met') AS ok FROM lead_events WHERE type='first_contact';
-- change a tenant's plan (to test subscription gating): plans are audit | shield | gro
UPDATE tenants SET plan='audit' WHERE name='Merrow Lane Wealth';
```
**Expect for leads:** `email_enc` looks like random text; there is no plaintext email anywhere in the database.

### 9.3 Verify the activity log chain
```bash
python - <<'EOF'
from advisorgro.container import build_container
from advisorgro.core.config import get_settings
from advisorgro.db.session import session_scope
from advisorgro.security.activity import verify_chain
c = build_container(get_settings())
with session_scope(c.sm, system=True) as s:
    print("activity log chain intact:", verify_chain(s))
EOF
```
**Expect:** `True`. Now change a row (`UPDATE activity_log SET action='forged' WHERE seq=2;` in SQLite) and run again: **`False`**.

### 9.4 Logs
The API logs one **JSON** line per event to the terminal, with `correlation_id`, and with emails/phones/secrets already redacted. Copy the `x-correlation-id` from a response header (D1) and search the terminal for it to follow one request across the audit pipeline. Confirm no raw email addresses appear in the logs.

---

## 10. Debugging in VS Code

| Goal | How |
|---|---|
| Step through an API call | Start **API (fake providers) - debug**, set a breakpoint, call the endpoint with curl/REST Client |
| Step through a test | **Testing** panel, bug icon beside the test, or **Pytest: current file** configuration |
| Step through the smoke test | **Smoke test** configuration |
| Debug the worker/event relay | Start API with `AG_INLINE_WORKER=false`, then **Worker (event relay + daily tick)** |

Good places for breakpoints (all under `backend/src/advisorgro/`):

| Behaviour | File : function |
|---|---|
| Audit intake and the one-time rule | `engine/shield/service.py : submit` |
| Group fan-out, deadlines, "unverified" | `engine/shield/orchestrator.py : run_groups` |
| Review gate and release | `engine/shield/service.py : _persist`, `_release` |
| Campaign pipeline | `engine/gro/campaigns.py : submit_for_review` |
| Fact guard | `engine/gro/factguard.py : check` |
| The content hash | `engine/gro/renderer.py : render` |
| Who may approve | `security/policy.py : authorize`, `engine/gro/approvals.py : decide` |
| Go-live and drift detection | `engine/gro/publisher.py : publish`, `verify_live` |
| Event delivery, retries, dead letters | `core/events.py : EventBus.relay_once` |
| First reply to a lead | `leads/service.py : follow_up` |

---

## 11. Resetting state

Start clean whenever you want to repeat the one-time free-audit scenarios (domains are remembered forever by design).

```bash
# stop the API first
cd backend
rm -rf advisorgro_dev.db .objects ../.dev
python ../scripts/seed_dev.py
```
PowerShell:
```powershell
cd backend
Remove-Item -Force advisorgro_dev.db -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .objects, ..\.dev -ErrorAction SilentlyContinue
python ..\scripts\seed_dev.py
```
Or run the tasks **3. Reset local state** then **4. Seed dev data**. After a reset, old tokens in the portal are invalid (the users have new ids): mint new ones and sign in again.

---

## 12. Optional: testing with real providers

Do this only after sections 4-8 pass. It spends real money and calls real services.

1. Copy `.env.example` to `backend/.env` and fill in only what you need. Leave the rest blank; groups that lack a key become **unverified**, which is itself a good test.
2. Set `AG_USE_FAKES=false`. (Staging/prod refuse dev secrets; dev does not.)
3. Which key unlocks which check:

| Key | Unlocks |
|---|---|
| `AG_ANTHROPIC_API_KEY` / `AG_OPENAI_API_KEY` / `AG_GEMINI_API_KEY` | AI-visibility answers; LLM compliance review; campaign drafting (the first available is primary, others are fallbacks) |
| `AG_BRAVE_API_KEY` / `AG_SERPER_API_KEY` (or `AG_SERPAPI_API_KEY`) | Search visibility |
| `AG_GOOGLE_PLACES_API_KEY` | Google Business Profile check |
| `AG_CONTACT` | Required courtesy contact sent to the SEC adviser database |
| `AG_POSTMARK_TOKEN`, `AG_TWILIO_*` | Real email/SMS delivery (otherwise `/dev/outbox` no longer works: the fakes are off) |

4. **Start with a site you own**, run one free audit, and compare each group's result against what you see by hand. Expect slower runs (up to the 120 s budget) and a score that varies slightly between runs in the AI-visibility group (it samples AI answers; the report states the sample size).
5. **Campaigns with real ad platforms:** the Google and Meta connectors in `backend/src/advisorgro/connectors/ads.py` have **never been run against a real account**. Use a **test account** (Google Ads test manager account; Meta test ad account), keep budgets minimal, keep every campaign `PAUSED` at first, and confirm the read-back matches before enabling. Offline conversion upload is intentionally not implemented until a conversion action / pixel id is configured.
6. Check vendor model names in config against each vendor's current documentation.

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'advisorgro'` | Venv not active or editable install missing. Re-run 3.3 and 3.4, open a new terminal |
| Tests collect nothing in the Testing panel | Select the interpreter (3.4), then **Testing > Refresh**. `python.testing.cwd` is already `backend` |
| `Address already in use` on :8000 | An earlier API is still running. Stop it (Ctrl+C in its terminal, or Run and Debug > stop), or use `--port 8001` and `--base http://localhost:8001` for the smoke test |
| `401 unauthorized` | Token missing, malformed or expired (8 h). Mint a new one |
| `401 step_up_required` | Expected for approvals with an old sign-in. Mint a **fresh** token and retry within 5 minutes |
| `403 forbidden` | Role is not allowed. That is usually the correct result; check the table in B2/D6 |
| `404` on an id that exists | You used another tenant's token (by design), or the reset replaced the ids |
| `429 rate_limited` | More than 30 public requests per minute from one client. Wait a minute |
| `CORS` error in the browser console | Site/portal must be served from `localhost:8081` / `localhost:5173` (the allow-list). Using `127.0.0.1` or another port is blocked by design; override with `AG_CORS_ORIGINS='["http://127.0.0.1:8081"]'` |
| Audit stays `queued` | `AG_INLINE_WORKER=false` without a worker running. Start the Worker configuration, or unset it |
| Campaign stays `Approved`, never `Live` | Same cause as above; or the ad link is missing (re-seed); check `/dev/outbox` and server logs for `publish.failed` |
| `/dev/outbox` is 404 | Only exists when `AG_ENV=dev` **and** fakes are on. With real providers read your real inbox |
| Audit says `duplicate` for a domain you never used | The free audit is one-time per firm **forever** (and by CRD). Use a new domain, or reset (section 11) |
| `database is locked` (SQLite) | Two processes writing at once under load. Retry; for heavier testing use Docker Postgres (`docker compose up postgres`) |
| Report or landing-page links open the production site | Start the API with `AG_REPORT_PAGE_URL` and `AG_PAGES_BASE_URL` (5.5) |
| `npm install` errors about prefix/config | Run it from `portal/`; if your global npm config sets `prefix`, run `npm_config_prefix= npm install` (bash) |
| PowerShell: `curl` behaves oddly | PowerShell aliases `curl` to `Invoke-WebRequest`. Use `curl.exe`, Git Bash, or `.vscode/requests.http` |
| PowerShell blocks venv activation | `Set-ExecutionPolicy -Scope Process Bypass` then activate again |
| Windows: `source` not found | Use `.\.venv\Scripts\Activate.ps1` in PowerShell, or `source .venv/Scripts/activate` in Git Bash |
| Smoke test says `No such file ... seed.json` | Run the seed first (5.1) from the `backend/` folder |

---

## 14. What cannot be tested locally

Be explicit about these so a green local run is not mistaken for production readiness.

| Area | Why not here | Where it is checked instead |
|---|---|---|
| **Postgres row-level security and the database immutability triggers** (`migrations/0002`) | SQLite has neither | CI job `backend` applies the migrations on a real Postgres. Locally, the ORM-level guards are tested; `docker compose up postgres` lets you try Postgres by hand |
| **Real search, AI-engine and directory results** | Needs keys and live sites; results vary | Section 12; the vendored MVP engine's 158 tests use stubs |
| **Google Ads / Meta connectors** | Never run against a real account | Section 12 with test accounts, in shadow mode |
| **Temporal workflows** | Needs a Temporal server | The in-process event runner exercises the same handlers (everything in sections 6-7). Validate the workflows in staging |
| **Single sign-on (Keycloak) and real MFA** | Dev uses signed tokens | Import `infra/keycloak/advisorgro-realm.json` in staging and test sign-in, MFA and step-up |
| **Infrastructure as code** (`infra/tofu`) | Not applied anywhere | Platform engineer review; `tofu validate` in CI |
| **Email/SMS deliverability, SPF/DKIM, carrier rules** | Needs real providers/domains | Staging with real providers |
| **Load, backup and restore, security scans** | Out of scope for a laptop | Staging; CI runs gitleaks, semgrep, pip-audit, trivy, checkov |
| **Legal wording** (consent text, quiet hours, retention) | Placeholders for counsel | `docs/OPEN_ITEMS.md` |

---

## 15. Sign-off checklist

Tick each before calling local testing done.

**Automated**
- [ ] `pytest`: 235 passed, 2 skipped
- [ ] `ruff check`: all checks passed
- [ ] Golden evals: 9 PASS
- [ ] Portal `npm run build` succeeds
- [ ] Smoke test: ALL PASSED (31)

**Shield**
- [ ] A1 clean audit: 202, completes, email in `/dev/outbox`, report opens by token; wrong token 404
- [ ] A2 one free audit per firm: same person gets the same audit; others get no id; `www`/case/path and same-CRD variants blocked
- [ ] A3 validation 400 with field errors; honeypot does nothing
- [ ] A4 held report: waits for review, no email, flagged wording absent; reviewer release sends it; firm admin cannot see the reviewer queue
- [ ] A5 scan that did not run is held
- [ ] A6 slow/crashing group: run completes, score is a lower bound, group unverified
- [ ] A8 free-audit-only customer gets 402 on monitoring; subscriber enrols; daily job runs once per day with no duplicate alerts; certificate alert routed to the client

**Gro**
- [ ] B1 draft → approve → Live with read-back verified; landing page shows disclosures and consent box
- [ ] B2 every refusal row returns the expected code
- [ ] B3 edit makes a new version; old approval refused (409)
- [ ] B4 fact guard blocks invented claims; no version created
- [ ] B6 tampered live ad is auto-paused by the daily job; incident and `readback.mismatch` logged
- [ ] B7 kill switch: tenant allowed, global refused for a firm admin
- [ ] B8 marketing can only request a pause; resume re-verifies

**Leads**
- [ ] C1 no consent → 400; accepted; duplicate recognised; exactly one first email (+ one SMS); SLA 1 of 1
- [ ] C2 masking by role; advisor sees none
- [ ] C3 STOP opts out; C4 booking books once and sends one conversion; bad/stale signatures 401

**Edge and data**
- [ ] D1-D5: headers, 413, rate limit 429, CORS allow-list, idempotency 409, auth 401
- [ ] D7 / 9.3 activity chain verifies `True`, and `False` after tampering
- [ ] 9.2 leads store only encrypted contact data
- [ ] Logs contain no raw emails or phone numbers

**Browser**
- [ ] Website: simulator, validation, consent required, results render, report link, one-time message, API-down message
- [ ] Portal: each role sees only its pages; approval console shows page + flags + hash; stale sign-in prompts re-authentication; approval goes Live
