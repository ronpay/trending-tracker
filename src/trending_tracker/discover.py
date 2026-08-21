"""Turn each day's papers into topics from groupings an agent writes to disk.

Grouping is a language model's job, but the model is no longer reached over HTTP from
inside this process. The agent that runs the daily update (see `.codex/skills/daily-update`)
reads a day's papers, writes its grouping to `data/cache/proposals/YYYY-MM-DD.json`, and this
module turns that proposal into the stored topic file. Everything that must be true of a
topic file regardless of which model produced it -- valid IDs, one topic per paper, computed
centroids, summary quality gates -- is enforced here, where a prompt cannot talk it away.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from .io import read_json, utc_now, write_json
from .text import mean_vector, tfidf_vectors, tokenize

PROPOSAL_METHOD = "llm-grouping-subagents"
DEFAULT_MODEL = "codex"
# An outlier share this high usually means the proposal was written for an earlier, smaller
# copy of the day: papers that arrived later belong to no topic and land here by default.
OUTLIER_WARNING_SHARE = 0.4

# Words a template generator pads summaries with; they carry no topic-specific content.
GENERIC_SUMMARY_TOKENS = {
    "research", "covering", "representative", "methods", "datasets", "evaluation",
    "practices", "applications", "area", "areas", "including", "related", "topics",
    "papers", "study", "studies", "field", "various", "techniques",
}


def summary_is_boilerplate(summary: str, name: str, keywords: list) -> bool:
    """True when a summary could be regenerated from the name alone.

    A useful summary says why the papers belong together; one assembled from the topic
    name plus filler carries no information the card does not already show. This gate
    exists because grouping is delegated to a model: a degraded model (or a fallback
    path) produces exactly this shape, and it must be flagged, never silently shipped.
    """
    text = summary.strip().lower()
    if not text:
        return True
    if text.startswith("research on "):
        return True
    known = set(tokenize(name)) | GENERIC_SUMMARY_TOKENS
    for keyword in keywords:
        known.update(tokenize(str(keyword)))
    distinctive = [token for token in tokenize(summary) if token not in known]
    return len(distinctive) < 3


def count_boilerplate(topics: list[dict]) -> int:
    return sum(
        1
        for topic in topics
        if summary_is_boilerplate(topic.get("summary", ""), topic.get("name", ""), topic.get("keywords", []))
    )


_SINGLE_PAPER_OPENERS = re.compile(r"^(this|the) paper\b|^we\b|^in this (work|paper)\b", re.IGNORECASE)


def summary_is_pasted(summary: str, documents: list[set[str]], ngram_size: int = 8) -> bool:
    """True when a summary copies a paper instead of describing the collection.

    Catches two shapes a weak model produces: prose in a single paper's voice
    ("This paper proposes…"), and a verbatim run of `ngram_size` words lifted from any
    source document. A summary that copies one paper cannot describe thirty; pasted
    text is also maximally "distinctive", which is how it slipped past the
    boilerplate gate.
    """
    if _SINGLE_PAPER_OPENERS.search(summary.strip()):
        return True
    target = word_ngrams(summary, ngram_size)
    return bool(target) and any(target & document for document in documents)


def word_ngrams(text: str, size: int = 8) -> set[str]:
    words = re.findall(r"[a-z0-9'-]+", text.lower())
    return {" ".join(words[index : index + size]) for index in range(len(words) - size + 1)}


def source_fingerprint(paper_ids) -> str:
    """Identifies the paper set a grouping was built from. Grouping is no longer batched
    by position, so the order papers arrive in no longer changes the result."""
    return hashlib.sha256("\0".join(sorted(paper_ids)).encode()).hexdigest()


def day_status(paper_path: Path, topic_path: Path, proposal_path: Path) -> dict:
    """Report whether a day still needs grouping, without writing anything."""
    source = read_json(paper_path)
    if source is None:
        raise FileNotFoundError(paper_path)
    paper_ids = {paper["id"] for paper in source.get("papers", [])}
    grouped = read_json(topic_path)
    proposal = read_json(proposal_path)
    proposed_ids = _proposed_ids(proposal)
    return {
        "date": paper_path.stem,
        "paper_count": len(paper_ids),
        "current": _is_current(grouped, paper_ids),
        "topic_count": len(grouped["topics"]) if grouped else 0,
        "has_proposal": proposal is not None,
        "proposal_covers": len(proposed_ids & paper_ids),
        "proposal_unknown": len(proposed_ids - paper_ids),
        "proposal_path": proposal_path,
        "paper_path": paper_path,
    }


def _is_current(grouped: dict | None, paper_ids: set[str]) -> bool:
    """A stored grouping is current when it accounts for exactly today's papers.

    The fingerprint answers that directly for files this version wrote. Files written
    before it carry no fingerprint, so fall back to what every topic file states anyway:
    assigned papers plus outliers. Both are the same question -- did papers arrive after
    this day was grouped -- and the fallback keeps a re-fetch from regrouping history
    that has not changed.
    """
    if grouped is None:
        return False
    stored = grouped.get("source_fingerprint")
    if stored:
        return stored == source_fingerprint(paper_ids)
    covered = set(grouped.get("outlier_paper_ids", []))
    for topic in grouped.get("topics", []):
        covered.update(topic.get("paper_ids", []))
    return covered == paper_ids


def _proposed_ids(proposal: dict | None) -> set[str]:
    if not proposal:
        return set()
    return {
        paper_id
        for topic in proposal.get("topics", [])
        for paper_id in topic.get("paper_ids", [])
    }


def ingest_proposals(
    input_path: Path,
    proposal_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Build a day's topic file from the grouping an agent proposed for it."""
    source = read_json(input_path)
    if source is None:
        raise FileNotFoundError(input_path)
    proposal = read_json(proposal_path)
    if proposal is None:
        raise FileNotFoundError(proposal_path)
    if not isinstance(proposal.get("topics"), list):
        raise ValueError(f"{proposal_path} must hold a topics array")
    proposal_date = proposal.get("date")
    if proposal_date and proposal_date != source["date"]:
        raise ValueError(f"{proposal_path} is dated {proposal_date}, not {source['date']}")
    result = build_topics(
        source,
        proposal["topics"],
        model=str(proposal.get("model") or model),
    )
    write_json(output_path, result)
    return result


