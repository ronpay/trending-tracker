from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .discover import day_status, ingest_proposals
from .fetch import DEFAULT_CATEGORIES, FetchConfig, fetch_range
from .io import dated_files, parse_date, read_json
from .linking import link_topics
from .site import build_site
from .trends import calculate_trends


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trending-tracker", description="Discover emerging AI research topics on arXiv."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Pipeline data directory")
    parser.add_argument("--site-dir", type=Path, default=Path("site"), help="Generated site directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch and store arXiv papers")
    _date_arguments(fetch)
    fetch.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    fetch.add_argument("--page-size", type=int, default=200)
    fetch.add_argument("--request-delay", type=float, default=3.0)
    fetch.add_argument("--max-results", type=int)

    discover = subparsers.add_parser(
        "discover", help="Build topic files from the groupings an agent proposed"
    )
    discover.add_argument("--date", help="One YYYY-MM-DD date; omit to process every stored day")
    discover.add_argument(
        "--check",
        action="store_true",
        help="List days still waiting to be grouped and write nothing",
    )

    link = subparsers.add_parser("link", help="Assign stable IDs to topics across days")
    link.add_argument("--similarity-threshold", type=float, default=0.2)
    link.add_argument("--lookback-days", type=int, default=30)

    subparsers.add_parser("trends", help="Calculate volume and momentum time series")

    subparsers.add_parser("build", help="Build the static website")

    pipeline = subparsers.add_parser("pipeline", help="Run fetch through site build")
    _date_arguments(pipeline)
    pipeline.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    pipeline.add_argument("--skip-fetch", action="store_true", help="Use paper files already on disk")
    pipeline.add_argument("--max-results", type=int)
    pipeline.add_argument("--request-delay", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            start, end = _dates(args)
            counts = fetch_range(
                start,
                end,
                args.data_dir,
                FetchConfig(
                    tuple(args.categories), args.page_size, args.request_delay, max_results=args.max_results
                ),
            )
            _report_counts("Stored", counts)
        elif args.command == "discover":
            _discover(args)
        elif args.command == "link":
            result = link_topics(
                args.data_dir,
                similarity_threshold=args.similarity_threshold,
                lookback_days=args.lookback_days,
            )
            print(f"Linked {len(result['topics'])} stable topics")
        elif args.command == "trends":
            result = calculate_trends(args.data_dir)
            print(f"Calculated {len(result['topics'])} topic trends across {len(result['dates'])} days")
            _report_sparse_days(result, args.data_dir)
        elif args.command == "build":
            result = build_site(args.data_dir, args.site_dir)
            print(f"Built {result['topic_count']} topics and {result['paper_count']} papers in {result['output']}")
        elif args.command == "pipeline":
            _pipeline(args)
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


def _pipeline(args: argparse.Namespace) -> None:
    start, end = _dates(args)
    if not args.skip_fetch:
        counts = fetch_range(
            start,
            end,
            args.data_dir,
            FetchConfig(
                categories=tuple(args.categories),
                request_delay=args.request_delay,
                max_results=args.max_results,
            ),
        )
        _report_counts("Stored", counts)
    discover_args = argparse.Namespace(
        data_dir=args.data_dir,
        date=None,
        start=start.isoformat(),
        end=end.isoformat(),
        check=False,
    )
    _discover(discover_args)
    linked = link_topics(args.data_dir)
    trend_data = calculate_trends(args.data_dir)
    _report_sparse_days(trend_data, args.data_dir)
    site = build_site(args.data_dir, args.site_dir)
    print(
        f"Pipeline complete: {len(linked['topics'])} linked topics, "
        f"{len(trend_data['dates'])} days, site at {site['output']}"
    )


def _discover(args: argparse.Namespace) -> None:
    """Turn proposals into topic files, and name the days that have no proposal yet.

    Grouping happens outside this process now, so a missing proposal is a normal state and
    not a failure: the rest of the pipeline still runs on the days that are grouped, and the
    agent driving the update reads these lines to know what is left to write.
    """
    paper_dir = args.data_dir / "papers"
    paths = [paper_dir / f"{parse_date(args.date).isoformat()}.json"] if args.date else dated_files(paper_dir)
    if not args.date and getattr(args, "start", None):
        first = parse_date(args.start).isoformat()
        last = parse_date(args.end).isoformat() if getattr(args, "end", None) else first
        paths = [path for path in paths if first <= path.stem <= last]
    if not paths:
        raise FileNotFoundError(f"No dated paper files in {paper_dir}")
    statuses = [
        day_status(
            path,
            args.data_dir / "topics" / path.name,
            args.data_dir / "cache" / "proposals" / path.name,
        )
        for path in paths
    ]
    pending = [status for status in statuses if not status["current"]]
    if args.check:
        _report_pending(pending, len(statuses))
        return
    ingested = 0
    for status in pending:
        if not status["has_proposal"]:
            continue
        result = ingest_proposals(
            status["paper_path"],
            status["proposal_path"],
            args.data_dir / "topics" / status["paper_path"].name,
        )
        ingested += 1
        print(
            f"Ingested {len(result['topics'])} topics for {status['date']} "
            f"({result['outlier_count']} of {result['paper_count']} papers left ungrouped)"
        )
    missing = [status for status in pending if not status["has_proposal"]]
    print(f"Topic ingest complete: {ingested} day(s) built, {len(missing)} awaiting a proposal")
    for status in missing:
        print(
            f"warning: {status['date']} has no grouping; write {status['proposal_path']} "
            f"for its {status['paper_count']} papers",
            file=sys.stderr,
        )


def _report_pending(pending: list[dict], total: int) -> None:
    if not pending:
        print(f"All {total} stored day(s) are grouped")
        return
    for status in pending:
        note = ""
        if status["has_proposal"]:
            note = (
                f" (a proposal on disk covers {status['proposal_covers']} of them; "
                "rewrite it for the papers now stored)"
            )
        print(
            f"{status['date']} needs grouping: {status['paper_count']} papers"
            f"{note} -> write {status['proposal_path']}"
        )
    print(f"{len(pending)} of {total} day(s) need grouping")


def _date_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="One YYYY-MM-DD date")
    parser.add_argument("--start", help="First YYYY-MM-DD date")
    parser.add_argument("--end", help="Last YYYY-MM-DD date (inclusive)")
    parser.add_argument(
        "--catch-up",
        action="store_true",
        help="Run from where the stored papers end through yesterday",
    )
    parser.add_argument(
        "--overlap-days",
        type=int,
        default=5,
        help="With --catch-up, how many recent days to re-ask for (default 5)",
    )


