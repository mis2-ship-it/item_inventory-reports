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
    categories: unionSorted_(historyIndex.category_names || historyIndex.categories || [], liveIndex.category_names || liveIndex.categories || []),
    partition_count: partitions.length
  };
}

function getDashboardData(filters) {
  filters = filters || {};
  const meta = getDashboardMeta();
  const fromDate = String(filters.fromDate || meta.live_date || meta.min_date || "2000-01-01");
  const toDate = String(filters.toDate || fromDate);
  const compareFromDate = String(filters.compareFromDate || "");
  const compareToDate = String(filters.compareToDate || "");
  if (fromDate > toDate) throw new Error("From Date cannot be after To Date.");
  if ((compareFromDate && !compareToDate) || (!compareFromDate && compareToDate)) {
    throw new Error("Select both comparison dates.");
  }
  if (compareFromDate && compareFromDate > compareToDate) throw new Error("Comparison start date cannot be after comparison end date.");

  const period = String(filters.period || "daily");
  let loadFrom = compareFromDate ? (fromDate < compareFromDate ? fromDate : compareFromDate) : fromDate;
  const loadTo = compareToDate ? (toDate > compareToDate ? toDate : compareToDate) : toDate;
  // Load extra history for trend panels: four weeks or three months.
  if (period === "week") {
    const trendStart = shiftDateIso_(fromDate, -21);
    if (trendStart < loadFrom) loadFrom = trendStart;
  } else if (period === "month") {
    const trendStart = shiftMonthStart_(fromDate.slice(0, 7), -2);
    if (trendStart < loadFrom) loadFrom = trendStart;
  }
  const historyIndex = fetchJson_(HISTORY_INDEX_URL, true);
  const liveIndex = fetchJson_(LIVE_INDEX_URL, true);
  const liveDate = String(liveIndex.business_date || "");
  const partitions = (historyIndex.files || []).filter(function (p) {
    return p.path && Number(p.rows || 0) > 0 && p.start_date <= loadTo && p.end_date >= loadFrom;
  });
  const rows = [], errors = [];

  partitions.forEach(function (p) {
    try {
      const parsed = parseCsvObjects_(fetchText_(REPO_RAW + "dashboard_data/" + p.path));
      parsed.forEach(function (r) {
        const d = String(r["Business Date"] || "").slice(0, 10);
        if (d < loadFrom || d > loadTo || (liveDate && d === liveDate)) return;
        rows.push(r);
      });
    } catch (e) { errors.push(p.file + ": " + e.message); }
  });

  if (liveDate && liveDate >= loadFrom && liveDate <= loadTo && liveIndex.file) {
    try {
      parseCsvObjects_(fetchText_(REPO_RAW + "dashboard_data/" + liveIndex.file)).forEach(function (r) {
        const d = String(r["Business Date"] || "").slice(0, 10);
        if (d >= loadFrom && d <= loadTo) rows.push(r);
      });
    } catch (e) { errors.push("Live file: " + e.message); }
  }
  if (errors.length && !rows.length) throw new Error("Could not load dashboard data. " + errors.join(" | "));

  const filtered = rows.filter(function (r) {
    if (String(r["Ownership"] || "").toUpperCase() !== "COCO") return false;
    if (filters.brand && String(r["brandName"] || "") !== filters.brand) return false;
    if (filters.region && String(r["Region"] || "") !== filters.region) return false;
    const categoryName = String(r["Category Name"] || "").trim();
    if (filters.category && categoryName !== filters.category) return false;
    return true;
  });

  const currentRows = filtered.filter(function (r) {
    const d = String(r["Business Date"] || "").slice(0, 10);
    return d >= fromDate && d <= toDate;
  });
  const comparisonRows = compareFromDate ? filtered.filter(function (r) {
    const d = String(r["Business Date"] || "").slice(0, 10);
    return d >= compareFromDate && d <= compareToDate;
  }) : [];

  return {
    info: {
      fromDate: fromDate, toDate: toDate,
      compareFromDate: compareFromDate, compareToDate: compareToDate,
      brand: String(filters.brand || ""), region: String(filters.region || ""),
      category: String(filters.category || ""), loadedRows: rows.length,
      matchingRows: currentRows.length, liveDate: liveDate,
      liveUpdatedAt: liveIndex.updated_at || "", historyUpdatedAt: historyIndex.updated_at || "",
      partitionCount: partitions.length, period: period, warnings: errors
    },
    current: buildDashboardResult_(currentRows),
    comparison: compareFromDate ? buildDashboardResult_(comparisonRows) : null,
    periodTrend: buildPeriodTrend_(filtered, period, fromDate, toDate)
  };
}

