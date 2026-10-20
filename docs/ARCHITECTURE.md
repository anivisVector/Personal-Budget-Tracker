# Architecture

## Overview

Ledgerly is a server-rendered Flask application with a small JSON API for interactive features. Every request is handled by one WSGI app (`app.py` → `budget_app.create_app()`), which Vercel runs as a single Python function. Static assets are served separately by Vercel's CDN from `public/`.

```
Browser ── HTML forms ────────────┐
        └─ fetch + X-CSRFToken ───┤
                                  ▼
                       routes/ (blueprints)
                pages · auth · dashboard · transactions
             categories · budgets · analytics · account · api
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
     validation.py          services.py             finance.py
   (every write path)  (writes, AI quota, caching) (queries & maths)
                                  │
              ┌───────────────────┼──────────────────┐
              ▼                   ▼                  ▼
            db.py              ai.py           classifier.py
     SQLite | Postgres   Gemini REST + rules   per-user Naive Bayes
```

## Request lifecycle

1. `before_request` loads the signed-in user from the session (`user_id`) with one query. A deleted account clears the session.
2. The view validates input (`validation.py`), calls services/finance functions, and commits explicitly. Anything not committed is rolled back at teardown.
3. `after_request` adds the security headers (CSP with a per-request nonce, `X-Frame-Options`, `nosniff`, HSTS in production, `no-store` for signed-in pages).
4. Errors render a friendly page, or JSON for `/api/*`.

## Data model

| Table | Purpose | Notes |
|---|---|---|
| `users` | accounts | `password` is a Werkzeug scrypt hash; `is_demo`, `created_at` |
| `categories` | shared defaults (`user_id IS NULL`) + per-user custom | type `income`/`expense` |
| `transactions` | ledger entries | `amount` NUMERIC (14,2 on Postgres), `date` ISO `YYYY-MM-DD`, `category` stored by name (legacy design) |
| `budgets` | monthly limit per category | unique `(user_id, category_id, month, year)` → safe upserts |
| `rate_events` | login failures, demo creation, AI calls | database-backed so limits hold across serverless instances |
| `ai_cache` | last generated insights per user | keyed by a hash of the aggregate data |
| `schema_migrations` | applied migration versions | |

Foreign keys cascade from `users`, so deleting an account removes all of its data.

## Migrations

`schema.py` holds an ordered list of idempotent steps. Version 1 reproduces the original schema with `IF NOT EXISTS`, so databases created by the first version of the app upgrade in place. Later steps only add things: indexes, columns, tables, and the one-way hashing of legacy plaintext passwords. On Postgres, all pending steps run in one transaction behind `pg_advisory_xact_lock`, so concurrent cold starts can't race.

## AI pipeline

| Feature | With Gemini | Without Gemini / over quota / on error |
|---|---|---|
| Quick add (`/api/ai/parse`) | Schema-constrained extraction; the category is an enum of the user's categories | regex + keyword rules (amounts like `2.5k`, `1.5 lakh`; dates like "last Friday") |
| Assistant (`/api/assistant`) | Intent + slots under a JSON schema | keyword intent rules |
| Insights (`/api/insights`) | Narrative from aggregate totals | deterministic rule-based insights |
| Category suggestion | n/a | per-user Naive Bayes trained on the user's own history |

Rules that keep this trustworthy:
- **Model output is untrusted input.** It goes through the same `validate_transaction` / `validate_budget` / `validate_category` as a form submission.
- **Facts come from the database.** The assistant's spoken and displayed numbers are formatted from `finance.snapshot()`, never copied from model text. The health score is computed deterministically and explained factor by factor.
- **Data minimization.** Prompts contain aggregates or the user's own sentence, never the username, email or transaction list.
- **Cost control.** Insights are cached until the underlying numbers change (12-hour TTL), each user has a daily AI quota, and "yes"/"no" replies skip the model.
- **Deprecation resilience.** The model name is configuration (`GEMINI_MODEL`). The original build hard-coded a model that Google later retired, which silently broke every AI feature.

## Security model

| Threat | Mitigation |
|---|---|
| Credential theft from the DB | scrypt hashes; legacy plaintext migrated |
| Session forgery | required random `SECRET_KEY` in production; signed, `HttpOnly`, `Secure`, `SameSite=Lax` cookies; session cleared on login |
| CSRF | Flask-WTF on every POST; JSON requests send `X-CSRFToken` |
| XSS | Jinja autoescaping; no `innerHTML` with data; CSP without inline scripts (nonces only) |
| SQL injection | parameters everywhere; dynamic `ORDER BY` and `WHERE` built only from whitelisted fragments |
| Brute force | DB-backed failure counters per account (8 / 15 min) and per IP (30 / 15 min) |
| Cross-user access | every query scoped by `user_id`; ownership checks return 404 |
| Cross-user ML leakage | classifier models are trained and cached per user |
| Open redirect | `next` parameters must be same-site relative paths |
| CSV formula injection | exported cells starting with `= + - @` are prefixed with `'` |
| Abuse of free features | rate-limited demo creation; per-user AI quota |

## Key decisions

1. **Keep Flask rather than rewrite in Next.js.** Vercel runs Flask natively, and the only real deployment blocker was SQLite. A rewrite would have reset the codebase without fixing anything.
2. **Thin SQL adapter rather than an ORM.** The app had about 40 hand-written queries. Rewriting them in the portable subset of SQL (single-quoted literals, `substr` on ISO dates, `RETURNING`, `ON CONFLICT`) plus a `?`→`%s` adapter made them run on both engines with minimal churn. The trade-off is that new queries must stay portable; CI runs the whole suite on both databases to enforce this.
3. **ISO date strings rather than a `DATE` column.** They keep SQLite and Postgres identical, sort chronologically, and work with range filters. Postgres enforces the format with a `CHECK` constraint.
4. **Pure-Python Naive Bayes rather than scikit-learn.** Training on one user's few thousand rows takes milliseconds. Dropping scikit-learn, SciPy, NumPy and pandas shrinks the serverless bundle and cold starts, and per-user models fix a data-leak bug in the original global model.
5. **Gemini REST via `urllib` rather than the SDK.** The previous SDK (`google-generativeai`) is deprecated. A 60-line client has no dependencies, explicit timeouts, and is easy to fake in tests.
6. **Server-rendered pages with small vanilla JS modules.** They load fast and work without a build step. Interactive features (quick add, insights, assistant, charts) are progressive enhancements over working HTML.

## Known limitations

- `transactions.category` stores a name, not a foreign key. Renaming a category would need a data migration, so it isn't supported yet.
- Single currency (INR) and a single app-wide time zone (`APP_TIMEZONE`).
- No email delivery, so no password reset or email verification.
- Voice input relies on the browser's Web Speech API (Chromium-based browsers). In Chrome, audio is processed by Google's speech service. Typing works everywhere.
