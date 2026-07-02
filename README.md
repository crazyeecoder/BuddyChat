# BuddyChat

BuddyChat is an AI mental wellness companion and the conversational layer behind **MINED** (Mental Intelligence & Emotional Design).

The goal wasn't to build another chatbot that forgets everything once you close the tab. BuddyChat is designed to remember context, adapt its responses based on emotional state, and provide support using evidence-based therapeutic frameworks like CBT, DBT and ACT.

---

## What it does

Every message goes through three stages before reaching the language model.

### 1. Crisis Detection

The message is embedded using `all-MiniLM-L6-v2` and compared against a curated set of crisis and non-crisis reference phrases.

Instead of a simple yes/no classification, BuddyChat produces a continuous distress score.

---

### 2. Knowledge Retrieval (RAG)

The same message is used to search a knowledge base stored in Supabase with pgvector.

Relevant CBT, ACT and communication resources are retrieved and injected into the prompt so responses stay grounded rather than relying only on the LLM.

---

### 3. Response Routing

Depending on the distress score, BuddyChat switches between three response styles.

| Score | Mode |
|--------|------|
| < 0.25 | Companion Mode |
| 0.25 – 0.65 | Therapeutic Mode |
| ≥ 0.65 | Crisis Mode |

This allows the conversation to stay casual when appropriate while becoming more structured and safety-focused when needed.

---

# Tech Stack

- FastAPI
- Python 3.11
- Supabase (PostgreSQL + pgvector)
- Sentence Transformers
- LangChain
- OpenRouter
- APScheduler
- Vanilla HTML/CSS/JavaScript

---

# Knowledge Base

The retrieval pipeline currently uses three sources.

| Source | Purpose |
|---------|---------|
| CBT Therapist Manual | Cognitive restructuring, behavioural activation, thought records |
| ACT Knowledge Base | Acceptance, values, grounding techniques |
| Language Resource | Therapist communication patterns collected from research papers |

These documents are chunked, embedded and stored inside Supabase using pgvector.

---

# Crisis Detection

The crisis score is calculated using semantic similarity.

```python
score =
cosine(message, crisis_examples)
-
cosine(message, non_crisis_examples)
```

The current setup uses:

- 75 crisis reference phrases
- 40 non-crisis reference phrases

Example outputs:

| Message | Score |
|----------|------:|
| "I want to kill myself." | ~0.84 |
| "I just want it all to stop." | ~0.60 |
| "I'm stressed about my exams." | ~0.10 |

---

# Project Structure

```
BuddyChat/
│
├── app/
│   ├── main.py
│   ├── prompts.py
│   └── services/
│       ├── chat_service.py
│       ├── context_service.py
│       ├── crisis_service.py
│       ├── summariser.py
│       └── supabase_client.py
│
├── papers/
├── ingest.py
├── debug_crisis_score.py
├── schema.sql
├── requirements.txt
├── .env.example
└── buddy.html
```

---

# Getting Started

Clone the repository.

```bash
git clone https://github.com/crazyeecoder/BuddyChat.git
cd BuddyChat
```

Create a virtual environment.

```bash
python -m venv venv
venv\Scripts\activate
```

Install dependencies.

```bash
pip install -r requirements.txt
```

Copy the environment file.

```bash
cp .env.example .env
```

Fill in:

```
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
```

Run the database schema inside Supabase.

Ingest the knowledge base.

```bash
python ingest.py
```

Start the backend.

```bash
uvicorn app.main:app --reload --port 8000
```

Open `buddy.html` using Live Server (or any local web server).

---

# Environment Variables

```
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
CRISIS_SCORE_THRESHOLD=0.65
CORS_ORIGINS=*
```

---

# About MINED

BuddyChat is the conversational component of **MINED**.

MINED extends BuddyChat with features like:

- Mood tracking
- Reflective journaling
- Self-Care Capsule
- Counsellor escalation
- Long-term emotional memory

Together they aim to provide a more continuous and personalised mental wellness experience rather than isolated conversations.