def build_topics(source: dict, proposals: list[dict], *, model: str = DEFAULT_MODEL) -> dict:
    """Normalize proposed groups into the stored topic schema.

    The proposal is a suggestion in the model's words; everything downstream depends on
    is derived here instead of trusted. IDs that are unknown or already used are dropped,
    so a paper lands in at most one topic; centroids are computed locally from the same
    TF-IDF vectors the linker compares, so cross-day matching never depends on a model
    being consistent between runs.
    """
    papers = source.get("papers", [])
    paper_by_id = {paper["id"]: paper for paper in papers}
    documents = [f"{paper['title']} {paper.get('abstract', '')}" for paper in papers]
    vectors, _ = tfidf_vectors(documents)
    vector_by_id = {paper["id"]: vector for paper, vector in zip(papers, vectors, strict=True)}
    document_ngrams = [word_ngrams(document) for document in documents]

    assigned: set[str] = set()
    unknown: set[str] = set()
    duplicates = 0
    topics = []
    for proposal in proposals:
        paper_ids = []
        for paper_id in proposal.get("paper_ids", []):
            if paper_id not in paper_by_id:
                unknown.add(str(paper_id))
            elif paper_id in assigned or paper_id in paper_ids:
                # A paper repeated inside one topic inflates its count and skews its
                # centroid exactly as one claimed by two topics does.
                duplicates += 1
            else:
                paper_ids.append(paper_id)
        if not paper_ids:
            continue
        assigned.update(paper_ids)
        summary = str(proposal.get("summary", ""))[:400]
        topics.append(
            {
                "cluster_id": f"{source['date']}-{len(topics) + 1:02d}",
                "topic_id": None,
                "name": str(proposal.get("name", "Unnamed topic"))[:100],
                "summary": summary,
                "summary_pasted": summary_is_pasted(summary, document_ngrams),
                "keywords": [str(value)[:40] for value in proposal.get("keywords", [])[:8]],
                "paper_ids": paper_ids,
                "representative_papers": paper_ids[:3],
                "count": len(paper_ids),
                "centroid": _rounded(mean_vector([vector_by_id[paper_id] for paper_id in paper_ids])),
            }
        )
    outliers = sorted(set(paper_by_id) - assigned)
    boilerplate = count_boilerplate(topics)
    pasted = sum(1 for topic in topics if topic["summary_pasted"])
    _report(source["date"], topics, boilerplate, pasted, unknown, duplicates, outliers, len(papers))
    return {
        "date": source["date"],
        "generated_at": utc_now(),
        "method": PROPOSAL_METHOD,
        "model": model,
        "paper_count": len(papers),
        "source_fingerprint": source_fingerprint(paper_by_id),
        "outlier_count": len(outliers),
        "outlier_paper_ids": outliers,
        "summary_quality": {
            "topic_count": len(topics),
            "boilerplate_count": boilerplate,
            "pasted_count": pasted,
            "duplicate_count": duplicates,
            "unknown_id_count": len(unknown),
        },
        "topics": topics,
    }


def _report(
    date: str,
    topics: list[dict],
    boilerplate: int,
    pasted: int,
    unknown: set[str],
    duplicates: int,
    outliers: list[str],
    paper_count: int,
) -> None:
    """Say on stderr what the proposal got wrong, because the agent that wrote it is the
    only thing that can fix it: a rewritten proposal re-ingested costs one more pass."""
    if boilerplate or pasted:
        print(
            f"warning: {date}: {boilerplate}/{len(topics)} summaries look like boilerplate "
            f"and {pasted} copy a source paper; rewrite those summaries in the proposal "
            "and ingest the day again",
            file=sys.stderr,
        )
    if unknown or duplicates:
        sample = ", ".join(sorted(unknown)[:3])
        print(
            f"warning: {date}: dropped {len(unknown)} paper IDs that are not in the day "
            f"({sample}) and {duplicates} already claimed by an earlier topic",
            file=sys.stderr,
        )
    if paper_count and len(outliers) > OUTLIER_WARNING_SHARE * paper_count:
        print(
            f"warning: {date}: {len(outliers)} of {paper_count} papers were left "
            "ungrouped; a proposal written before the day finished filling in looks "
            "exactly like this",
            file=sys.stderr,
        )


def _rounded(vector: dict[str, float], limit: int = 80) -> dict[str, float]:
    strongest = sorted(vector.items(), key=lambda item: (-abs(item[1]), item[0]))[:limit]
    return {key: round(value, 6) for key, value in strongest}
