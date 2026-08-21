# Automation and GitHub Pages

The update runs where the agent runs, not in CI, because grouping is the step that needs a
model. [`.codex/skills/daily-update/SKILL.md`](../.codex/skills/daily-update/SKILL.md) is
the whole job: fetch with `--catch-up`, which re-asks for the last five days so a delayed
arXiv announcement still lands and reaches further back when the stored papers end earlier,
group every day `discover --check` lists, ingest, link, score, build, and commit `data/`.
Run it on a schedule with whatever already runs on your machine — `codex exec "run the
daily-update skill"` from a cron entry is enough — or by hand after a gap; a gap of any
length is picked up from where the data ends rather than skipped.

Pushing that commit is what publishes. The
[`Publish research trends`](../.github/workflows/pages.yml) workflow rebuilds `site/` from
the committed data and deploys it with the official GitHub Pages actions; it holds no
secret and never writes to `data/`. In the repository settings, set **Pages → Build and
deployment → Source** to **GitHub Actions**.

## Responsible operation

The client identifies itself and waits three seconds between paginated arXiv requests by
default. Keep that delay when running large backfills. Grouping happens wherever the agent
runs; this repository makes no model calls of its own.
