const REPO_RAW = "https://raw.githubusercontent.com/mis2-ship-it/item_inventory-reports/main/";
const LIVE_INDEX_URL = REPO_RAW + "dashboard_data/live_index.json";
const HISTORY_INDEX_URL = REPO_RAW + "dashboard_data/history_index.json";

function doGet() {
  return HtmlService.createHtmlOutputFromFile("Index")
    .setTitle("Product Level Dashboard")
    .addMetaTag("viewport", "width=device-width, initial-scale=1");
}

function getDashboardMeta() {
  const liveIndex = fetchJson_(LIVE_INDEX_URL, true);
  const historyIndex = fetchJson_(HISTORY_INDEX_URL, true);
  const partitions = (historyIndex.files || []).filter(function (f) {
    return Number(f.rows || 0) > 0 && f.path;
  });

  let minDate = "";
  let maxDate = "";
  partitions.forEach(function (p) {
    if (!minDate || p.start_date < minDate) minDate = p.start_date;
    if (!maxDate || p.end_date > maxDate) maxDate = p.end_date;
  });

  const liveDate = String(liveIndex.business_date || "");
  if (liveDate && (!maxDate || liveDate > maxDate)) maxDate = liveDate;
  if (liveDate && (!minDate || liveDate < minDate)) minDate = liveDate;

  const monthSet = {};
  partitions.forEach(function (p) {
    let d = new Date(p.start_date + "T00:00:00Z");
    const end = new Date(p.end_date + "T00:00:00Z");
    while (d <= end) {
      monthSet[d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0")] = true;
      d.setUTCDate(d.getUTCDate() + 1);
    }
  });
  if (liveDate) monthSet[liveDate.slice(0, 7)] = true;

  return {
    updated_at: liveIndex.updated_at || historyIndex.updated_at || "",
    live_updated_at: liveIndex.updated_at || "",
    history_updated_at: historyIndex.updated_at || "",
    live_date: liveDate,
    min_date: minDate,
    max_date: maxDate,
    months: Object.keys(monthSet).sort(),
    brands: unionSorted_(historyIndex.brands || [], liveIndex.brands || []),
    regions: unionSorted_(historyIndex.regions || [], liveIndex.regions || []),
    categories: unionSorted_(historyIndex.categories || [], liveIndex.categories || []),
    partition_count: partitions.length
  };
}

function getDashboardData(filters) {
  filters = filters || {};
  const meta = getDashboardMeta();
  const fromDate = String(filters.fromDate || meta.min_date || "2000-01-01");
  const toDate = String(filters.toDate || meta.max_date || "2099-12-31");
  if (fromDate > toDate) throw new Error("From Date cannot be after To Date.");

  const historyIndex = fetchJson_(HISTORY_INDEX_URL, true);
  const partitions = (historyIndex.files || []).filter(function (p) {
    return p.path && Number(p.rows || 0) > 0 &&
      p.start_date <= toDate && p.end_date >= fromDate;
  });

  const liveIndex = fetchJson_(LIVE_INDEX_URL, true);
  const liveDate = String(liveIndex.business_date || "");
  const rows = [];
  const errors = [];

  partitions.forEach(function (p) {
    try {
      const csvText = fetchText_(REPO_RAW + "dashboard_data/" + p.path);
      const parsed = parseCsvObjects_(csvText);
      parsed.forEach(function (r) {
        const d = String(r["Business Date"] || "").slice(0, 10);
        if (d < fromDate || d > toDate) return;
        // Live data replaces the archive for the same business date.
        if (liveDate && d === liveDate) return;
        rows.push(r);
      });
    } catch (e) {
      errors.push(p.file + ": " + e.message);
    }
  });

  if (liveDate && liveDate >= fromDate && liveDate <= toDate && liveIndex.file) {
    try {
      const liveRows = parseCsvObjects_(fetchText_(REPO_RAW + "dashboard_data/" + liveIndex.file));
      liveRows.forEach(function (r) {
        const d = String(r["Business Date"] || "").slice(0, 10);
        if (d >= fromDate && d <= toDate) rows.push(r);
      });
    } catch (e) {
      errors.push("Live file: " + e.message);
    }
  }

  if (errors.length && !rows.length) {
    throw new Error("Could not load dashboard data. " + errors.join(" | "));
  }

  const filtered = rows.filter(function (r) {
    if (String(r["Ownership"] || "").toUpperCase() !== "COCO") return false;
    if (filters.brand && String(r["brandName"] || "") !== filters.brand) return false;
    if (filters.region && String(r["Region"] || "") !== filters.region) return false;
    if (filters.category && String(r["Category Group"] || "") !== filters.category) return false;
    return true;
  });

  return buildDashboardResult_(filtered, {
    fromDate: fromDate,
    toDate: toDate,
    brand: String(filters.brand || ""),
    region: String(filters.region || ""),
    category: String(filters.category || ""),
    loadedRows: rows.length,
    matchingRows: filtered.length,
    liveDate: liveDate,
    liveUpdatedAt: liveIndex.updated_at || "",
    historyUpdatedAt: historyIndex.updated_at || "",
    partitionCount: partitions.length,
    warnings: errors
  });
}

function buildDashboardResult_(rows, info) {
  let gross = 0, discount = 0, net = 0, qty = 0;
  const invoices = {};
  const products = {}, brands = {}, regions = {}, categories = {}, dates = {};

  rows.forEach(function (r) {
    const invoice = String(r["invoiceNumber"] || "").trim();
    const brand = String(r["brandName"] || "Unknown").trim() || "Unknown";
    const region = String(r["Region"] || "Unmapped").trim() || "Unmapped";
    const category = String(r["Category Group"] || "Others").trim() || "Others";
    const item = String(r["Item Name"] || "Unknown item").trim() || "Unknown item";
    const date = String(r["Business Date"] || "").slice(0, 10);
    const g = num_(r["Gross Amount"]);
    const d = Math.abs(num_(r["Discount"]));
    const n = num_(r["Net Amount"]);
    const q = num_(r["Qty"]);

    gross += g; discount += d; net += n; qty += q;
    if (invoice) invoices[invoice] = true;

    addAgg_(products, [brand, region, category, item].join("||"), {
      item: item, brand: brand, region: region, category: category,
      ordersSet: {}, qty: 0, gross: 0, discount: 0, net: 0
    }, invoice, q, g, d, n);
    addAgg_(brands, brand, {name: brand, ordersSet: {}, qty: 0, gross: 0, discount: 0, net: 0}, invoice, q, g, d, n);
    addAgg_(regions, region, {name: region, ordersSet: {}, qty: 0, gross: 0, discount: 0, net: 0}, invoice, q, g, d, n);
    addAgg_(categories, category, {name: category, ordersSet: {}, qty: 0, gross: 0, discount: 0, net: 0}, invoice, q, g, d, n);
    if (date) {
      if (!dates[date]) dates[date] = {date: date, gross: 0, discount: 0, net: 0, qty: 0, ordersSet: {}};
      dates[date].gross += g;
      dates[date].discount += d;
      dates[date].net += n;
      dates[date].qty += q;
      if (invoice) dates[date].ordersSet[invoice] = true;
    }
  });

  function toAggArray(map, isProduct) {
    return Object.keys(map).map(function (key) {
      const a = map[key];
      const orders = Object.keys(a.ordersSet).length;
      return {
        name: a.name || a.item,
        item: a.item || "",
        brand: a.brand || "",
        region: a.region || "",
        category: a.category || "",
        orders: orders,
        qty: round2_(a.qty),
        gross: round2_(a.gross),
        discount: round2_(a.discount),
        net: round2_(a.net),
        discountPct: a.gross ? round2_(a.discount / a.gross * 100) : 0,
        aov: orders ? round2_(a.net / orders) : 0
      };
    }).sort(function (a, b) { return b.net - a.net; });
  }

  const trend = Object.keys(dates).sort().map(function (date) {
    const d = dates[date];
    return {date: date, net: round2_(d.net), orders: Object.keys(d.ordersSet).length, qty: round2_(d.qty)};
  });

  return {
    info: info,
    kpis: {
      gross: round2_(gross), discount: round2_(discount), net: round2_(net),
      orders: Object.keys(invoices).length, qty: round2_(qty),
      discountPct: gross ? round2_(discount / gross * 100) : 0,
      aov: Object.keys(invoices).length ? round2_(net / Object.keys(invoices).length) : 0
    },
    trend: trend,
    products: toAggArray(products, true),
    brands: toAggArray(brands, false),
    regions: toAggArray(regions, false),
    categories: toAggArray(categories, false)
  };
}

function addAgg_(map, key, base, invoice, qty, gross, discount, net) {
  if (!map[key]) map[key] = base;
  const a = map[key];
  if (invoice) a.ordersSet[invoice] = true;
  a.qty += qty; a.gross += gross; a.discount += discount; a.net += net;
}

function parseCsvObjects_(csvText) {
  const values = Utilities.parseCsv(csvText);
  if (!values || values.length < 2) return [];
  const headers = values[0].map(function (h) { return String(h || "").replace(/^\uFEFF/, "").trim(); });
  const output = [];
  for (let i = 1; i < values.length; i++) {
    const row = values[i];
    if (!row || !row.length) continue;
    const obj = {};
    headers.forEach(function (h, j) { obj[h] = row[j] === undefined ? "" : row[j]; });
    output.push(obj);
  }
  return output;
}

function fetchJson_(url, cacheBust) {
  const suffix = cacheBust ? (url.indexOf("?") >= 0 ? "&" : "?") + "v=" + Date.now() : "";
  const response = UrlFetchApp.fetch(url + suffix, {muteHttpExceptions: true, followRedirects: true});
  if (response.getResponseCode() !== 200) throw new Error("GitHub request failed (" + response.getResponseCode() + "): " + url);
  return JSON.parse(response.getContentText("UTF-8"));
}

function fetchText_(url) {
  const response = UrlFetchApp.fetch(url + (url.indexOf("?") >= 0 ? "&" : "?") + "v=" + Date.now(), {
    muteHttpExceptions: true, followRedirects: true
  });
  if (response.getResponseCode() !== 200) throw new Error("GitHub CSV request failed (" + response.getResponseCode() + "): " + url);
  return response.getContentText("UTF-8");
}

function unionSorted_(a, b) {
  const values = {};
  a.concat(b).forEach(function (x) { const v = String(x || "").trim(); if (v) values[v] = true; });
  return Object.keys(values).sort();
}

function num_(value) {
  const n = Number(String(value === undefined ? "" : value).replace(/,/g, ""));
  return isFinite(n) ? n : 0;
}

function round2_(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}