def _catch_up_range(data_dir: Path, end: date, overlap_days: int) -> tuple[date, date]:
    """Resume where the stored papers end, so a gap of any length heals itself instead of
    falling outside a window pinned to today.

    The overlap re-asks for the newest days because arXiv announces on a delay and a day
    first fetched near its own date is usually still filling. Days older than the newest
    stored one need no overlap of their own: the run that stored that day re-asked for them
    the same way."""
    if overlap_days < 1:
        raise ValueError("--overlap-days must be at least 1")
    start = end - timedelta(days=overlap_days - 1)
    stored = dated_files(data_dir / "papers")
    latest = parse_date(stored[-1].stem) if stored else None
    if latest and latest < start:
        start = latest
    stored_through = f"papers stored through {latest}" if latest else "no papers stored yet"
    print(f"Catching up {start} through {end} ({stored_through})")
    return start, end


def _dates(args: argparse.Namespace):
    if args.catch_up and (args.date or args.start or args.end):
        raise ValueError("Use --catch-up or explicit dates, not both")
    if args.date and (args.start or args.end):
        raise ValueError("Use --date or --start/--end, not both")
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    if args.catch_up:
        return _catch_up_range(args.data_dir, yesterday, args.overlap_days)
    if args.date:
        target = parse_date(args.date)
        return target, target
    start = parse_date(args.start) if args.start else yesterday
    end = parse_date(args.end) if args.end else start
    if start > end:
        raise ValueError("Start date must not be after end date")
    return start, end


def _report_counts(action: str, counts: dict[str, int]) -> None:
    for day, count in counts.items():
        print(f"{action} {count} papers for {day}")


def _report_sparse_days(trend_data: dict, data_dir: Path) -> None:
    """A day fetched only in part scores as a real dip, and nothing downstream can tell the
    difference, so say so here: re-fetching the day is what fixes it.

    Trends counts papers through topics, so a day whose papers are stored but not grouped yet
    reads as the same dip and needs the opposite fix. Telling the two apart takes the papers
    file, which only this layer has."""
    for day in trend_data.get("sparse_days", []):
        stored = len((read_json(data_dir / "papers" / f"{day['date']}.json") or {}).get("papers", []))
        if stored > day["papers"]:
            print(
                f"warning: {day['date']} holds {stored} papers that no topic accounts for "
                "yet; it is waiting to be grouped, not to be re-fetched",
                file=sys.stderr,
            )
        else:
            print(
                f"warning: {day['date']} holds {day['papers']} papers, well under the "
                f"{day['expected']} its weekday usually carries; consider re-fetching it",
                file=sys.stderr,
            )


if __name__ == "__main__":
    sys.exit(main())
