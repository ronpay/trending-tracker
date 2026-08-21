# Data layout

```text
data/
├── papers/YYYY-MM-DD.json          # normalized arXiv records
├── cache/proposals/YYYY-MM-DD.json # groupings the agent wrote, gitignored
├── topics/YYYY-MM-DD.json          # daily topics with centroids and stable IDs
├── index/topics.json               # stable topic registry + link events
└── trends/trends.json              # time series consumed by the site

site/
├── weekly/index.html
├── monthly/index.html
├── data/dashboard.json
└── assets/
```

Paper files are merged by arXiv ID, so overlapping backfills are safe. Topic linking and
trend calculation are rebuilt chronologically from the stored daily files, which makes
changed thresholds or corrected inputs reproducible. Proposals are the agent's raw output
and are gitignored; the topic files built from them are the record that is kept.
