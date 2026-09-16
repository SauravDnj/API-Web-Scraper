(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => Number(n || 0).toLocaleString("en-IN");

  async function post(path, data) {
    const res = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(data),
    });
    const out = await res.json().catch(() => ({}));
    if (res.status === 401 && out.auth) location.href = "/login";
    if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
    return out;
  }
  const show = (id, msg) => { const el = $(id); if (el) { el.textContent = msg; el.hidden = !msg; } };

  document.querySelectorAll("[data-toggle-pw]").forEach((b) => b.addEventListener("click", () => {
    const input = $(b.dataset.togglePw);
    input.type = input.type === "password" ? "text" : "password";
    b.textContent = input.type === "password" ? "Show" : "Hide";
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

  // ---------- API key + settings
  const keyForm = $("keyForm");
  if (keyForm) {
    fetch("/api/me").then((r) => r.json()).then((me) => {
      $("keyStatus").innerHTML = me.has_key
        ? `Current key: <code>••••••••${me.api_key_last4}</code>`
        : "No API key added yet.";
      if ($("sessionInfo")) {
        const exp = new Date(me.session_expires_at * 1000);
        $("sessionInfo").textContent = `Logged in as ${me.email}. Session ends ${exp.toLocaleString()} (24 hours after login). Storage: ${me.storage === "blob" ? "Vercel Blob" : "local JSON files"}.`;
      }
    }).catch(() => {});

    keyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("keyErr", ""); show("keyOk", "");
      const key = $("apiKey").value.trim();
      if (!key) return show("keyErr", "Paste your API key.");
      $("keyBtn").disabled = true;
      try {
        const out = await post("/api/me/api-key", { api_key: key });
        $("apiKey").value = "";
        $("keyStatus").innerHTML = `Current key: <code>••••••••${out.last4}</code>`;
        show("keyOk", `Key saved. Credit balance: ${fmt(out.balance)}.`);
        if (window.FIRST_TIME) setTimeout(() => { location.href = "/"; }, 900);
      } catch (err) {
        show("keyErr", err.message);
      } finally {
        $("keyBtn").disabled = false;
      }
    });
  }

  const delForm = $("delForm");
  if (delForm) {
    delForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      show("delErr", "");
      if (!confirm("Delete your account, API key and all scraped jobs? This cannot be undone.")) return;
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