function buildDashboardResult_(rows) {
  let gross = 0, discount = 0, net = 0, qty = 0;
  const invoices = {}, products = {}, brands = {}, regions = {}, categories = {};
  const productMix = {}, sessionMix = {}, discounts = {}, dates = {}, sources = {};

  function add(map, key, base, invoice, q, g, d, n) {
    if (!map[key]) map[key] = base;
    const a = map[key];
    if (invoice) a.ordersSet[invoice] = true;
    a.qty += q; a.gross += g; a.discount += d; a.net += n;
  }

  rows.forEach(function (r) {
    const invoice = String(r["invoiceNumber"] || "").trim();
    const brand = String(r["brandName"] || "Unknown").trim() || "Unknown";
    const region = String(r["Region"] || "Unmapped").trim() || "Unmapped";
    const category = String(r["Category Name"] || "").trim() || "Category unavailable in source data";
    const groupedCategory = String(r["Category Group"] || "Others").trim() || "Others";
    const item = String(r["Item Name"] || "Unknown item").trim() || "Unknown item";
    const mix = String(r["Product Mix"] || "Unmapped").trim() || "Unmapped";
    const session = String(r["Session"] || sessionFromDate_(r["createdDate"]) || "Unknown");
    const channel = String(r["channel"] || "").trim();
    const sourceRaw = String(r["Source"] || "").trim();
    const source = /swiggy/i.test(sourceRaw + " " + channel) ? "Swiggy" : (/zomato/i.test(sourceRaw + " " + channel) ? "Zomato" : (sourceRaw || channel || "Other"));
    const date = String(r["Business Date"] || "").slice(0, 10);
    const g = num_(r["Gross Amount"]), d = Math.abs(num_(r["Discount"]));
    const n = num_(r["Net Amount"]), q = num_(r["Qty"]);
    gross += g; discount += d; net += n; qty += q;
    if (invoice) invoices[invoice] = true;

    const dims = {item:item, brand:brand, region:region, category:category, groupedCategory:groupedCategory, mix:mix, session:session, source:source};
    add(products, [item,brand,region,category,mix,source].join("||"), Object.assign({name:item, ordersSet:{},qty:0,gross:0,discount:0,net:0},dims), invoice,q,g,d,n);
    add(brands, brand, {name:brand,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);
    add(regions, region, {name:region,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);
    add(categories, category, {name:category,ordersSet:{},qty:0,gross:0,discount:0,net:0,groupedCategory:groupedCategory},invoice,q,g,d,n);
    add(productMix, mix, {name:mix,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);
    add(sessionMix, session + "||" + mix, {name:session,session:session,mix:mix,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);
    add(sources, source, {name:source,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);

    const platformSource = /swiggy/i.test(sourceRaw + " " + channel) ? "Swiggy" : (/zomato/i.test(sourceRaw + " " + channel) ? "Zomato" : source);
    const platformField = platformSource === "Swiggy" ? "Swiggy Code" : (platformSource === "Zomato" ? "Zomato Code" : "Discount Code");
    const platformCode = String(r[platformField] || "").trim();
    const generalCode = String(r["Discount Code"] || "").trim();
    const isNoOffer = function(v) { return !v || /^no offer$|^-$|^n\/a$/i.test(v); };
    const code = !isNoOffer(platformCode) ? platformCode : (!isNoOffer(generalCode) ? generalCode : "No Offer");
    add(discounts, [platformSource,code].join("||"), {name:code,source:platformSource,ordersSet:{},qty:0,gross:0,discount:0,net:0},invoice,q,g,d,n);

    if (date) {
      if (!dates[date]) dates[date] = {date:date,gross:0,discount:0,net:0,qty:0,ordersSet:{}};
      dates[date].gross += g; dates[date].discount += d; dates[date].net += n; dates[date].qty += q;
      if (invoice) dates[date].ordersSet[invoice] = true;
    }
  });

  function arr(map) {
    return Object.keys(map).map(function (key) {
      const a = map[key], orders = Object.keys(a.ordersSet).length;
      return {name:a.name || "", item:a.item || "", brand:a.brand || "", region:a.region || "",
        category:a.category || "", groupedCategory:a.groupedCategory || "", mix:a.mix || "",
        session:a.session || "", source:a.source || "", orders:orders, qty:round2_(a.qty),
        gross:round2_(a.gross), discount:round2_(a.discount), net:round2_(a.net),
        discountPct:a.gross ? round2_(a.discount/a.gross*100) : 0,
        aov:orders ? round2_(a.net/orders) : 0};
    }).sort(function(a,b){return b.net-a.net;});
  }

  return {
    kpis:{gross:round2_(gross),discount:round2_(discount),net:round2_(net),orders:Object.keys(invoices).length,
      qty:round2_(qty),discountPct:gross?round2_(discount/gross*100):0,
      aov:Object.keys(invoices).length?round2_(net/Object.keys(invoices).length):0,itemLines:rows.length},
    trend:Object.keys(dates).sort().map(function(date){const x=dates[date];return {date:date,net:round2_(x.net),orders:Object.keys(x.ordersSet).length,qty:round2_(x.qty)};}),
    products:arr(products),brands:arr(brands),regions:arr(regions),categories:arr(categories),
    productMix:arr(productMix),sessionMix:arr(sessionMix),discounts:arr(discounts),sources:arr(sources)
  };
}


function shiftDateIso_(iso, days) {
  const d = new Date(String(iso).slice(0, 10) + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + Number(days || 0));
  return d.toISOString().slice(0, 10);
}

function shiftMonthStart_(yyyyMm, offset) {
  const p = String(yyyyMm).split("-");
  const d = new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1 + Number(offset || 0), 1));
  return d.toISOString().slice(0, 10);
}

function buildPeriodTrend_(rows, period, fromDate, toDate) {
  const groups = {};
  rows.forEach(function (r) {
    const date = String(r["Business Date"] || "").slice(0, 10);
    if (!date || date > toDate) return;
    let key = date, label = date;
    if (period === "week") {
      const d = new Date(date + "T00:00:00Z");
      const dow = (d.getUTCDay() + 6) % 7;
      d.setUTCDate(d.getUTCDate() - dow);
      key = d.toISOString().slice(0, 10);
      label = key + " to " + shiftDateIso_(key, 6);
    } else if (period === "month") {
      key = date.slice(0, 7);
      label = key;
    }
    if (!groups[key]) groups[key] = {key:key,label:label,net:0,gross:0,discount:0,qty:0,invoices:{},lines:0};
    const g = groups[key];
    g.net += num_(r["Net Amount"]); g.gross += num_(r["Gross Amount"]);
    g.discount += Math.abs(num_(r["Discount"])); g.qty += num_(r["Qty"]); g.lines++;
    const inv = String(r["invoiceNumber"] || "").trim(); if (inv) g.invoices[inv] = true;
  });
  let out = Object.keys(groups).sort().map(function(k){const g=groups[k];const orders=Object.keys(g.invoices).length;return {key:g.key,label:g.label,net:round2_(g.net),gross:round2_(g.gross),discount:round2_(g.discount),qty:round2_(g.qty),orders:orders,discountPct:g.gross?round2_(g.discount/g.gross*100):0,aov:orders?round2_(g.net/orders):0,lines:g.lines};});
  if (period === "week") out = out.slice(-4);
  else if (period === "month") out = out.slice(-3);
  else out = out.filter(function(x){return x.key >= fromDate && x.key <= toDate;}).slice(-31);
  return out;
}

function sessionFromDate_(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (isNaN(date.getTime())) return "Unknown";
  const hour = (date.getUTCHours() + 5 + (date.getUTCMinutes() >= 30 ? 1 : 0)) % 24;
  if (hour >= 8 && hour <= 11) return "Breakfast";
  if (hour >= 12 && hour <= 15) return "Lunch";
  if (hour >= 16 && hour <= 19) return "Snacks";
  if (hour >= 20 && hour <= 22) return "Dinner";
  return "Post Dinner (Closing / Late Night)";
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
