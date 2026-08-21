from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from .discover import summary_is_boilerplate
from .io import dated_files, read_json, utc_now, write_json
from .text import cosine, normalise, tokenize


def link_topics(
    data_dir: Path,
    *,
    similarity_threshold: float = 0.2,
    lookback_days: int = 30,
    label_weight: float = 0.5,
    centroid_alpha: float = 0.3,
) -> dict:
    """Assign stable topic IDs across days.

    Similarity combines two channels: centroid cosine (what the papers say) and label
    cosine over name+keyword tokens (what the topic is about). Small-sample TF-IDF
    centroids of the same theme on different days rarely exceed ~0.2 cosine, so content
    alone cannot carry an absolute threshold; the label channel disambiguates theme
    while the content channel keeps dissimilar paper sets from merging on a name.

    Matching is many-to-one: several same-day topics may map to one registry topic.
    Day-to-day the discovery stage cuts the corpus at different granularities, and
    forcing 1:1 matches turns every finer-than-yesterday split into a spurious "new"
    topic. Within a single day, granularity is the discovery stage's decision and is
    left alone: topics created today are not match candidates for their siblings.
    """
    topic_dir = data_dir / "topics"
    registry: dict[str, dict] = {}
    events: list[dict] = []
    for path in dated_files(topic_dir):
        daily = read_json(path)
        day = date.fromisoformat(daily["date"])
        active = {
            topic_id: topic
            for topic_id, topic in registry.items()
            if (day - date.fromisoformat(topic["last_seen"])).days <= lookback_days
        }
        fragments: defaultdict[str, list[dict]] = defaultdict(list)
        for current in sorted(daily.get("topics", []), key=lambda value: (-value["count"], value["cluster_id"])):
            label = _label_vector(current["name"], current.get("keywords", []))
            best_score, best_id = 0.0, None
            for topic_id, previous in active.items():
                score = (1 - label_weight) * cosine(current.get("centroid", {}), previous["centroid"]) + (
                    label_weight * cosine(label, previous["label"])
                )
                if score > best_score:
                    best_score, best_id = score, topic_id
            if best_id is not None and best_score >= similarity_threshold:
                current["topic_id"] = best_id
                current["link_similarity"] = round(best_score, 4)
            else:
                topic_id = _topic_id(current["name"], current["cluster_id"])
                current["topic_id"] = topic_id
                current["link_similarity"] = None
                registry[topic_id] = {
                    "topic_id": topic_id,
                    "name": current["name"],
                    "summary": current["summary"],
                    "keywords": current["keywords"],
                    "label": label,
                    "first_seen": daily["date"],
                    "last_seen": daily["date"],
                    "paper_total": 0,
                    "canonical_count": 0,
                    "summary_rank": [-1, 0],
                    "centroid": {},
                }
            fragments[current["topic_id"]].append(current)

        for topic_id, day_topics in fragments.items():
            entry = registry[topic_id]
            day_count = sum(topic["count"] for topic in day_topics)
            day_centroid = _weighted_mean(
                [(topic.get("centroid", {}), topic["count"]) for topic in day_topics]
            )
            entry["centroid"] = (
                _blend(entry["centroid"], day_centroid, centroid_alpha) if entry["paper_total"] else day_centroid
            )
            entry["paper_total"] += day_count
            entry["last_seen"] = daily["date"]
            # The canonical name comes from the topic's biggest day so far, so a
            # long-lived topic keeps a stable identity instead of being renamed daily.
            largest = max(day_topics, key=lambda topic: topic["count"])
            if day_count > entry["canonical_count"]:
                entry["canonical_count"] = day_count
                entry["name"] = largest["name"]
                entry["keywords"] = largest["keywords"]
                entry["label"] = _label_vector(largest["name"], largest.get("keywords", []))
            # The summary follows the same biggest-day rule, except that a real summary
            # always outranks placeholder or pasted text, whatever day it came from.
            degraded = largest.get("summary_pasted", False) or summary_is_boilerplate(
                largest["summary"], largest["name"], largest.get("keywords", [])
            )
            summary_rank = (0 if degraded else 1, day_count)
            if summary_rank > tuple(entry["summary_rank"]):
                entry["summary_rank"] = list(summary_rank)
                entry["summary"] = largest["summary"]
            if len(day_topics) > 1:
                events.append(
                    {
                        "date": daily["date"],
                        "type": "merge",
                        "from": sorted(topic["cluster_id"] for topic in day_topics),
                        "to": topic_id,
                    }
                )

        daily["linked_at"] = daily.get("linked_at", utc_now())
        daily["linking"] = {
            "similarity_threshold": similarity_threshold,
            "lookback_days": lookback_days,
            "label_weight": label_weight,
            "centroid_alpha": centroid_alpha,
        }
        write_json(path, daily)

    _consolidate_duplicate_names(topic_dir, registry, events)
    result = {
        "generated_at": utc_now(),
        "similarity_threshold": similarity_threshold,
        "lookback_days": lookback_days,
        "label_weight": label_weight,
        "centroid_alpha": centroid_alpha,
        "topics": sorted(registry.values(), key=lambda value: value["topic_id"]),
        "events": events,
    }
    write_json(data_dir / "index" / "topics.json", result)
    return result


