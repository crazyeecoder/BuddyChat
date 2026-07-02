# app/models/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ChatMessage(BaseModel):
    role: str                  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None   # None = start new session
    message: str
    history: List[ChatMessage] = []    # full conversation so far (client manages this)


class CrisisFlag(BaseModel):
    detected: bool
    score: float                       # 0.0 – 1.0
    capsule_content: Optional[str]     # user's own Self Care Capsule message if found


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    crisis: CrisisFlag
    tone_shift: bool                   # did we shift to crisis mode?


class MoodEntry(BaseModel):
    user_id: str
    score: int = Field(..., ge=1, le=10)
    emoji: Optional[str] = None
    note: Optional[str] = None
    logged_at: datetime = Field(default_factory=datetime.utcnow)


class JournalEntry(BaseModel):
    user_id: str
    content: str
    prompt_used: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserContext(BaseModel):
    """
    The assembled context block injected into the system prompt.
    Built by ContextService before every session start.
    """
    user_id: str
    display_name: Optional[str]
    profile_summary: Optional[str]        # compressed 90-day history
    recent_mood_logs: List[dict]           # last 7 days
    recent_journal_entries: List[dict]     # last 7 days
    self_care_capsule: Optional[str]       # latest capsule message
    mood_trend: Optional[str]             # "stable" | "declining" | "improving"
