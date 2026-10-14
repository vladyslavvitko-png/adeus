# Adeus

A local Gmail reading room with DeepSeek classification, reply detection, Gmail
draft creation, Notion task extraction, and scheduled full-account analysis. Adeus
never sends, deletes, or marks email as read.

## Setup

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** for the same project that issued your OAuth client.
2. On the OAuth consent screen, add your Google account as a **test user**.
3. Copy `.env.example` to `.env` and fill in the API keys and Google OAuth client values.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and click **Connect Gmail**.
Approve both read and compose access: compose access is used only to save reply
drafts, never to send them.

Open **Settings** for filing categories, the Task Extraction Prompt, and the scheduler.
API keys and the Google OAuth client live in gitignored `.env`. The Gmail session
token remains in gitignored `token.json`.

## Full analysis

Choose a date and click **Run Full Analysis**. One background run will:

- download every thread in the selected range and repopulate the tray;
- compare Gmail message IDs with the local analysis store and skip DeepSeek
  analysis for unchanged, successfully analyzed threads;
- classify each thread with DeepSeek using plain text;
- extract work tasks and create Notion pages;
- perform the reply scan and update **Needs your reply**;
- create Gmail reply drafts where needed.

The dashboard shows aggregate category totals. Individual thread details appear
only for threads that need your reply.

## Daily scheduler

The scheduler under **Settings → Schedule** is disabled by default. Enable it,
choose a local 24-hour time, set a lookback period in hours, and click **Save schedule**. The default is
07:00 with a 24-hour lookback. Scheduled and manual runs share the same full
analysis code and appear in **Past runs**.

The in-app scheduler runs only while the FastAPI process is running. For reliable
daily execution on macOS, install the included LaunchAgent after stopping any
manually started server:

```bash
python scripts/adeus-launch-agent.py install
python scripts/adeus-launch-agent.py status
```

The LaunchAgent starts Adeus at login and keeps it running in the background.
To remove it:

```bash
python scripts/adeus-launch-agent.py uninstall
```

Its output is written to `adeus-launch-agent.log`. Schedule configuration and
aggregate run history are stored locally in gitignored `scheduler.json`.
