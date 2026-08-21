import json
import math
from datetime import date, timedelta
from pathlib import Path

from trending_tracker.io import read_json, write_json
from trending_tracker.linking import link_topics
from trending_tracker.site import build_site
from trending_tracker.trends import calculate_trends


def _papers(day: str, ids: list[str]) -> dict:
    return {
        "date": day,
        "papers": [
            {
                "id": identifier,
                "title": f"Paper {identifier}",
                "abstract": "A paper abstract.",
                "authors": ["A. Researcher"],
                "categories": ["cs.AI"],
                "primary_category": "cs.AI",
                "published": f"{day}T01:00:00Z",
                "updated": f"{day}T01:00:00Z",
                "url": f"https://arxiv.org/abs/{identifier}",
                "pdf_url": f"https://arxiv.org/pdf/{identifier}",
            }
            for identifier in ids
        ],
    }


def _topic(
    day: str,
    ordinal: int,
    count: int,
    centroid: dict[str, float],
    *,
    name: str = "Language agents",
    keywords: list[str] | None = None,
    ids: list[str] | None = None,
) -> dict:
    ids = ids if ids is not None else [f"{ordinal}.{index}" for index in range(count)]
    return {
        "cluster_id": f"{day}-{ordinal:02d}",
        "topic_id": None,
        "name": name,
        "summary": f"{name} papers that belong together.",
        "keywords": keywords if keywords is not None else ["agents", "language"],
        "paper_ids": ids,
        "representative_papers": ids[:2],
        "count": count,
        "centroid": centroid,
    }


def _day(day: str, topics: list[dict], paper_count: int | None = None) -> dict:
    payload = {
        "date": day,
        "topics": topics,
        "outlier_count": 0,
        "outlier_paper_ids": [],
    }
    if paper_count is not None:
        payload["paper_count"] = paper_count
    return payload


def _days(start: str, length: int) -> list[str]:
    first = date.fromisoformat(start)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(length)]


def test_link_trend_and_site_pipeline(tmp_path: Path):
    data_dir = tmp_path / "data"
    days = ["2026-08-13", "2026-08-14", "2026-08-15"]
    counts = [2, 3, 7]
    centroids = [
        {"agent": 0.8, "language": 0.6},
        {"agent": 0.78, "language": 0.62},
        {"agent": 0.75, "language": 0.66},
    ]
    for ordinal, (day, count, centroid) in enumerate(zip(days, counts, centroids, strict=True), 1):
        ids = [f"{ordinal}.{index}" for index in range(count)]
        write_json(data_dir / "papers" / f"{day}.json", _papers(day, ids))
        write_json(data_dir / "topics" / f"{day}.json", _day(day, [_topic(day, ordinal, count, centroid)]))

    linked = link_topics(data_dir, similarity_threshold=0.5)
    assert len(linked["topics"]) == 1
    assigned_ids = {
        read_json(data_dir / "topics" / f"{day}.json")["topics"][0]["topic_id"] for day in days
    }
    assert len(assigned_ids) == 1

    trends = calculate_trends(data_dir)
    topic = trends["topics"][0]
    assert topic["counts"] == counts
    assert topic["periods"]["weekly"]["count"] == sum(counts)
    # The whole dataset is younger than one week, so the weekly window is its first.
    assert topic["periods"]["weekly"]["status"] == "new"
    assert len(topic["latest_paper_ids"]) == 7

    site_dir = tmp_path / "site"
    result = build_site(data_dir, site_dir)
    assert result == {"output": str(site_dir), "paper_count": 12, "topic_count": 1}
    assert (site_dir / "weekly" / "index.html").exists()
    assert (site_dir / "monthly" / "index.html").exists()
    # One calendar day of a topic is a handful of papers on an announcement schedule, so
    # there is no daily view to build and weekly is both the shortest window and the landing.
    assert not (site_dir / "daily").exists()
    assert 'data-view="weekly"' in (site_dir / "index.html").read_text()
    dashboard = json.loads((site_dir / "data" / "dashboard.json").read_text())
    assert dashboard["topics"][0]["name"] == "Language agents"

    # The payload ships on every page load, so it carries only the papers the topics link to,
    # and only the fields the page renders.
    linked = set(dashboard["topics"][0]["latest_paper_ids"])
    assert set(dashboard["papers"]) == linked
    assert len(linked) == 7
    assert len(linked) < result["paper_count"]
    assert all(set(paper) == {"title", "url"} for paper in dashboard["papers"].values())
    assert dashboard["paper_days"] == {"2026-08-13": 2, "2026-08-14": 3, "2026-08-15": 7}

    app = (site_dir / "assets" / "app.js").read_text()
    assert '<details class="paper-list">' in app
    assert "escapeHtml(paper.title)" in app
    assert "latest_paper_ids.slice(0, 2)" not in app


