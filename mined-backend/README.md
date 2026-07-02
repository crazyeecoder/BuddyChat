# MINED — BuddyChat Backend

FastAPI + LangChain + Supabase + OpenRouter  
The RAG-powered AI therapist backend for MINED.

---

## Architecture

```
buddy.html (frontend)
    │
    ▼  POST /api/v1/chat
FastAPI (app/main.py)
    │
    ├── Crisis Detection (sentence-transformers, semantic scoring)
    │
    ├── Context Service (RAG layer)
    │   ├── user_profiles.profile_summary   ← compressed 90-day history
    │   ├── mood_logs (last 7 days)          ← from Mood Calendar
    │   ├── journal_entries (last 7 days)    ← from Journal feature
    │   └── self_care_capsules (latest)      ← for crisis surfacing
    │
    ├── LangChain → OpenRouter (mistral-7b-instruct)
    │   └── System prompt: CBT + DBT + ACT + injected context
    │
    └── Supabase
        ├── chat_turns (saved every message)
        └── Nightly job → user_profiles.profile_summary
```

---

## Setup

### 1. Clone & install

```bash
cd mined-backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment

```bash
cp .env.example .env
# Fill in OPENROUTER_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
```

**Get your free OpenRouter key:** https://openrouter.ai  
Default model: `mistralai/mistral-7b-instruct` (free tier)  
For crisis/deep sessions, switch to: `anthropic/claude-sonnet-4-5` (paid)

### 3. Supabase schema

1. Go to your Supabase project → SQL Editor
2. Run the contents of `schema.sql`
3. Go to Extensions → enable **pgvector**
4. Go to Authentication → enable **Email** provider

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

API will be live at: `http://localhost:8000`  
Docs at: `http://localhost:8000/docs`

---

## API Reference

### `POST /api/v1/chat`

Send a message to BuddyChat.

```json
{
  "user_id": "uuid-from-supabase-auth",
  "session_id": null,
  "message": "I've been feeling really low today",
  "history": []
}
```

Response:

```json
{
  "session_id": "new-uuid",
  "reply": "I hear you — that kind of heaviness is real...",
  "crisis": {
    "detected": false,
    "score": 0.21,
    "capsule_content": null
  },
  "tone_shift": false
}
```

**Crisis response** (when `crisis.detected = true`):
- Surface the Self Care Capsule UI in `buddy.html`
- Show the counsellor booking prompt
- `crisis.capsule_content` contains the user's own words to display

---

## Context Injection Layers

| Layer | Source | When |
|---|---|---|
| CBT/DBT/ACT frameworks | `app/prompts.py` | Every session (static) |
| Profile summary | `user_profiles.profile_summary` | Every session |
| Last 7 days mood | `mood_logs` | Every session |
| Last 7 days journal | `journal_entries` | Every session |
| Self Care Capsule | `self_care_capsules` | Every session (surfaced in crisis) |
| Live history | Client-sent `history[]` | Every turn |

---

## Nightly Summarisation

Runs automatically at 2:00 AM via APScheduler.  
Compresses chat turns older than 7 days → `user_profiles.profile_summary`.  
This keeps the context window lean and the AI smarter over time.

To trigger manually:
```python
import asyncio
from app.services.summariser import run_nightly_summarisation
asyncio.run(run_nightly_summarisation())
```

---

## Deploying to Railway

1. Push this folder to a GitHub repo
2. New Railway project → Deploy from GitHub
3. Add environment variables in Railway dashboard
4. Railway auto-detects Python; start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
