---
name: daily-update
description: Run the trending-tracker daily update for this repository - fetch new arXiv papers, group every ungrouped day into research topics, then link, score, build, and commit. Use for the scheduled daily update, for a backfill, or whenever `trending-tracker discover --check` reports days that need grouping.
---

# trending-tracker daily update

You are the grouping model for this pipeline. Nothing in the repository calls a model API:
`trending-tracker` fetches papers and does every deterministic step, and it stops where a
judgment call starts. Your job is that judgment call — reading a day's papers and deciding
which research topics they form — plus running the commands around it.

Run the whole update on `gpt-5.6-luna`, from fetch through commit. If this session is on any
other model, stop and have it relaunched on it (`codex exec -m gpt-5.6-luna`, or `/model` in
the TUI) before grouping anything.

Work from the repository root. The environment is managed with uv: run `uv sync` first, then
prefix each command below with `uv run` (`uv run trending-tracker fetch ...`). If the venv is
already active, `trending-tracker` on PATH works as written.

## Steps

1. **Fetch.** `--catch-up` runs from where `data/papers/` ends through yesterday. Today is
   never fetched: arXiv announces on a delay. Fetching merges, it never replaces, which is
   what lets a missed day heal itself.

   ```bash
   trending-tracker fetch --catch-up
   ```

   It prints the window it resolved. When the data is current that is the last 5 days
   (`--overlap-days` resizes it), re-asking for the days arXiv may still have been filling.
   When the newest stored day is older than that, the window reaches back to it instead, so
   an update skipped for a week fills the whole gap rather than leaving a hole in the middle.
   Days older than the newest stored one are not re-asked — the run that stored it covered
   them with its own overlap.

2. **See what needs grouping.**

   ```bash
   trending-tracker discover --check
   ```

   It prints one line per day that has papers but no current grouping, with the proposal path
   to write. A day is listed when it has never been grouped or when papers arrived after it
   was. If nothing is listed, skip to step 5. Write a fresh proposal for every listed day even
   if a proposal file is already there — an existing one was written for fewer papers.

3. **Group each listed day.** Read `data/papers/YYYY-MM-DD.json` (each paper has `id`, `title`,
   `abstract`) and write `data/cache/proposals/YYYY-MM-DD.json`:

   ```json
   {
     "date": "2026-08-15",
     "model": "gpt-5.6-luna",
     "topics": [
       {
         "name": "Readable topic name",
         "summary": "Why these papers belong together.",
         "keywords": ["term", "term"],
         "paper_ids": ["2608.13897", "2608.13905"]
       }
     ]
   }
   ```

   Follow the rules below. A day of 200–500 papers usually forms 25–60 topics.

4. **Ingest and check the report.**

   ```bash
   trending-tracker discover
   ```

   This validates the proposal and builds `data/topics/YYYY-MM-DD.json`. Read its warnings:
   they name the only problems you can still fix. Rewrite the offending part of the proposal
   and run it again until it is quiet. Warnings mean:

   - *summaries look like boilerplate / copy a source paper* — rewrite those summaries.
   - *dropped N paper IDs* — you invented IDs or claimed one paper twice; correct them.
   - *N of M papers were left ungrouped* — your proposal covers a stale copy of the day;
     re-read the papers file and regroup it.

5. **Link, score, build, commit.** These are deterministic and safe to re-run.

   ```bash
   trending-tracker link && trending-tracker trends && trending-tracker build
   git add data &&
     git commit --author="Codex <noreply@openai.com>" \
       -m "📈 data: update arXiv trends $(date -u +%F)" &&
     git push
   ```

   `trends` warns about days that carry far fewer papers than their weekday usually does,
   and says which fix each one needs: re-fetch a day that came up short, group a day that is
   only waiting on you. Either way, do not leave it to score as a real dip. `site/` and
   `data/cache/` are gitignored — commit `data/papers`, `data/topics`, `data/index`, and
   `data/trends` only. The commit is authored as `Codex <noreply@openai.com>` — the grouping
   inside it is yours, not the machine owner's — while the committer stays whoever ran the
   update. Pushing is what publishes: CI rebuilds and deploys the site from the committed
   data.

## Grouping rules

- **Fine-grained and coherent.** A topic is a theme a reader would recognize, not a
  department. "Infrared small-target detection" over "Computer vision".
- **One topic per paper, at most.** Every ID must come from that day's papers file. Leave
  genuine outliers out — they are recorded as outliers, not lost.
- **Names** are readable and under 100 characters. Reuse the wording of a topic you gave
  the same theme on an earlier day when it fits; day-to-day matching reads names as one of
  its two channels, and gratuitous renaming weakens a chain.
- **Summaries** say why these papers belong together, in one or two sentences. Two gates
  reject the shapes a rushed pass produces, and a rejected summary never becomes the
  topic's canonical text:
  - It must not be reconstructible from the name and keywords alone. "Research on X,
    covering methods, datasets, and evaluation" carries nothing the card does not show.
  - It must not be one paper's voice or one paper's words. No "This paper proposes…",
    no "We introduce…", and no eight-word run copied from any abstract in the day.
  - Write what the collection shares that the name does not already say: the methods,
    the setting, the tension between the papers.
- **Keywords**: up to 8 short terms.
- **Large days.** Above roughly 150 papers, split the day into chunks, group each chunk in
  parallel (subagents are worth it here), then merge before writing. Spawn every subagent with
  `model` set to `gpt-5.6-luna`; if that field is rejected, spawn without it — subagents inherit
  the session model, which the rule above has already pinned. Merging means: fold duplicate or
  overlapping topics into one, split anything that became a grab-bag, and check that no
  paper ID appears twice across the merged set.

## What not to do

- Do not edit `data/topics/`, `data/index/`, or `data/trends/` by hand. They are built.
  The proposal is the only file you author.
- Do not regroup a day that `discover --check` did not list. Stored history is what the
  topic chains and momentum scores are computed from, and rewriting it churns them.
- Do not lower `--similarity-threshold` on `link`; the default was calibrated end to end.
