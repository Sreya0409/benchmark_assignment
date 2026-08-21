const RESULT_URL = "../results/latest.json";

const formatNumber = (value) => {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") return value.toFixed(2);
  return String(value);
};

const get = (object, path) =>
  path.reduce((current, key) => {
    if (!current || typeof current !== "object") return undefined;
    return current[key];
  }, object);

const makeTable = (headers, rows) => {
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const tbody = document.createElement("tbody");

  const headerRow = document.createElement("tr");
  headers.forEach((header) => {
    const th = document.createElement("th");
    th.textContent = header;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.appendChild(thead);
  table.appendChild(tbody);
  return table;
};

const renderTable = (elementId, headers, rows) => {
  const container = document.getElementById(elementId);
  container.replaceChildren(makeTable(headers, rows));
};

const platformNames = (results) => Object.keys(results.platforms || {});

const successfulPlatforms = (results) =>
  platformNames(results).filter(
    (platform) => get(results, ["platforms", platform, "status"]) === "ok",
  );

const bestValue = (results, pathFactory, preferHigher = false) => {
  const candidates = successfulPlatforms(results)
    .map((platform) => ({
      platform,
      value: get(results, pathFactory(platform)),
    }))
    .filter((item) => typeof item.value === "number");

  if (!candidates.length) return { platform: "-", value: undefined };
  candidates.sort((a, b) => (preferHigher ? b.value - a.value : a.value - b.value));
  return candidates[0];
};

const renderKpis = (results) => {
  const completed = successfulPlatforms(results).length;
  const total = platformNames(results).length;
  const traversal = bestValue(results, (platform) => [
    "platforms",
    platform,
    "workloads",
    "traversals",
    "1_hop",
    "percentiles_ms",
    "p95",
  ]);
  const lookup = bestValue(results, (platform) => [
    "platforms",
    platform,
    "workloads",
    "indexed_lookup",
    "percentiles_ms",
    "p50",
  ]);
  const mixed = bestValue(
    results,
    (platform) => [
      "platforms",
      platform,
      "mixed_concurrent",
      "c1",
      "ops_per_second",
    ],
    true,
  );
  const cards = [
    ["Completed", `${completed}/${total}`, "platforms finished"],
    ["Best 1-hop p95", `${formatNumber(traversal.value)} ms`, traversal.platform],
    ["Best indexed p50", `${formatNumber(lookup.value)} ms`, lookup.platform],
    ["Best c1 throughput", `${formatNumber(mixed.value)} qps`, mixed.platform],
  ];

  document.getElementById("kpi-grid").replaceChildren(
    ...cards.map(([label, value, footnote]) => {
      const card = document.createElement("article");
      const labelNode = document.createElement("div");
      const valueNode = document.createElement("div");
      const footnoteNode = document.createElement("div");
      card.className = "kpi-card";
      labelNode.className = "kpi-label";
      valueNode.className = "kpi-value";
      footnoteNode.className = "kpi-footnote";
      labelNode.textContent = label;
      valueNode.textContent = value;
      footnoteNode.textContent = footnote;
      card.append(labelNode, valueNode, footnoteNode);
      return card;
    }),
  );
};

const renderStatus = (results) => {
  const strip = document.getElementById("status-strip");
  const items = platformNames(results).map((platform) => {
    const status = get(results, ["platforms", platform, "status"]) || "unknown";
    const item = document.createElement("div");
    item.className = "status-item";
    const name = document.createElement("div");
    const value = document.createElement("div");
    name.className = "status-name";
    value.className = `status-value ${status}`;
    name.textContent = platform;
    value.textContent = status;
    item.append(name, value);
    return item;
  });
  strip.replaceChildren(...items);
};

const renderTraversal = (results, platforms) => {
  const rows = [];
  ["1_hop", "2_hop", "3_hop"].forEach((hop) => {
    ["p50", "p95"].forEach((percentile) => {
      rows.push([
        `${hop.replace("_", "-")} ${percentile} ms`,
        ...platforms.map((platform) =>
          formatNumber(
            get(results, [
              "platforms",
              platform,
              "workloads",
              "traversals",
              hop,
              "percentiles_ms",
              percentile,
            ]),
          ),
        ),
      ]);
    });
  });
  renderTable("traversal-table", ["metric", ...platforms], rows);
};

const renderLookups = (results, platforms) => {
  const rows = [];
  ["point_lookup", "indexed_lookup"].forEach((workload) => {
    ["p50", "p95"].forEach((percentile) => {
      rows.push([
        `${workload.replace("_", " ")} ${percentile} ms`,
        ...platforms.map((platform) =>
          formatNumber(
            get(results, [
              "platforms",
              platform,
              "workloads",
              workload,
              "percentiles_ms",
              percentile,
            ]),
          ),
        ),
      ]);
    });
  });
  renderTable("lookup-table", ["metric", ...platforms], rows);
};

const renderAggregation = (results, platforms) => {
  const rows = ["p50", "p95"].map((percentile) => [
    `aggregation ${percentile} ms`,
    ...platforms.map((platform) =>
      formatNumber(
        get(results, [
          "platforms",
          platform,
          "workloads",
          "aggregation",
          "percentiles_ms",
          percentile,
        ]),
      ),
    ),
  ]);
  renderTable("aggregation-table", ["metric", ...platforms], rows);
};

const renderIngest = (results, platforms) => {
  const formatIngest = (platform, path) => {
    const value = get(results, ["platforms", platform, ...path]);
    if (value !== null && value !== undefined) return formatNumber(value);
    const ingest = get(results, ["platforms", platform, "ingest"]);
    return ingest && ingest.skipped ? "Unavailable" : "-";
  };

  const rows = [
    [
      "nodes per second",
      ...platforms.map((platform) => formatIngest(platform, ["ingest", "nodes", "nodes_per_second"])),
    ],
    [
      "relationships per second",
      ...platforms.map((platform) => formatIngest(platform, ["ingest", "edges", "rels_per_second"])),
    ],
    [
      "total load wall-clock seconds",
      ...platforms.map((platform) => formatIngest(platform, ["ingest", "total_wall_clock_seconds"])),
    ],
  ];
  renderTable("ingest-table", ["metric", ...platforms], rows);
};

const renderMixed = (results, platforms) => {
  const rows = ["c1", "c10", "c40"].map((concurrency) => [
    `${concurrency} qps`,
    ...platforms.map((platform) =>
      formatNumber(
        get(results, [
          "platforms",
          platform,
          "mixed_concurrent",
          concurrency,
          "ops_per_second",
        ]),
      ),
    ),
  ]);
  renderTable("mixed-table", ["metric", ...platforms], rows);
};

const renderFailures = (results) => {
  const caveats = [
    "CognoDB fresh-ingestion throughput is unavailable, not zero: a safe isolated reset could not be completed after a relationship-delete/commit inconsistency.",
    "TigerGraph setup uses schema-first GSQL rather than ad hoc Cypher/AQL inserts.",
    "Memgraph uses Bolt-compatible ingest, but its index DDL differs from Neo4j Aura.",
  ];

  const list = document.createElement("ul");
  platformNames(results)
    .filter((platform) => get(results, ["platforms", platform, "status"]) === "failed")
    .forEach((platform) => {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      const error = get(results, ["platforms", platform, "error"]) || "unknown error";
      label.textContent = `${platform}: `;
      item.append(label, document.createTextNode(error));
      list.appendChild(item);
    });

  caveats.forEach((caveat) => {
    const item = document.createElement("li");
    item.textContent = caveat;
    list.appendChild(item);
  });

  document.getElementById("failures").replaceChildren(list);
};

const render = (results) => {
  const platforms = platformNames(results);
  document.getElementById("run-meta").textContent = [
    `Started: ${results.started_at_utc || "-"}`,
    `Finished: ${results.finished_at_utc || "-"}`,
  ].join(" | ");

  renderStatus(results);
  renderKpis(results);
  renderTraversal(results, platforms);
  renderLookups(results, platforms);
  renderAggregation(results, platforms);
  renderIngest(results, platforms);
  renderMixed(results, platforms);
  renderFailures(results);
};

fetch(RESULT_URL)
  .then((response) => {
    if (!response.ok) throw new Error(`Could not load ${RESULT_URL}`);
    return response.json();
  })
  .then(render)
  .catch((error) => {
    document.getElementById("run-meta").textContent = error.message;
  });
