/* Penny chat client.
 *
 * Mirrors the streaming pattern of the production iOS client this was modelled
 * on: a tokenizer buffers the chunked HTTP body and splits on "\n", each
 * complete line decodes to one UI component, and a renderer is dispatched
 * polymorphically on the "component" key. A component paints the moment it
 * lands, so the answer's first sentence is on screen while the chart behind it
 * is still being generated.
 *
 * Unknown component -> renderUnsupported. Adding a component means adding one
 * entry to RENDERERS; nothing else in the pipeline changes.
 */

const thread = document.getElementById("thread");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

/* ----------------------------------------------------------------- helpers */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const scroll = () => { thread.scrollTop = thread.scrollHeight; };

const money = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : n ?? "";

const compact = (n) => {
  const abs = Math.abs(n);
  if (abs >= 1000) return `${(n / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
  return abs >= 100 ? n.toFixed(0) : n.toFixed(abs >= 10 ? 1 : 2);
};

function mount(node) {
  thread.appendChild(node);
  scroll();
  return node;
}

/* Merchant logos: Google's favicon service - no API key, no signup - falling
   back to a monogram when the domain is missing or the image fails. */
function logoNode(item) {
  if (item.logo_domain) {
    const img = el("img", "logo");
    img.src = `https://www.google.com/s2/favicons?sz=128&domain=${encodeURIComponent(item.logo_domain)}`;
    img.alt = "";
    img.loading = "lazy";
    img.onerror = () => img.replaceWith(monogram(item.merchant));
    return img;
  }
  return monogram(item.merchant);
}

function monogram(merchant = "?") {
  return el("div", "logo-fallback", (merchant.trim()[0] || "?").toUpperCase());
}

/* ------------------------------------------------------------- the stream */

/** Buffers the chunked body and yields one complete JSON object per line.
 *  Tolerant by design: blank lines skipped, a malformed line dropped with a
 *  console warning rather than killing the stream, trailing partial retained. */
class ComponentTokenizer {
  constructor() { this.buffer = ""; }

  process(text) {
    this.buffer += text;
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop();          // last piece may be incomplete
    return this.decode(lines);
  }

  finalize() {
    const rest = this.buffer.trim();
    this.buffer = "";
    return rest ? this.decode([rest]) : [];
  }

  decode(lines) {
    const out = [];
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        out.push(JSON.parse(trimmed));
      } catch {
        console.warn("dropped malformed component line:", trimmed.slice(0, 120));
      }
    }
    return out;
  }
}

/* ---------------------------------------------------------------- charts */

const CHART_W = 300;
const CHART_H = 150;
const PALETTE = ["#4f46e5", "#0d9488", "#f59e0b", "#db2777", "#7c3aed", "#0284c7", "#65a30d", "#dc2626"];

function svg(w, h) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("viewBox", `0 0 ${w} ${h}`);
  node.setAttribute("width", "100%");
  node.setAttribute("role", "img");
  return node;
}

