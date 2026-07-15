# RSS-to-Social Automation

Self-hosted Python script that reads AI/news RSS feeds, deduplicates articles in SQLite, generates a caption with MiniMax M3, and queues the newest unposted item in ContentStudio.

## Files

- `scripts/rss_social_poster.py`: main script
- `scripts/.env.example`: environment variable template
- `scripts/requirements.txt`: Python dependencies

## Requirements

- Python 3.10+
- MiniMax API key
- ContentStudio API key
- ContentStudio workspace ID
- ContentStudio social account IDs

## Setup

Clone the repo:

```bash
git clone https://github.com/happymeerkat001/-RSS-to-Social-Automation.git
cd -RSS-to-Social-Automation
```

Install dependencies:

```bash
cd scripts
python3 -m pip install -r requirements.txt
```

Create the environment file:

```bash
cp .env.example .env
```

Edit `scripts/.env` and fill in:

```bash
MINIMAX_API_KEY=...
CONTENTSTUDIO_API_KEY=cs_...
CONTENTSTUDIO_WORKSPACE_ID=...
CONTENTSTUDIO_ACCOUNT_IDS=id1,id2,id3
```

## Commands

All commands run from the `scripts/` directory so `.env` is loaded automatically.

Important:
- Run the Python script directly; do not run this workflow through `hermes`.
- `OPENAI_BASE_URL` is ignored by this project. The script explicitly sends caption generation requests to MiniMax at `https://api.minimaxi.chat/v1`.
- You must provide `MINIMAX_API_KEY` in `scripts/.env`.
- Use the project virtualenv interpreter so required packages (`feedparser`, `python-dotenv`, `openai`, `requests`) are available.

Dry run (prints caption without posting):

```bash
cd scripts
./venv/bin/python rss_social_poster.py --dry-run
```

Live run:

```bash
cd scripts
./venv/bin/python rss_social_poster.py
```
  
Custom database path:

```bash
cd scripts
./venv/bin/python rss_social_poster.py --db-path /path/to/posted.db
```

Syntax check:

```bash
cd scripts
./venv/bin/python -m py_compile rss_social_poster.py
```

## Cron

Run every Monday, Wednesday, and Friday at 9:00 AM:

```cron
0 9 * * 1,3,5 cd "/path/to/-RSS-to-Social-Automation/scripts" && ./venv/bin/python rss_social_poster.py >> ~/.rss_social_poster/run.log 2>&1
```

## ContentStudio API values

API key:

Find it in `Settings -> API Keys`.

Workspace ID:

List workspaces:

```bash
curl -H "X-API-Key: $CONTENTSTUDIO_API_KEY" https://api.contentstudio.io/api/v1/workspaces
```

Account IDs:

List accounts:

```bash
curl -H "X-API-Key: $CONTENTSTUDIO_API_KEY" https://api.contentstudio.io/api/v1/workspaces/$CONTENTSTUDIO_WORKSPACE_ID/accounts
```

## Behavior

1. Fetches articles from TechCrunch AI, VentureBeat AI, and Social Media Today.
2. Filters out URLs already stored in `~/.rss_social_poster/posted.db`.
3. Picks the newest remaining article.
4. Generates a caption with MiniMax M3.
5. Queues the post in ContentStudio.
6. Stores the posted URL in SQLite after a successful queue operation.
