from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from .io import date_range, dated_files, read_json, utc_now, write_json

# Each view is scored on its own window: the current window against up to three prior
# windows of the same length. Ranking a monthly view by a weekly score surfaces one week of
# churn as "trend"; window-for-window comparison is what makes each view mean what it says.
# Seven days is the shortest window offered. arXiv's announcement cycle swings volume nearly
# threefold across a week -- Saturday carries barely a third of Monday's -- and a topic's
# day is a handful of papers, so a one-day window reports the calendar and the Poisson noise
# rather than the field. A week contains every weekday once and evens both of them out.
PERIODS = {"weekly": 7, "monthly": 30}


def calculate_trends(data_dir: Path) -> dict:
    topic_files = dated_files(data_dir / "topics")
    registry_data = read_json(data_dir / "index" / "topics.json", {"topics": [], "events": []})
    registry = {topic["topic_id"]: topic for topic in registry_data.get("topics", [])}
    daily_counts: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    daily_details: defaultdict[str, dict[str, dict]] = defaultdict(dict)
    available_dates: list[date] = []
    papers_by_date: dict[str, int] = {}
    for path in topic_files:
        payload = read_json(path)
        available_dates.append(date.fromisoformat(payload["date"]))
        papers_by_date[payload["date"]] = payload.get(
            "paper_count",
            sum(topic.get("count", 0) for topic in payload.get("topics", []))
            + payload.get("outlier_count", 0),
        )
        for topic in payload.get("topics", []):
            if not topic.get("topic_id"):
                continue
            # Several same-day topics may share a topic_id (many-to-one linking), so
            # counts sum and details merge instead of overwriting.
            daily_counts[topic["topic_id"]][payload["date"]] += topic["count"]
            detail = daily_details[topic["topic_id"]].setdefault(
                payload["date"],
                {"name": "", "summary": "", "keywords": [], "paper_ids": [], "count": 0},
            )
            if topic["count"] > detail["count"]:
                detail["name"] = topic["name"]
                detail["summary"] = topic["summary"]
                detail["keywords"] = topic["keywords"]
                detail["count"] = topic["count"]
            detail["paper_ids"] = detail["paper_ids"] + topic["paper_ids"]
    if not available_dates:
        result = {
            "generated_at": utc_now(),
            "dates": [],
            "topics": [],
            "events": [],
            "sparse_days": [],
        }
        write_json(data_dir / "trends" / "trends.json", result)
        return result

    dates = [value.isoformat() for value in date_range(min(available_dates), max(available_dates))]
    # Momentum is measured against the newest day that actually has papers. arXiv announces on
    # a delay, so the last calendar day in range is routinely still empty; anchoring to it would
    # read every topic as having dropped to zero.
    while len(dates) > 1 and papers_by_date.get(dates[-1], 0) == 0:
        dates.pop()
    # A day with no papers on disk is missing data, not a quiet day: a fetch can run before
    # arXiv announces and store nothing at all. Reading those days as gaps rather than as
    # zeros keeps one outage from dragging the current window down for a week and then
    # depressing the baseline it becomes for three more.
    covered = [papers_by_date.get(day, 0) > 0 for day in dates]
    topics = []
    for topic_id, counts_by_date in daily_counts.items():
        counts = [counts_by_date.get(day, 0) for day in dates]
        first_index = next((index for index, count in enumerate(counts) if count), len(counts) - 1)
        latest_active = next((day for day, count in zip(reversed(dates), reversed(counts), strict=True) if count), dates[-1])
        latest_detail = daily_details[topic_id].get(latest_active, {})
        meta = registry.get(topic_id, {})
        topics.append(
            {
                "topic_id": topic_id,
                "name": meta.get("name") or latest_detail.get("name", topic_id),
                "summary": meta.get("summary") or latest_detail.get("summary", ""),
                "keywords": meta.get("keywords") or latest_detail.get("keywords", []),
                "first_seen": meta.get("first_seen", dates[first_index]),
                "last_seen": meta.get("last_seen", latest_active),
                "paper_total": sum(counts),
                "counts": counts,
                "latest_active": latest_active,
                "latest_paper_ids": latest_detail.get("paper_ids", []),
                "periods": {
                    period: _period_stats(counts, covered, length, first_index)
                    for period, length in PERIODS.items()
                },
            }
        )
    topics.sort(
        key=lambda value: (
            -value["periods"]["weekly"]["score"],
            -value["periods"]["weekly"]["count"],
            value["name"],
        )
    )
    result = {
        "generated_at": utc_now(),
        "dates": dates,
        "topics": topics,
        "events": registry_data.get("events", []),
        "sparse_days": _sparse_days(papers_by_date, dates),
    }
    write_json(data_dir / "trends" / "trends.json", result)
    return result


