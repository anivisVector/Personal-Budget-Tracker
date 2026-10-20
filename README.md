# Lekha: a personal budget tracker

[![CI](https://github.com/anivisVector/Personal-Budget-Tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/anivisVector/Personal-Budget-Tracker/actions/workflows/ci.yml)

Know where every rupee goes, without the spreadsheet. **Lekha** (लेखा, Hindi for "account") is a full-stack personal finance app for logging income and expenses, setting monthly budgets with **pace projections**, and understanding spending through clear analytics. You can type entries in plain words ("spent 2.5k on dinner last Friday") or use the built-in **voice & chat assistant**.

**Live demo:** [lekha-money.vercel.app](https://lekha-money.vercel.app). The landing page has an **Explore the live demo** button that creates a private sample account (deleted after 24 hours).

![Landing page](docs/screenshots/landing-dark.jpg)

## Features

**Tracking**
- Income and expense entries with categories, notes, search, filters, sorting and pagination
- Natural-language quick add: type a sentence and review the parsed amount, type, category and date before saving
- Category suggestions learned from **your own** history (a per-user Naive Bayes model)
- CSV import with row-by-row validation, duplicate detection, and optional auto-created categories; CSV export that re-imports cleanly

**Budgets & insight**
- Monthly budgets per category, with an even-pace marker and a month-end projection that flags "at risk" categories before they go over
- "Copy last month" that fills gaps without overwriting anything
- Dashboard: balance, month income/spend, budget status, recent activity, spending by category
- Financial health score (0–100) built from savings rate, budget adherence and cash cushion, with every point explained
- Insights written by Google Gemini from aggregate monthly totals, with a rule-based fallback
- Analytics over 3/6/12/24 months: income vs spending, true running balance, category breakdown, table views

**Assistant**
- Chat or voice commands: log a transaction, set a budget, add a category, ask for your balance, spending or budget status, or open a page
- Every proposed change is validated server-side and saved only after you confirm
- Answers about your money come from the database, never from model output

**Personalization**
- Five curated color themes: Nebula (default; violet, blue & cyan with a starfield), Ink, Midnight, Lagoon and Sage, each with light and dark modes and its own typography; follows the system setting unless you choose
- Palette picker in the top bar and under Account → Appearance, applied instantly with a smooth transition

**Account & privacy**
- Change email or password (requires your current password), export all data as JSON, permanently delete your account
- One-click demo accounts with realistic sample data, rate-limited and auto-expiring

## Screenshots

| Dashboard (light) | Dashboard (dark) |
|---|---|
| ![Dashboard light](docs/screenshots/dashboard-light.jpg) | ![Dashboard dark](docs/screenshots/dashboard-dark.jpg) |
| **Analytics** | **Assistant** |
| ![Analytics](docs/screenshots/analytics-light.jpg) | ![Assistant](docs/screenshots/assistant-dark.jpg) |
| **Budgets** | **Mobile** |
| ![Budgets](docs/screenshots/budgets-light.jpg) | <img src="docs/screenshots/mobile-dashboard-dark.jpg" width="260" alt="Mobile dashboard"> |

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.13, Flask 3 (app factory + blueprints), Jinja2 |
| Database | PostgreSQL in production (Supabase or Neon), SQLite for local development and tests; one portable SQL layer with versioned migrations |
| Security | Werkzeug scrypt password hashing, Flask-WTF CSRF, strict CSP with per-request nonces, DB-backed rate limiting |
| AI | Google Gemini (`generateContent` REST, JSON-schema output) + a pure-Python per-user Naive Bayes classifier |
| Frontend | Server-rendered HTML, a hand-written CSS design system (light/dark), vanilla JS, Chart.js 4 |
| Quality | pytest (79 tests, run on SQLite **and** Postgres), Ruff (incl. Bandit security rules), GitHub Actions CI |
| Hosting | Vercel (Python runtime, Fluid compute), static assets from Vercel's CDN, daily cron keep-alive |

## Architecture

```mermaid
flowchart LR
  Browser -->|HTML forms + JSON fetch with CSRF| Flask
  subgraph Vercel
    CDN[(CDN: /static)]
    Flask[Flask app<br/>blueprints]
  end
  Browser --> CDN
  Flask --> Services[services / finance / validation]
  Services --> DB[(Postgres<br/>Supabase)]
  Services --> AI[ai.py]
  AI -->|aggregates only| Gemini[Google Gemini API]
  AI --> Rules[rule-based fallback]
  Services --> NB[per-user classifier]
```

- **One write path.** HTML forms, the JSON API, CSV import and the assistant all go through the same validation and service functions.
- **LLM for language, database for facts.** Gemini classifies intent and extracts fields under a JSON schema; amounts and balances in replies are computed from the database.
- **Serverless-safe state.** Rate limits, AI quotas and cached insights live in the database, not process memory, so they hold across function instances.

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Getting started

Requirements: Python 3.12+ (3.13 recommended).

```bash
git clone https://github.com/anivisVector/Personal-Budget-Tracker.git
cd Personal-Budget-Tracker
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
python app.py               # http://127.0.0.1:5000
```

With no `DATABASE_URL`, the app uses a local SQLite file and creates or migrates it automatically on startup. Click **Try the live demo** to explore with sample data.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | Production | Signs session cookies. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. The app refuses to start in production without it. |
| `DATABASE_URL` | Production | Postgres URL. On Supabase use the **Transaction pooler** string (port 6543). Falls back to `POSTGRES_URL`, which Vercel's Supabase integration sets. The app refuses to start on Vercel without one. |
| `GEMINI_API_KEY` | No | Enables Gemini features. Without it, everything runs on built-in rules. |
| `GEMINI_MODEL` | No | Default `gemini-3.5-flash-lite`. |
| `AI_DAILY_LIMIT` | No | Gemini calls per user per day (default 40). |
| `APP_TIMEZONE` | No | Month boundaries and "today" (default `Asia/Kolkata`). |
| `DEMO_MODE_ENABLED` | No | Landing-page demo accounts (default on). |
| `AUTO_MIGRATE` | No | Apply migrations at startup (default on). |
| `SQLITE_PATH` | No | Local SQLite file when `DATABASE_URL` is empty. |

### Tests and lint

```bash
pytest                      # 79 tests on SQLite
ruff check .
# Same suite against Postgres:
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/test_db pytest
```

CI runs lint plus the full suite against both SQLite and a Postgres 17 service container on every push.

## Deployment (Vercel + Supabase)

1. Create a free Supabase project and copy the **Transaction pooler** connection string.
2. Import the GitHub repo in Vercel. Flask is detected automatically; no build settings needed.
3. Add `SECRET_KEY`, `DATABASE_URL` (and optionally `GEMINI_API_KEY`) under **Settings → Environment Variables**.
4. Deploy. Migrations run on first start, and `/healthz` should return `{"status": "ok"}`.

Every push to a branch gets a **preview deployment**; merging to `main` deploys to production. The full guide, including the Supabase keep-alive cron, preview databases, rollbacks and monitoring, is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Security

- Passwords hashed with scrypt; legacy plaintext passwords are upgraded by a migration
- CSRF tokens on every form and JSON request; session rotated on login; `HttpOnly`, `Secure`, `SameSite=Lax` cookies
- Content-Security-Policy without inline scripts (nonce-based), `frame-ancestors 'none'`, `nosniff`, HSTS in production, pinned CDN assets with Subresource Integrity
- Every query is parameterized, every write is validated server-side, and all user text is escaped (no `innerHTML` with data)
- Brute-force protection per account and per IP, rate-limited demo creation, and a per-user daily AI quota
- Spreadsheet formula injection neutralized in CSV exports
- Only aggregate monthly totals (or the sentence you typed) are sent to Gemini: no name, email or transaction list

## Project structure

```
app.py                  WSGI entrypoint (Vercel looks for `app` here)
init_db.py              Run migrations manually
budget_app/
  __init__.py           App factory, error handlers, template globals
  config.py             Environment settings + production safety checks
  db.py                 SQLite/Postgres adapter
  schema.py             Versioned migrations
  security.py           Auth helpers, rate limiting, security headers
  validation.py         Input validation for every write path
  finance.py            Totals, budgets, pace, analytics, health score
  services.py           Writes, AI quota, insight caching
  ai.py                 Gemini client, NL parsing, insights, assistant
  classifier.py         Per-user Naive Bayes category suggestions
  csv_io.py             CSV import/export
  demo.py               Demo accounts and sample data
  routes/               Blueprints: pages, auth, dashboard, transactions,
                        categories, budgets, analytics, account, api
  templates/            Jinja templates
public/static/          CSS, JS, fonts, favicon (served by Vercel's CDN)
tests/                  pytest suite
docs/                   Architecture, deployment, screenshots
```

## Roadmap

Not implemented yet:
- Password reset by email (needs an email provider)
- Recurring transactions (via Vercel Cron)
- Category foreign keys and renaming
- Multi-currency support
- Installable PWA with offline entry
- Bank statement parsing

## License

No license file has been added yet. Add one (for example MIT) before inviting others to reuse the code.
