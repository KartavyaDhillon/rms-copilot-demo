/* pages/reports.js — Report Center.
   Seven report types generated live from the execution archive. Preview shows
   the document in a modal; PDF opens the print-ready document (browser's
   Print → Save as PDF); CSV exports the underlying rows (opens in Excel). */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;

  const REPORTS = [
    { id: "daily",   icon: "file",     name: "Daily Operations Report",  days: 1,  desc: "Today's complete picture: every execution, success rate, failures, emails and files produced." },
    { id: "weekly",  icon: "chart",    name: "Weekly Report",            days: 7,  desc: "Last 7 days aggregated: per-day volumes, success trend, and per-pipeline totals." },
    { id: "monthly", icon: "calendar", name: "Monthly Report",           days: 30, desc: "Last 30 days aggregated: long-range reliability and workload view for management." },
    { id: "exec",    icon: "terminal", name: "Execution Summary",        days: 7,  desc: "Raw execution ledger — one row per run with status, duration and log file name." },
    { id: "failed",  icon: "alert",    name: "Failed Jobs Report",       days: 7,  desc: "Every failed or stopped execution in the range, ready for a follow-up review." },
    { id: "dl",      icon: "download", name: "Download Summary",         days: 7,  desc: "All download-type steps (NSE/MCX/NCDEX/FTP fetches) and their outcomes." },
    { id: "email",   icon: "mail",     name: "Email & WhatsApp Report",  days: 7,  desc: "Send counts detected per run, including failures, grouped by pipeline." },
  ];

  const fmtIN = (d) => d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

  function rangeItems(days) {
    return (RMS._histCache30 || []).filter((e) =>
      new Date(e.start.replace(" ", "T")) >= new Date(Date.now() - days * 86400000));
  }

  function kpisHTML(items) {
    const ok = items.filter((e) => e.status === "ok").length;
    const fail = items.filter((e) => e.status === "failed").length;
    const durs = items.filter((e) => e.status === "ok" && e.duration_s).map((e) => e.duration_s);
    const emails = items.reduce((a, e) => a + (e.emails || 0), 0);
    return '<div class="kpis">' +
      "<div><b>" + items.length + "</b><span>Executions</span></div>" +
      "<div><b>" + (ok + fail ? Math.round(100 * ok / (ok + fail)) + "%" : "—") + "</b><span>Success</span></div>" +
      "<div><b>" + fail + "</b><span>Failed</span></div>" +
      "<div><b>" + RMS.fmtDur(durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : null) + "</b><span>Avg runtime</span></div>" +
      "<div><b>" + emails + "</b><span>Emails</span></div></div>";
  }

  function tableHTML(items, cols) {
    const head = "<tr>" + cols.map((c) => "<th>" + c[0] + "</th>").join("") + "</tr>";
    const body = items.map((e) => "<tr>" + cols.map((c) => "<td>" + c[1](e) + "</td>").join("") + "</tr>").join("");
    return "<table>" + head + body + "</table>";
  }

  const COLS_FULL = [
    ["Time", (e) => e.start.slice(5, 16)],
    ["Pipeline", (e) => esc(e.pipe_name)],
    ["Step", (e) => esc(e.label)],
    ["Status", (e) => e.status === "ok" ? '<span class="ok">success</span>' : e.status === "failed" ? '<span class="bad">FAILED</span>' : e.status],
    ["Duration", (e) => RMS.fmtDur(e.duration_s)],
    ["Emails", (e) => e.emails || ""],
  ];

  function perDayTable(items, days) {
    const rows = [];
    for (let i = days - 1; i >= 0; i--) {
      const key = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
      const de = items.filter((e) => e.date === key);
      const ok = de.filter((e) => e.status === "ok").length, fl = de.filter((e) => e.status === "failed").length;
      rows.push({ date: key, total: de.length, ok, fl,
                  rate: ok + fl ? Math.round(100 * ok / (ok + fl)) + "%" : "—" });
    }
    return tableHTML(rows, [["Date", (r) => r.date], ["Executions", (r) => r.total],
      ["Success", (r) => '<span class="ok">' + r.ok + "</span>"], ["Failed", (r) => r.fl ? '<span class="bad">' + r.fl + "</span>" : "0"],
      ["Success rate", (r) => r.rate]]);
  }

  function perPipeTable(items) {
    const pipes = {};
    items.forEach((e) => {
      const p = (pipes[e.pipe_name] = pipes[e.pipe_name] || { n: 0, ok: 0, fl: 0, emails: 0 });
      p.n++; if (e.status === "ok") p.ok++; if (e.status === "failed") p.fl++; p.emails += e.emails || 0;
    });
    return tableHTML(Object.entries(pipes).map(([k, v]) => ({ k, ...v })), [
      ["Pipeline", (r) => esc(r.k)], ["Executions", (r) => r.n],
      ["Success", (r) => '<span class="ok">' + r.ok + "</span>"],
      ["Failed", (r) => r.fl ? '<span class="bad">' + r.fl + "</span>" : "0"],
      ["Emails", (r) => r.emails]]);
  }

  function buildDoc(rep) {
    const items = rangeItems(rep.days);
    const from = fmtIN(new Date(Date.now() - (rep.days - 1) * 86400000)), to = fmtIN(new Date());
    let body = "<h1>RMS Copilot — " + esc(rep.name) + "</h1>" +
      '<div class="sub">Range: ' + (rep.days === 1 ? to : from + " → " + to) + " · generated " +
      new Date().toLocaleString("en-IN") + " · mastertrust RMS</div>";
    if (rep.id === "daily") {
      body += kpisHTML(items) + "<h2>Executions</h2>" + tableHTML(items, COLS_FULL) +
        "<h2>Per pipeline</h2>" + perPipeTable(items);
    } else if (rep.id === "weekly" || rep.id === "monthly") {
      body += kpisHTML(items) + "<h2>Per day</h2>" + perDayTable(items, rep.days) +
        "<h2>Per pipeline</h2>" + perPipeTable(items);
    } else if (rep.id === "exec") {
      body += kpisHTML(items) + "<h2>Execution ledger</h2>" +
        tableHTML(items, COLS_FULL.concat([["Log file", (e) => "<small>" + esc(e.file) + "</small>"]]));
    } else if (rep.id === "failed") {
      const f = items.filter((e) => e.status === "failed" || e.status === "stopped");
      body += '<div class="kpis"><div><b>' + f.length + "</b><span>Failed / stopped</span></div></div>" +
        (f.length ? tableHTML(f, COLS_FULL) : "<p>No failed executions in this range. ✔</p>");
    } else if (rep.id === "dl") {
      const d = items.filter((e) => /download|mcx|nmass|ncdex|ftp/i.test(e.label));
      body += kpisHTML(d) + "<h2>Download steps</h2>" + tableHTML(d, COLS_FULL);
    } else if (rep.id === "email") {
      const m = items.filter((e) => (e.emails || 0) + (e.whatsapp || 0) + (e.emails_failed || 0) > 0);
      body += kpisHTML(items) + "<h2>Runs that sent messages</h2>" +
        tableHTML(m, COLS_FULL.concat([["WhatsApp", (e) => e.whatsapp || ""],
          ["Email fails", (e) => e.emails_failed ? '<span class="bad">' + e.emails_failed + "</span>" : ""]])) +
        "<h2>Per pipeline</h2>" + perPipeTable(items);
    }
    return body;
  }

  function csvFor(rep) {
    const items = rangeItems(rep.days);
    const pick = rep.id === "failed" ? items.filter((e) => e.status === "failed" || e.status === "stopped")
      : rep.id === "dl" ? items.filter((e) => /download|mcx|nmass|ncdex|ftp/i.test(e.label))
      : rep.id === "email" ? items.filter((e) => (e.emails || 0) + (e.whatsapp || 0) > 0)
      : items;
    const rows = [["start", "pipeline", "step", "status", "duration_s", "emails", "whatsapp", "log_file"]];
    pick.forEach((e) => rows.push([e.start, e.pipe_name, e.label, e.status, e.duration_s ?? "", e.emails, e.whatsapp, e.file]));
    RMS.downloadCSV(rows, "rms_" + rep.id + "_report.csv");
  }

  function preview(rep) {
    RMS.modal(rep.name,
      '<div style="background:#fff;color:#17202e;border-radius:10px;padding:26px;font-family:Segoe UI,Arial,sans-serif">' +
      '<style>.modal-body table{width:100%;border-collapse:collapse;margin:8px 0}.modal-body th{font-size:10px;text-transform:uppercase;color:#667;text-align:left}' +
      ".modal-body th,.modal-body td{padding:5px 8px;border-bottom:1px solid #e9edf3;font-size:12px;color:#17202e}" +
      ".modal-body h1{font-size:19px;margin:0 0 2px;color:#111}.modal-body h2{font-size:13px;border-bottom:2px solid #e3e8f0;padding-bottom:4px;margin:20px 0 8px;color:#111}" +
      ".modal-body .sub{color:#667;font-size:11px;margin-bottom:16px}.modal-body .ok{color:#0ea371;font-weight:600}.modal-body .bad{color:#dc2626;font-weight:600}" +
      ".modal-body .kpis{display:flex;gap:24px;margin:12px 0}.modal-body .kpis div b{display:block;font-size:19px;color:#111}" +
      ".modal-body .kpis div span{font-size:9.5px;color:#667;text-transform:uppercase;letter-spacing:.06em}</style>" +
      buildDoc(rep) + "</div>", { icon: rep.icon, width: 900 });
  }

  function shell() {
    return '<div class="rep-grid">' + REPORTS.map((r, i) =>
      '<div class="card rep-card hover"><h4>' + RMS.icon(r.icon) + esc(r.name) + "</h4><p>" + esc(r.desc) + "</p>" +
      '<div class="rep-actions">' +
      '<button class="btn sm" data-act="preview" data-i="' + i + '">' + RMS.icon("eye") + " Preview</button>" +
      '<button class="btn sm ghost" data-act="pdf" data-i="' + i + '">' + RMS.icon("file") + " PDF</button>" +
      '<button class="btn sm ghost" data-act="csv" data-i="' + i + '">' + RMS.icon("download") + " CSV / Excel</button>" +
      "</div></div>").join("") + "</div>" +
      '<div class="card-sub" style="margin-top:14px">PDF opens a print-ready document — use the browser\'s <b>Print → Save as PDF</b>. CSV opens directly in Excel. All reports are generated live from the execution archive.</div>';
  }

  function load(cb) {
    RMS.api("/ops/history?days=30", { timeout: 25000 }).then((d) => {
      RMS._histCache30 = d.items || []; if (cb) cb();
    }).catch(() => { RMS._histCache30 = RMS._histCache30 || []; if (cb) cb(); });
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.reports = {
    title: "Reports", crumb: "Generate, preview and export operations reports",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        el.querySelectorAll("button[data-act]").forEach((b) => {
          b.onclick = () => {
            const rep = REPORTS[+b.dataset.i];
            load(() => {
              if (b.dataset.act === "preview") preview(rep);
              else if (b.dataset.act === "pdf") RMS.printDoc("RMS Copilot — " + rep.name, buildDoc(rep));
              else csvFor(rep);
            });
          };
        });
        booted = true;
      }
      load();
    },
  };
})();