def _period_stats(
    counts: list[int], covered: list[bool], length: int, first_index: int
) -> dict:
    start = max(0, len(counts) - length)
    observed = sum(
        count for count, seen in zip(counts[start:], covered[start:], strict=True) if seen
    )
    # `observed` is what was actually counted and is what the site shows; `current` is that
    # total scaled to a whole window, which is what the comparisons below are in terms of.
    current = _window_total(counts, covered, start, len(counts), length)
    # First appearance inside the current window: the topic is new, and novelty is
    # labelled as novelty. A topic with no history has nothing to burst against, so it
    # gets a volume-only score instead of maxed-out burst and velocity bonuses. A window
    # too full of gaps to stand in for itself is the same case -- nothing to compare with.
    is_new = first_index >= start
    prior = [] if current is None else _prior_windows(counts, covered, length)
    if current is None:
        current = float(observed)
    if is_new or not prior:
        score = round(math.log1p(current), 3)
        return {
            "count": observed,
            "baseline": 0.0,
            "change": round(current, 2),
            "score": score,
            "status": "new" if current else "fading",
        }
    previous = prior[0]
    baseline = statistics.fmean(prior)
    deviation = statistics.pstdev(prior) if len(prior) > 1 else 0.0
    burst = (current - baseline) / max(deviation, 1.0)
    velocity = (current - previous) / max(previous, 1.0)
    score = math.log1p(current) + max(-2.0, min(5.0, burst)) + max(-1.0, min(2.0, velocity))
    return {
        "count": observed,
        "baseline": round(baseline, 2),
        "change": round(current - previous, 2),
        "score": round(score, 3),
        "status": _status(current, previous, baseline, score),
    }


def _prior_windows(
    counts: list[int], covered: list[bool], length: int, windows: int = 3
) -> list[float]:
    """Totals of up to `windows` back-to-back windows preceding the current one. A window the
    fetch never covered is skipped rather than broken on, so one outage costs a single
    comparison point instead of every older one behind it."""
    prior: list[float] = []
    for index in range(1, windows + 1):
        stop = len(counts) - index * length
        if stop <= 0:
            break
        total = _window_total(counts, covered, max(0, stop - length), stop, length)
        if total is not None:
            prior.append(total)
    return prior


def _window_total(
    counts: list[int], covered: list[bool], start: int, stop: int, length: int
) -> float | None:
    """Total of the days actually fetched in `counts[start:stop]`, scaled up to `length`
    days so a window shortened by truncated history or by a gap compares like-for-like
    with a full one. None when too little of it was fetched to stand in for the whole."""
    days = sum(covered[start:stop])
    if days < max(1, length // 2):
        return None
    seen = zip(counts[start:stop], covered[start:stop], strict=True)
    return sum(count for count, ok in seen if ok) * length / days


def _sparse_days(papers_by_date: dict[str, int], dates: list[str]) -> list[dict]:
    """Days holding less than half the papers their weekday usually does. Masking covers a
    day that came back empty, but one that came back a third full still skews its window
    and nothing downstream can tell -- and only the weekday it belongs to says what full
    looks like, since Saturday runs at barely a third of Monday."""
    by_weekday: defaultdict[int, list[int]] = defaultdict(list)
    for day in dates:
        by_weekday[date.fromisoformat(day).weekday()].append(papers_by_date.get(day, 0))
    sparse = []
    for day in dates:
        peers = by_weekday[date.fromisoformat(day).weekday()]
        # Two samples cannot say what a normal Tuesday looks like.
        if len(peers) < 3:
            continue
        expected = statistics.median(peers)
        papers = papers_by_date.get(day, 0)
        if expected and papers * 2 < expected:
            sparse.append({"date": day, "papers": papers, "expected": round(expected)})
    return sparse


def _status(current: float, previous: float, baseline: float, score: float) -> str:
    if current == 0:
        return "fading"
    if current >= max(3, baseline * 2) and score >= 3:
        return "bursting"
    if current > previous or current > baseline * 1.25:
        return "rising"
    if current < previous or current < baseline * 0.75:
        return "fading"
    return "steady"
