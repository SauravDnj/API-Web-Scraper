(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => Number(n || 0).toLocaleString("en-IN");

  async function post(path, data, method = "POST") {
    const res = await fetch(path, {
      method, headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(data || {}),
    });
    const out = await res.json().catch(() => ({}));
    if (res.status === 401 && out.auth) location.href = "/login";
    if (!res.ok) throw Object.assign(new Error(out.error || `HTTP ${res.status}`), { errors: out.errors });
    return out;
  }
  const ic = (name, cls = "") => `<svg class="ico${cls ? ` ${cls}` : ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const show = (id, msg) => { const el = $(id); if (el) { el.textContent = msg; el.hidden = !msg; } };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  document.querySelectorAll("[data-toggle-pw]").forEach((b) => b.addEventListener("click", () => {
    const input = $(b.dataset.togglePw);
    input.type = input.type === "password" ? "text" : "password";
    (b.querySelector("span") || b).textContent = input.type === "password" ? "Show" : "Hide";
  }));

  // ---------- login / signup
  const authForm = $("authForm");
  if (authForm) {
    authForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("formErr", "");
      const mode = authForm.dataset.mode;
      const data = { email: $("email").value.trim(), password: $("password").value };
      if (mode === "signup") data.name = $("name").value.trim();
      else data.next = new URLSearchParams(location.search).get("next") || "/";
      if (!data.email || !data.password) return show("formErr", "Enter your email and password.");
      $("submitBtn").disabled = true;
      try {
        const out = await post(`/api/auth/${mode}`, data);
        location.href = out.next || "/";
      } catch (err) {
        show("formErr", err.message);
        $("submitBtn").disabled = false;
      }
    });
  }

  // ---------- API keys
  const STATUS = {
    active: ["Active", "done"], empty: ["No credits", "error"], invalid: ["Invalid", "error"],
    error: ["Check failed", "paused"], off: ["Disabled", "stopped"],
  };
  const ago = (iso) => {
    if (!iso) return "never";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleDateString();
  };

  function renderKeys(data) {
    const keys = data.keys || [];
    $("keysRefresh").hidden = !keys.length;
    if ($("startBtn")) $("startBtn").hidden = !data.enabled;
    $("keyTotals").hidden = !keys.length;
    $("keyTotals").innerHTML = [
      ["Total credits", fmt(data.total_balance)],
      ["Active keys", `${fmt(data.active)} <small>/ ${fmt(data.count)}</small>`],
      ["Enabled", fmt(data.enabled)],
    ].map(([l, v]) => `<div class="stat"><span>${l}</span><b>${v}</b></div>`).join("");
    $("keyTotals").querySelector(".stat")?.classList.add("hero");
    if (!keys.length) {
      $("keyList").innerHTML = `<div class="empty-keys">${ic("key")}<p>No API keys added yet. Paste your first key below.</p></div>`;
      return;
    }
    const top = Math.max(1, ...keys.map((k) => k.balance || 0));
    $("keyList").innerHTML = `<div class="table-scroll"><table class="key-table">
      <thead><tr><th>#</th><th>Key</th><th class="num">Credits</th><th>Status</th><th>Checked</th><th></th></tr></thead>
      <tbody>${keys.map((k, i) => {
        const [label, cls] = STATUS[k.enabled === false ? "off" : k.status] || STATUS.active;
        const pct = Math.round(((k.balance || 0) / top) * 100);
        return `<tr class="${k.enabled === false ? "off" : ""}">
          <td class="muted">${i + 1}</td>
          <td><b>${esc(k.label)}</b><br><code>${esc(k.hint)}</code></td>
          <td class="num"><b>${k.balance === null || k.balance === undefined ? "–" : fmt(k.balance)}</b>
            <div class="meter"><i style="width:${pct}%"></i></div></td>
          <td><span class="status ${cls}" title="${esc(k.error || "")}">${label}</span></td>
          <td class="muted nowrap">${ic("clock", "tiny")}${ago(k.checked_at)}</td>
          <td class="key-actions">
            <label class="switch" title="Use this key in jobs"><input type="checkbox" aria-label="Use ${esc(k.label)} in jobs" data-toggle="${esc(k.id)}"${k.enabled === false ? "" : " checked"}><span></span></label>
            <button type="button" class="btn ghost sm icon-only" data-rename="${esc(k.id)}" data-label="${esc(k.label)}" title="Rename" aria-label="Rename ${esc(k.label)}">${ic("pencil")}</button>
            <button type="button" class="btn ghost sm icon-only danger-hover" data-remove="${esc(k.id)}" data-label="${esc(k.label)}" title="Remove" aria-label="Remove ${esc(k.label)}">${ic("trash")}</button>
          </td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  async function keyAction(fn) {
    show("keyErr", ""); show("keyOk", "");
    try { renderKeys(await fn()); } catch (err) { show("keyErr", err.message); }
  }

  const keyForm = $("keyForm");
  if (keyForm) {
    fetch("/api/me/keys").then((r) => r.json()).then(renderKeys).catch(() => {});

    $("keyList").addEventListener("change", (e) => {
      const id = e.target.dataset.toggle;
      if (id) keyAction(() => post(`/api/me/keys/${id}`, { enabled: e.target.checked }));
    });
    $("keyList").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      if (btn.dataset.rename) {
        const label = prompt("Name for this key:", btn.dataset.label);
        if (label !== null) keyAction(() => post(`/api/me/keys/${btn.dataset.rename}`, { label }));
      } else if (btn.dataset.remove) {
        if (confirm(`Remove the key "${btn.dataset.label}"?`)) keyAction(() => post(`/api/me/keys/${btn.dataset.remove}`, null, "DELETE"));
      }
    });
    $("keysRefresh").addEventListener("click", async () => {
      const label = $("keysRefresh").querySelector("span");
      $("keysRefresh").disabled = true; $("keysRefresh").classList.add("busy"); label.textContent = "Checking…";
      await keyAction(() => post("/api/me/keys/refresh"));
      $("keysRefresh").disabled = false; $("keysRefresh").classList.remove("busy"); label.textContent = "Refresh credits";
    });

    fetch("/api/me").then((r) => r.json()).then((me) => {
      if ($("sessionInfo")) {
        const exp = new Date(me.session_expires_at * 1000);
        $("sessionInfo").textContent = `Logged in as ${me.email}. Session ends ${exp.toLocaleString()} (24 hours after login). Storage: ${me.storage === "blob" ? "Vercel Blob" : "local JSON files"}.`;
      }
    }).catch(() => {});

    keyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("keyErr", ""); show("keyOk", "");
      const keys = $("apiKeys").value.trim();
      if (!keys) return show("keyErr", "Paste at least one API key.");
      const n = keys.split(/[\r\n,]+/).filter((x) => x.trim()).length;
      $("keyBtn").disabled = true; $("keyBtn").classList.add("busy");
      $("keyBtn").querySelector("span").textContent = `Checking ${n} key${n === 1 ? "" : "s"}…`;
      try {
        const out = await post("/api/me/keys", { keys });
        $("apiKeys").value = "";
        renderKeys(out);
        show("keyOk", `${out.added} key${out.added === 1 ? "" : "s"} added. Total credits: ${fmt(out.total_balance)}.`);
        if (out.errors?.length) show("keyErr", `Not added:\n${out.errors.join("\n")}`);
        if (window.FIRST_TIME) setTimeout(() => { location.href = "/"; }, 1200);
      } catch (err) {
        show("keyErr", err.errors?.length ? `${err.message}\n${err.errors.join("\n")}` : err.message);
      } finally {
        $("keyBtn").disabled = false; $("keyBtn").classList.remove("busy");
        $("keyBtn").querySelector("span").textContent = window.FIRST_TIME ? "Save keys & start" : "Add keys";
      }
    });
  }

  const delForm = $("delForm");
  if (delForm) {
    delForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("delErr", "");
      if (!confirm("Delete your account, API keys and all scraped jobs? This cannot be undone.")) return;
      try {
        await post("/api/me/delete", { password: $("delPw").value });
        location.href = "/login";
      } catch (err) {
        show("delErr", err.message);
      }
    });
  }

  const pwForm = $("pwForm");
  if (pwForm) {
    pwForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("pwErr", ""); show("pwOk", "");
      try {
        await post("/api/me/password", { current: $("pwCurrent").value, new: $("pwNew").value });
        $("pwCurrent").value = ""; $("pwNew").value = "";
        show("pwOk", "Password changed.");
      } catch (err) {
        show("pwErr", err.message);
      }
    });
  }
})();