def test_finer_grained_day_links_to_one_topic_and_counts_sum(tmp_path: Path):
    """A theme the discovery stage splits finer than yesterday must not fork into
    spurious new topics: both fragments link to the same registry topic and the
    day's counts sum."""
    data_dir = tmp_path / "data"
    write_json(
        data_dir / "topics" / "2026-08-13.json",
        _day("2026-08-13", [_topic("2026-08-13", 1, 4, {"agent": 0.8, "language": 0.6})]),
    )
    write_json(
        data_dir / "topics" / "2026-08-14.json",
        _day(
            "2026-08-14",
            [
                _topic(
                    "2026-08-14", 1, 3,
                    {"agent": 0.74, "language": 0.53, "memory": 0.41},
                    name="Language agent memory",
                    keywords=["language", "memory"],
                ),
                _topic(
                    "2026-08-14", 2, 2,
                    {"agent": 0.8, "planning": 0.6},
                    name="Agent planning",
                    keywords=["agents", "planning"],
                ),
            ],
        ),
    )

    linked = link_topics(data_dir)
    assert len(linked["topics"]) == 1
    day_two = read_json(data_dir / "topics" / "2026-08-14.json")["topics"]
    assert len({topic["topic_id"] for topic in day_two}) == 1
    assert any(event["type"] == "merge" for event in linked["events"])

    trends = calculate_trends(data_dir)
    assert len(trends["topics"]) == 1
    assert trends["topics"][0]["counts"] == [4, 5]
    # Merged detail keeps every fragment's papers.
    assert len(trends["topics"][0]["latest_paper_ids"]) == 5


def test_duplicate_canonical_names_consolidate_into_one_identity(tmp_path: Path):
    data_dir = tmp_path / "data"
    day = "2026-08-14"
    write_json(
        data_dir / "topics" / f"{day}.json",
        _day(
            day,
            [
                _topic(day, 1, 3, {"planning": 1.0}, name="Agentic Systems"),
                _topic(day, 2, 4, {"memory": 1.0}, name="  agentic   systems  "),
            ],
        ),
    )

    linked = link_topics(data_dir)

    assert len(linked["topics"]) == 1
    assert linked["topics"][0]["name"] == "agentic systems"
    assignments = read_json(data_dir / "topics" / f"{day}.json")["topics"]
    assert len({topic["topic_id"] for topic in assignments}) == 1
    identity_merge = next(event for event in linked["events"] if event["type"] == "identity_merge")
    assert identity_merge["to"] == linked["topics"][0]["topic_id"]

    trends = calculate_trends(data_dir)
    assert len(trends["topics"]) == 1
    assert trends["topics"][0]["periods"]["monthly"]["count"] == 7


def test_new_topic_is_labelled_new_not_bursting(tmp_path: Path):
    """A topic first seen inside the current window has no history to burst against:
    it gets a volume-only score and the "new" status, so novelty cannot outrank
    genuine momentum."""
    data_dir = tmp_path / "data"
    # Three weeks, so the incumbent has the prior windows the weekly view compares against
    # and the two topics are told apart by age rather than by lack of data.
    days = _days("2026-07-26", 21)
    for ordinal, day in enumerate(days, 1):
        topics = [_topic(day, ordinal, 2, {"agent": 0.8, "language": 0.6})]
        if day == days[-1]:
            topics.append(
                _topic(
                    day, 99, 30,
                    {"quantum": 0.9, "error-correction": 0.44},
                    name="Quantum error correction",
                    keywords=["quantum", "error-correction"],
                )
            )
        write_json(data_dir / "topics" / f"{day}.json", _day(day, topics))
    link_topics(data_dir)

    trends = calculate_trends(data_dir)
    by_name = {topic["name"]: topic for topic in trends["topics"]}
    newcomer = by_name["Quantum error correction"]["periods"]["weekly"]
    assert newcomer["status"] == "new"
    assert newcomer["score"] == round(math.log1p(30), 3)
    assert by_name["Language agents"]["periods"]["weekly"]["status"] != "new"


