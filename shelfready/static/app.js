"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const ALLOWED = new Set(["P", "BR", "B", "EM", "I", "U", "H4", "H5", "H6", "UL", "OL", "LI"]);
  const EXPECTED_CHARS = 2200; // typical size of a full listing, used for the progress bar
  const TRADEMARKS = ["disney", "pixar", "marvel", "pokemon", "barbie", "bluey", "minecraft", "hello kitty", "paw patrol", "frozen", "star wars", "harry potter", "lego", "roblox", "crayola", "sharpie", "posca"];

  const state = { fixes: [], listing: null, controller: null, timer: null, checkTimer: null, marketplace: "amazon.com", model: "" };

  // ---------- utilities ----------
  const toast = (msg) => {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 1800);
  };
  const copy = async (text, label) => {
    try { await navigator.clipboard.writeText(text); toast(`${label} copied`); }
    catch { // clipboard API needs a secure context; fall back for plain http
      const ta = Object.assign(document.createElement("textarea"), { value: text });
      document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast(`${label} copied`);
    }
  };
  const show = (id, on) => { $(id).hidden = !on; };

  /** Render model-written HTML safely: only KDP's allowed tags survive, and never any attributes. */
  function safeRender(html, into) {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    const walk = (node, parent) => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) parent.appendChild(document.createTextNode(child.textContent));
        else if (child.nodeType === Node.ELEMENT_NODE) {
          if (ALLOWED.has(child.tagName)) { const el = document.createElement(child.tagName); parent.appendChild(el); walk(child, el); }
          else walk(child, parent); // unwrap unknown tags, keep their text
        }
      }
    };
    into.replaceChildren(); walk(doc.body.firstChild, into);
  }

  // ---------- health ----------
  async function health() {
    const s = $("status");
    try {
      const r = await fetch("/api/health"); const h = await r.json();
      state.model = h.model; $("model-name").textContent = h.model;
      s.className = "status " + (h.ok ? "ok" : "bad");
      $("status-text").textContent = h.ok ? `${h.model} · local · ready` : `${h.message}${h.hint ? ". " + h.hint : ""}`;
    } catch {
      s.className = "status bad"; $("status-text").textContent = "ShelfReady server not reachable";
    }
  }

  // ---------- brief ----------
  function brief() {
    const f = $("brief");
    return {
      theme: f.theme.value.trim(), audience: f.audience.value, style: f.style.value,
      pages: f.pages.value ? Number(f.pages.value) : null, trim: f.trim.value, marketplace: f.marketplace.value,
      single_sided: f.single_sided.checked, large_print: f.large_print.checked, notes: f.notes.value.trim(),
    };
  }
  $("theme").addEventListener("input", (e) => {
    e.target.removeAttribute("aria-invalid");
    const low = e.target.value.toLowerCase();
    const hit = TRADEMARKS.find((t) => low.includes(t));
    $("theme-hint").textContent = hit ? `Heads up: "${hit}" is a brand. KDP pulls listings that use brands without a licence.` : "";
  });
  $("sample").addEventListener("click", () => {
    const f = $("brief");
    f.theme.value = "Cozy cabins and wildlife of the Canadian Rockies";
    f.audience.value = "adults"; f.style.value = "cozy"; f.pages.value = 50; f.trim.value = "8.5 x 11 in";
    f.notes.value = "Mountain cabins, moose, bears, loons, campfires and snowy pines. Calm, unhurried scenes for winding down after work. Designed in Alberta.";
    f.theme.dispatchEvent(new Event("input")); f.theme.focus();
  });

  // ---------- generate ----------
  function setWorking(on) {
    $("generate").disabled = on;
    $("generate").querySelector(".label").textContent = on ? "Writing…" : (state.listing ? "Write again" : "Write my listing");
    show("working", on);
    if (on) { show("empty", false); show("alert", false); }
  }
  function fail(message, hint) {
    $("alert-title").textContent = message; $("alert-hint").textContent = hint || "";
    show("alert", true); if (!state.listing) show("empty", true);
  }

  async function generate(ev) {
    ev && ev.preventDefault();
    if (state.controller) return; // already writing
    const b = brief();
    if (b.theme.length < 3) { $("theme").setAttribute("aria-invalid", "true"); $("theme").focus(); toast("Add a theme first"); return; }
    state.marketplace = b.marketplace;
    state.controller = new AbortController();
    setWorking(true);
    const started = performance.now(); let chars = 0, stage = "drafting";
    const tick = () => {
      const secs = Math.round((performance.now() - started) / 1000);
      $("working-meta").textContent = `${chars.toLocaleString()} characters · ${secs}s`;
    };
    state.timer = setInterval(tick, 500);
    $("bar-fill").style.width = "4%"; $("stage-text").textContent = "Drafting your listing…";
    try {
      const res = await fetch("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b), signal: state.controller.signal,
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw Object.assign(new Error(e.error || `Server error ${res.status}`), { hint: "" }); }
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "", done = false;
      while (!done) {
        const chunk = await reader.read(); done = chunk.done;
        buf += dec.decode(chunk.value || new Uint8Array(), { stream: !done });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
          if (!line.trim()) continue;
          const evn = JSON.parse(line);
          if (evn.type === "stage") {
            stage = evn.stage; chars = 0;
            $("stage-text").textContent = stage === "repairing" ? "Fixing KDP issues the checker found…" : "Drafting your listing…";
          } else if (evn.type === "progress") {
            chars = evn.chars; tick();
            const base = stage === "repairing" ? 60 : 0, span = stage === "repairing" ? 38 : 90;
            $("bar-fill").style.width = `${Math.min(base + span, base + span * (chars / EXPECTED_CHARS)) + 4}%`;
          } else if (evn.type === "result") {
            $("bar-fill").style.width = "100%";
            state.listing = evn.listing; state.fixes = evn.fixes || []; render(evn);
            $("meta").textContent = `${evn.model} · ${evn.seconds}s${evn.repaired ? " · auto-repaired" : ""}`;
          } else if (evn.type === "error") {
            throw Object.assign(new Error(evn.message), { hint: evn.hint });
          }
        }
      }
    } catch (err) {
      if (err.name === "AbortError") toast("Cancelled");
      else fail(err.message || "Something went wrong.", err.hint || "");
    } finally {
      clearInterval(state.timer); state.controller = null; setWorking(false);
    }
  }
  $("brief").addEventListener("submit", generate);
  document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") generate(e); });
  $("cancel").addEventListener("click", () => state.controller && state.controller.abort());

  // ---------- render ----------
  function render({ listing, issues, score }) {
    show("empty", false); show("result", true); show("score", true);
    $("out-title").value = listing.title; $("out-subtitle").value = listing.subtitle;
    $("out-description").value = listing.description;
    const ol = $("out-keywords"); ol.replaceChildren();
    for (let i = 0; i < 7; i++) {
      const li = document.createElement("li");
      const input = Object.assign(document.createElement("input"), { value: listing.keywords[i] || "", maxLength: 80, placeholder: "empty box", ariaLabel: `Keyword ${i + 1}` });
      const len = Object.assign(document.createElement("span"), { className: "len" });
      input.addEventListener("input", onEdit);
      li.append(input, len); ol.appendChild(li);
    }
    paint(issues, score);
  }

  function current() {
    return {
      title: $("out-title").value.trim(), subtitle: $("out-subtitle").value.trim(), description: $("out-description").value,
      keywords: [...$("out-keywords").querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean),
    };
  }

  function paint(issues, score) {
    const l = current();
    safeRender(l.description, $("desc-preview"));
    // counters
    const combo = l.title.length + l.subtitle.length;
    $("title-count").textContent = `${l.title.length} characters`;
    $("title-count").classList.toggle("over", l.title.length > 60);
    $("combo-fill").style.width = `${Math.min(100, (combo / 199) * 100)}%`;
    $("combo-fill").style.background = combo > 199 ? "var(--err)" : combo > 170 ? "var(--warn)" : "var(--ok)";
    $("combo-text").textContent = `Title + subtitle: ${combo} / 199 characters`;
    $("desc-count").textContent = `${l.description.length.toLocaleString()} / 4,000`;
    $("desc-count").classList.toggle("over", l.description.length > 4000);
    const lis = [...$("out-keywords").children];
    lis.forEach((li, idx) => {
      const v = li.querySelector("input").value.trim();
      li.querySelector(".len").textContent = `${v.length}/50`;
      li.classList.toggle("over", v.length > 50);
      const mine = issues.filter((x) => x.field === "keywords" && x.message.startsWith(`Keyword ${keywordIndex(lis, idx)} `));
      li.classList.toggle("bad", mine.some((x) => x.level === "error"));
      li.classList.toggle("flag", !mine.some((x) => x.level === "error") && mine.length > 0);
    });
    // score
    $("score-value").textContent = score;
    const meter = $("score-meter");
    meter.style.strokeDashoffset = String(97.4 * (1 - score / 100));
    meter.style.stroke = score >= 85 ? "var(--ok)" : score >= 60 ? "var(--warn)" : "var(--err)";
    // issues
    const ul = $("issues"); ul.replaceChildren();
    const errors = issues.filter((i) => i.level === "error").length, warns = issues.length - errors;
    $("checks-count").textContent = issues.length ? `${errors} blocking · ${warns} advisory` : "all clear";
    if (!issues.length) {
      ul.appendChild(issueEl({ level: "pass", field: "listing", message: "Passes every KDP check ShelfReady runs.", fix: "Give it a final human read, then paste it into KDP." }));
    }
    [...issues].sort((a, b) => (a.level === b.level ? 0 : a.level === "error" ? -1 : 1)).forEach((i) => ul.appendChild(issueEl(i)));
    (state.fixes || []).forEach((f) => ul.appendChild(issueEl({ level: "pass", field: "auto-fixed", message: f })));
  }
  // keyword numbers in messages count only non-empty boxes
  function keywordIndex(lis, idx) {
    let n = 0; for (let i = 0; i <= idx; i++) if (lis[i].querySelector("input").value.trim()) n++; return n;
  }
  function issueEl(i) {
    const li = document.createElement("li"); li.className = i.level;
    const icon = document.createElement("i"); icon.textContent = i.level === "error" ? "!" : i.level === "warn" ? "?" : "✓";
    const body = document.createElement("div");
    const tag = Object.assign(document.createElement("span"), { className: "field-tag", textContent: i.field });
    body.append(tag, document.createTextNode(i.message));
    if (i.fix) body.appendChild(Object.assign(document.createElement("small"), { textContent: i.fix }));
    li.append(icon, body); return li;
  }

  // live re-check on edit
  function onEdit() {
    clearTimeout(state.checkTimer);
    state.checkTimer = setTimeout(async () => {
      try {
        const r = await fetch("/api/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing: current(), marketplace: state.marketplace }) });
        const d = await r.json(); paint(d.issues, d.score);
      } catch { toast("Couldn't re-run checks"); }
    }, 250);
  }
  ["out-title", "out-subtitle", "out-description"].forEach((id) => $(id).addEventListener("input", onEdit));

  // description view toggle
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    const html = b.dataset.view === "html"; show("out-description", html); show("desc-preview", !html);
  }));

  // copy + export
  const asText = () => {
    const l = current();
    return [`TITLE\n${l.title}`, `SUBTITLE\n${l.subtitle}`, `KEYWORDS\n${l.keywords.map((k, i) => `${i + 1}. ${k}`).join("\n")}`, `DESCRIPTION (HTML)\n${l.description}`].join("\n\n");
  };
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    const l = current(), k = b.dataset.copy;
    copy(k === "keywords" ? l.keywords.join("\n") : l[k], k[0].toUpperCase() + k.slice(1));
  }));
  $("copy-all").addEventListener("click", () => copy(asText(), "Listing"));
  $("download").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([asText()], { type: "text/plain" }));
    a.download = (current().title || "listing").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + ".txt";
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  health(); setInterval(health, 30000);
})();
