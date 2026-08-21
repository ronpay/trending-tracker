from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from trending_tracker.cli import _dates, build_parser
from trending_tracker.fetch import FetchConfig, fetch_range
from trending_tracker.io import read_json, write_json

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00001v1</id>
    <updated>2026-08-15T12:00:00Z</updated><published>2026-08-15T12:00:00Z</published>
    <title>  Vision agents   that plan </title>
    <summary>We introduce an agent for visual planning.</summary>
    <author><name>Ada Researcher</name></author>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI"/><category term="cs.CV"/>
    <link title="pdf" href="https://arxiv.org/pdf/2608.00001v1"/>
  </entry>
</feed>"""


def test_fetch_stores_normalized_deduplicated_papers(tmp_path: Path):
    calls = []

    def opener(url: str, timeout: float) -> bytes:
        calls.append((url, timeout))
        return ATOM

    counts = fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(("cs.AI", "cs.CV"), page_size=100, request_delay=0),
        opener=opener,
    )

    assert counts == {"2026-08-15": 1}
    stored = read_json(tmp_path / "papers" / "2026-08-15.json")
    assert stored["papers"][0]["id"] == "2608.00001"
    assert stored["papers"][0]["title"] == "Vision agents that plan"
    assert stored["papers"][0]["categories"] == ["cs.AI", "cs.CV"]
    assert "submittedDate" in calls[0][0]


def _feed(entries: str, total: int) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>{total}</opensearch:totalResults>
  {entries}
</feed>""".encode()


def _entry(index: int) -> str:
    return f"""<entry>
    <id>http://arxiv.org/abs/2608.0{index:04d}v1</id>
    <updated>2026-08-15T12:00:00Z</updated><published>2026-08-15T12:00:00Z</published>
    <title>Paper {index}</title><summary>Abstract {index}.</summary>
    <author><name>Ada Researcher</name></author>
    <arxiv:primary_category term="cs.AI"/><category term="cs.AI"/>
  </entry>"""


def test_fetch_retries_transient_transport_errors(tmp_path: Path):
    attempts = []

    def opener(url: str, timeout: float) -> bytes:
        attempts.append(url)
        if len(attempts) == 1:
            raise OSError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return _feed(_entry(1), total=1)

    counts = fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(page_size=100, request_delay=0, retry_delay=0),
        opener=opener,
    )

    assert len(attempts) == 2
    assert counts == {"2026-08-15": 1}


def test_fetch_retries_empty_page_instead_of_truncating(tmp_path: Path):
    """A blank page part-way through must not be mistaken for the end of the results."""
    pages = []

    def opener(url: str, timeout: float) -> bytes:
        pages.append(url)
        if len(pages) == 1:
            return _feed(_entry(1) + _entry(2), total=3)
        if len(pages) == 2:
            return _feed("", total=3)  # transient blank page
        return _feed(_entry(3), total=3)

    counts = fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(page_size=2, request_delay=0, retry_delay=0),
        opener=opener,
    )

    assert counts == {"2026-08-15": 3}


def test_fetch_retries_empty_first_page(tmp_path: Path):
    """The first request of a range is the one most likely to hit a transient fault."""
    pages = []

    def opener(url: str, timeout: float) -> bytes:
        pages.append(url)
        if len(pages) == 1:
            return _feed("", total=2)  # blank, but the total says there is data
        return _feed(_entry(1) + _entry(2), total=2)

    counts = fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(page_size=100, request_delay=0, retry_delay=0),
        opener=opener,
    )

    assert len(pages) == 2
    assert counts == {"2026-08-15": 2}


def test_fetch_accepts_a_genuinely_empty_range(tmp_path: Path):
    """An empty day arXiv has not announced yet must not be retried into an error."""
    pages = []

    def opener(url: str, timeout: float) -> bytes:
        pages.append(url)
        return _feed("", total=0)

    counts = fetch_range(
        date(2026, 8, 16),
        date(2026, 8, 16),
        tmp_path,
        FetchConfig(page_size=100, request_delay=0, retry_delay=0),
        opener=opener,
    )

    assert len(pages) == 1
    assert counts == {"2026-08-16": 0}


def test_fetch_merges_with_existing_day(tmp_path: Path):
    fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(page_size=100, request_delay=0),
        opener=lambda _url, _timeout: ATOM,
    )
    fetch_range(
        date(2026, 8, 15),
        date(2026, 8, 15),
        tmp_path,
        FetchConfig(page_size=100, request_delay=0),
        opener=lambda _url, _timeout: ATOM,
    )
    assert len(read_json(tmp_path / "papers" / "2026-08-15.json")["papers"]) == 1



def _catch_up_args(data_dir: Path, *options: str):
    return build_parser().parse_args(["--data-dir", str(data_dir), "fetch", "--catch-up", *options])


def _store_day(data_dir: Path, day: date) -> None:
    write_json(data_dir / "papers" / f"{day.isoformat()}.json", {"date": day.isoformat(), "papers": []})


def test_catch_up_reasks_the_overlap_window_when_the_data_is_current(tmp_path: Path):
    """Steady state: the stored data already reaches yesterday, so catch-up asks only for the
    recent days arXiv may still have been filling."""
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    _store_day(tmp_path, yesterday)

    assert _dates(_catch_up_args(tmp_path)) == (yesterday - timedelta(days=4), yesterday)


def test_catch_up_starts_at_the_newest_stored_day_after_a_long_gap(tmp_path: Path):
    """A window pinned to today silently skips whatever fell before it. Starting from the
    newest stored day is what makes a missed week heal instead of leaving a hole."""
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    latest = yesterday - timedelta(days=20)
    _store_day(tmp_path, latest)

    assert _dates(_catch_up_args(tmp_path)) == (latest, yesterday)


def test_catch_up_on_an_empty_repository_uses_the_overlap_window(tmp_path: Path):
    """With nothing stored there is no gap to bridge, so catch-up asks for its window."""
    yesterday = datetime.now(UTC).date() - timedelta(days=1)

    assert _dates(_catch_up_args(tmp_path)) == (yesterday - timedelta(days=4), yesterday)


def test_catch_up_overlap_days_sizes_the_window(tmp_path: Path):
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    _store_day(tmp_path, yesterday)

    assert _dates(_catch_up_args(tmp_path, "--overlap-days", "1")) == (yesterday, yesterday)
    with pytest.raises(ValueError):
        _dates(_catch_up_args(tmp_path, "--overlap-days", "0"))


def test_catch_up_and_explicit_dates_are_refused(tmp_path: Path):
    """Both name a range; honouring one silently would fetch something nobody asked for."""
    with pytest.raises(ValueError):
        _dates(_catch_up_args(tmp_path, "--start", "2026-08-01"))