def test_canonical_name_comes_from_biggest_day(tmp_path: Path):
    """A long-lived topic keeps the identity of its biggest day instead of being
    renamed by whatever its latest small fragment was called."""
    data_dir = tmp_path / "data"
    write_json(
        data_dir / "topics" / "2026-08-13.json",
        _day("2026-08-13", [_topic("2026-08-13", 1, 9, {"agent": 0.8, "language": 0.6}, name="Language agents")]),
    )
    write_json(
        data_dir / "topics" / "2026-08-14.json",
        _day(
            "2026-08-14",
            [
                _topic(
                    "2026-08-14", 1, 2,
                    {"agent": 0.79, "language": 0.61},
                    name="Agents, language",
                    keywords=["agents", "language"],
                )
            ],
        ),
    )
    link_topics(data_dir)

    trends = calculate_trends(data_dir)
    assert len(trends["topics"]) == 1
    assert trends["topics"][0]["name"] == "Language agents"


def test_real_summary_outranks_boilerplate_from_a_bigger_day(tmp_path: Path):
    """Placeholder text never wins over a real summary, whatever day each came from."""
    data_dir = tmp_path / "data"
    big = _topic("2026-08-13", 1, 9, {"agent": 0.8, "language": 0.6})
    big["summary"] = "Research on language agents, covering representative methods."
    small = _topic("2026-08-14", 1, 2, {"agent": 0.79, "language": 0.61})
    small["summary"] = (
        "Agents that coordinate browser tools, plan multi-step workflows, "
        "and recover from execution failures."
    )
    write_json(data_dir / "topics" / "2026-08-13.json", _day("2026-08-13", [big]))
    write_json(data_dir / "topics" / "2026-08-14.json", _day("2026-08-14", [small]))
    link_topics(data_dir)

    trends = calculate_trends(data_dir)
    assert len(trends["topics"]) == 1
    assert trends["topics"][0]["name"] == "Language agents"
    assert trends["topics"][0]["summary"] == small["summary"]


def test_trailing_empty_days_do_not_zero_out_momentum(tmp_path: Path):
    """arXiv announces on a delay, so the newest day in range is often still empty."""
    data_dir = tmp_path / "data"
    for ordinal, (day, count) in enumerate(zip(["2026-08-13", "2026-08-14"], [3, 9], strict=True), 1):
        write_json(
            data_dir / "topics" / f"{day}.json",
            _day(day, [_topic(day, ordinal, count, {"agent": 0.8, "language": 0.6})]),
        )
    # The pipeline still writes a topic file for a day arXiv has not announced yet.
    empty = _day("2026-08-15", [])
    empty["paper_count"] = 0
    write_json(data_dir / "topics" / "2026-08-15.json", empty)
    link_topics(data_dir, similarity_threshold=0.5)

    trends = calculate_trends(data_dir)

    assert trends["dates"][-1] == "2026-08-14"
    topic = trends["topics"][0]
    # Both announced days counted, and the day arXiv had not reached is not a collapse.
    assert topic["periods"]["weekly"]["count"] == 12
    assert topic["periods"]["weekly"]["status"] != "fading"
    assert topic["periods"]["weekly"]["score"] > 0


def test_empty_trends_are_written(tmp_path: Path):
    result = calculate_trends(tmp_path / "data")
    assert result["dates"] == []
    assert (tmp_path / "data" / "trends" / "trends.json").exists()


