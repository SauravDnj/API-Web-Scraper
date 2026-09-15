# Serper Data Scraper

A multi-user web app with **one tab per Serper.dev API**. Each user logs in with email and password,
adds **their own Serper API key**, and runs scrape jobs. Each tab has its own form, job history,
results table, filters, and CSV / Excel download.

| Tab | API | Input | Credits |
|---|---|---|---|
| 🔎 Search | `/search` | categories/keywords × locations | 1 / page (10 results) |
| 🖼️ Images | `/images` | keywords (+ optional locations) | 1 / page |
| 🎬 Videos | `/videos` | keywords (+ optional locations) | 1 / page |
| 📍 Places | `/places` | categories/keywords × locations | 1 / page (10 places) |
| 🗺️ Maps | `/maps` | categories/keywords × locations, nearby area grid | 3 / page (20 places) |
| ⭐ Reviews | `/reviews` | CID / Place ID / FID, or import from a Maps/Places job | 1 / page (20 reviews) |
| 📰 News | `/news` | keywords (+ optional locations, time range) | 1 / page |
| 🛒 Shopping | `/shopping` | product keywords | 2 / page (40 products) |
| 🔍 Image Search (Lens) | `/lens` | image URLs, or import from an Images job | 3 / image |
| 🎓 Scholar | `/scholar` | keywords | 1 / page |
| 📜 Patents | `/patents` | keywords | 1 / page |
| ⌨️ Autocomplete | `/autocomplete` | seed keywords (+ optional locations) | 1 / query |
| 🌐 Webpage | `scrape.serper.dev` | URLs, or import websites from a Maps/Places/Search job | ~2 / page |

## Accounts

- **Create account** with email and password. Passwords are stored as PBKDF2-SHA256 hashes.
- **First login**: the app asks for your **Serper API key**, checks it with Serper, and saves it encrypted in your user JSON.
- **Settings** (⚙ in the top bar): replace the API key at any time, change your password, see when your session ends.
- **Sessions** last **24 hours** from login, then you log in again.
- Every user has their own API key and only sees their own jobs.

## Storage (JSON)

| Where the app runs | Storage |
|---|---|
| Your PC | Plain JSON files in `data/` |
| Vercel | The same JSON documents in a private **Vercel Blob** store (jobs are gzip-compressed) |

```
users/<sha256(email)>.json          account: email, name, password hash, encrypted API key
accounts/<user-id>/jobs.json        that user's job list
accounts/<user-id>/jobs/<job>.json  one job: settings, rows, log, progress (for resume)
```

## How jobs run

The **browser runs the job**: it sends one small request per Serper API call, so it works within Vercel's
short function time limits. That means:

- **Keep the tab open** while a job runs. Several tabs (tools) can run at the same time.
- Progress is **saved automatically** (every 15 s locally, every 3 min on Vercel) and when the job ends or you press Stop.
- If you close the tab or lose your connection, open the job again and click **▶ Resume**. It continues from the last save.
- When a job stops at its lead limit or credit budget, **▶ Continue** lets you raise the limit and keep going.
- CSV and Excel files are generated in the browser from the rows currently shown (after filters).

## Run on your PC

Double-click **`run.bat`**, or:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open http://127.0.0.1:5000 and create an account. The first account created locally also gets the jobs
from the earlier single-user version (`data/jobs/`).

## Deploy on Vercel

1. Import the GitHub repo in Vercel (framework: *Other*; Vercel detects the Flask `app` in `app.py`).
2. **Storage → Create → Blob**, choose **Private** access, and connect it to the project. This adds `BLOB_READ_WRITE_TOKEN`.
3. **Settings → Environment Variables**: add `SECRET_KEY` = a long random string. It signs sessions and encrypts API keys; keep it the same or everyone must re-enter their key.
4. Deploy. `vercel.json` sets the Mumbai region (`bom1`) and a 60 s function limit.

Vercel Blob **Hobby (free) limits**: 1 GB storage, 2,000 writes/lists and 10,000 reads per month. A job typically uses
3 - 10 writes, depending on how long it runs. If the limit is exceeded, Blob is blocked until the month resets, so upgrade to Pro for heavy use.
Request bodies are limited to 4.5 MB, so very large Webpage jobs save shortened page text online.

## Big Maps jobs: 1,000 / 5,000 / 10,000+ leads

Google returns at most ~20 places per page and only a few pages per search, so the Maps tab has a **Nearby area grid**:

1. It first runs the normal search, e.g. `Gyms in Satellite, Ahmedabad, Gujarat`, and uses the GPS center Google returns.
2. It then searches the plain category (`Gyms`) at GPS points spiralling outward from that center, nearest first.
3. It keeps going until the **lead limit** is reached, the **credit budget** is used up, or the **radius** is covered.

| Setting | Meaning |
|---|---|
| Nearby radius | How far from each location to search (1 - 50 km) |
| Grid density | Wide = a point every 4 km, Normal = every 2 km, Dense = every 1 km |
| Pages per grid point | 1 - 5 pages (20 places each); paging stops early when nothing new appears |
| Lead limit | Stop when this many unique leads are collected |
| Credit budget | Stop before spending more than this many credits |

Rough cost: after removing duplicates, expect **10 - 15 unique leads per Maps page** (3 credits) in a busy city,
so 1,000 leads ≈ 200 - 300 credits and 10,000 leads ≈ 2,000 - 3,000+ credits.

## Lead workflow

1. **Maps**: scrape businesses by category and area.
2. **Reviews**: import the top places from that Maps job and pull their reviews.
3. **Webpage**: import the websites from that Maps job to find emails, phones, social links and schema.org address.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app: pages, auth, tool steps, job saving |
| `auth.py` | Users, password hashing, API key encryption, 24 h sessions |
| `storage.py` | JSON storage: local files or private Vercel Blob |
| `tools.py` | One class per Serper API: inputs, one-call steps, normalizing, columns, filters |
| `serper_client.py` | Serper HTTP client with retries |
| `templates/` | Login / signup, settings, main app |
| `public/static/` | CSS, `app.js` (job runner, table, exports), `account.js`, ExcelJS |
| `vercel.json` | Vercel region and function settings |

> Tip: to work in Excel, use the Excel download. Excel can misread CSV phone numbers that start with `+`.
