(() => {
  "use strict";
  const body = document.body;
  const view = body.dataset.view || "weekly";
  const root = body.dataset.root || ".";
  const PAGE = 30;
  const periods = {
    weekly: {
      days: 7,
      spark: 28,
      unit: "past 7 days",
      headline: "Weekly momentum,<br><em>without the noise.</em>",
      rankedBy: "RANKED BY 7-DAY MOMENTUM",
      description: "Research themes gaining or losing ground across seven days of new AI submissions — the primary trend signal."
    },
    monthly: {
      days: 30,
      spark: 90,
      unit: "past 30 days",
      headline: "The wider arc,<br><em>month over month.</em>",
      rankedBy: "RANKED BY 30-DAY MOMENTUM",
      description: "Thirty days of sustained themes, fresh bursts, and topics winding down."
    }
  };
  const period = periods[view];
  let data;
  let filter = "all";
  let shown = PAGE;

  document.querySelector(`[data-nav="${view}"]`)?.classList.add("active");
  document.getElementById("headline").innerHTML = period.headline;
  document.getElementById("period-description").textContent = period.description;
  document.getElementById("ranked-by").textContent = period.rankedBy;

  fetch(`${root}/data/dashboard.json`)
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(payload => { data = payload; render(); })
    .catch(error => {
      document.getElementById("latest-date").textContent = "Data unavailable";
      document.getElementById("topics").innerHTML = `<p class="empty">Could not load trend data: ${escapeHtml(error.message)}</p>`;
    });

  document.querySelectorAll("[data-filter]").forEach(button => {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      shown = PAGE;
      document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("active", item === button));
      renderTopics();
    });
  });

  document.getElementById("show-more").addEventListener("click", () => {
    shown += 60;
    renderTopics();
  });

  function stats(topic) { return topic.periods[view]; }

  function render() {
    const latest = data.dates.at(-1);
    document.getElementById("latest-date").textContent = latest
      ? `Latest data · ${new Date(`${latest}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" })}`
      : "Waiting for the first run";
    const windowDates = new Set(data.dates.slice(-period.days));
    const paperCount = Object.entries(data.paper_days).reduce((sum, [day, count]) => sum + (windowDates.has(day) ? count : 0), 0);
    const active = data.topics.filter(topic => stats(topic).count > 0);
    document.getElementById("metric-papers").textContent = number(paperCount);
    document.getElementById("metric-papers-label").textContent = `papers ${period.unit}`;
    document.getElementById("metric-topics").textContent = number(active.length);
    document.getElementById("metric-rising").textContent = number(active.filter(topic => ["rising", "bursting"].includes(stats(topic).status)).length);
    document.getElementById("generated-at").textContent = data.site_generated_at ? `Site generated ${new Date(data.site_generated_at).toLocaleString()}` : "";
    renderTopics();
  }

  function renderTopics() {
    if (!data) return;
    const visible = data.topics
      .filter(topic => stats(topic).count > 0)
      .filter(topic => filter === "all" || stats(topic).status === filter)
      .sort((a, b) => (stats(b).score - stats(a).score) || (stats(b).count - stats(a).count));
    const list = document.getElementById("topics");
    const more = document.getElementById("show-more");
    document.getElementById("empty").hidden = visible.length > 0;
    list.innerHTML = visible.slice(0, shown).map((topic, index) => topicHtml(topic, index)).join("");
    const remaining = Math.max(0, visible.length - shown);
    more.hidden = remaining === 0;
    more.textContent = `Show more (${number(remaining)} of ${number(visible.length)} hidden)`;
  }

  function topicHtml(topic, index) {
    const current = stats(topic);
    const papers = topic.latest_paper_ids.map(id => {
      const paper = data.papers[id];
      return paper ? `<li><a href="${escapeAttr(paper.url)}" target="_blank" rel="noreferrer">${escapeHtml(paper.title)}</a></li>` : "";
    }).filter(Boolean);
    const paperList = papers.length
      ? `<details class="paper-list"><summary>Recent papers <span>${number(papers.length)}</span></summary><ul>${papers.join("")}</ul></details>`
      : "";
    return `<article class="topic">
      <span class="rank">${String(index + 1).padStart(2, "0")}</span>
      <div class="topic-body">
        <h3>${escapeHtml(topic.name)}</h3>
        <p class="topic-summary">${escapeHtml(topic.summary)}</p>
        ${paperList}
      </div>
      <div>
        <div class="chart-wrap">${sparkline(topic.counts)}<div class="count"><strong>${number(current.count)}</strong><span>${period.unit}</span></div></div>
        <span class="status ${escapeAttr(current.status)}">${escapeHtml(current.status)}</span>
      </div>
    </article>`;
  }

  function sparkline(counts) {
    // Always chart a trailing context window so a single day can never masquerade
    // as a trend line; a young dataset is left-padded with zeros.
    const series = counts.slice(-period.spark);
    while (series.length < 2) series.unshift(0);
    const max = Math.max(...series, 1);
    const points = series.map((value, index) => `${(index / (series.length - 1)) * 108 + 1},${40 - (value / max) * 36}`).join(" ");
    const last = points.split(" ").at(-1).split(",");
    return `<svg class="spark" viewBox="0 0 110 42" role="img" aria-label="Paper volume, trailing ${series.length} days"><path class="area" d="M ${points.replaceAll(" ", " L ")} L 109 42 L 1 42 Z"></path><polyline points="${points}"></polyline><circle cx="${last[0]}" cy="${last[1]}" r="2.5"></circle></svg>`;
  }

  function number(value) { return new Intl.NumberFormat().format(value || 0); }
  function escapeHtml(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
  function escapeAttr(value) { return escapeHtml(value).replaceAll('"', "&quot;"); }
})();
