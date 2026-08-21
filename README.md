# trending-tracker

[![Publish research trends](https://github.com/ronpay/trending-tracker/actions/workflows/pages.yml/badge.svg)](https://github.com/ronpay/trending-tracker/actions/workflows/pages.yml)
[![CI](https://github.com/ronpay/trending-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/ronpay/trending-tracker/actions/workflows/ci.yml)
[![Data updated](https://img.shields.io/github/last-commit/ronpay/trending-tracker?path=data&label=data%20updated)](https://github.com/ronpay/trending-tracker/commits)

**[ronpay.github.io/trending-tracker](https://ronpay.github.io/trending-tracker/)** — a weekly-updated board of which AI research topics are rising on arXiv.

Newly submitted AI papers are grouped into topics, the topics are followed from one day to the next, and every topic is scored on how much ground it is gaining or losing. The site ranks them by that momentum and links straight to the papers.

## What's on the site

- **Week and Month views.** Seven days is the primary signal; thirty days shows the wider arc. Each view is scored against its own preceding windows, so a month is never ranked by one week of churn.
- **Topics ranked by momentum.** Every topic has a name, a short summary of what its papers are about, and its paper count for the period.
- **Momentum status.** Topics are labelled `bursting`, `rising`, `new`, `steady`, or `fading`; filter buttons narrow the board to bursting, rising, new, or fading topics.
- **Sparklines.** Per-day paper volume over a trailing window — 28 days on the weekly view, 90 on the monthly — so a single busy day cannot pass for a trend.
- **Recent papers.** Expand any topic to see its latest papers, each linked to arXiv.
- **Overview.** Papers in the period, active topics, and how many of them are on the rise.

## Where the data comes from

Papers are new submissions to arXiv's AI and adjacent categories (cs.AI, cs.LG, stat.ML, cs.CL, cs.CV, cs.RO, cs.NE, cs.MA, cs.IR, cs.HC, cs.SI, eess.AS, eess.IV). Only titles and abstracts are read; no PDFs are downloaded and no citation impact is computed. Topics are not drawn from a fixed taxonomy: a language model groups each day's papers, and the pipeline validates, links, and scores the result — see [how topic discovery works](docs/topic-discovery.md).

The committed data is the database and GitHub Pages is the front end: pushing new data rebuilds and redeploys the site. Data is stored per day and the site refreshes weekly; it goes back to June 2026.

## Running your own

The site is a framework-free static build from the JSON in `data/`. The pipeline is a small Python CLI with no runtime dependencies and no API key — the grouping step is a [Codex skill](.codex/skills/daily-update/SKILL.md) run by an agent, not a model call in the code.

- [Commands](docs/commands.md) — setup, every pipeline stage, backfills, options, and development.
- [Data layout](docs/data-layout.md) — what lives in `data/` and `site/`, and why re-runs are safe.
- [How topic discovery works](docs/topic-discovery.md) — proposals, quality gates, cross-day linking, and momentum scoring.
- [Automation and GitHub Pages](docs/automation.md) — scheduling the update and publishing the site.

## License

MIT
