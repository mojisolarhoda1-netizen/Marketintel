"""Transparent, offline sentiment scoring for English + Nigerian Pidgin/slang.

Lexicon-based on purpose: every score can be explained word by word, it costs
nothing to run, and it works without internet. Accuracy is moderate — treat it
as a signal. A local or paid model can replace it later through the AI engine.
"""
from __future__ import annotations

import re

POSITIVE = {
    # English
    "good": 1, "great": 2, "excellent": 2, "amazing": 2, "awesome": 2, "love": 2, "loved": 2, "loves": 2,
    "best": 2, "happy": 1, "impressive": 2, "reliable": 1, "fast": 1, "affordable": 1, "recommend": 2,
    "recommended": 2, "satisfied": 1, "smooth": 1, "win": 1, "wins": 1, "won": 1, "growth": 1, "grow": 1,
    "grows": 1, "profit": 1, "profits": 1, "record": 1, "expands": 1, "expansion": 1, "launch": 1,
    "launches": 1, "partnership": 1, "jobs": 1, "hires": 1, "award": 2, "awarded": 2, "innovative": 1, "strong": 1, "surge": 1,
    "boost": 1, "boosts": 1, "success": 2, "successful": 2, "trusted": 1, "trust": 1, "quality": 1, "thank": 1,
    "thanks": 1, "helpful": 1, "beautiful": 1, "delicious": 2, "perfect": 2, "easy": 1, "secure": 1,
    "deepen": 1, "strengthen": 1, "gain": 1, "gains": 1, "praise": 2, "praises": 2,
    # Nigerian Pidgin / slang
    "sweet": 1, "correct": 1, "sabi": 1, "enjoyment": 1, "enjoy": 1, "gbedu": 1, "fire": 1, "jara": 1,
    "baddest": 2, "oshey": 2, "na_correct": 2, "e_sweet": 2, "dey_try": 1,
}

NEGATIVE = {
    # English
    "bad": -1, "poor": -1, "worst": -2, "terrible": -2, "awful": -2, "hate": -2, "scam": -3, "fraud": -3,
    "fake": -2, "slow": -1, "expensive": -1, "overpriced": -2, "delay": -1, "delayed": -1, "delays": -1,
    "complaint": -1, "complaints": -1, "refund": -1, "broken": -1, "failed": -1, "fails": -1, "failure": -2,
    "loss": -1, "losses": -1, "decline": -1, "declines": -1, "drop": -1, "drops": -1, "fall": -1, "falls": -1,
    "lawsuit": -2, "sued": -2, "fine": 0, "fined": -2, "penalty": -2, "ban": -2, "banned": -2, "outage": -2,
    "hack": -2, "hacked": -2, "breach": -2, "disappointed": -2, "disappointing": -2, "rude": -2,
    "useless": -2, "unreliable": -2, "crisis": -2, "debt": -1, "shutdown": -2, "layoffs": -2, "probe": -1,
    "suspended": -2, "sanction": -2, "sanctions": -2, "rip-off": -3, "ripoff": -3, "stolen": -2, "theft": -2,
    "backlash": -2, "recall": -2, "recalls": -2, "complain": -1, "hike": -1, "hikes": -1, "raises_prices": -1, "price_hike": -2, "nonsense": -2, "rubbish": -2, "insult": -2, "boycott": -2,
    # Nigerian Pidgin / slang
    "wahala": -2, "yeye": -2, "k-leg": -2, "kleg": -2, "mumu": -2, "vex": -1, "dey_vex": -2, "nawa": -1,
    "e_no_work": -2, "no_network": -2, "nepa": -1, "shege": -2, "gbege": -2, "japa": 0,
}

PHRASES = {
    "na correct": "na_correct", "e sweet": "e_sweet", "dey try": "dey_try",
    "dey vex": "dey_vex", "raises prices": "raises_prices", "price hike": "price_hike", "e no work": "e_no_work", "no network": "no_network",
}

NEGATORS = {"not", "no", "never", "isn't", "wasn't", "don't", "doesn't", "didn't", "no be", "nor", "hardly", "without"}
INTENSIFIERS = {"very": 1.5, "really": 1.3, "extremely": 1.8, "so": 1.3, "too": 1.3, "well": 1.2, "die": 1.5}

_WORD = re.compile(r"[a-z][a-z'\-_]*")


def tokenize(text: str) -> list[str]:
    t = text.lower()
    for phrase, token in PHRASES.items():
        t = t.replace(phrase, token)
    t = t.replace("no be", "no_be")
    return _WORD.findall(t)


def score(text: str | None) -> tuple[float, str, list[str]]:
    """Return (score in -1..1, label, words that drove it)."""
    if not text:
        return 0.0, "neutral", []
    toks = tokenize(text)
    total, hits = 0.0, []
    for i, w in enumerate(toks):
        val = POSITIVE.get(w) or NEGATIVE.get(w)
        if not val:
            continue
        window = toks[max(0, i - 3):i]
        mult = 1.0
        for p in window:
            mult *= INTENSIFIERS.get(p, 1.0)
        if any(p in NEGATORS or p == "no_be" for p in window):
            mult *= -0.9
        total += val * mult
        hits.append(w.replace("_", " "))
    if not hits:
        return 0.0, "neutral", []
    norm = total / (abs(total) + 4)          # squash into -1..1, stable for short texts
    norm = round(max(-1.0, min(1.0, norm)), 3)
    label = "positive" if norm >= 0.15 else "negative" if norm <= -0.15 else "neutral"
    return norm, label, hits
