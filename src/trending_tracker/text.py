from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9-]{2,}")
STOPWORDS = {
    "about", "above", "after", "again", "against", "also", "among", "and", "any", "are",
    "because", "been", "before", "being", "between", "both", "but", "can", "could", "data",
    "does", "each", "from", "further", "has", "have", "having", "into", "its", "may", "method",
    "model", "models", "more", "most", "not", "our", "over", "paper", "results", "show", "such",
    "than", "that", "the", "their", "then", "there", "these", "they", "this", "those", "through",
    "under", "using", "via", "was", "were", "which", "while", "with", "within", "would", "your",
    "new", "based", "propose", "proposed", "approach", "task", "performance", "work", "use",
}


def tokenize(value: str) -> list[str]:
    return [token for token in TOKEN_PATTERN.findall(value.lower()) if token not in STOPWORDS]


def tfidf_vectors(documents: Iterable[str]) -> tuple[list[dict[str, float]], dict[str, float]]:
    tokenized = [tokenize(document) for document in documents]
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))
    count = max(len(tokenized), 1)
    idf = {term: math.log((1 + count) / (1 + frequency)) + 1 for term, frequency in document_frequency.items()}
    vectors = [normalise({term: frequency * idf[term] for term, frequency in Counter(tokens).items()}) for tokens in tokenized]
    return vectors, idf


def normalise(vector: dict[str, float]) -> dict[str, float]:
    magnitude = math.sqrt(sum(value * value for value in vector.values()))
    return {key: value / magnitude for key, value in vector.items()} if magnitude else {}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def mean_vector(vectors: list[dict[str, float]]) -> dict[str, float]:
    if not vectors:
        return {}
    total: Counter[str] = Counter()
    for vector in vectors:
        total.update(vector)
    return normalise({key: value / len(vectors) for key, value in total.items()})

