# app/services/crisis_service.py
# ─────────────────────────────────────────────────────────────────────────────
# MINED — Crisis Detection Service
#
# Uses semantic similarity (sentence-transformers), NOT keyword matching.
# Keyword matching creates false positives that destroy user trust.
#
# Flow:
#   1. Embed the incoming user message
#   2. Compute cosine similarity against crisis reference phrase embeddings
#   3. Compute similarity against non-crisis reference phrases
#   4. Score = (max crisis sim) - 0.5 * (max non-crisis sim)
#   5. If score > CRISIS_SCORE_THRESHOLD → flag + fetch Self Care Capsule
# ─────────────────────────────────────────────────────────────────────────────

import os
import numpy as np
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from app.prompts import CRISIS_REFERENCE_PHRASES, NON_CRISIS_REFERENCE_PHRASES

CRISIS_THRESHOLD = float(os.getenv("CRISIS_SCORE_THRESHOLD", "0.72"))


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    return SentenceTransformer("all-MiniLM-L6-v2")


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


_crisis_embeddings: list[np.ndarray] | None = None
_non_crisis_embeddings: list[np.ndarray] | None = None


def _get_reference_embeddings():
    global _crisis_embeddings, _non_crisis_embeddings
    if _crisis_embeddings is None:
        model = _load_model()
        _crisis_embeddings = model.encode(CRISIS_REFERENCE_PHRASES, normalize_embeddings=True)
        _non_crisis_embeddings = model.encode(NON_CRISIS_REFERENCE_PHRASES, normalize_embeddings=True)
    return _crisis_embeddings, _non_crisis_embeddings


def score_message(message: str) -> float:
    model = _load_model()
    crisis_refs, non_crisis_refs = _get_reference_embeddings()

    msg_embedding = model.encode([message], normalize_embeddings=True)[0]

    crisis_sims = [_cosine_sim(msg_embedding, ref) for ref in crisis_refs]
    max_crisis = max(crisis_sims)

    non_crisis_sims = [_cosine_sim(msg_embedding, ref) for ref in non_crisis_refs]
    max_non_crisis = max(non_crisis_sims)

    score = max_crisis - 0.4 * max_non_crisis
    return max(0.0, min(1.0, score))


def is_crisis(message: str) -> tuple[bool, float]:
    score = score_message(message)
    return score >= CRISIS_THRESHOLD, score