# Deploying Ledgerly (Vercel + Supabase)

Vercel runs the Flask app as a Python function, and Supabase provides the Postgres database. Both have free tiers that are enough for a personal project.

> **Why not SQLite in production?** Vercel functions have a read-only filesystem, apart from an ephemeral, per-instance `/tmp`. A SQLite file there would be lost. The app refuses to start on Vercel without `DATABASE_URL`, so this mistake can't happen silently.

## What happens automatically vs manually

| Step | Automatic? |
|---|---|
| Building and deploying when you push to GitHub | ✅ Vercel Git integration (after the one-time import) |
| Preview deployment for every branch / pull request | ✅ |
| Production deployment when `main` changes | ✅ |
| Creating database tables and applying new migrations | ✅ on the first request to a new deployment (`AUTO_MIGRATE=1`), guarded by a Postgres advisory lock |
| Daily keep-alive request to `/healthz` | ✅ Vercel Cron (production only) |
| Creating the Supabase project and copying its URL | ❌ manual, once |
| Adding environment variables in Vercel | ❌ manual, once (and whenever a secret changes) |
| Local edits reaching the live site | ❌ only after you **commit and push**. Saving a file locally changes nothing online. |

## 1. Create the database (Supabase)

1. Sign in at [supabase.com](https://supabase.com) → **New project**. Pick the region closest to your users (for India, Mumbai). Save the database password somewhere safe.
2. Open **Connect** (top bar) → **Connection string** → choose **Transaction pooler** (port **6543**).
   - Use the pooler, not the direct connection: free-plan direct connections are IPv6-only, and the transaction pooler is built for serverless functions that open many short-lived connections.
   - Transaction mode doesn't support prepared statements; the app already disables them in its driver.
3. Replace `[YOUR-PASSWORD]` in the string and append `?sslmode=require`:
   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
   ```
   If your password contains special characters (`@`, `:`, `/`, `#`…), URL-encode them.

You don't need to create any tables; the app's migrations do that.

**Shortcut: Vercel's Supabase integration.** Instead of steps 1–3, you can create the database from Vercel (**Project → Storage → Create Database → Supabase**, or `vercel integration add supabase`). It sets `POSTGRES_URL` (the transaction pooler) on the project automatically. The app uses `POSTGRES_URL` when `DATABASE_URL` is not set, and strips the integration's extra `supa=` parameter, which the Postgres driver would otherwise reject.

## 2. Import the project into Vercel

1. Push this repository to GitHub (private or public both work).
2. In [vercel.com](https://vercel.com) → **Add New… → Project** → import the repository.
3. Leave the defaults: **Framework Preset: Flask** (auto-detected), **Root Directory: `./`**, no build command, no output directory. Python 3.13 is selected from `.python-version`, and dependencies come from `requirements.txt`.
4. Before the first deploy, open **Environment Variables** and add:

   | Name | Value | Environments |
   |---|---|---|
   | `SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_hex(32))"` | Production (use a *different* value for Preview) |
   | `DATABASE_URL` | the Supabase pooler URL from step 1 | Production (see "Preview databases" below) |
   | `GEMINI_API_KEY` | your key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (optional) | Production, Preview |

   Mark secrets as **Sensitive**. Never put real values in `.env.example`, `vercel.json` or the code.
5. Click **Deploy**.

## 3. Verify the deployment

1. Open `https://<your-project>.vercel.app/healthz` and expect `{"status": "ok", "database": "ok", "schema_version": 7}`.
   - `503` with `"database": "unreachable"` means `DATABASE_URL` is wrong, or the Supabase project is paused (see Troubleshooting).
2. Open the home page → **Try the live demo**. You should land on a dashboard with sample data.
3. Register a real account, add a transaction, set a budget, and open Analytics.
4. If you set `GEMINI_API_KEY`, the dashboard's Insights card should end with *"Written by Gemini from your monthly totals"*.

## 4. Daily workflow

```
edit locally → pytest && ruff check . → git commit → git push (feature branch)
        → Vercel builds a Preview deployment → check the preview URL
        → open/merge a pull request into main → Vercel deploys to Production
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint and the test suite (SQLite and Postgres) on every push and pull request. In GitHub → Settings → Branches you can make CI a required check before merging to `main`.

## Preview databases

Preview deployments run your branch's code. If they share the production `DATABASE_URL`, a branch that adds a migration would migrate production before you merge it. Recommended setup:

- Create a second free Supabase project (for example `ledgerly-preview`) and set its pooler URL as `DATABASE_URL` for the **Preview** environment only.
- Or give Preview no `DATABASE_URL` at all. The app will then refuse to start there, which is safe but means previews can't be clicked through.

Migrations in this project are additive (new tables, new nullable columns, new indexes), so older deployments keep working against a newer schema.

## Supabase free-tier keep-alive

Supabase pauses free projects after about a week without activity. `vercel.json` defines a cron job that requests `/healthz` every day at 06:00 UTC, and that endpoint runs a real query. Cron jobs run on the production deployment only. You can see their runs under **Vercel → Project → Settings → Cron Jobs**. If a project does get paused, restore it from the Supabase dashboard.

## Custom domain & HTTPS

Vercel → Project → **Settings → Domains** → add your domain and follow the DNS instructions. Certificates are issued and renewed automatically. The app sets `Secure` cookies and HSTS in production, so always use the `https://` URL.

## Rollback and recovery

- **Code:** Vercel → **Deployments** → choose a previous production deployment → **Instant Rollback** (or *Promote to Production*). This takes seconds, with no rebuild.
- **Database:** migrations never drop or rewrite columns, so rolling back code doesn't require a database rollback. Check your Supabase plan's backup policy. For your own copy, run `pg_dump` against the *direct* connection string, or use **Account → Export everything (JSON)** in the app.
- **Leaked secret:** rotate it at the source (regenerate the Gemini key in AI Studio; generate a new `SECRET_KEY`), update the Vercel environment variable, and redeploy. Changing `SECRET_KEY` signs everyone out, which is expected.

## Monitoring

- **Runtime logs:** Vercel → Project → **Logs**. Unhandled errors are logged with method and path (never request bodies or secrets). AI fallbacks log a one-line warning such as `Gemini returned HTTP 429`.
- **Uptime:** point any free uptime monitor at `/healthz`. It returns `503` when the database is unreachable.

## Running with the Vercel CLI (optional)

```bash
npm i -g vercel
vercel link                       # connect this folder to the Vercel project
vercel env pull .env.development  # downloads Development env vars (contains secrets: keep it out of git)
vercel dev                        # runs the app the way Vercel does
vercel deploy                     # preview deployment from your machine
```

`.vercelignore` keeps `.env`, local databases, tests and docs out of CLI uploads.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Build log: `ConfigError: SECRET_KEY must be set in production` | Add `SECRET_KEY` for that environment and redeploy. |
| `ConfigError: DATABASE_URL is required on Vercel` | Add `DATABASE_URL` for that environment (Preview included, or accept that previews won't start). |
| `/healthz` 503, logs show a connection timeout | Use the **Transaction pooler** URL (port 6543), not the IPv6 direct host, and check the password and `sslmode=require`. |
| Supabase "project paused" | Restore it in the Supabase dashboard; the daily cron prevents this afterwards. |
| Insights say "rule-based" although a key is set | Check `GEMINI_API_KEY` in that environment and look for `Gemini returned HTTP …` in the logs (403 = key restricted or invalid, 429 = quota). |
| Styles missing on Vercel | Assets must live under `public/static/`; Vercel serves `public/` from its CDN. |
