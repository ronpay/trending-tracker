from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path

from .io import date_range, read_json, utc_now, write_json

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
DEFAULT_CATEGORIES = (
    "cs.AI",
    "cs.LG",
    "stat.ML",
    "cs.CL",
    "cs.CV",
    "cs.RO",
    "cs.NE",
    "cs.MA",
    "cs.IR",
    "cs.HC",
    "cs.SI",
    "eess.AS",
    "eess.IV",
)
USER_AGENT = "trending-tracker/0.1 (+https://github.com/)"


@dataclass(frozen=True)
class FetchConfig:
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    page_size: int = 200
    request_delay: float = 3.0
    timeout: float = 30.0
    max_results: int | None = None
    max_attempts: int = 4
    retry_delay: float = 5.0


def fetch_range(
    start: date,
    end: date,
    data_dir: Path,
    config: FetchConfig,
    *,
    opener: Callable[[str, float], bytes] | None = None,
) -> dict[str, int]:
    """Fetch a date interval and merge results into one append-only file per UTC day."""
    opener = opener or _open_url
    papers = fetch_arxiv(start, end, config, opener=opener)
    counts: dict[str, int] = {}
    for target_date in date_range(start, end):
        day = target_date.isoformat()
        day_papers = [paper for paper in papers if paper["published"].startswith(day)]
        output = data_dir / "papers" / f"{day}.json"
        previous = read_json(output, {"papers": []})
        merged = {paper["id"]: paper for paper in previous.get("papers", [])}
        merged.update({paper["id"]: paper for paper in day_papers})
        result = {
            "date": day,
            "fetched_at": utc_now(),
            "categories": list(config.categories),
            "papers": sorted(merged.values(), key=lambda paper: paper["id"]),
        }
        write_json(output, result)
        counts[day] = len(result["papers"])
    return counts


def fetch_arxiv(
    start: date,
    end: date,
    config: FetchConfig,
    *,
    opener: Callable[[str, float], bytes],
) -> list[dict]:
    start_at = datetime.combine(start, datetime_time.min, tzinfo=UTC)
    end_at = datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=UTC)
    date_clause = f"submittedDate:[{start_at:%Y%m%d%H%M} TO {end_at:%Y%m%d%H%M}]"
    category_clause = " OR ".join(f"cat:{category}" for category in config.categories)
    query = f"({category_clause}) AND {date_clause}"

    papers: dict[str, dict] = {}
    offset = 0
    total_results: int | None = None
    while True:
        remaining = None if config.max_results is None else config.max_results - len(papers)
        if remaining is not None and remaining <= 0:
            break
        page_size = config.page_size if remaining is None else min(config.page_size, remaining)
        parameters = urllib.parse.urlencode(
            {
                "search_query": query,
                "start": offset,
                "max_results": page_size,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            }
        )
        # An empty page part-way through a result set is a transient arXiv fault, not the end of
        # the data, so it is only worth retrying once we know how many results to expect.
        url = f"{ARXIV_API}?{parameters}"
        expect_entries = total_results is not None and offset < total_results
        root = _request_page(url, config, opener, expect_entries)
        if total_results is None:
            total_results = _total_results(root)
            # The first request of a range has no total to check against yet. Once the reply
            # tells us one, an empty first page is just as much a fault as an empty later one.
            if total_results and offset < total_results and not root.findall("atom:entry", ATOM):
                root = _request_page(url, config, opener, True)
        entries = root.findall("atom:entry", ATOM)
        for entry in entries:
            paper = _parse_entry(entry)
            published_date = date.fromisoformat(paper["published"][:10])
            if start <= published_date <= end:
                papers[paper["id"]] = paper
        offset += len(entries)
        if not entries:
            break
        if total_results is not None:
            if offset >= total_results:
                break
        elif len(entries) < page_size:
            break
        if config.request_delay:
            time.sleep(config.request_delay)
    return sorted(papers.values(), key=lambda paper: paper["id"])


def _request_page(
    url: str,
    config: FetchConfig,
    opener: Callable[[str, float], bytes],
    expect_entries: bool,
) -> ET.Element:
    """Fetch one page, retrying transport errors and unexpectedly empty pages."""
    last_error: Exception | None = None
    for attempt in range(1, max(1, config.max_attempts) + 1):
        try:
            root = ET.fromstring(opener(url, config.timeout))
        except (OSError, ET.ParseError) as exc:
            last_error = exc
        else:
            if root.findall("atom:entry", ATOM) or not expect_entries:
                return root
            last_error = RuntimeError("arXiv returned an empty page mid-result-set")
        if attempt < max(1, config.max_attempts):
            time.sleep(config.retry_delay * attempt)
    raise RuntimeError(f"arXiv request failed after {config.max_attempts} attempts: {last_error}")


def _total_results(root: ET.Element) -> int | None:
    node = root.find("opensearch:totalResults", ATOM)
    if node is None or not node.text:
        return None
    try:
        return int(node.text)
    except ValueError:
        return None


def _open_url(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def _parse_entry(entry: ET.Element) -> dict:
    raw_id = _text(entry, "atom:id")
    match = re.search(r"/abs/([^/?#]+)", raw_id)
    arxiv_id = (match.group(1) if match else raw_id.rsplit("/", 1)[-1]).split("v", 1)[0]
    categories = sorted({node.attrib["term"] for node in entry.findall("atom:category", ATOM)})
    authors = [
        _clean_text(_text(author, "atom:name")) for author in entry.findall("atom:author", ATOM)
    ]
    pdf_url = next(
        (
            link.attrib.get("href", "")
            for link in entry.findall("atom:link", ATOM)
            if link.attrib.get("title") == "pdf"
        ),
        f"https://arxiv.org/pdf/{arxiv_id}",
    )
    return {
        "id": arxiv_id,
        "title": _clean_text(_text(entry, "atom:title")),
        "abstract": _clean_text(_text(entry, "atom:summary")),
        "authors": authors,
        "categories": categories,
        "primary_category": _primary_category(entry, categories),
        "published": _text(entry, "atom:published"),
        "updated": _text(entry, "atom:updated"),
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": pdf_url,
    }


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path, ATOM)
    return found.text.strip() if found is not None and found.text else ""


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _primary_category(entry: ET.Element, fallback: list[str]) -> str:
    node = entry.find("arxiv:primary_category", ATOM)
    return node.attrib.get("term", "") if node is not None else (fallback[0] if fallback else "")

