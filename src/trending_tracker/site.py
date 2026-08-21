from __future__ import annotations

import shutil
from pathlib import Path

from .io import dated_files, read_json, utc_now, write_json


def build_site(data_dir: Path, output_dir: Path) -> dict:
    trends = read_json(data_dir / "trends" / "trends.json")
    if trends is None:
        raise FileNotFoundError(f"No trends in {data_dir / 'trends'}; run trends first")
    papers: dict[str, dict] = {}
    paper_days: dict[str, int] = {}
    for path in dated_files(data_dir / "papers"):
        payload = read_json(path)
        stored = payload.get("papers", [])
        paper_days[payload["date"]] = len(stored)
        for paper in stored:
            papers[paper["id"]] = paper

    # The site only links the papers a topic cites, and only by title and URL. Shipping every
    # stored record (abstracts included) put tens of megabytes of unread JSON on the page load.
    linked_ids = {
        paper_id for topic in trends["topics"] for paper_id in topic.get("latest_paper_ids", [])
    }
    linked_papers = {
        paper_id: {"title": papers[paper_id]["title"], "url": papers[paper_id]["url"]}
        for paper_id in sorted(linked_ids)
        if paper_id in papers
    }

    # Keywords stay in the trends file because linking scores against them, but nothing on
    # the page reads them; shipping them added 4% to the download for no rendered pixel.
    dashboard = {
        **trends,
        "topics": [
            {key: value for key, value in topic.items() if key != "keywords"}
            for topic in trends["topics"]
        ],
        "site_generated_at": utc_now(),
        "papers": linked_papers,
        "paper_days": paper_days,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    # Browsers parse this, nobody reads it; indentation would be a third of the download.
    write_json(output_dir / "data" / "dashboard.json", dashboard, compact=True)
    web_dir = Path(__file__).parent / "web"
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(web_dir / "style.css", assets / "style.css")
    shutil.copyfile(web_dir / "app.js", assets / "app.js")
    (output_dir / ".nojekyll").touch()
    # The weekly view is the landing page: seven days is the shortest window on which
    # arXiv volume reads as a trend rather than announcement-calendar noise.
    (output_dir / "404.html").write_text(_html("weekly", "."), encoding="utf-8")
    (output_dir / "index.html").write_text(_html("weekly", "."), encoding="utf-8")
    for view in ("weekly", "monthly"):
        directory = output_dir / view
        directory.mkdir(exist_ok=True)
        (directory / "index.html").write_text(_html(view, ".."), encoding="utf-8")
    return {"output": str(output_dir), "paper_count": len(papers), "topic_count": len(trends["topics"])}


_TITLES = {
    "weekly": "Weekly research trends",
    "monthly": "Monthly research trends",
}


def _html(view: str, root: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Emerging AI research topics from arXiv">
  <title>{_TITLES[view]} · trending-tracker</title>
  <link rel="stylesheet" href="{root}/assets/style.css">
</head>
<body data-view="{view}" data-root="{root}">
  <header class="topbar">
    <a class="brand" href="{root}/"><span class="brand-mark">TT</span><span>trending tracker</span></a>
    <nav aria-label="Trend period">
      <a href="{root}/weekly/" data-nav="weekly">Week</a>
      <a href="{root}/monthly/" data-nav="monthly">Month</a>
    </nav>
    <a class="source-link" href="https://arxiv.org" rel="noreferrer">Source: arXiv ↗</a>
  </header>
  <main>
    <section class="hero">
      <p class="eyebrow">AI RESEARCH SIGNAL</p>
      <h1 id="headline">Weekly momentum,<br><em>without the noise.</em></h1>
      <p class="intro" id="period-description">Unsupervised topic discovery across newly submitted AI papers.</p>
      <div class="dateline"><span class="pulse"></span><span id="latest-date">Loading data…</span></div>
    </section>
    <section class="overview" aria-label="Overview">
      <div><span class="metric" id="metric-papers">—</span><span class="metric-label" id="metric-papers-label">papers tracked</span></div>
      <div><span class="metric" id="metric-topics">—</span><span class="metric-label">active topics</span></div>
      <div><span class="metric accent" id="metric-rising">—</span><span class="metric-label">on the rise</span></div>
    </section>
    <section class="content">
      <div class="section-head">
        <div><p class="eyebrow" id="ranked-by">RANKED BY MOMENTUM</p><h2>Research topics</h2></div>
        <div class="filters" role="group" aria-label="Filter topics">
          <button class="active" data-filter="all">All</button>
          <button data-filter="bursting">Bursting</button>
          <button data-filter="rising">Rising</button>
          <button data-filter="new">New</button>
          <button data-filter="fading">Fading</button>
        </div>
      </div>
      <div id="topics" class="topic-list" aria-live="polite"></div>
      <p id="empty" class="empty" hidden>No topics match this filter.</p>
      <button id="show-more" class="show-more" hidden>Show more</button>
    </section>
  </main>
  <footer><span>Built from titles and abstracts. Updated daily.</span><span id="generated-at"></span></footer>
  <script src="{root}/assets/app.js" defer></script>
</body>
</html>
"""