def _consolidate_duplicate_names(
    topic_dir: Path, registry: dict[str, dict], events: list[dict]
) -> None:
    """Collapse identities whose canonical names are indistinguishable to readers."""
    groups: defaultdict[str, list[dict]] = defaultdict(list)
    for topic in registry.values():
        key = " ".join(topic["name"].casefold().split())
        if key:
            groups[key].append(topic)

    aliases: dict[str, str] = {}
    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        survivor = min(duplicates, key=lambda topic: (topic["first_seen"], topic["topic_id"]))
        canonical = max(
            duplicates,
            key=lambda topic: (topic["canonical_count"], topic["paper_total"], topic["topic_id"]),
        )
        summary = max(
            duplicates,
            key=lambda topic: (tuple(topic["summary_rank"]), topic["paper_total"], topic["topic_id"]),
        )
        survivor["name"] = " ".join(canonical["name"].split())
        survivor["keywords"] = canonical["keywords"]
        survivor["label"] = _label_vector(survivor["name"], survivor["keywords"])
        survivor["summary"] = summary["summary"]
        survivor["summary_rank"] = summary["summary_rank"]
        survivor["first_seen"] = min(topic["first_seen"] for topic in duplicates)
        survivor["last_seen"] = max(topic["last_seen"] for topic in duplicates)
        survivor["paper_total"] = sum(topic["paper_total"] for topic in duplicates)
        survivor["canonical_count"] = max(topic["canonical_count"] for topic in duplicates)
        survivor["centroid"] = _weighted_mean(
            [(topic["centroid"], topic["paper_total"]) for topic in duplicates]
        )
        replaced = sorted(
            topic["topic_id"] for topic in duplicates if topic["topic_id"] != survivor["topic_id"]
        )
        for topic_id in replaced:
            aliases[topic_id] = survivor["topic_id"]
            del registry[topic_id]
        events.append(
            {
                "date": survivor["last_seen"],
                "type": "identity_merge",
                "from": replaced,
                "to": survivor["topic_id"],
                "name": survivor["name"],
            }
        )

    if not aliases:
        return
    for event in events:
        event["to"] = aliases.get(event.get("to"), event.get("to"))
    for path in dated_files(topic_dir):
        daily = read_json(path)
        changed = False
        for topic in daily.get("topics", []):
            replacement = aliases.get(topic.get("topic_id"))
            if replacement:
                topic["topic_id"] = replacement
                changed = True
        if changed:
            write_json(path, daily)


def _label_vector(name: str, keywords: list) -> dict[str, float]:
    tokens = set(tokenize(name))
    for keyword in keywords:
        tokens.update(tokenize(str(keyword)))
    vector = normalise(dict.fromkeys(tokens, 1.0))
    return {key: round(value, 6) for key, value in vector.items()}


def _weighted_mean(vectors: list[tuple[dict[str, float], int]]) -> dict[str, float]:
    total: defaultdict[str, float] = defaultdict(float)
    weight_sum = sum(weight for _, weight in vectors)
    if not weight_sum:
        return {}
    for vector, weight in vectors:
        for key, value in vector.items():
            total[key] += value * weight
    return _strongest(normalise({key: value / weight_sum for key, value in total.items()}))


def _blend(previous: dict[str, float], current: dict[str, float], alpha: float) -> dict[str, float]:
    if not current:
        return previous
    merged: defaultdict[str, float] = defaultdict(float)
    for key, value in previous.items():
        merged[key] += (1 - alpha) * value
    for key, value in current.items():
        merged[key] += alpha * value
    return _strongest(normalise(dict(merged)))


def _strongest(vector: dict[str, float], limit: int = 80) -> dict[str, float]:
    top = sorted(vector.items(), key=lambda item: (-abs(item[1]), item[0]))[:limit]
    return {key: round(value, 6) for key, value in top}


def _topic_id(name: str, cluster_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "topic"
    suffix = hashlib.sha1(cluster_id.encode(), usedforsecurity=False).hexdigest()[:7]
    return f"{slug}-{suffix}"