function svgEl(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function chartFrame(title) {
  const wrap = el("div", "viz");
  if (title) wrap.appendChild(el("div", "viz-title", title));
  return wrap;
}

/** data: [{label, values:[n,...]}] - values[i] belongs to series labels[i]. */
function renderBarChart(c) {
  const wrap = chartFrame(c.valueTitle);
  const points = c.data;
  const series = Math.max(...points.map((p) => p.values.length));
  const max = Math.max(...points.flatMap((p) => p.values), 0) || 1;
  const chart = svg(CHART_W, CHART_H);

  const padL = 4, padB = 20, padT = 14;
  const plotH = CHART_H - padB - padT;
  const slot = (CHART_W - padL) / points.length;
  const barW = Math.min(28, (slot * 0.72) / series);

  points.forEach((point, i) => {
    point.values.forEach((value, s) => {
      const h = Math.max(2, (value / max) * plotH);
      const x = padL + i * slot + slot / 2 - (series * barW) / 2 + s * barW;
      chart.appendChild(svgEl("rect", {
        x: x + 1, y: padT + plotH - h, width: barW - 2, height: h,
        rx: 3, fill: PALETTE[s % PALETTE.length],
      }));
      if (series === 1) {
        const label = svgEl("text", {
          x: x + barW / 2, y: padT + plotH - h - 4,
          "text-anchor": "middle", class: "viz-value",
        });
        label.textContent = compact(value);
        chart.appendChild(label);
      }
    });

    const tick = svgEl("text", {
      x: padL + i * slot + slot / 2, y: CHART_H - 6,
      "text-anchor": "middle", class: "viz-tick",
    });
    tick.textContent = point.label;
    chart.appendChild(tick);
  });

  wrap.appendChild(chart);
  if (series > 1) wrap.appendChild(legend(c.labels));
  return wrap;
}

function renderLineChart(c) {
  const wrap = chartFrame(c.valueTitle);
  const points = c.data;
  const series = Math.max(...points.map((p) => p.values.length));
  const max = Math.max(...points.flatMap((p) => p.values), 0) || 1;
  const chart = svg(CHART_W, CHART_H);

  const padL = 8, padR = 8, padB = 20, padT = 14;
  const plotH = CHART_H - padB - padT;
  const plotW = CHART_W - padL - padR;
  const step = points.length > 1 ? plotW / (points.length - 1) : 0;
  const xy = (i, v) => [padL + i * step, padT + plotH - (v / max) * plotH];

  for (let s = 0; s < series; s++) {
    const coords = points.map((p, i) => xy(i, p.values[s] ?? 0));
    const colour = PALETTE[s % PALETTE.length];

    chart.appendChild(svgEl("polyline", {
      points: coords.map(([x, y]) => `${x},${y}`).join(" "),
      fill: "none", stroke: colour, "stroke-width": 2.4,
      "stroke-linecap": "round", "stroke-linejoin": "round",
    }));
    for (const [x, y] of coords) {
      chart.appendChild(svgEl("circle", { cx: x, cy: y, r: 3.2, fill: "#fff", stroke: colour, "stroke-width": 2 }));
    }
  }

  points.forEach((point, i) => {
    const tick = svgEl("text", {
      x: xy(i, 0)[0], y: CHART_H - 6, "text-anchor": "middle", class: "viz-tick",
    });
    tick.textContent = point.label;
    chart.appendChild(tick);
  });

  wrap.appendChild(chart);
  if (series > 1) wrap.appendChild(legend(c.labels));
  return wrap;
}

/** Pie slices are shares of one total: exactly one value per slice, 0-100. */
function renderPieChart(c) {
  const wrap = chartFrame(c.valueTitle);
  const row = el("div", "pie-row");
  const size = 132, r = 58, cx = size / 2, cy = size / 2;
  const chart = svg(size, size);
  chart.style.width = `${size}px`;
  chart.style.flexShrink = "0";

  const total = c.data.reduce((sum, d) => sum + (d.values[0] || 0), 0) || 100;
  let angle = -Math.PI / 2;

  c.data.forEach((slice, i) => {
    const sweep = ((slice.values[0] || 0) / total) * Math.PI * 2;
    const end = angle + sweep;
    const [x1, y1] = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
    const [x2, y2] = [cx + r * Math.cos(end), cy + r * Math.sin(end)];
    chart.appendChild(svgEl("path", {
      d: `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`,
      fill: PALETTE[i % PALETTE.length], stroke: "#fff", "stroke-width": 1.5,
    }));
    angle = end;
  });

  // Donut hole - reads better at phone scale than a solid pie.
  chart.appendChild(svgEl("circle", { cx, cy, r: r * 0.52, fill: "#fff" }));

  const key = el("div", "pie-key");
  c.data.forEach((slice, i) => {
    const item = el("div", "pie-key-item");
    const dot = el("span", "swatch");
    dot.style.background = PALETTE[i % PALETTE.length];
    item.append(dot, el("span", "pie-key-label", slice.label), el("span", "pie-key-val", `${slice.values[0]}%`));
    key.appendChild(item);
  });

  row.append(chart, key);
  wrap.appendChild(row);
  return wrap;
}

function legend(labels = []) {
  const wrap = el("div", "legend");
  labels.forEach((label, i) => {
    const item = el("span", "legend-item");
    const dot = el("span", "swatch");
    dot.style.background = PALETTE[i % PALETTE.length];
    item.append(dot, el("span", null, label));
    wrap.appendChild(item);
  });
  return wrap;
}

/* ------------------------------------------------------------- renderers */

function renderChatResponse(c) {
  return mount(el("div", "msg penny", c.body));
}

function renderTable(c) {
  const wrap = el("div", "viz table-wrap");
  const table = el("table", "data-table");
  const thead = el("thead");
  const hrow = el("tr");
  for (const header of c.headers) hrow.appendChild(el("th", null, header));
  thead.appendChild(hrow);
  const tbody = el("tbody");
  for (const row of c.data) {
    const tr = el("tr");
    row.forEach((cell, i) => {
      const td = el("td", i === 0 ? null : "num", cell);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  table.append(thead, tbody);
  wrap.appendChild(table);
  return mount(wrap);
}

function renderList(c) {
  const wrap = el("div", "viz");
  const list = el(c.component === "ordered-list" ? "ol" : "ul", "bullets");
  for (const item of c.items) list.appendChild(el("li", null, item));
  wrap.appendChild(list);
  return mount(wrap);
}

function renderTransactionList(c) {
  const wrap = el("div", "cards");
  for (const item of c.transactions) {
    const card = el("div", "card");
    const body = el("div", "card-body");
    body.append(el("div", "card-merchant", item.merchant), el("div", "card-sub", item.subtitle || ""));
    card.append(logoNode(item), body, el("div", "card-amount", money(item.amount)));
    wrap.appendChild(card);
  }
  return mount(wrap);
}

function renderSuggestedIntents(c) {
  const wrap = el("div", "intents");
  for (const intent of c.intents.slice(0, 4)) {
    const chip = el("button", "intent", intent);
    chip.type = "button";
    chip.addEventListener("click", () => { if (!busy) ask(intent); });
    wrap.appendChild(chip);
  }
  return mount(wrap);
}

function renderFeedback() {
  const wrap = el("div", "feedback");
  wrap.appendChild(el("span", "feedback-q", "Was this helpful?"));
  for (const [glyph, label] of [["👍", "helpful"], ["👎", "not helpful"]]) {
    const btn = el("button", "feedback-btn", glyph);
    btn.type = "button";
    btn.setAttribute("aria-label", label);
    btn.addEventListener("click", () => {
      wrap.textContent = "Thanks — noted.";
      wrap.classList.add("feedback-done");
    });
    wrap.appendChild(btn);
  }
  return mount(wrap);
}

function renderTryAgainError(c) {
  const wrap = el("div", "msg error");
  wrap.appendChild(el("div", null, c.body));
  const retry = el("button", "retry", "Try again");
  retry.type = "button";
  retry.addEventListener("click", () => { if (!busy && lastQuestion) ask(lastQuestion); });
  wrap.appendChild(retry);
  return mount(wrap);
}

/* A component this client does not know how to draw still gets a place on
   screen - silent omission would hide a backend/client version mismatch. */
function renderUnsupported(c) {
  const wrap = el("div", "unsupported");
  wrap.append(
    el("div", "unsupported-title", "Unsupported UI element"),
    el("div", "unsupported-reason", c.reason || c.component || "unknown component"),
  );
  return mount(wrap);
}

const RENDERERS = {
  "chat-response": renderChatResponse,
  "table": renderTable,
  "ordered-list": renderList,
  "unordered-list": renderList,
  "bar-chart": (c) => mount(renderBarChart(c)),
  "line-chart": (c) => mount(renderLineChart(c)),
  "pie-chart": (c) => mount(renderPieChart(c)),
  "transaction-list": renderTransactionList,
  "suggested-user-intents": renderSuggestedIntents,
  "feedback": renderFeedback,
  "try-again-error": renderTryAgainError,
  "unsupported": renderUnsupported,
};

/* ------------------------------------------------------- smart loading */

/* Covers the gap where the model is calling tools and has produced nothing
   renderable yet. One indicator, relabelled per tool. */
const smartLoading = {
  node: null,
  label: null,
  show(text) {
    if (!this.node) {
      this.node = el("div", "loading");
      const dots = el("span", "loading-dots");
      dots.append(el("i"), el("i"), el("i"));
      this.label = el("span", "loading-label", text);
      this.node.append(dots, this.label);
      mount(this.node);
    } else {
      this.label.textContent = text;
      scroll();
    }
  },
  hide() {
    this.node?.remove();
    this.node = null;
    this.label = null;
  },
};

/* --------------------------------------------------------------- driver */

/* Session lives on the server: we hold only its id and echo it back. History is
   never replayed from the client, so the client cannot rewrite the conversation. */
let sessionId = null;
let busy = false;
let lastQuestion = null;

function handle(component) {
  if (component.component === "done") return;
  if (component.component === "smart-loading") {
    smartLoading.show(component.body);
    return;
  }
  smartLoading.hide();
  (RENDERERS[component.component] || renderUnsupported)(component);
}

/* Reads a chunked JSONL body and dispatches each complete component.
   Shared by the greeting and by every chat turn. */
async function consumeStream(response) {
  const incoming = response.headers.get("x-session-id");
  if (incoming) sessionId = incoming;

  const tokenizer = new ComponentTokenizer();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const component of tokenizer.process(decoder.decode(value, { stream: true }))) {
      handle(component);
    }
  }
  for (const component of tokenizer.finalize()) handle(component);
}

async function ask(message) {
  if (busy || !message.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  input.value = "";
  lastQuestion = message;

  document.querySelectorAll(".intents, .feedback").forEach((n) => n.remove());
  mount(el("div", "msg user", message));
  smartLoading.show("Thinking");

  try {
    const headers = { "Content-Type": "application/json" };
    if (sessionId) headers["x-session-id"] = sessionId;

    const response = await fetch("/agent/chat", {
      method: "POST",
      headers,
      // Production wire contract: the message is the whole body.
      body: JSON.stringify({ input: { content: { body: message } } }),
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed (${response.status})`);
    }
    await consumeStream(response);
  } catch (err) {
    smartLoading.hide();
    renderTryAgainError({ body: `Penny couldn't answer: ${err.message}` });
  } finally {
    smartLoading.hide();
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  ask(input.value);
});

/* Penny opens the conversation. The shell has already painted; the opener
   streams in behind it, so nothing blocks first paint. */
async function loadGreeting() {
  smartLoading.show("Reading your recent spending");
  try {
    const response = await fetch("/agent/greeting");
    if (!response.ok) throw new Error(`greeting unavailable (${response.status})`);
    await consumeStream(response);
  } catch (err) {
    smartLoading.hide();
    mount(el("div", "msg penny", "Hi, I'm Penny \u{1F44B} — ask me anything about your spending."));
    console.warn("greeting failed:", err.message);
  } finally {
    smartLoading.hide();
  }
}

async function boot() {
  try {
    const health = await fetch("/api/health").then((r) => r.json());
    if (health.data) {
      document.getElementById("meta-model").textContent = health.model;
      document.getElementById("meta-range").textContent =
        `${health.data.first_date} \u2192 ${health.data.latest_date}`;
      document.getElementById("meta-count").textContent = health.data.transaction_count;
    }
  } catch {
    renderTryAgainError({
      body: "Backend not reachable. Start it with: uvicorn penny.presentation.http.app:app --reload",
    });
    return;
  }
  await loadGreeting();
  input.focus();
}

boot();
