"""Detects latent user needs from linguistic and behavioural signals."""
from __future__ import annotations
import re
from typing import Optional

HEDGING = ["i guess","maybe","probably","i think","not sure","kind of","sort of","i suppose","perhaps","might"]
CONCERN_THRESHOLD = 3
SEARCH_WINDOW_DAYS = 7
_SKIP_WORDS = {"this","that","what","when","where","which","about","from","with","have","been","will","would","could","should","they","their","there","here","just","some","like","make","open","close","tell","show","want"}

def detect_hedging(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in HEDGING)

def get_topic_frequency(topic: str) -> int:
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=SEARCH_WINDOW_DAYS)).isoformat()
    try:
        from memory.conversation_log import search_interactions
        hits = search_interactions(topic, limit=100, all_sessions=True)
        return len([h for h in hits if h.timestamp[:10] >= since])
    except Exception:
        return 0

def analyse(user_text: str) -> Optional[str]:
    text = user_text.strip()
    if not text or len(text) < 10:
        return None
    if detect_hedging(text):
        return None
    words = re.findall(r'\b[A-Za-z]{4,}\b', text)
    for word in words[:5]:
        if word.lower() in _SKIP_WORDS:
            continue
        freq = get_topic_frequency(word)
        if freq >= CONCERN_THRESHOLD:
            return (f"Sir, I have noticed you have mentioned '{word}' {freq} times "
                    f"this week without a resolution. Would you like me to create a "
                    f"dedicated plan or reminder for it?")
    return None
