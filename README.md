# Food Log

A personal, phone-first web app for tracking eating habits over time and getting honest suggestions to improve them. It brings in sleep and activity from a Garmin watch to look for links between them.

You log a meal by photo, by typing or dictating a description, or with one tap from a favourite. Claude estimates the items, portions, calories and macros. The Summary tab shows trends against your targets and Claude's written analysis.

Logging is only the input. The point of the app is the Summary: changes should make its insights sharper.

## How it fits together

```
iPhone (installed web app)
   │  HTTPS, bearer token
   ▼
FastAPI on Render (free tier) ──► Turso (hosted SQLite): meals, profile, favourites,
   │                                 sleep, activity, cached analyses
   ├──► Claude API (claude-opus-5-5): meal estimates, Summary analysis
   └──► Garmin Personal API: sleep, daily activity, workouts (pulled on app open)
```

- **Frontend:** plain HTML, CSS and JavaScript in `backend/static/`, with no build step. The scripts load in order and share one global scope, so two top-level names must never clash. A test checks this.
- **Backend:** FastAPI in `backend/`. Locally it uses `food_log.db` (SQLite). When `TURSO_URL` is set it uses Turso over HTTP.
- **Claude calls:** `backend/vision.py` estimates meals from a photo, a description or a correction. `backend/summary.py` writes the Summary analysis and caches it until the data changes.
- **Garmin:** `backend/garmin.py` is a thin client for Garmin's official Personal API. `backend/health.py` handles parsing, syncing and the summary text. Render's free tier has no scheduler, so the app syncs when it opens, skipping any sync within the last hour.

### Repo layout

| Path | What it is |
|---|---|
| `backend/main.py` | API routes. The docstring lists every endpoint |
| `backend/db.py` | Schema, migrations and all SQL |
| `backend/vision.py`, `summary.py` | Claude calls and prompts |
| `backend/targets.py` | Calorie and protein targets (Mifflin-St Jeor, adjusted by measured activity) |
| `backend/nutrition.py` | Food-quality measures against UK guidelines (fibre, 5 a day, plants, protein sources...) |
| `backend/garmin.py`, `health.py` | Garmin client, sync, parsing |
| `backend/maintenance.py`, `maintenance.json` | Things that expire, and the in-app banner |
| `backend/static/` | The web app: `index.html`, `css/app.css`, `js/*.js` (load order is in `index.html`) |
| `tests/` | pytest suite, plus `tests/browser/smoke.mjs` for headless-Chrome scenarios |
| `garmin_probe.py` | Prints the structure of Garmin responses (field names only, no values) |
| `loader.py`, `build_notebook.py`, `analysis.ipynb` | Older notebook analysis of the manual Excel log |

## Running it locally

In PowerShell, from the project folder:

```powershell
$env:ANTHROPIC_API_KEY = 'your-key'                # needed for photo analysis and the Summary
$env:GARMIN_PERSONAL_API_KEY = 'your-garmin-key'   # optional: Sleep and Fitness tabs
python -m uvicorn backend.main:app --port 8001
```

Then open http://127.0.0.1:8001.

- **Database:** locally the app uses `food_log.db`, which is ignored by git and never pushed. It is not the live database.
- **Sign-in:** with `APP_TOKEN` unset, local requests aren't checked.
- **Corporate networks:** some re-sign HTTPS with their own certificate. The Garmin client trusts the operating system's certificates as well as Python's bundled list, so it works there. If another library fails with `CERTIFICATE_VERIFY_FAILED`, that's the cause.

## Tests

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

This runs about 85 tests in roughly 30 seconds. Claude and Garmin are faked, each test gets an empty throwaway database, and nothing touches the network or real data.

- **Unit and API tests:** every endpoint and the main logic.
- **Frontend checks:** each script parses, no duplicate top-level names, every element the code looks up exists, and every referenced file exists.
- **Browser scenarios:** headless Chrome clicks through the real app (food log, first-run setup, describe → correct → save → edit, favourites, the maintenance banner, Summary with Claude up and down, Sleep and Fitness). These need Node 22+ and Chrome. Locally they're skipped if either is missing; in CI that's a failure.

To add a browser scenario, write it in `tests/browser/smoke.mjs` and add a test in `tests/test_browser.py` that seeds the database and runs it.

## Deploying

Pushing to `main` deploys:

1. GitHub Actions (`.github/workflows/tests.yml`) runs the test suite.
2. Render deploys only if the checks pass. This needs the service's **Settings → Auto-Deploy** set to **After CI Checks Pass**.
3. The new frontend files get the deployed commit in their URLs, so phones never mix old and new files.

### Environment variables (Render → foodlog → Environment)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API |
| `APP_TOKEN` | Shared secret the app sends as a bearer token. On a new device, open the app once as `https://…/#token=<APP_TOKEN>` |
| `TURSO_URL`, `TURSO_AUTH_TOKEN` | Live database |
| `GARMIN_PERSONAL_API_KEY` | Garmin Personal API key (expires; see below) |

Tests use `FOODLOG_DB_PATH` to point at a throwaway database. Render sets `RENDER_GIT_COMMIT` itself.

## Keeping it running

- **Things that expire** live in `maintenance.json`: the Garmin key, a review of the Claude model, and a review of the pinned `anthropic` SDK. Each has a due date and instructions.
- **Weekly check:** `.github/workflows/weekly.yml` runs every Monday. It fails, and GitHub emails you, when anything is due within its warning window. The same run re-runs the tests against freshly installed libraries, to catch a new release breaking something. You can also start it from the Actions tab with **Run workflow**.
- **In the app:** the same items, plus "Garmin rejected the key", show as a banner at the top of the Food Log.
- **When you deal with one:** do what it says, then move its `due` date on and push.
- **Paused schedules:** GitHub pauses scheduled workflows after 60 days without repo activity. The Actions tab offers to re-enable it.

## Data notes

- **Food quality:** each item also records its food group, processing level (whole, processed, ultra-processed), fibre, free sugars, saturated fat, salt, fruit and veg portions and the plants in it. `backend/nutrition.py` turns these into exact measures against UK guidelines, and Claude writes the insights from them. Items logged before this existed have no detail and aren't counted. `PROMPT_VERSION` in `summary.py` is part of the analysis cache key, so bump it when the prompt changes.
- **Meals:** stored as parsed items, not photos. Your own typed or dictated words are kept as the meal's `note` and given to the Summary analysis as the most reliable part of the record.
- **Sleep:** `sleep_night.night_date` is the date you woke up, so night N lines up with food on day N.
- **Garmin field names:** the sleep fields are confirmed against a real response. The daily-summary, HRV and activity fields follow Garmin Connect's long-standing names but haven't been checked against real data yet. Run `python garmin_probe.py` with the key set once the watch has a few days of data. Raw responses are stored in `raw_json`, so a mapping fix doesn't need a refetch.
- **Backups:** none yet beyond what Turso provides. The live Turso database is the only copy of the food log.
