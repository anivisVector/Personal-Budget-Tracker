"""Per-user category suggestions with a small multinomial Naive Bayes model.

Each user gets a model trained only on their own transactions, so suggestions can
never reveal another user's data. The model is tiny (word counts per category),
trains in milliseconds on a few thousand rows, and needs no heavy ML libraries,
which keeps the serverless bundle small and cold starts fast.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

_TOKEN_RE = re.compile(r'[a-z0-9]+')
_STOPWORDS = frozenset(
    'a an and at by for from in of on or the to with my me i paid spent bought got rs inr rupees'.split()
)
MIN_EXAMPLES = 5
MIN_CLASSES = 2
TRAINING_ROWS = 2000
CACHE_TTL_SECONDS = 120


def tokenize(text: str) -> list[str]:
    words = [w for w in _TOKEN_RE.findall((text or '').lower()) if len(w) > 1 and w not in _STOPWORDS and not w.isdigit()]
    # Adjacent word pairs help with phrases like "electricity bill" vs "phone bill".
    return words + [f'{a}_{b}' for a, b in zip(words, words[1:], strict=False)]


@dataclass
class Model:
    priors: dict[str, float]
    word_counts: dict[str, Counter]
    totals: dict[str, int]
    vocab_size: int
    category_types: dict[str, str]

    def predict(self, text: str, type_: str | None = None) -> tuple[str, float] | None:
        tokens = tokenize(text)
        if not tokens:
            return None
        known = [t for t in tokens if any(t in counts for counts in self.word_counts.values())]
        if not known:
            return None  # nothing we've seen before; don't guess

        scores: dict[str, float] = {}
        for label, prior in self.priors.items():
            if type_ and self.category_types.get(label) != type_:
                continue
            denom = self.totals[label] + self.vocab_size
            counts = self.word_counts[label]
            scores[label] = math.log(prior) + sum(math.log((counts.get(t, 0) + 1) / denom) for t in known)
        if not scores:
            return None

        # Softmax over log scores gives a comparable confidence in [0, 1].
        best = max(scores.values())
        exp_scores = {k: math.exp(v - best) for k, v in scores.items()}
        norm = sum(exp_scores.values())
        label = max(exp_scores, key=exp_scores.get)
        return label, exp_scores[label] / norm


def train(rows: list[dict]) -> Model | None:
    """rows: dicts with title, description, category, type (one user's history)."""
    word_counts: dict[str, Counter] = defaultdict(Counter)
    doc_counts: Counter = Counter()
    category_types: dict[str, str] = {}
    vocab: set[str] = set()
    examples = 0

    for row in rows:
        label = (row.get('category') or '').strip()
        text = f"{row.get('title') or ''} {row.get('description') or ''}"
        # Titles that merely repeat the category teach nothing (and leak the label).
        tokens = [t for t in tokenize(text) if t != label.lower()]
        if not label or not tokens:
            continue
        examples += 1
        doc_counts[label] += 1
        category_types.setdefault(label, row.get('type') or '')
        word_counts[label].update(tokens)
        vocab.update(tokens)

    if examples < MIN_EXAMPLES or len(doc_counts) < MIN_CLASSES:
        return None
    total_docs = sum(doc_counts.values())
    return Model(
        priors={label: count / total_docs for label, count in doc_counts.items()},
        word_counts=dict(word_counts),
        totals={label: sum(c.values()) for label, c in word_counts.items()},
        vocab_size=len(vocab),
        category_types=category_types,
    )


class ModelCache:
    """Short-lived per-instance cache so typing in a field doesn't retrain each keystroke."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS, max_users: int = 256):
        self.ttl = ttl
        self.max_users = max_users
        self._items: dict[int, tuple[float, Model | None]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: int, loader) -> Model | None:
        now = time.monotonic()
        with self._lock:
            hit = self._items.get(user_id)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        model = train(loader())
        with self._lock:
            if len(self._items) >= self.max_users:
                self._items.pop(next(iter(self._items)))
            self._items[user_id] = (now, model)
        return model

    def invalidate(self, user_id: int) -> None:
        with self._lock:
            self._items.pop(user_id, None)


cache = ModelCache()


def suggest(db, user_id: int, text: str, type_: str | None = None, min_confidence: float = 0.45) -> dict | None:
    def load():
        return db.all(
            'SELECT title, description, category, type FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?',
            (user_id, TRAINING_ROWS),
        )

    model = cache.get(user_id, load)
    if model is None:
        return None
    result = model.predict(text, type_)
    if not result or result[1] < min_confidence:
        return None
    label, confidence = result
    return {'category': label, 'type': model.category_types.get(label), 'confidence': round(confidence, 3)}
