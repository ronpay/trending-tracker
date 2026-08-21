# Commands

Every command is local and can be re-run freely. Global `--data-dir` and `--site-dir`
options go before the command. Use `trending-tracker COMMAND --help` for linking
thresholds, rate limits, and other options.

## Setup

The environment is managed with [uv](https://docs.astral.sh/uv/). It reads
`.python-version` and fetches that interpreter itself, so no Python install of your own is
required; there are no runtime dependencies and no API key.

```bash
uv sync
```

`uv sync` creates `.venv` from `uv.lock`. The examples below write `trending-tracker`
bare — prefix them with `uv run`, or `source .venv/bin/activate` once and drop the prefix.

With no date option, the fetch and pipeline commands process yesterday; `--catch-up` runs
from the newest day already in `data/papers/` through yesterday instead. arXiv dates are
interpreted as UTC.

## The daily pipeline

```bash
# Fetch yesterday, ingest any grouping written for it, link, score, and build site/
trending-tracker pipeline

# Or resume from where the stored papers end, re-asking for the last 5 days on top
trending-tracker pipeline --catch-up
```

`--catch-up` is what a schedule wants: a window pinned to today skips whatever fell before
it, so a run missed for a week leaves a permanent hole, while catch-up starts at the newest
stored day. `--overlap-days N` sets how many recent days it re-asks for (default 5) — arXiv
announces on a delay, so the newest days are still filling.

`pipeline` runs every step the machine can do alone. A day nobody has grouped yet is named
on stderr rather than guessed at, and the rest of the pipeline still runs on the days that
are grouped. To do the whole update including the grouping, run the skill from the
repository root:

```bash
codex "run the daily-update skill"
```

## Backfills

To backfill a range or change categories:

```bash
trending-tracker pipeline \
  --start 2026-08-01 \
  --end 2026-08-07 \
  --categories cs.AI cs.CV cs.LG cs.CL
```

Paper files are merged by arXiv ID, so overlapping backfills are safe.

## Individual stages

```bash
# Fetch one day
trending-tracker fetch --date 2026-08-15

# Fetch everything missing since the newest stored day, through yesterday
trending-tracker fetch --catch-up

# Name the days that still need grouping, and where to write it
trending-tracker discover --check

# Build topic files from the proposals on disk (or add --date YYYY-MM-DD)
trending-tracker discover

# Recompute stable IDs, trends, and site
trending-tracker link
trending-tracker trends
trending-tracker build

# Use non-default directories
trending-tracker --data-dir archive --site-dir public pipeline --skip-fetch
```

The default `link --similarity-threshold` of `0.2` was calibrated end-to-end; lowering it
contaminates topic chains (see [how topic discovery works](topic-discovery.md)).

## Previewing the site

```bash
uv run python -m http.server 8000 --directory site
```

Open <http://localhost:8000>. Do not open the HTML directly from disk: the browser needs
HTTP to load `data/dashboard.json`.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
```

`uv.lock` pins the dev tools; `uv sync --locked` fails instead of resolving when it has
drifted from `pyproject.toml`, which is what CI runs. Add a dependency with `uv add`, a
dev-only one with `uv add --dev`, and commit the updated lockfile.

Tests cover Atom parsing and idempotent storage, model-response parsing and caching, the
summary quality gates, topic continuity, per-period burst scoring, and a complete
static-site build.
