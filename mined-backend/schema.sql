-- ─────────────────────────────────────────────────────────────────────────────
-- MINED — Supabase Schema
-- Run this in the Supabase SQL editor to set up all tables.
-- Enable the pgvector extension first (Extensions tab → pgvector → Enable).
-- ─────────────────────────────────────────────────────────────────────────────

-- Enable pgvector (for future semantic search over journal entries)
create extension if not exists vector;

-- ── User Profiles ─────────────────────────────────────────────────────────────
-- One row per user. profile_summary is the compressed longitudinal memory
-- written by the nightly summarisation job.

create table if not exists user_profiles (
  user_id           uuid primary key references auth.users(id) on delete cascade,
  display_name      text,
  profile_summary   text,                    -- compressed 90-day history
  summary_updated_at timestamptz,
  onboarding_done   boolean default false,
  data_consent      boolean default false,   -- DPDP Act compliance
  counsellor_consent boolean default false,  -- explicit consent for counsellor data sharing
  created_at        timestamptz default now()
);

-- ── Mood Logs ────────────────────────────────────────────────────────────────
-- Daily mood check-ins from the Mood Calendar feature.

create table if not exists mood_logs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  score       smallint not null check (score between 1 and 10),
  emoji       text,
  note        text,
  logged_at   timestamptz default now()
);

create index if not exists mood_logs_user_date
  on mood_logs (user_id, logged_at desc);

-- ── Journal Entries ──────────────────────────────────────────────────────────
-- Free-text journal entries. embedding column for pgvector semantic search
-- (populated by a background job using sentence-transformers).

create table if not exists journal_entries (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  content      text not null,
  prompt_used  text,
  embedding    vector(384),               -- all-MiniLM-L6-v2 dimension
  created_at   timestamptz default now()
);

create index if not exists journal_entries_user_date
  on journal_entries (user_id, created_at desc);

-- pgvector index for semantic search over journal entries
create index if not exists journal_entries_embedding
  on journal_entries using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- ── Self Care Capsules ───────────────────────────────────────────────────────
-- Messages users write to their future selves.
-- Surfaced by BuddyChat when crisis is detected.

create table if not exists self_care_capsules (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  content      text not null,
  unlock_at    timestamptz,              -- optional scheduled unlock date
  surfaced_at  timestamptz,             -- when it was auto-surfaced in crisis
  created_at   timestamptz default now()
);

create index if not exists capsules_user
  on self_care_capsules (user_id, created_at desc);

-- ── Chat Sessions ────────────────────────────────────────────────────────────
-- One row per conversation session (multiple turns).

create table if not exists chat_sessions (
  session_id   uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

create index if not exists chat_sessions_user
  on chat_sessions (user_id, updated_at desc);

-- ── Chat Turns ───────────────────────────────────────────────────────────────
-- Individual message pairs within a session.
-- crisis_score and crisis_detected power the safety audit trail.
-- summarised flag drives the nightly summarisation job.

create table if not exists chat_turns (
  id                uuid primary key default gen_random_uuid(),
  session_id        uuid not null references chat_sessions(session_id) on delete cascade,
  user_id           uuid not null references auth.users(id) on delete cascade,
  user_message      text not null,
  assistant_reply   text not null,
  crisis_score      real,                -- 0.0 – 1.0 semantic score
  crisis_detected   boolean default false,
  summarised        boolean default false,
  created_at        timestamptz default now()
);

create index if not exists chat_turns_session
  on chat_turns (session_id, created_at asc);

create index if not exists chat_turns_unsummarised
  on chat_turns (user_id, summarised, created_at)
  where summarised = false;

-- ── Counsellor Bookings ──────────────────────────────────────────────────────
-- Triggered explicitly by the user. NEVER auto-created.

create table if not exists counsellor_bookings (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  counsellor_id   uuid,                  -- references counsellors table (add later)
  booked_at       timestamptz default now(),
  session_at      timestamptz,
  session_summary text,                  -- structured summary shared with counsellor
  notes_synced    boolean default false  -- counsellor notes synced back to MINED
);

-- ── Row Level Security ───────────────────────────────────────────────────────
-- Users can only see their own data. Always.

alter table user_profiles       enable row level security;
alter table mood_logs           enable row level security;
alter table journal_entries     enable row level security;
alter table self_care_capsules  enable row level security;
alter table chat_sessions       enable row level security;
alter table chat_turns          enable row level security;
alter table counsellor_bookings enable row level security;

-- Policies: users see only their own rows
create policy "own data only" on user_profiles
  for all using (auth.uid() = user_id);

create policy "own data only" on mood_logs
  for all using (auth.uid() = user_id);

create policy "own data only" on journal_entries
  for all using (auth.uid() = user_id);

create policy "own data only" on self_care_capsules
  for all using (auth.uid() = user_id);

create policy "own data only" on chat_sessions
  for all using (auth.uid() = user_id);

create policy "own data only" on chat_turns
  for all using (auth.uid() = user_id);

create policy "own data only" on counsellor_bookings
  for all using (auth.uid() = user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- After running this schema:
-- 1. Go to Supabase → Extensions → enable pgvector
-- 2. Go to Supabase → Authentication → enable Email provider
-- 3. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in your .env
-- ─────────────────────────────────────────────────────────────────────────────
