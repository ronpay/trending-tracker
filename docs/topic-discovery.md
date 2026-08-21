# How topic discovery works

## Proposals

`trending-tracker discover --check` names every day that has papers but no current
grouping. The agent reads that day's papers file and writes
`data/cache/proposals/YYYY-MM-DD.json` — topics with names, summaries, keywords, and paper
IDs. `trending-tracker discover` turns proposals into topic files.

The proposal is a suggestion in the model's words; nothing downstream depends on it being
well-formed. Ingestion drops IDs that are not in the day or already claimed, so a paper
lands in at most one topic; papers no topic claimed become outliers rather than
disappearing; names, summaries, and keyword lists are truncated. What was wrong is printed
on stderr — invented IDs, duplicated ones, summaries that failed a gate, a proposal that
covers fewer papers than the day now holds — because the agent that wrote the proposal is
the only thing that can fix it, and a rewritten proposal re-ingested costs one more pass.

A day is offered for grouping again only when papers arrived after it was grouped. That is
what keeps a widened fetch window from regrouping history: re-asking arXiv for the last
five days is free, while regrouping a day costs a pass over every abstract in it.

Grouping is the model's decision; centroids are not. Each topic's TF-IDF centroid is
computed locally from the same vectors the linker compares, so cross-day matching never
depends on a model being consistent between runs.

## Summary quality gates

Two gates guard the summaries, because a degraded model run fails in predictable shapes.
`summary_is_boilerplate` rejects text that could be regenerated from the topic name alone.
`summary_is_pasted` rejects a single paper's voice ("This paper proposes…") and any
verbatim eight-word run lifted from a source abstract — pasted text is maximally
distinctive, which is exactly how it slips past the first gate. Flagged summaries are
reported on stderr and never become a topic's canonical summary.

## Cross-day linking

After discovery, each topic is matched against topics active in the trailing 30 days.
Similarity combines centroid cosine (what the papers say) with label cosine over name and
keyword tokens (what the topic is about); small-sample centroids of the same theme rarely
exceed ~0.2 cosine on their own, so the label channel carries the theme while the content
channel keeps unrelated paper sets from merging on a shared name. Matching is many-to-one:
when discovery cuts a theme finer than yesterday, the fragments rejoin one topic instead
of forking into spurious new ones. The default `--similarity-threshold` of `0.2` was
calibrated end-to-end; lowering it contaminates topic chains.

## Momentum

Momentum is scored per view. The weekly and monthly windows are each compared against up
to three preceding windows of the same length, so a monthly view is never ranked by one
week of churn. A topic first appearing inside the current window has nothing to burst
against and is labelled `new` with a volume-only score, so novelty cannot outrank genuine
movement.

Seven days is the shortest window published. arXiv volume swings nearly threefold across a
week — Saturday carries barely a third of Monday's — and a single topic's day is a handful
of papers, so a one-day window ranks announcement schedule and counting noise rather than
research. Data is still stored and charted per day: the sparkline on every card plots the
per-day series across the scored window and the windows it is compared against.
