from pathlib import Path

from trending_tracker.discover import day_status, ingest_proposals
from trending_tracker.io import write_json


def _day(tmp_path: Path, papers: list[dict], date: str = "2026-08-15"):
    paths = (
        tmp_path / "papers" / f"{date}.json",
        tmp_path / "cache" / "proposals" / f"{date}.json",
        tmp_path / "topics" / f"{date}.json",
    )
    write_json(paths[0], {"date": date, "papers": papers})
    return paths


def test_ingest_normalizes_a_proposed_grouping(tmp_path: Path):
    papers = [
        {"id": "1", "title": "Language web agents", "abstract": "Agents plan browser actions."},
        {"id": "2", "title": "Browser agent planning", "abstract": "Language agents navigate the web."},
        {"id": "3", "title": "Protein folding", "abstract": "Structure prediction from sequence."},
    ]
    input_path, proposal_path, output_path = _day(tmp_path, papers)
    write_json(
        proposal_path,
        {
            "date": "2026-08-15",
            "model": "gpt-5.6-luna",
            "topics": [
                {
                    "name": "Web agents",
                    "summary": "Agents that drive browsers, from action planning to recovery.",
                    "keywords": ["agents", "browser"],
                    # "2" is claimed twice and "99" is not in the day; both must be dropped.
                    "paper_ids": ["1", "2", "2", "99"],
                }
            ],
        },
    )

    result = ingest_proposals(input_path, proposal_path, output_path)

    topic = result["topics"][0]
    assert topic["paper_ids"] == ["1", "2"]
    assert topic["cluster_id"] == "2026-08-15-01"
    assert topic["centroid"]
    assert topic["summary_pasted"] is False
    # A paper no topic claimed is an outlier, not a silent loss.
    assert result["outlier_paper_ids"] == ["3"]
    assert result["model"] == "gpt-5.6-luna"
    assert result["summary_quality"]["duplicate_count"] == 1
    assert result["summary_quality"]["unknown_id_count"] == 1


def test_a_grouped_day_stays_current_until_papers_arrive(tmp_path: Path):
    """Every run re-asks arXiv for the last few days so a late announcement still lands.
    Regrouping a day costs an agent pass over every abstract in it, so a day that gained
    nothing must not be offered for grouping again."""
    papers = [{"id": "1", "title": "Language web agents", "abstract": "Agents plan actions."}]
    input_path, proposal_path, output_path = _day(tmp_path, papers)
    write_json(
        proposal_path,
        {
            "date": "2026-08-15",
            "topics": [
                {
                    "name": "Web agents",
                    "summary": "Agents that drive browsers, from action planning to recovery.",
                    "keywords": ["agents"],
                    "paper_ids": ["1"],
                }
            ],
        },
    )
    assert day_status(input_path, output_path, proposal_path)["current"] is False

    ingest_proposals(input_path, proposal_path, output_path)
    assert day_status(input_path, output_path, proposal_path)["current"] is True

    # A day that gained a paper is a different day, and is offered for grouping again.
    papers.append({"id": "2", "title": "Browser planning", "abstract": "Agents navigate."})
    write_json(input_path, {"date": "2026-08-15", "papers": papers})
    status = day_status(input_path, output_path, proposal_path)
    assert status["current"] is False
    assert status["proposal_covers"] == 1


def test_a_grouping_written_before_fingerprints_is_still_current(tmp_path: Path):
    """Topic files predating the fingerprint state their coverage the long way, through
    assigned papers plus outliers. Reading that instead of regrouping is what keeps stored
    history -- and the topic chains linked from it -- out of a needless rebuild."""
    papers = [
        {"id": "1", "title": "Language web agents", "abstract": "Agents plan actions."},
        {"id": "2", "title": "Protein folding", "abstract": "Structure prediction."},
    ]
    input_path, proposal_path, output_path = _day(tmp_path, papers)
    write_json(
        output_path,
        {
            "date": "2026-08-15",
            "outlier_paper_ids": ["2"],
            "topics": [{"cluster_id": "2026-08-15-01", "paper_ids": ["1"]}],
        },
    )

    assert day_status(input_path, output_path, proposal_path)["current"] is True


def test_boilerplate_summary_gate():
    from trending_tracker.discover import summary_is_boilerplate

    assert summary_is_boilerplate(
        "Research on change methods, including change, himec, cascade.",
        "Change Methods",
        ["change", "himec", "cascade"],
    )
    assert summary_is_boilerplate("", "Anything", [])
    # A summary whose only content tokens restate the name and keywords adds nothing.
    assert summary_is_boilerplate(
        "Papers covering robot navigation and embodied agents techniques.",
        "Robot navigation and embodied agents",
        ["robot", "navigation"],
    )
    assert not summary_is_boilerplate(
        "Methods for long-horizon agents covering planning, context selection, memory, "
        "tool authorization, workflow execution, and operational recovery.",
        "LLM agents, planning, memory, and tool use",
        ["agents", "planning"],
    )


def test_pasted_summary_gate():
    from trending_tracker.discover import summary_is_pasted, word_ngrams

    abstract = (
        "Infrared small target detection aims to identify long distance small targets "
        "from complex infrared backgrounds and is a fundamental task in remote sensing. "
        "Deep learning methods have recently improved detection accuracy substantially."
    )
    documents = [word_ngrams(abstract)]
    # Verbatim copy of a source abstract is a paste, not a summary.
    assert summary_is_pasted(abstract[:200], documents)
    # Single-paper voice is a paste even without verbatim overlap.
    assert summary_is_pasted("This paper proposes a new detector for small targets.", documents)
    assert summary_is_pasted("We introduce a benchmark for infrared detection.", documents)
    # Original synthesis over the same theme passes.
    assert not summary_is_pasted(
        "Detectors for faint distant objects in infrared imagery, combining background "
        "suppression with learned features; several papers target remote-sensing deployments.",
        documents,
    )


def test_a_stale_proposal_is_reported_rather_than_trusted(tmp_path: Path):
    """A proposal written before the day finished filling in leaves the papers that arrived
    after it ungrouped. Nothing downstream can tell that from a day of genuine outliers, so
    the count says so where the agent writing proposals will read it."""
    papers = [
        {"id": str(index), "title": f"Paper {index}", "abstract": "Agents plan actions."}
        for index in range(10)
    ]
    input_path, proposal_path, output_path = _day(tmp_path, papers)
    write_json(
        proposal_path,
        {
            "date": "2026-08-15",
            "topics": [
                {
                    "name": "Web agents",
                    "summary": "Agents that drive browsers, from action planning to recovery.",
                    "keywords": ["agents"],
                    "paper_ids": ["0", "1"],
                }
            ],
        },
    )

    result = ingest_proposals(input_path, proposal_path, output_path)

    assert result["outlier_count"] == 8
    status = day_status(input_path, output_path, proposal_path)
    assert status["current"] is True
    assert status["proposal_covers"] == 2