def test_pasted_summary_loses_to_real_one_from_a_smaller_day(tmp_path: Path):
    """A summary the discovery stage stamped as pasted never becomes canonical while
    a real summary exists anywhere in the chain."""
    data_dir = tmp_path / "data"
    big = _topic("2026-08-13", 1, 9, {"agent": 0.8, "language": 0.6})
    big["summary"] = "This paper proposes a novel agent framework with strong results."
    big["summary_pasted"] = True
    small = _topic("2026-08-14", 1, 2, {"agent": 0.79, "language": 0.61})
    small["summary"] = (
        "Agents that coordinate browser tools, plan multi-step workflows, "
        "and recover from execution failures."
    )
    small["summary_pasted"] = False
    write_json(data_dir / "topics" / "2026-08-13.json", _day("2026-08-13", [big]))
    write_json(data_dir / "topics" / "2026-08-14.json", _day("2026-08-14", [small]))
    link_topics(data_dir)

    trends = calculate_trends(data_dir)
    assert len(trends["topics"]) == 1
    assert trends["topics"][0]["summary"] == small["summary"]


def test_unfetched_day_does_not_read_as_a_topic_fading(tmp_path: Path):
    """A day the fetch missed is absence of evidence. Counting it as zero papers costs
    the current window a seventh of its volume and reads as a real decline."""
    data_dir = tmp_path / "data"
    days = _days("2026-07-26", 21)
    gap = days[-3]
    for ordinal, day in enumerate(days, 1):
        if day == gap:
            # What the pipeline writes for a day arXiv had not announced yet.
            write_json(data_dir / "topics" / f"{day}.json", _day(day, [], paper_count=0))
            continue
        topic = _topic(day, ordinal, 2, {"agent": 0.8, "language": 0.6})
        topic["topic_id"] = "language-agents"
        write_json(data_dir / "topics" / f"{day}.json", _day(day, [topic], paper_count=40))

    trends = calculate_trends(data_dir)

    weekly = trends["topics"][0]["periods"]["weekly"]
    # Six days seen at two papers each, scaled back up to the week it stands for.
    assert weekly["count"] == 12
    assert weekly["baseline"] == 14.0
    assert weekly["change"] == 0.0
    assert weekly["status"] == "steady"


def test_a_half_empty_day_is_reported_even_though_it_cannot_be_masked(tmp_path: Path):
    """A day that came back part-full still skews its window, and no count downstream
    can tell it from a quiet one. Only its own weekday says what full looks like."""
    data_dir = tmp_path / "data"
    weekly_shape = [200, 500, 500, 450, 450, 350, 200]
    days = _days("2026-07-26", 21)
    thin = days[-2]
    for ordinal, day in enumerate(days, 1):
        papers = weekly_shape[ordinal % 7 - 1]
        topic = _topic(day, ordinal, 2, {"agent": 0.8, "language": 0.6})
        topic["topic_id"] = "language-agents"
        write_json(
            data_dir / "topics" / f"{day}.json",
            _day(day, [topic], paper_count=30 if day == thin else papers),
        )

    trends = calculate_trends(data_dir)

    assert [entry["date"] for entry in trends["sparse_days"]] == [thin]
    assert trends["sparse_days"][0] == {"date": thin, "papers": 30, "expected": 350}


def test_an_ungrouped_day_is_not_reported_as_an_under_fetched_one(tmp_path: Path, capsys):
    """Trends counts papers through topics, so a day waiting to be grouped reads exactly like
    a day whose fetch came up short -- and the two need opposite fixes. Grouping happens
    outside the pipeline now, so this is a normal state, not a rare one."""
    from trending_tracker.cli import _report_sparse_days

    write_json(
        tmp_path / "papers" / "2026-08-15.json",
        {"date": "2026-08-15", "papers": [{"id": str(index), "title": "t"} for index in range(230)]},
    )
    trend_data = {
        "sparse_days": [
            {"date": "2026-08-15", "papers": 0, "expected": 209},
            {"date": "2026-08-16", "papers": 12, "expected": 200},
        ]
    }

    _report_sparse_days(trend_data, tmp_path)

    errors = capsys.readouterr().err.splitlines()
    assert "waiting to be grouped" in errors[0]
    assert "230 papers" in errors[0]
    # A day whose papers really are missing from disk still points at the fetch.
    assert "re-fetching" in errors[1]
