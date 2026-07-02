# debug_crisis_score.py
# Run this from inside mined-backend (with venv active) to see REAL scores.
# Usage: python debug_crisis_score.py
#
# This prints the raw max_crisis, max_non_crisis, and final score for a
# set of test messages — so we tune the threshold based on actual numbers,
# not guesses.

from app.services.crisis_service import _load_model, _get_reference_embeddings, _cosine_sim
import numpy as np

TEST_MESSAGES = [
    ("I don't see the point in anything anymore. I just want it all to stop.", "SHOULD BE CRISIS"),
    ("I want to kill myself", "SHOULD BE CRISIS (near-exact match)"),
    ("I have an exam tomorrow and I just know I'm going to fail.", "SHOULD NOT BE CRISIS"),
    ("I'm so stressed about this exam, I can't think straight.", "SHOULD NOT BE CRISIS"),
    ("I feel really low lately and I don't know why.", "BORDERLINE - low mood, not crisis"),
]

model = _load_model()
crisis_refs, non_crisis_refs = _get_reference_embeddings()

print(f"{'MESSAGE':<60} {'MAX_CRISIS':<12} {'MAX_NONCRISIS':<14} {'SCORE':<8} EXPECTED")
print("-" * 120)

for message, expected in TEST_MESSAGES:
    msg_embedding = model.encode([message], normalize_embeddings=True)[0]

    crisis_sims = [_cosine_sim(msg_embedding, ref) for ref in crisis_refs]
    max_crisis = max(crisis_sims)

    non_crisis_sims = [_cosine_sim(msg_embedding, ref) for ref in non_crisis_refs]
    max_non_crisis = max(non_crisis_sims)

    score_current_formula = max(0.0, min(1.0, max_crisis - 0.4 * max_non_crisis))

    print(f"{message[:58]:<60} {max_crisis:<12.3f} {max_non_crisis:<14.3f} {score_current_formula:<8.3f} {expected}")

print("\nIf 'SHOULD BE CRISIS' rows score below 0.72, the threshold or formula needs adjusting.")
print("Try lowering CRISIS_SCORE_THRESHOLD in .env based on these real numbers,")
print("or reduce the 0.4 penalty multiplier in crisis_service.py's score_message().")
