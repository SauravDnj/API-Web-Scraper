(() => {
  "use strict";

  const PRESET_CATEGORIES = [
    "Restaurants", "Cafes", "Hotels", "Hospitals", "Clinics", "Dentists", "Pharmacies", "Diagnostic Labs",
    "Gyms", "Yoga Classes", "Salons", "Spas", "Beauty Parlours", "Schools", "Colleges", "Coaching Centres",
    "Real Estate Agents", "Builders", "Interior Designers", "Architects", "Chartered Accountants", "Lawyers",
    "Car Dealers", "Car Repair", "Bike Showrooms", "Supermarkets", "Bakeries", "Sweet Shops",
    "Electronics Stores", "Mobile Shops", "Furniture Stores", "Clothing Stores", "Jewellery Shops",
    "Hardware Stores", "Travel Agents", "Event Planners", "Wedding Planners", "Photographers", "Caterers",
    "Banquet Halls", "Pet Shops", "Veterinary Clinics", "Packers and Movers", "Courier Services",
    "IT Companies", "Software Companies", "Digital Marketing Agencies", "Manufacturers", "Wholesalers",
    "Textile Shops", "Printing Services", "Insurance Agents", "PG and Hostels", "Driving Schools",
    "Plumbers", "Electricians", "Pest Control", "Solar Panel Dealers", "Opticians", "Petrol Pumps",
  ];
  const QUICK = ["Restaurants", "Hotels", "Hospitals", "Gyms", "Schools", "Real Estate Agents", "Salons", "Chartered Accountants"];
  const COUNTRIES = [["in", "India"], ["us", "United States"], ["gb", "United Kingdom"], ["ae", "UAE"], ["ca", "Canada"],
    ["au", "Australia"], ["sg", "Singapore"], ["sa", "Saudi Arabia"], ["de", "Germany"]];
  const LANGS = [["en", "English"], ["hi", "Hindi"], ["gu", "Gujarati"], ["mr", "Marathi"]];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lines = (s) => [...new Set(String(s || "").split(/[\n;]+/).map((x) => x.trim()).filter(Boolean))];
  const fmt = (n) => Number(n || 0).toLocaleString("en-IN");
  const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
  // SVG icon from the sprite in templates/_icons.html
  const ic = (name, cls = "") => `<svg class="ico${cls ? ` ${cls}` : ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const opt = (v, l, sel) => `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(l)}</option>`;

  let ME = { email: "" };
  // browser-side conveniences (form drafts, last tab) are kept per logged-in user
  const store = {
    key: (k) => `sds:${ME.email}:${k}`,
    get(k, d) { try { const v = localStorage.getItem(this.key(k)); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(this.key(k), JSON.stringify(v)); } catch { /* ignore */ } },
  };

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, opts = {}) {
    let res;
    try {
      res = await fetch(path, {
        ...opts,
        headers: { "Content-Type": "application/json", "X-Requested-With": "fetch", ...(opts.headers || {}) },
        body: opts.body === undefined ? undefined : (opts.raw ? opts.body : JSON.stringify(opts.body)),
      });
    } catch (e) {
      throw new ApiError(`Network error: ${e.message}`, 0);
    }
    if (opts.binary && res.ok) return res.arrayBuffer();
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && data.auth) {
      await checkpointAll();
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.hash)}`;
    }
    if (res.status === 403 && data.need_key) location.href = "/api-key";
    if (!res.ok) throw new ApiError(data.error || `HTTP ${res.status}`, res.status);
    return data;
  }

  async function gzipText(text) {
    const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
    return new Response(stream).arrayBuffer();
  }
  async function gunzipText(buf) {
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).text();
  }
  const nowIso = () => new Date().toISOString().slice(0, 19) + "Z";
  const clock = () => new Date().toTimeString().slice(0, 8);

  // ------------------------------------------------------------ state
  let TOOLS = [];
  let tool = null;                 // active tool meta
  let allJobs = [];                // job summaries (index) for this user
  const forms = {};                // toolId -> form values
  const views = {};                // toolId -> result view state
  const runners = new Map();       // jobId -> Runner (jobs running in this browser tab)
  const docs = new Map();          // jobId -> loaded job document
  let uiTimer = null;

  const view = () => (views[tool.id] ||= { jobId: store.get(`job.${tool.id}`, null), sort: { key: null, dir: 1 }, page: 1, pageSize: 50, filters: {}, logShown: 0, lastSig: "" });
  const docOf = (v = view()) => (v.jobId ? docs.get(v.jobId) : null);
  const rowsOf = (v = view()) => docOf(v)?.rows || [];

  function form(t = tool) {
    if (!forms[t.id]) {
      const saved = store.get(`form.${t.id}`, null);
      const options = {};
      (t.options || []).forEach((o) => { options[o.key] = o.default ?? (o.type === "checkbox" ? false : ""); });
      forms[t.id] = { name: "", categories: [], keywords: "", locations: [], locState: "", locCities: "", inputs: "",
        country: "in", language: "en", pages: t.default_pages, limit: "", budget: "", ...saved, options: { ...options, ...(saved?.options || {}) } };
    }
    return forms[t.id];
  }
  const saveForm = () => store.set(`form.${tool.id}`, form());

  // ------------------------------------------------------------ tabs
  function renderTabs() {
    const running = new Set([...runners.values()].map((r) => r.tool.id));
    document.body.classList.toggle("busy", running.size > 0);  // animates the Waloop logo while jobs run
    $("toolTabs").innerHTML = TOOLS.map((t) =>
      `<button type="button" class="tool-tab${t.id === tool?.id ? " active" : ""}" data-tool="${t.id}"${t.id === tool?.id ? ' aria-current="page"' : ""}>
        ${ic(t.icon)}<span>${esc(t.label)}</span>${running.has(t.id) ? '<i class="dot" title="Running"></i>' : ""}</button>`).join("");
  }
  $("toolTabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tool-tab");
    if (b) location.hash = b.dataset.tool;
  });
  window.addEventListener("hashchange", () => selectTool(location.hash.slice(1)));

  function selectTool(id) {
    const next = TOOLS.find((t) => t.id === id) || TOOLS.find((t) => t.id === "maps") || TOOLS[0];
    if (tool && next.id === tool.id) return;
    tool = next;
    store.set("tab", tool.id);
    renderTabs();
    document.querySelector(".tool-tab.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    $("toolTitle").textContent = tool.label;
    $("toolBadge").innerHTML = ic(tool.icon);
    $("toolDesc").textContent = `${tool.description} · ${tool.credits} credit${tool.credits > 1 ? "s" : ""} per ${tool.mode === "query" ? "page" : "call"}`;
    $("emptyIcon").innerHTML = ic(tool.icon);
    renderBuilder();
    renderJobPicker();
    const v = view();
    if (v.jobId && allJobs.some((j) => j.id === v.jobId)) openJob(v.jobId);
    else showEmpty();
  }

  // ------------------------------------------------------------ builder
  function renderBuilder() {
    const t = tool, f = form();
    const parts = [];
    parts.push(`<section class="block"><label class="lbl" for="bName">Job name</label>
      <input id="bName" type="text" value="${esc(f.name)}" placeholder="e.g. ${esc(t.label)} - Ahmedabad"></section>`);

    if (t.mode === "query") {
      if (t.categories) {
        parts.push(`<section class="block"><label class="lbl" for="catInput">Categories <small>(Enter to add, paste many lines)</small></label>
          <div class="chips-input" id="catBox"><div class="chips" id="catChips"></div>
          <input id="catInput" type="text" list="catList" placeholder="Restaurants, Gyms, Dentists…"></div>
          <datalist id="catList">${PRESET_CATEGORIES.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
          <div class="quick" id="quickCats">${QUICK.map((c) => `<button type="button" data-cat="${esc(c)}">+ ${esc(c)}</button>`).join("")}</div></section>`);
      }
      parts.push(`<section class="block"><label class="lbl" for="bKeywords">${esc(t.input_label)} <small>(one per line${t.categories ? ", optional" : ""})</small></label>
        <textarea id="bKeywords" rows="${t.categories ? 3 : 5}" placeholder="${esc(placeholderFor(t))}">${esc(f.keywords)}</textarea></section>`);
      if (t.location) {
        parts.push(`<${t.categories ? "section" : "details"} class="block${t.categories ? "" : " adv"}"${!t.categories && f.locations.length ? " open" : ""}>
          ${t.categories ? '<span class="lbl">Locations</span>' : `<summary>Locations <small>(optional, ${f.locations.length} added)</small></summary>`}
          <div class="grid2">
            <div><label class="sublbl" for="locState">State</label><input id="locState" type="text" value="${esc(f.locState)}" placeholder="Gujarat"></div>
            <div><label class="sublbl" for="locCities">City <small>(one per line)</small></label><textarea id="locCities" rows="2" placeholder="Ahmedabad">${esc(f.locCities)}</textarea></div>
            <div><label class="sublbl" for="locAreas">Areas <small>(one per line)</small></label><textarea id="locAreas" rows="3" placeholder="Navrangpura&#10;Satellite"></textarea></div>
            <div><label class="sublbl" for="locPins">Pincodes <small>(one per line)</small></label><textarea id="locPins" rows="3" placeholder="380009&#10;380015"></textarea></div>
          </div>
          <div class="row-btns"><button class="btn secondary" id="addLoc" type="button">＋ Add to location list</button>
            <button class="btn ghost" id="clearLoc" type="button">Clear list</button></div>
          <p class="hint">Each area and each pincode becomes its own search in every city. Only a city = the whole city.</p>
          <div class="loc-list" id="locList"></div>
        </${t.categories ? "section" : "details"}>`);
      }
    } else {
      parts.push(`<section class="block"><label class="lbl" for="bInputs">${esc(t.input_label)}</label>
        <textarea id="bInputs" rows="7" placeholder="${esc(t.input_placeholder)}">${esc(f.inputs)}</textarea>
        <p class="hint">${t.mode === "ids" ? "Accepts a CID, Place ID or FID. Add a name with “Name | ID”." : "Add a name with “Name | URL”."}</p></section>`);
      if (t.imports.length) {
        parts.push(`<section class="block import"><span class="lbl">Import from another tab</span>
          <select id="impJob"></select>
          <div class="grid2"><div><label class="sublbl" for="impMax">Max items</label><input id="impMax" type="text" value="50"></div>
          <div class="imp-btn"><button class="btn secondary" id="impBtn" type="button">⤵ Import</button></div></div>
          <p class="hint" id="impHint"></p></section>`);
      }
    }

    const settings = [];
    (t.options || []).forEach((o) => {
      const val = f.options[o.key];
      const attrs = `${o.depends ? ` data-depends="${o.depends}"` : ""}${o.depends && !f.options[o.depends] ? " hidden" : ""}`;
      if (o.type === "select") {
        settings.push(`<div${attrs}><label class="sublbl" for="opt_${o.key}">${esc(o.label)}</label><select id="opt_${o.key}" data-opt="${o.key}">
          ${o.choices.map(([v, l]) => opt(v, l, String(val) === String(v))).join("")}</select></div>`);
      } else if (o.type === "checkbox") {
        settings.push(`<div class="chk-cell${o.wide ? " span2 feature" : ""}"${attrs}><label class="chk"><input type="checkbox" id="opt_${o.key}" data-opt="${o.key}"${val ? " checked" : ""}> <span>${esc(o.label)}</span></label></div>`);
      }
    });
    if (t.country) {
      settings.push(`<div><label class="sublbl" for="bCountry">Country</label><select id="bCountry">${COUNTRIES.map(([v, l]) => opt(v, l, f.country === v)).join("")}</select></div>`);
      settings.push(`<div><label class="sublbl" for="bLang">Language</label><select id="bLang">${LANGS.map(([v, l]) => opt(v, l, f.language === v)).join("")}</select></div>`);
    }
    if (t.max_pages > 1) {
      const unit = t.id === "reviews" ? "reviews" : "results";
      settings.push(`<div><label class="sublbl" for="bPages">Pages per ${t.mode === "query" ? "query" : "item"} <small>(${t.per_page} ${unit}/page)</small></label>
        <select id="bPages">${Array.from({ length: t.max_pages }, (_, i) => opt(i + 1, `${i + 1} (up to ${(i + 1) * t.per_page})`, +f.pages === i + 1)).join("")}</select></div>`);
    }
    if (settings.length) parts.push(`<section class="block"><span class="lbl">Settings</span><div class="grid2">${settings.join("")}</div></section>`);

    const unit = t.id === "maps" || t.id === "places" ? "leads" : "rows";
    const presets = (id, values, noneLabel) => `<div class="quick presets" data-for="${id}">${values.map((n) =>
      `<button type="button" data-v="${n}">${fmt(n)}</button>`).join("")}<button type="button" data-v="">${noneLabel}</button></div>`;
    parts.push(`<section class="block limits"><span class="lbl">Limits</span>
      <div class="grid2">
        <div><label class="sublbl" for="bLimit">Lead limit <small>(stop at this many ${unit})</small></label>
          <input id="bLimit" type="text" inputmode="numeric" value="${esc(f.limit)}" placeholder="No limit"></div>
        <div><label class="sublbl" for="bBudget">Credit budget <small>(max credits to spend)</small></label>
          <input id="bBudget" type="text" inputmode="numeric" value="${esc(f.budget)}" placeholder="No budget"></div>
      </div>
      <span class="sublbl preset-lbl">Lead limit</span>${presets("bLimit", [100, 500, 1000, 2000, 5000, 10000], "No limit")}
      <span class="sublbl preset-lbl">Credit budget</span>${presets("bBudget", [100, 300, 500, 1000, 2000, 5000], "No budget")}
    </section>`);

    parts.push(`<section class="block estimate"><div class="est-grid">
        <div><b id="estTasks">0</b><span>${t.mode === "query" ? "queries" : "items"}</span></div>
        <div><b id="estResults">0</b><span>max results</span></div>
        <div><b id="estCredits">0</b><span>max credits</span></div></div>
      <p class="est-note" id="estNote" hidden></p>
      <details><summary>Show list</summary><ol id="taskList"></ol></details></section>
      <button class="btn primary big" id="startBtn" type="button">${ic("play")}<span>Start ${esc(t.label)}</span></button>
      <p class="err" id="formErr" hidden></p>`);

    $("builder").innerHTML = parts.join("");
    bindBuilder();
    if (t.categories) renderCats();
    if (t.location) renderLocs();
    if (t.imports.length) renderImportJobs();
    refreshEstimate();
  }

  function placeholderFor(t) {
    return {
      search: "best gym equipment suppliers\nCBSE schools", images: "modern cafe interior\nwedding stage decoration",
      videos: "gym workout tutorial\nahmedabad food vlog", places: "gyms\ncoaching classes", maps: "wedding photographers\nsolar panel dealers",
      news: "ahmedabad real estate\nstartup funding india", shopping: "gym dumbbells\nwireless earbuds",
      scholar: "machine learning agriculture india", patents: "solar panel cleaning robot", autocomplete: "gyms in\nbest cafe",
    }[t.id] || "one per line";
  }

  function bindBuilder() {
    const f = form();
    const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
    const changed = () => { saveForm(); refreshEstimate(); };
    on("bName", "input", (e) => { f.name = e.target.value; saveForm(); });
    on("bKeywords", "input", (e) => { f.keywords = e.target.value; changed(); });
    on("bInputs", "input", (e) => { f.inputs = e.target.value; changed(); });
    on("bCountry", "input", (e) => { f.country = e.target.value; changed(); });
    on("bLang", "input", (e) => { f.language = e.target.value; changed(); });
    on("bPages", "input", (e) => { f.pages = +e.target.value; changed(); });
    on("locState", "input", (e) => { f.locState = e.target.value; saveForm(); });
    on("locCities", "input", (e) => { f.locCities = e.target.value; saveForm(); });
    document.querySelectorAll("[data-opt]").forEach((el) => el.addEventListener("input", () => {
      f.options[el.dataset.opt] = el.type === "checkbox" ? el.checked : el.value;
      document.querySelectorAll("[data-depends]").forEach((d) => { d.hidden = !f.options[d.dataset.depends]; });
      changed();
    }));
    const setNum = (id, key, value) => {
      const clean = String(value).replace(/[^\d]/g, "");
      f[key] = clean; if ($(id).value !== clean) $(id).value = clean; changed();
    };
    on("bLimit", "input", (e) => setNum("bLimit", "limit", e.target.value));
    on("bBudget", "input", (e) => setNum("bBudget", "budget", e.target.value));
    document.querySelectorAll(".presets").forEach((box) => box.addEventListener("click", (e) => {
      if (e.target.dataset.v === undefined) return;
      const id = box.dataset.for;
      setNum(id, id === "bLimit" ? "limit" : "budget", e.target.dataset.v);
    }));

    // categories
    on("quickCats", "click", (e) => { if (e.target.dataset.cat) addCats(e.target.dataset.cat); });
    on("catChips", "click", (e) => { if (e.target.dataset.i !== undefined) { f.categories.splice(+e.target.dataset.i, 1); renderCats(); } });
    on("catBox", "click", () => $("catInput").focus());
    on("catInput", "keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addCats(e.target.value); e.target.value = ""; }
      else if (e.key === "Backspace" && !e.target.value && f.categories.length) { f.categories.pop(); renderCats(); }
    });
    on("catInput", "change", (e) => { if (PRESET_CATEGORIES.includes(e.target.value)) { addCats(e.target.value); e.target.value = ""; } });
    on("catInput", "paste", (e) => { const text = e.clipboardData.getData("text"); if (/[\n,]/.test(text)) { e.preventDefault(); addCats(text); } });
    on("catInput", "blur", (e) => { if (e.target.value.trim()) { addCats(e.target.value); e.target.value = ""; } });

    // locations
    on("addLoc", "click", addLocations);
    on("clearLoc", "click", () => { f.locations = []; renderLocs(); });
    on("locList", "click", (e) => { if (e.target.dataset.i !== undefined) { f.locations.splice(+e.target.dataset.i, 1); renderLocs(); } });

    on("impBtn", "click", importInputs);
    on("startBtn", "click", startJob);
  }

  function addCats(text) {
    const f = form();
    for (const c of lines(String(text).replace(/,/g, "\n"))) {
      if (!f.categories.some((x) => x.toLowerCase() === c.toLowerCase())) f.categories.push(c);
    }
    renderCats();
  }
  function renderCats() {
    $("catChips").innerHTML = form().categories.map((c, i) =>
      `<span class="chip">${esc(c)}<button type="button" data-i="${i}" title="Remove" aria-label="Remove ${esc(c)}">${ic("x")}</button></span>`).join("");
    saveForm(); refreshEstimate();
  }

  const locLabel = (l) => [[l.area, l.city, l.state].filter(Boolean).join(", "), l.pincode].filter(Boolean).join(" ");
  function renderLocs() {
    const f = form();
    $("locList").innerHTML = f.locations.length
      ? f.locations.map((l, i) => `<div class="loc-item"><span>${ic("map-pin")}${esc(locLabel(l))}</span><button type="button" data-i="${i}" title="Remove" aria-label="Remove location">${ic("x")}</button></div>`).join("")
      : `<p class="hint">No locations added${tool.categories ? " yet" : " - queries run without a location"}.</p>`;
    const sum = document.querySelector("#builder details.adv summary small");
    if (sum) sum.textContent = `(optional, ${f.locations.length} added)`;
    saveForm(); refreshEstimate();
  }
  function addLocations() {
    const f = form();
    const st = $("locState").value.trim(), cities = lines($("locCities").value), areas = lines($("locAreas").value), pins = lines($("locPins").value);
    const add = [];
    for (const city of (cities.length ? cities : [""])) {
      for (const area of areas) add.push({ area, city, state: st, pincode: "" });
      for (const pincode of pins) add.push({ area: "", city, state: st, pincode });
      if (!areas.length && !pins.length && (city || st)) add.push({ area: "", city, state: st, pincode: "" });
    }
    if (!add.length) return showErr("Enter at least a city, state, area or pincode.");
    showErr("");
    const seen = new Set(f.locations.map((l) => locLabel(l).toLowerCase()));
    for (const l of add) if (!seen.has(locLabel(l).toLowerCase())) { f.locations.push(l); seen.add(locLabel(l).toLowerCase()); }
    $("locAreas").value = ""; $("locPins").value = "";
    renderLocs();
  }

  // import inputs (reviews <- maps cids, webpage <- websites, lens <- image urls)
  function renderImportJobs() {
    const opts = [];
    tool.imports.forEach((imp, idx) => {
      const jobs = allJobs.filter((j) => j.tool === imp.tool && j.count);
      if (jobs.length) {
        opts.push(`<optgroup label="${esc(imp.label)}">${jobs.map((j) =>
          opt(`${idx}|${j.id}`, `${j.name} (${fmt(j.count)} rows)`)).join("")}</optgroup>`);
      }
    });
    $("impJob").innerHTML = opts.length ? opts.join("") : `<option value="">No ${tool.imports.map((i) => i.tool).join(" / ")} jobs yet</option>`;
    $("impBtn").disabled = !opts.length;
    $("impHint").textContent = `Run a ${tool.imports.map((i) => TOOLS.find((x) => x.id === i.tool).label).join(" or ")} job first, then import its results here.`;
  }
  async function importInputs() {
    const [idx, jobId] = ($("impJob").value || "").split("|");
    if (!jobId) return;
    const imp = tool.imports[+idx];
    const max = Math.max(1, parseInt($("impMax").value, 10) || 50);
    let doc;
    try { doc = await loadDoc(jobId); } catch (e) { $("impHint").textContent = e.message; return; }
    let items = (doc.rows || []).filter((r) => r[imp.field]);
    if (imp.tool === "maps" || imp.tool === "places") items.sort((a, b) => (Number(b.reviews) || 0) - (Number(a.reviews) || 0));
    const f = form();
    const existing = new Set(lines(f.inputs).map((l) => l.split(" | ").pop().toLowerCase()));
    const add = [];
    for (const r of items) {
      if (add.length >= max) break;
      const val = String(r[imp.field]);
      if (existing.has(val.toLowerCase())) continue;
      existing.add(val.toLowerCase());
      const name = r.name || r.title || "";
      add.push(name ? `${name.replace(/\|/g, "/")} | ${val}` : val);
    }
    f.inputs = [f.inputs.trim(), ...add].filter(Boolean).join("\n");
    $("bInputs").value = f.inputs;
    $("impHint").textContent = `Imported ${add.length} item${add.length === 1 ? "" : "s"}.`;
    saveForm(); refreshEstimate();
  }

  function spec() {
    const f = form();
    return { name: f.name.trim(), categories: tool.categories ? f.categories : [], keywords: lines(f.keywords),
      locations: tool.location ? f.locations : [], inputs: lines(f.inputs), options: f.options,
      country: f.country, language: f.language, pages: f.pages,
      limit: parseInt(f.limit, 10) || 0, budget: parseInt(f.budget, 10) || 0 };
  }

  let estTimer = null, lastEstimate = null, balance = null;
  function refreshEstimate() {
    clearTimeout(estTimer);
    const t = tool;
    estTimer = setTimeout(async () => {
      try {
        const est = await api(`/api/tools/${t.id}/preview`, { method: "POST", body: spec() });
        if (t !== tool) return;
        lastEstimate = est;
        $("estTasks").textContent = fmt(est.count);
        $("estResults").textContent = fmt(est.max_results);
        $("estCredits").textContent = fmt(est.max_credits);
        const notes = [];
        if (est.note) notes.push(`<span>${ic("info")}${esc(est.note)}</span>`);
        if (est.limit && est.uncapped_results < est.limit) {
          notes.push(`<span class="warn">${ic("alert")}These settings can return at most ~${fmt(est.uncapped_results)} results, below your lead limit of ${fmt(est.limit)}. ${t.id === "maps" ? "Increase the radius, use a denser grid, or add more categories/locations." : "Add more queries or pages."}</span>`);
        }
        if (est.limit) notes.push(`<span>${ic("check")}The job stops as soon as ${fmt(est.limit)} unique rows are collected.</span>`);
        if (balance !== null && est.max_credits > balance) {
          notes.push(`<span class="warn">${ic("alert")}Up to ${fmt(est.max_credits)} credits, more than your balance of ${fmt(balance)}. Set a credit budget.</span>`);
        } else if (!est.budget && est.max_credits > 1000) {
          notes.push(`<span class="warn">${ic("alert")}Can use up to ${fmt(est.max_credits)} credits. Set a credit budget to cap spending.</span>`);
        }
        $("estNote").innerHTML = notes.join("");
        $("estNote").hidden = !notes.length;
        $("taskList").innerHTML = est.tasks.map((q) => `<li>${esc(q)}</li>`).join("") + (est.count > est.tasks.length ? `<li>… and ${fmt(est.count - est.tasks.length)} more</li>` : "");
      } catch { /* offline */ }
    }, 250);
  }

  function showErr(msg) { const el = $("formErr"); if (el) { el.textContent = msg; el.hidden = !msg; } }

  async function startJob() {
    const s = spec();
    if (tool.mode === "query" && !s.categories.length && !s.keywords.length) return showErr(`Add at least one ${tool.categories ? "category or keyword" : "query"}.`);
    if (tool.mode !== "query" && !s.inputs.length) return showErr("Add at least one item.");
    if (tool.id === "maps" && !s.locations.length && !confirm("No locations added - search without a location?")) return;
    const est = lastEstimate;
    if (est && !s.budget && est.max_credits > 1000 &&
        !confirm(`This job can use up to ${fmt(est.max_credits)} credits and no credit budget is set. Start anyway?`)) return;
    if (!s.name) s.name = [s.categories[0] || s.keywords[0] || (s.inputs[0] || "").split(" | ")[0], s.locations[0]?.city].filter(Boolean).join(" - ").slice(0, 60);
    showErr("");
    $("startBtn").disabled = true;
    try {
      const { job, tasks } = await api(`/api/tools/${tool.id}/jobs`, { method: "POST", body: s });
      const doc = { ...job, spec: s, errors: 0, counters: {}, finished_at: null, log: [], rows: [],
        pending: tasks.map((task, idx) => ({ idx, task, cursor: null })), claimed: [] };
      docs.set(job.id, doc);
      allJobs.unshift(summaryOf(doc));
      const runner = new Runner(tool, doc);
      runners.set(job.id, runner);
      await openJob(job.id);
      runner.start();
    } catch (e) {
      showErr(e.message);
    } finally {
      $("startBtn").disabled = false;
    }
  }

  // ------------------------------------------------------------ runner
  // The browser runs the job: one server call = one Serper API call, so it works on
  // Vercel's short-lived functions. Progress is saved to the server as gzip checkpoints.
  // Indian mobile number from a phone cell ("098250 12345", "+91 98250-12345"), "" for landlines.
  // Same rules as indian_mobile() in tools.py.
  function indianMobile(value) {
    for (const part of cellText(value).split(/[,/;]/)) {
      let groups = part.match(/\d+/g) || [];
      let digits = groups.join("");
      if (digits.length === 12 && digits.startsWith("91")) {
        digits = digits.slice(2);
        groups = groups[0] === "91" ? groups.slice(1) : [digits];
      } else if (digits.length === 11 && digits.startsWith("0")) {
        digits = digits.slice(1);
        groups = [groups[0].slice(1), ...groups.slice(1)];
      }
      if (!/^[6-9]\d{9}$/.test(digits)) continue;
      if (groups.length > 1 && groups[0].length >= 2 && groups[0].length <= 4) continue; // STD code + landline
      return digits;
    }
    return "";
  }

  // Keys that mark two place rows as the same lead: same phone number, or same name + address.
  function duplicateKeys(r) {
    const keys = [];
    const digits = cellText(r.phone).split(/[,/;]/)[0].replace(/\D/g, "");
    if (digits.length >= 8) keys.push(`p:${digits.slice(-10)}`);
    const name = cellText(r.name).toLowerCase().replace(/[^a-z0-9]/g, "");
    const addr = cellText(r.address).toLowerCase().replace(/[^a-z0-9]/g, "");
    if (name && addr) keys.push(`na:${name}|${addr}`);
    return keys;
  }

  class Runner {
    constructor(toolMeta, doc) {
      this.tool = toolMeta;
      this.doc = doc;
      this.keys = new Map(doc.rows.map((r) => [r._key, r]));
      this.dupes = new Map(); // duplicateKeys() -> row, when "Remove duplicates" is on
      this.dedupe = !!toolMeta.dedupe && doc.spec?.options?.dedupe !== false;
      if (this.dedupe) doc.rows.forEach((r) => duplicateKeys(r).forEach((k) => { if (!this.dupes.has(k)) this.dupes.set(k, r); }));
      this.claimed = new Set(doc.claimed || []);
      this.queue = (doc.pending || []).slice();
      this.active = new Map();
      this.userStopped = false;
      this.paused = "";
      this.fatal = "";
      this.lastSave = Date.now() - (ME.checkpoint_seconds - 25) * 1000; // first save after ~25 s
      this.saving = null;
    }

    log(msg, level = "info") {
      this.doc.log.push({ t: clock(), level, msg });
      if (this.doc.log.length > 600) this.doc.log.splice(0, this.doc.log.length - 400);
    }

    shouldStop() {
      const d = this.doc;
      if (this.userStopped || this.paused || this.fatal) return true;
      let reason = "";
      if (d.limit && d.rows.length >= d.limit) reason = `Lead limit of ${fmt(d.limit)} reached`;
      else if (d.budget && d.credits_used + this.tool.credits > d.budget) reason = `Credit budget of ${fmt(d.budget)} reached`;
      if (reason) {
        if (!d.stop_reason) { d.stop_reason = reason; this.log(`${reason} - stopping`, "success"); }
        return true;
      }
      return false;
    }

    addRows(rows) {
      const d = this.doc, mf = this.tool.merge_field;
      let added = 0;
      for (const r of rows || []) {
        const dupKeys = this.dedupe ? duplicateKeys(r) : [];
        let old = this.keys.get(r._key);
        if (!old && dupKeys.length) {
          old = dupKeys.map((k) => this.dupes.get(k)).find(Boolean);
          if (old) d.counters.duplicates = (d.counters.duplicates || 0) + 1;
        }
        if (old) {
          if (mf && r[mf]) {
            const parts = String(old[mf] || "").split("|").map((x) => x.trim()).filter(Boolean);
            if (!parts.includes(r[mf])) old[mf] = [...parts, r[mf]].join(" | ");
          }
          continue;
        }
        if (d.limit && d.rows.length >= d.limit) break;
        this.keys.set(r._key, r);
        dupKeys.forEach((k) => { if (!this.dupes.has(k)) this.dupes.set(k, r); });
        d.rows.push(r);
        added++;
      }
      return added;
    }

    async start() {
      const d = this.doc;
      d.status = "running"; d.stop_reason = ""; d.finished_at = null;
      this.log(d.credits_used || d.rows.length ? `Resumed - ${fmt(this.queue.length)} task${this.queue.length === 1 ? "" : "s"} left` : `Started ${fmt(d.total)} ${this.tool.label} tasks`);
      renderTabs(); refreshView();
      const workers = Array.from({ length: Math.max(1, Math.min(this.tool.workers || 4, this.queue.length)) }, () => this.worker());
      await Promise.all(workers);
      await this.finish();
    }

    async worker() {
      while (!this.shouldStop() && this.queue.length) {
        const item = this.queue.shift();
        this.active.set(item.idx, item);
        let found = 0, lastProgress = "", ended = false;
        while (!this.shouldStop()) {
          const res = await this.callStep(item);
          if (!res) break; // paused (network) - item stays active and is saved as pending
          const d = this.doc;
          d.credits_used += res.credits || 0;
          if (!res.skipped) d.counters.api_calls = (d.counters.api_calls || 0) + 1;
          if (res.claim) this.claimed.add(res.claim);
          Object.entries(res.count || {}).forEach(([k, n]) => { d.counters[k] = (d.counters[k] || 0) + n; });
          const added = this.addRows(res.rows);
          found += added;
          if (res.progress) lastProgress = res.progress;
          if (res.error) {
            d.errors += 1;
            this.log(`${item.task.label}: ${res.error}`, "error");
            if (res.fatal) { this.fatal = res.error; break; }
          }
          item.cursor = added === 0 && res.next_if_empty !== undefined ? res.next_if_empty : res.next;
          if (!item.cursor || res.error) { ended = true; break; }
          if (item.cursor.phase === "grid" && (d.counters.grid_points || 0) % 25 === 0 && res.claim) {
            this.log(`${item.task.label}: ${lastProgress}, ${fmt(found)} new so far`);
          }
          this.maybeSave();
        }
        if (ended) {
          this.active.delete(item.idx);
          this.doc.done += 1;
          this.log(`${item.task.label}: ${fmt(found)} new rows${lastProgress && this.tool.id === "webpage" ? ` (${lastProgress})` : ""}`);
          this.maybeSave();
        } else if (!this.paused && !this.fatal) {
          // stopped by limit / budget / user: keep the task (with its cursor) so the job can be continued
          this.active.delete(item.idx);
          this.queue.unshift(item);
        }
      }
    }

    async callStep(item) {
      const body = { spec: this.doc.spec, task: item.task, cursor: item.cursor };
      if (item.cursor?.phase === "grid") {
        const prefix = `${String(item.task.term).toLowerCase()}|`;
        body.claimed = [...this.claimed].filter((k) => k.startsWith(prefix));
      }
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          return await api(`/api/tools/${this.tool.id}/step`, { method: "POST", body });
        } catch (e) {
          if (e.status && e.status < 500 && e.status !== 429) {
            this.fatal = e.message;
            this.log(`${item.task.label}: ${e.message}`, "error");
            return null;
          }
          await sleepMs(2000 * (attempt + 1));
        }
      }
      this.paused = "Connection problem - job paused. Click Resume to continue.";
      this.log(this.paused, "warn");
      return null;
    }

    stop() {
      if (!this.userStopped) { this.userStopped = true; this.log("Stop requested - finishing in-flight requests", "warn"); }
      refreshView();
    }

    pendingList() {
      return [...this.active.values(), ...this.queue].map(({ idx, task, cursor }) => ({ idx, task, cursor }));
    }

    async finish() {
      const d = this.doc;
      d.pending = this.pendingList();
      d.claimed = [...this.claimed];
      if (this.fatal) { d.status = "error"; d.stop_reason = this.fatal; }
      else if (this.paused) { d.status = "paused"; d.stop_reason = this.paused; }
      else if (this.userStopped) { d.status = "stopped"; d.stop_reason = d.stop_reason || "Stopped by user"; }
      else d.status = "done";
      d.finished_at = nowIso();
      this.log(`Finished: ${fmt(d.rows.length)} rows, ${fmt(d.credits_used)} credits used`, d.status === "done" ? "success" : "warn");
      runners.delete(d.id);
      await this.save(true);
      renderTabs(); refreshView(true); refreshCredits();
    }

    maybeSave() {
      if (Date.now() - this.lastSave > ME.checkpoint_seconds * 1000) this.save();
    }

    async save(final = false) {
      if (this.saving) { await this.saving; if (!final) return; }
      this.lastSave = Date.now();
      const d = this.doc;
      if (!final) { d.pending = this.pendingList(); d.claimed = [...this.claimed]; }
      this.saving = saveDoc(d).finally(() => { this.saving = null; });
      return this.saving;
    }
  }

  function summaryOf(d) {
    return { id: d.id, tool: d.tool, name: d.name, status: d.status, created_at: d.created_at, updated_at: nowIso(),
      count: d.rows.length, credits_used: d.credits_used, total: d.total, done: d.done, limit: d.limit, budget: d.budget,
      stop_reason: d.stop_reason };
  }

  async function saveDoc(d) {
    d.updated_at = nowIso();
    const idx = allJobs.findIndex((j) => j.id === d.id);
    if (idx >= 0) allJobs[idx] = summaryOf(d);
    let doc = { ...d, count: d.rows.length };
    for (let attempt = 0; attempt < 2; attempt++) {
      const gz = await gzipText(JSON.stringify(doc));
      if (gz.byteLength > 4_300_000 && attempt === 0) {
        // Vercel limits request size: shrink long page text before saving
        doc = { ...doc, rows: doc.rows.map((r) => (r.text || r.markdown ? { ...r, text: String(r.text || "").slice(0, 1500), markdown: "" } : r)) };
        continue;
      }
      try {
        await api(`/api/jobs/${d.id}`, { method: "PUT", body: gz, raw: true, headers: { "Content-Type": "application/octet-stream" } });
        d.saved_at = Date.now();
        d.save_error = "";
      } catch (e) {
        d.save_error = e.status === 413 ? "Job is too large to save online - download it now." : `Could not save progress: ${e.message}`;
      }
      break;
    }
    if (view().jobId === d.id) renderJobHead();
  }

  async function checkpointAll() {
    await Promise.all([...runners.values()].map((r) => r.save()));
  }

  async function loadDoc(id) {
    if (docs.has(id)) return docs.get(id);
    let buf;
    try {
      buf = await api(`/api/jobs/${id}`, { binary: true });
    } catch (e) {
      if (e.status !== 404) throw e;
      const s = allJobs.find((j) => j.id === id);
      if (!s) throw e;
      // job was started but never saved (tab closed within the first seconds)
      buf = null;
      const doc = { ...s, spec: {}, errors: 0, counters: {}, log: [{ t: "", level: "warn", msg: "No progress was saved for this job." }],
        rows: [], pending: [], claimed: [], status: s.status === "running" ? "paused" : s.status };
      docs.set(id, doc);
      return doc;
    }
    const doc = JSON.parse(await gunzipText(buf));
    doc.rows ||= []; doc.log ||= []; doc.pending ||= []; doc.counters ||= {}; doc.claimed ||= [];
    const t = TOOLS.find((x) => x.id === doc.tool);
    doc.rows.forEach((r) => { if (!r._key && t) r._key = JSON.stringify([r.cid, r.link, r.url, r.review_id, r.title, r.name]); });
    if (doc.status === "running" && !runners.has(id)) doc.status = "paused"; // was running in a closed tab
    docs.set(id, doc);
    return doc;
  }

  // ------------------------------------------------------------ jobs
  async function refreshJobs() {
    try {
      const fresh = await api("/api/jobs");
      // keep live numbers for jobs running in this tab
      allJobs = fresh.map((j) => (runners.has(j.id) ? summaryOf(runners.get(j.id).doc) : j));
    } catch { return; }
    renderTabs();
    renderJobPicker();
    if (tool.imports.length && $("impJob")) {
      const cur = $("impJob").value;
      renderImportJobs();
      if ([...$("impJob").options].some((o) => o.value === cur)) $("impJob").value = cur;
    }
  }

  function renderJobPicker() {
    const jobs = allJobs.filter((j) => j.tool === tool.id);
    $("jobPicker").innerHTML = opt("", `— ${tool.label} jobs (${jobs.length}) —`) + jobs.map((j) => {
      const live = runners.has(j.id) ? "Running · " : j.status === "running" || j.status === "paused" ? "Paused · " : "";
      const count = runners.has(j.id) ? runners.get(j.id).doc.rows.length : j.count;
      return opt(j.id, `${live}${j.name} · ${fmt(count)} rows · ${String(j.created_at || "").replace("T", " ").slice(0, 16)}`);
    }).join("");
    $("jobPicker").value = view().jobId && jobs.some((j) => j.id === view().jobId) ? view().jobId : "";
  }
  $("jobPicker").addEventListener("change", (e) => { if (e.target.value) openJob(e.target.value); });

  async function refreshCredits() {
    try {
      const a = await api("/api/account");
      balance = a.balance;
      $("creditsText").innerHTML = `<b>${fmt(a.balance)}</b> credits${a.count > 1 ? ` <small>${fmt(a.active)}/${fmt(a.count)} keys</small>` : ""}`;
      $("credits").title = `Total Serper credits of ${fmt(a.keys)} enabled API key${a.keys === 1 ? "" : "s"}. Per-key credits are in Settings.`;
    }
    catch (e) { $("creditsText").textContent = "Credits n/a"; $("credits").title = e.message; }
  }

  function showEmpty() {
    $("jobView").hidden = true; $("emptyState").hidden = false;
  }

  async function openJob(id) {
    const t = tool, v = view();
    if (v.jobId !== id) Object.assign(v, { jobId: id, page: 1, filters: {}, sort: { key: null, dir: 1 } });
    v.logShown = 0; v.lastSig = "";
    store.set(`job.${t.id}`, id);
    $("log").innerHTML = "";
    $("emptyState").hidden = true; $("jobView").hidden = false;
    $("jobPicker").value = id;
    if (!docs.has(id)) {
      $("jobTitle").textContent = "Loading…";
      $("table").querySelector("tbody").innerHTML = `<tr><td class="muted empty-row">Loading saved data…</td></tr>`;
    }
    try {
      await loadDoc(id);
    } catch (e) {
      if (t === tool && v.jobId === id) { $("jobTitle").textContent = `Could not load job: ${e.message}`; }
      return;
    }
    if (t !== tool || v.jobId !== id) return;
    refreshView(true);
  }

  // re-render the open job while its runner is working
  function refreshView(force = false) {
    const v = view(), d = docOf(v);
    if (!d || $("jobView").hidden) return;
    const sig = `${d.id}|${d.rows.length}|${d.status}|${d.done}|${d.log.length}|${d.credits_used}`;
    if (!force && sig === v.lastSig) return;
    const rowsChanged = !v.lastSig || v.lastSig.split("|")[1] !== String(d.rows.length) || force;
    v.lastSig = sig;
    renderJobHead();
    renderLog();
    if (rowsChanged) { renderFilters(); renderTable(); }
    renderJobPicker();
  }

  function renderJobHead() {
    const v = view(), d = docOf(v);
    if (!d) return;
    const live = runners.get(d.id);
    $("jobTitle").textContent = d.name;
    const saved = d.saved_at ? ` · saved ${new Date(d.saved_at).toLocaleTimeString()}` : "";
    $("jobMeta").textContent = `${fmt(d.total)} ${tool.mode === "query" ? "queries" : "items"} · started ${String(d.created_at || "").replace("T", " ").slice(0, 16)}${d.stop_reason ? ` · ${d.stop_reason}` : ""}${saved}`;
    const status = live ? (live.userStopped ? "stopping" : "running") : d.status;
    const st = $("jobStatus");
    st.innerHTML = `${status === "running" || status === "stopping" ? ic("loader", "spin") : ""}<span>${esc(status)}</span>`;
    st.className = `status ${status}`;
    $("progress").classList.toggle("live", !!live);
    $("stopBtn").hidden = !live;
    const resumable = !live && (d.pending || []).length > 0;
    $("resumeBtn").hidden = !resumable;
    $("resumeBtn").querySelector("span").textContent = d.stop_reason && /limit|budget/i.test(d.stop_reason) ? "Continue" : "Resume";
    let pct = d.total ? (d.done / d.total) * 100 : 0;
    if (d.limit) pct = Math.max(pct, (d.rows.length / d.limit) * 100);
    if (!live && d.status === "done") pct = 100;
    $("progBar").style.width = `${Math.min(100, Math.round(pct))}%`;
    $("errCount").textContent = d.errors ? `· ${d.errors} errors` : "";
    $("saveWarn").textContent = d.save_error || (live ? "Keep this tab open while the job runs. Progress is saved automatically." : "");
    $("saveWarn").className = d.save_error ? "save-warn err" : "save-warn";
    $("saveWarn").hidden = !$("saveWarn").textContent;
    renderStats();
  }

  function renderStats() {
    const v = view(), d = docOf(v);
    if (!d) return;
    const c = d.counters || {}, count = d.rows.length;
    const cards = [
      ["Rows", d.limit ? `${fmt(count)} <small>/ ${fmt(d.limit)}</small>` : fmt(count)],
      [tool.mode === "query" ? "Queries done" : "Items done", `${fmt(d.done)}/${fmt(d.total)}`],
      ["Credits used", d.budget ? `${fmt(d.credits_used)} <small>/ ${fmt(d.budget)}</small>` : fmt(d.credits_used)],
    ];
    if (c.grid_points) cards.push(["Nearby points", fmt(c.grid_points)]);
    if (c.api_calls) cards.push(["API calls", fmt(c.api_calls)]);
    if (c.not_mobile) cards.push(["Skipped (no mobile)", fmt(c.not_mobile)]);
    if (c.duplicates) cards.push(["Duplicates removed", fmt(c.duplicates)]);
    tool.stats.forEach((s) => cards.push([s.label, fmt(d.rows.filter((r) => r[s.key]).length)]));
    if (d.errors) cards.push(["Errors", fmt(d.errors)]);
    $("stats").innerHTML = cards.map(([l, n]) => `<div class="stat"><span>${esc(l)}</span><b>${n}</b></div>`).join("");
  }

  function renderLog() {
    const v = view(), d = docOf(v), box = $("log");
    if (!d) return;
    if (v.logShown > d.log.length) { box.innerHTML = ""; v.logShown = 0; }
    const entries = d.log.slice(v.logShown);
    v.logShown = d.log.length;
    if (!entries.length) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
    box.insertAdjacentHTML("beforeend", entries.map((e) => `<div class="${esc(e.level)}">${e.t ? `[${esc(e.t)}] ` : ""}${esc(e.msg)}</div>`).join(""));
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  $("stopBtn").addEventListener("click", () => { runners.get(view().jobId)?.stop(); });
  $("resumeBtn").addEventListener("click", async () => {
    const v = view(), d = docOf(v);
    if (!d || runners.has(d.id) || !d.pending.length) return;
    if (d.limit && d.rows.length >= d.limit) {
      const n = parseInt(prompt(`Lead limit of ${fmt(d.limit)} reached. New lead limit (0 = no limit):`, String(d.limit * 2)) || "", 10);
      if (Number.isNaN(n)) return;
      d.limit = n;
    }
    if (d.budget && d.credits_used + tool.credits > d.budget) {
      const n = parseInt(prompt(`Credit budget of ${fmt(d.budget)} used. New credit budget (0 = no budget):`, String(d.budget * 2)) || "", 10);
      if (Number.isNaN(n)) return;
      d.budget = n;
    }
    const runner = new Runner(tool, d);
    runners.set(d.id, runner);
    runner.start();
  });
  $("deleteBtn").addEventListener("click", async () => {
    const v = view(), id = v.jobId;
    if (!id || !confirm("Delete this job and its data?")) return;
    const r = runners.get(id);
    if (r) { r.stop(); runners.delete(id); }
    await api(`/api/jobs/${id}`, { method: "DELETE" }).catch(() => {});
    docs.delete(id);
    allJobs = allJobs.filter((j) => j.id !== id);
    v.jobId = null;
    store.set(`job.${tool.id}`, null);
    showEmpty();
    renderTabs(); renderJobPicker();
  });

  // ------------------------------------------------------------ filters + table
  const cellText = (v) => (v === null || v === undefined ? "" : String(v));

  function renderFilters() {
    const v = view();
    const typing = document.activeElement?.id === "fText" ? document.activeElement.selectionStart : null;
    $("filters").innerHTML = `<input id="fText" type="search" placeholder="Filter rows…" value="${esc(v.filters.text || "")}">` +
      tool.filters.map((flt, i) => {
        const val = v.filters[i];
        if (flt.type === "select") {
          const values = new Set();
          rowsOf(v).forEach((r) => {
            const raw = cellText(r[flt.key]);
            (flt.split ? raw.split(flt.split) : [raw]).forEach((x) => { if (x.trim()) values.add(x.trim()); });
          });
          const sorted = [...values].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
          return `<select data-f="${i}">${opt("", flt.label)}${sorted.map((x) => opt(x, x, val === x)).join("")}</select>`;
        }
        if (flt.type === "min") {
          return `<select data-f="${i}">${opt("", flt.label)}${flt.choices.map((c) => opt(c, `${c}+`, String(val) === String(c))).join("")}</select>`;
        }
        if (flt.type === "max") {
          return `<select data-f="${i}">${opt("", flt.label)}${flt.choices.map((c) => opt(c, `Within ${c}${flt.suffix || ""}`, String(val) === String(c))).join("")}</select>`;
        }
        return `<label class="chk"><input type="checkbox" data-f="${i}"${val ? " checked" : ""}> ${esc(flt.label)}</label>`;
      }).join("");
    if (typing !== null) { $("fText").focus(); $("fText").setSelectionRange(typing, typing); }
    $("fText").addEventListener("input", (e) => { v.filters.text = e.target.value; v.page = 1; renderTable(); });
    $("filters").querySelectorAll("[data-f]").forEach((el) => el.addEventListener("input", () => {
      v.filters[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value; v.page = 1; renderTable();
    }));
    renderStats();
  }

  function filtered() {
    const v = view();
    const text = (v.filters.text || "").trim().toLowerCase();
    const seen = new Set();
    let out = rowsOf(v).filter((r) => {
      if (text && !Object.entries(r).some(([k, val]) => k !== "_key" && String(val ?? "").toLowerCase().includes(text))) return false;
      return tool.filters.every((flt, i) => {
        const val = v.filters[i];
        if (!val) return true;
        if (flt.type === "select") {
          const raw = cellText(r[flt.key]);
          return (flt.split ? raw.split(flt.split).map((x) => x.trim()) : [raw.trim()]).includes(val);
        }
        if (flt.type === "min") return Number(r[flt.key]) >= Number(val);
        if (flt.type === "max") return cellText(r[flt.key]) !== "" && Number(r[flt.key]) <= Number(val);
        if (flt.type === "mobile") return !!indianMobile(r[flt.key]);
        if (flt.type === "unique") return true; // applied below, after the other filters
        const has = flt.key.split("|").some((k) => cellText(r[k]).trim());
        return flt.type === "has" ? has : !has;
      });
    });
    if (tool.filters.some((flt, i) => flt.type === "unique" && v.filters[i])) {
      out = out.filter((r) => {
        const keys = duplicateKeys(r);
        const dup = keys.some((k) => seen.has(k));
        keys.forEach((k) => seen.add(k));
        return !dup;
      });
    }
    const { key, dir } = v.sort;
    if (key) {
      const numeric = ["num", "num1", "rating"].includes(tool.columns.find((c) => c.key === key)?.type);
      out = [...out].sort((a, b) => numeric
        ? ((Number(a[key]) || 0) - (Number(b[key]) || 0)) * dir
        : cellText(a[key]).localeCompare(cellText(b[key])) * dir);
    }
    return out;
  }

  const shortUrl = (u) => cellText(u).replace(/^https?:\/\/(www\.)?/, "").slice(0, 40);
  const a = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);

  function cell(c, r, rowIdx) {
    const v = r[c.key];
    const s = cellText(v);
    switch (c.type) {
      case "title": return `<td class="name">${esc(s)}${c.sub_key && r[c.sub_key] ? `<small>${esc(r[c.sub_key])}</small>` : ""}</td>`;
      case "long": return `<td class="long"><div class="clamp" title="${esc(s.slice(0, 1500))}">${esc(s)}</div></td>`;
      case "link": {
        const href = r[c.href_key || c.key];
        const text = c.text || cellText(r[c.text_key || c.key]) || shortUrl(href);
        return `<td class="${c.key === "title" ? "name" : "nowrap"}">${href ? a(href, text) : esc(c.text ? "" : text)}</td>`;
      }
      case "url": return `<td class="nowrap">${s ? a(s, shortUrl(s)) : ""}</td>`;
      case "image": return `<td>${s ? `<a href="${esc(r[c.full_key] || s)}" target="_blank" rel="noopener"><img class="thumb" src="${esc(s)}" loading="lazy" alt=""></a>` : ""}</td>`;
      case "num": return `<td class="nowrap">${s === "" ? "" : fmt(v)}</td>`;
      case "num1": return `<td class="nowrap">${s === "" ? "" : Number(v).toFixed(2)}</td>`;
      case "rating": return `<td class="nowrap">${s === "" ? "" : `<span class="rating">${ic("star", "star")}${esc(s)}</span>`}</td>`;
      case "phone": return `<td class="nowrap">${s.split(", ").filter(Boolean).map((p) => `<a href="tel:${esc(p.replace(/[\s-]/g, ""))}">${esc(p)}</a>`).join("<br>")}</td>`;
      case "email": return `<td class="nowrap">${s.split(", ").filter(Boolean).map((m) => `<a href="mailto:${esc(m)}">${esc(m)}</a>`).join("<br>")}</td>`;
      case "links": return `<td class="nowrap">${s.split("\n").filter(Boolean).map((u) => a(u, shortUrl(u))).join("<br>")}</td>`;
      case "view": return `<td>${s ? `<button class="btn ghost sm" type="button" data-view="${rowIdx}" data-key="${c.key}">View (${fmt(s.length)})</button>` : ""}</td>`;
      default: return `<td>${esc(s)}</td>`;
    }
  }

  let pageRows = [];
  function renderTable() {
    const v = view();
    const cols = tool.columns.filter((c) => c.show);
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / v.pageSize));
    v.page = Math.min(Math.max(1, v.page), pages);
    const start = (v.page - 1) * v.pageSize;
    pageRows = rows.slice(start, start + v.pageSize);

    $("table").querySelector("thead").innerHTML = `<tr><th>#</th>${cols.map((c) => {
      const sorted = v.sort.key === c.key;
      return `<th data-key="${c.key}" class="${sorted ? `sorted ${v.sort.dir > 0 ? "asc" : "desc"}` : ""}"${sorted ? ` aria-sort="${v.sort.dir > 0 ? "ascending" : "descending"}"` : ""}>${esc(c.label)}</th>`;
    }).join("")}</tr>`;
    const running = runners.has(v.jobId);
    $("table").querySelector("tbody").innerHTML = pageRows.length
      ? pageRows.map((r, i) => `<tr><td class="muted">${start + i + 1}</td>${cols.map((c) => cell(c, r, i)).join("")}</tr>`).join("")
      : `<tr><td colspan="${cols.length + 1}" class="muted empty-row">${running ? "Working… rows will appear here." : "No rows match."}</td></tr>`;

    const total = rowsOf(v).length;
    $("showing").textContent = rows.length === total ? `${fmt(total)} rows` : `${fmt(rows.length)} of ${fmt(total)} rows (filtered)`;
    $("pgInfo").textContent = `${v.page} / ${pages}`;
    $("prevPg").disabled = v.page <= 1;
    $("nextPg").disabled = v.page >= pages;
    $("pgSize").value = String(v.pageSize);
  }

  $("table").querySelector("thead").addEventListener("click", (e) => {
    const key = e.target.closest("th")?.dataset.key;
    if (!key) return;
    const v = view(), type = tool.columns.find((c) => c.key === key)?.type;
    v.sort = v.sort.key === key ? { key, dir: -v.sort.dir } : { key, dir: ["num", "rating"].includes(type) ? -1 : 1 };  // distance (num1) sorts nearest first
    renderTable();
  });
  $("table").querySelector("tbody").addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]");
    if (!b) return;
    const r = pageRows[+b.dataset.view];
    $("modalTitle").textContent = `${r.title || r.name || r.url || ""} — ${b.dataset.key}`;
    $("modalBody").textContent = r[b.dataset.key] || "";
    $("modal").hidden = false;
  });
  $("modalClose").addEventListener("click", () => { $("modal").hidden = true; });
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("modal").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("modal").hidden = true; });
  $("modalCopy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("modalBody").textContent); $("modalCopy").textContent = "Copied"; await sleepMs(1200); } catch { /* ignore */ }
    $("modalCopy").textContent = "Copy";
  });
  $("prevPg").addEventListener("click", () => { view().page--; renderTable(); });
  $("nextPg").addEventListener("click", () => { view().page++; renderTable(); });
  $("pgSize").addEventListener("change", (e) => { const v = view(); v.pageSize = +e.target.value; v.page = 1; renderTable(); });

  // ------------------------------------------------------------ export
  // Files are built in the browser (no upload limits): every column the tool defines, thumbnails last.
  const exportColumns = () => [...tool.columns.filter((c) => c.type !== "image"), ...tool.columns.filter((c) => c.type === "image")];
  const exportValue = (v) => (typeof v === "string" && v.startsWith("data:") ? "" : v ?? "");
  // Phone cells are exported as plain digits with the country code: "098250 12345",
  // "+91 98250-12345" and "9825012345" all become "919825012345". Several numbers stay comma separated.
  function exportPhones(value, india) {
    return cellText(value).split(/[,/;]/).map((part) => {
      let digits = part.replace(/\D/g, "");
      if (!india) return digits;
      if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
      if (digits.length === 10) digits = `91${digits}`;
      return digits;
    }).filter(Boolean).join(", ");
  }
  // A row with several phone numbers becomes one row per number (other columns copied).
  function splitPhoneRows(rows, india) {
    const phoneCol = exportColumns().find((c) => c.type === "phone");
    if (!phoneCol) return rows;
    return rows.flatMap((r) => {
      const phones = exportPhones(r[phoneCol.key], india).split(", ").filter(Boolean);
      return phones.length > 1 ? phones.map((p) => ({ ...r, [phoneCol.key]: p })) : [r];
    });
  }
  const exportCell = (r, c, india) => (c.type === "phone" ? exportPhones(r[c.key], india) : exportValue(r[c.key]));
  const WIDE = { long: 50, view: 40, links: 40, url: 40, link: 36, title: 34, email: 30, phone: 20 };

  function saveFile(blob, filename) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  }

  function toCsv(rows, india) {
    const cols = exportColumns();
    const q = (v) => { const s = String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const out = [cols.map((c) => q(c.label)).join(",")];
    rows.forEach((r) => out.push(cols.map((c) => q(exportCell(r, c, india))).join(",")));
    return new Blob(["﻿" + out.join("\r\n")], { type: "text/csv;charset=utf-8" });
  }

  async function toXlsx(rows, jobName, india) {
    const cols = exportColumns();
    const wb = new ExcelJS.Workbook();
    wb.creator = "Waloop Data Scraper";
    const ws = wb.addWorksheet(tool.label.slice(0, 31), { views: [{ state: "frozen", xSplit: 1, ySplit: 1 }] });
    ws.columns = cols.map((c) => ({ header: c.label, key: c.key, width: WIDE[c.type] || Math.max(12, c.label.length + 4) }));
    ws.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
    ws.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1E3A8A" } };
    const linkCols = cols.map((c, i) => [i + 1, c]).filter(([, c]) => ["url", "link", "image"].includes(c.type));
    rows.forEach((r) => {
      const values = {};
      cols.forEach((c) => {
        let v = exportCell(r, c, india); // phones stay text so Excel doesn't show 9.19825E+11
        if (typeof v === "string") v = v.slice(0, 32000);
        values[c.key] = v;
      });
      const row = ws.addRow(values);
      linkCols.forEach(([i, c]) => {
        const v = r[c.key];
        if (typeof v === "string" && v.startsWith("http") && v.length < 2000) {
          row.getCell(i).value = { text: v, hyperlink: v };
          row.getCell(i).font = { color: { argb: "FF1D4ED8" }, underline: true };
        }
      });
    });
    if (rows.length) ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: rows.length + 1, column: cols.length } };
    const info = wb.addWorksheet("Summary");
    info.columns = [{ width: 20 }, { width: 44 }];
    [["Tool", tool.label], ["Job", jobName], ["Rows", rows.length], ["Exported at", new Date().toLocaleString()]]
      .concat(tool.stats.map((s) => [s.label, rows.filter((r) => r[s.key]).length])).forEach((l) => info.addRow(l));
    const buf = await wb.xlsx.writeBuffer();
    return new Blob([buf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  }

  async function download(format) {
    if (!filtered().length) { alert("Nothing to export yet."); return; }
    const btns = [$("expCsv"), $("expXlsx")];
    btns.forEach((b) => { b.disabled = true; });
    try {
      const name = docOf()?.name || tool.label;
      const india = (docOf()?.spec?.country || "in") === "in";
      const rows = splitPhoneRows(filtered(), india);
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "").replace(/(\d{8})(\d{4})/, "$1_$2");
      const base = `${name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_|_$/g, "").slice(0, 60) || "data"}_${tool.id}_${stamp}`;
      if (format === "csv") saveFile(toCsv(rows, india), `${base}.csv`);
      else saveFile(await toXlsx(rows, name, india), `${base}.xlsx`);
    } catch (e) {
      alert(`Export failed: ${e.message}`);
    } finally {
      btns.forEach((b) => { b.disabled = false; });
    }
  }
  $("expCsv").addEventListener("click", () => download("csv"));
  $("expXlsx").addEventListener("click", () => download("xlsx"));

  // ------------------------------------------------------------ boot
  window.addEventListener("beforeunload", (e) => {
    if (!runners.size) return;
    checkpointAll();
    e.preventDefault();
    e.returnValue = "A scrape job is still running in this tab.";
  });

  (async () => {
    try {
      ME = await api("/api/me");
      TOOLS = await api("/api/tools");
      allJobs = await api("/api/jobs").catch(() => []);
    } catch (e) {
      document.body.insertAdjacentHTML("afterbegin", `<p class="err" style="padding:12px 20px">Server not reachable: ${esc(e.message)}</p>`);
      return;
    }
    refreshCredits();
    selectTool(location.hash.slice(1) || store.get("tab", "maps"));
    let tabSig = "";
    uiTimer = setInterval(() => {
      const sig = [...runners.keys()].join();
      if (sig !== tabSig) { tabSig = sig; renderTabs(); }
      if (runners.size) refreshView();
    }, 1200);
  })();
})();
