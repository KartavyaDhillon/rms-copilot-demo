/* pages/email.js — Email Center.
   Operational view of everything the pipelines SEND: sent/failed counts
   detected in run logs, per-pipeline breakdown, recent send activity, and the
   full recipients directory read live from the same config the pipelines use.
   Editing recipients stays where it always was — the password-protected
   editors inside the Process Center — this page links there instead of
   duplicating a second (unprotected) editing path. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { days: 1, hist: [], recips: null };

  function shell() {
    return (
      '<div class="kpi-grid" id="eKpis" style="margin-bottom:12px"></div>' +
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="eDays"><button data-d="1" class="on">Today</button><button data-d="3">3d</button><button data-d="7">7d</button></div>' +
      '<div class="spacer"></div>' +
      '<button class="btn sm ghost" id="eRefresh">' + RMS.icon("refresh") + " Refresh</button></div></div>" +

      '<div class="dash-cols" style="align-items:start">' +
      '<div><div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("send") + ' Send Activity</div>' +
      '<span class="card-sub">runs that sent emails / WhatsApp — click for the log</span></div>' +
      '<div class="feed" id="eFeed"></div></div>' +
      '<div class="card" style="margin-top:12px"><div class="card-head"><div class="card-title">' + RMS.icon("chart") + ' Per Pipeline</div></div>' +
      '<div id="ePipes"></div></div></div>' +

      '<div><div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("mail") + ' Recipients Directory</div>' +
      '<span class="card-sub">live from .env config</span></div><div id="eRecips"><span class="skel" style="display:block;height:60px"></span></div>' +
      '<div class="card-sub" style="margin-top:12px">To change recipients, use the pipeline\'s own ✉️ / 📱 editors in the ' +
      '<a style="color:var(--accent);cursor:pointer" onclick="RMS.go(\'process\')">Process Center</a> — they are password-protected and update the live config.</div></div></div>' +
      "</div>"
    );
  }

  function kpi(icon, label, v, foot, cls) {
    return '<div class="card kpi' + (cls ? " accent-" + cls : "") + '"><div class="k-label">' + RMS.icon(icon) + label +
      '</div><div class="k-value" style="font-size:22px">' + v + '</div><div class="k-foot">' + (foot || "") + "</div></div>";
  }

  function inRange(e) {
    return new Date(e.start.replace(" ", "T")) >= new Date(Date.now() - state.days * 86400000);
  }

  function render() {
    const items = state.hist.filter(inRange);
    const emails = items.reduce((a, e) => a + (e.emails || 0), 0);
    const wa = items.reduce((a, e) => a + (e.whatsapp || 0), 0);
    const fails = items.reduce((a, e) => a + (e.emails_failed || 0), 0);
    const waFails = items.reduce((a, e) => a + (e.wa_failed || 0), 0);
    const senders = items.filter((e) => (e.emails || 0) + (e.whatsapp || 0) + (e.emails_failed || 0) + (e.wa_failed || 0) > 0);

    $("#eKpis").innerHTML =
      kpi("mail", "Emails Sent", emails, "detected in run logs", "ok") +
      kpi("send", "WhatsApp Sent", wa, "report images / alerts") +
      kpi("alert", "Email Failures", fails, fails ? "check the runs below" : "none 🎉", fails ? "err" : "ok") +
      kpi("alert", "WhatsApp Failures", waFails, waFails ? "check the runs below" : "none 🎉", waFails ? "err" : "ok");

    $("#eFeed").innerHTML = senders.length ? senders.slice(0, 20).map((e, i) =>
      '<div class="feed-item" data-i="' + i + '"><span class="feed-ic ' + ((e.emails_failed || e.wa_failed) ? "failed" : "ok") + '">' +
      RMS.icon((e.emails_failed || e.wa_failed) ? "alert" : "check") + "</span>" +
      '<div class="feed-main"><b>' + esc(e.label) + "</b><small>" + esc(e.pipe_name) +
      " · ✉ " + (e.emails || 0) + (e.whatsapp ? " · WA " + e.whatsapp : "") +
      (e.emails_failed ? ' · <span style="color:var(--err)">✕ ' + e.emails_failed + " failed</span>" : "") + "</small></div>" +
      '<span class="feed-time">' + esc(e.start.slice(5, 16)) + "</span></div>").join("")
      : '<div class="bm-empty">No send activity in this range yet.</div>';
    $("#eFeed").querySelectorAll("[data-i]").forEach((el) => {
      el.onclick = () => RMS.openLogViewer(senders[+el.dataset.i]);
    });

    const pipes = {};
    items.forEach((e) => {
      const p = (pipes[e.pipe_name] = pipes[e.pipe_name] || { emails: 0, wa: 0, fails: 0 });
      p.emails += e.emails || 0; p.wa += e.whatsapp || 0; p.fails += (e.emails_failed || 0) + (e.wa_failed || 0);
    });
    const rows = Object.entries(pipes).filter(([, v]) => v.emails + v.wa + v.fails > 0);
    $("#ePipes").innerHTML = rows.length ? '<div class="feed">' + rows.map(([k, v]) =>
      '<div class="feed-item" style="cursor:default;grid-template-columns:1fr auto auto"><div class="feed-main"><b>' + esc(k) + "</b></div>" +
      '<span class="badge ok">✉ ' + v.emails + (v.wa ? " · WA " + v.wa : "") + "</span>" +
      (v.fails ? '<span class="badge err">✕ ' + v.fails + "</span>" : "<span></span>") + "</div>").join("") + "</div>"
      : '<div class="bm-empty">No sends in range.</div>';
  }

  function renderRecips() {
    const box = $("#eRecips");
    const r = state.recips;
    if (!r) { box.innerHTML = '<div class="bm-empty">Could not load recipients config.</div>'; return; }
    let html = "";
    const group = (title, entries) => {
      if (!entries.length) return "";
      return '<div class="sec-head" style="margin:14px 2px 8px"><h3 style="font-size:12px">' + title + "</h3></div>" +
        entries.map(([label, to, cc]) =>
          '<div style="padding:7px 2px;border-bottom:1px solid var(--line);font-size:12px">' +
          "<b>" + esc(label) + '</b><div style="color:var(--text-2);word-break:break-all;margin-top:2px">' +
          esc(to || "—") + "</div>" +
          (cc ? '<div style="color:var(--text-3);word-break:break-all;font-size:11px">CC: ' + esc(cc) + "</div>" : "") + "</div>").join("");
    };
    ["ctcl", "ms15"].forEach((p) => {
      const cfg = r[p];
      if (cfg && cfg.emails) {
        html += group(p === "ctcl" ? "CTCL vs NMASS" : "Margin Shortage (15-min)",
          Object.entries(cfg.emails).map(([lbl, v]) => [lbl, v.to, v.cc]));
      }
    });
    if (r.sa && r.sa.mail) {
      html += group("Short Allocation / LAS",
        Object.entries(r.sa.mail).map(([k, v]) => [k.replace(/^SA2?_MAIL_|^SA_MAIL_/, "").replace(/_/g, " "),
          Array.isArray(v) ? v.join(", ") : String(v || ""), ""]));
    }
    if (r.sa && r.sa.whatsapp) {
      html += group("WhatsApp (Short Allocation zones)",
        Object.entries(r.sa.whatsapp).map(([k, v]) => [k.replace(/^SA_WA_/, ""),
          Array.isArray(v) ? v.join(", ") : String(v || ""), ""]));
    }
    box.innerHTML = html || '<div class="bm-empty">No recipient config found.</div>';
  }

  function load() {
    RMS.api("/ops/history?days=" + Math.max(state.days, 2), { timeout: 25000 })
      .then((d) => { state.hist = d.items || []; render(); }).catch(() => {});
    if (!state.recips) {
      Promise.all([
        RMS.api("/px/config?pipe=ctcl", { quiet: true }).catch(() => null),
        RMS.api("/px/config?pipe=ms15", { quiet: true }).catch(() => null),
        RMS.api("/sa/config", { quiet: true }).catch(() => null),
      ]).then(([ctcl, ms15, sa]) => { state.recips = { ctcl, ms15, sa }; renderRecips(); });
    }
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.email = {
    title: "Email Center", crumb: "Everything the pipelines send — emails, WhatsApp, recipients",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#eDays").querySelectorAll("button").forEach((b) => {
          b.onclick = () => { $("#eDays").querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); state.days = +b.dataset.d; render(); };
        });
        $("#eRefresh").onclick = load;
        booted = true;
      }
      load();
    },
  };
})();
