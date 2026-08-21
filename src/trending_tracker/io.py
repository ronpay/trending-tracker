from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any, compact: bool = False) -> None:
    """Atomically write stable JSON, readable by default and minified when `compact`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    else:
        payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def dated_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.glob("????-??-??.json")
        if path.is_file() and _is_iso_date(path.stem)
    )


def _is_iso_date(value: str) -> bool:
    try:
        parse_date(value)
        return True
    except ValueError:
        return False


def date_range(start: date, end: date) -> Iterable[date]:
    if start > end:
        raise ValueError("Start date must not be after end date")
    current = start
    while current <= end:
        yield current
        current = date.fromordinal(current.toordinal() + 1)

