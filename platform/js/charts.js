/* charts.js — tiny hand-rolled SVG chart helpers (no CDN, no dependencies).
   Consistent look across Analytics/Reports: reads the active theme's CSS vars. */
(function () {
  window.RMS = window.RMS || {};

  function svgOpen(w, h) {
    return '<svg viewBox="0 0 ' + w + " " + h + '" width="100%" height="' + h + '" preserveAspectRatio="none" style="display:block">';
  }

  /* stacked ok/fail bars: data = [{label, ok, fail}] */
  RMS.chartBars = function (data, height) {
    const H = height || 120, W = Math.max(200, data.length * 26);
    const max = Math.max(1, ...data.map((d) => d.ok + d.fail));
    const bw = W / data.length;
    let s = svgOpen(W, H);
    data.forEach((d, i) => {
      const x = i * bw + bw * 0.18, w = bw * 0.64;
      const hOk = (H - 18) * d.ok / max, hF = (H - 18) * d.fail / max;
      s += '<rect x="' + x + '" y="' + (H - 14 - hOk) + '" width="' + w + '" height="' + Math.max(hOk, d.ok ? 2 : 0) + '" rx="2" fill="var(--ok)" opacity=".85"><title>' + d.label + ": " + d.ok + " ok</title></rect>";
      s += '<rect x="' + x + '" y="' + (H - 14 - hOk - hF) + '" width="' + w + '" height="' + Math.max(hF, d.fail ? 2 : 0) + '" rx="2" fill="var(--err)" opacity=".85"><title>' + d.label + ": " + d.fail + " failed</title></rect>";
    });
    s += "</svg>";
    const lbl = '<div style="display:flex">' + data.map((d) =>
      '<span style="flex:1;text-align:center;font-size:9px;color:var(--text-3);font-family:var(--mono);overflow:hidden">' + d.label + "</span>").join("") + "</div>";
    return s + lbl;
  };

  /* line chart: series = [{name, color, points:[num|null]}], labels = [str] */
  RMS.chartLine = function (series, labels, height, unit) {
    const H = height || 130, W = 560, P = 8;
    const all = series.flatMap((s) => s.points).filter((v) => v != null);
    const max = Math.max(1, ...all), min = Math.min(0, ...all);
    const n = Math.max(2, labels.length);
    const X = (i) => P + (W - 2 * P) * i / (n - 1);
    const Y = (v) => H - 16 - (H - 26) * (v - min) / (max - min || 1);
    let s = svgOpen(W, H);
    // soft grid
    for (let g = 1; g <= 3; g++) {
      const y = 6 + (H - 26) * g / 4;
      s += '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="var(--line)" stroke-width="1"/>';
    }
    series.forEach((sr) => {
      let path = "", started = false;
      sr.points.forEach((v, i) => {
        if (v == null) { started = false; return; }
        path += (started ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
        started = true;
      });
      s += '<path d="' + path + '" fill="none" stroke="' + sr.color + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
      sr.points.forEach((v, i) => {
        if (v == null) return;
        s += '<circle cx="' + X(i) + '" cy="' + Y(v) + '" r="2.6" fill="' + sr.color + '"><title>' + labels[i] + ": " + v + (unit || "") + "</title></circle>";
      });
    });
    s += "</svg>";
    const step = Math.ceil(n / 10);
    const lbl = '<div style="display:flex;justify-content:space-between;padding:0 2px">' +
      labels.map((l, i) => i % step ? "" : '<span style="font-size:9px;color:var(--text-3);font-family:var(--mono)">' + l + "</span>").join("") + "</div>";
    return s + lbl;
  };

  /* donut: parts = [{label, value, color}] */
  RMS.chartDonut = function (parts, size) {
    const S = size || 130, R = S / 2 - 10, C = 2 * Math.PI * R;
    const total = Math.max(1, parts.reduce((a, p) => a + p.value, 0));
    let off = 0, s = '<svg viewBox="0 0 ' + S + " " + S + '" width="' + S + '" height="' + S + '" style="transform:rotate(-90deg)">';
    s += '<circle cx="' + S / 2 + '" cy="' + S / 2 + '" r="' + R + '" fill="none" stroke="var(--surface-3)" stroke-width="13"/>';
    parts.forEach((p) => {
      const frac = p.value / total;
      if (frac <= 0) return;
      s += '<circle cx="' + S / 2 + '" cy="' + S / 2 + '" r="' + R + '" fill="none" stroke="' + p.color +
        '" stroke-width="13" stroke-dasharray="' + (frac * C).toFixed(1) + " " + C.toFixed(1) +
        '" stroke-dashoffset="' + (-off * C).toFixed(1) + '"><title>' + p.label + ": " + p.value + "</title></circle>";
      off += frac;
    });
    return s + "</svg>";
  };

  /* histogram: values (seconds) bucketed automatically */
  RMS.chartHist = function (values, height) {
    if (!values.length) return '<div class="bm-empty">No runtime data.</div>';
    const buckets = [30, 60, 120, 300, 600, 1200, 3600, Infinity];
    const names = ["<30s", "30-60s", "1-2m", "2-5m", "5-10m", "10-20m", "20-60m", ">1h"];
    const counts = buckets.map(() => 0);
    values.forEach((v) => { counts[buckets.findIndex((b) => v < b)]++; });
    return RMS.chartBars(names.map((n, i) => ({ label: n, ok: counts[i], fail: 0 })), height || 110);
  };

  RMS.legend = function (parts) {
    return '<div style="display:flex;flex-direction:column;gap:6px">' + parts.map((p) =>
      '<div style="display:flex;align-items:center;gap:8px;font-size:12px">' +
      '<span style="width:9px;height:9px;border-radius:3px;background:' + p.color + ';flex:none"></span>' +
      '<span style="flex:1;color:var(--text-2)">' + RMS.esc(p.label) + "</span><b>" + p.value + "</b></div>").join("") + "</div>";
  };

  /* CSV download helper (Excel opens .csv natively) */
  RMS.downloadCSV = function (rows, filename) {
    const csv = rows.map((r) => r.map((c) => {
      c = String(c == null ? "" : c);
      return /[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c;
    }).join(",")).join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = filename;
    a.click();
    RMS.toast("ok", "Exported " + filename);
  };

  /* printable document → user saves as PDF via the browser print dialog */
  RMS.printDoc = function (title, bodyHTML) {
    const w = window.open("", "_blank");
    if (!w) { RMS.toast("warn", "Popup blocked", "Allow popups to export PDF."); return; }
    w.document.write(
      "<!DOCTYPE html><html><head><title>" + RMS.esc(title) + "</title><style>" +
      "body{font-family:'Segoe UI',Arial,sans-serif;color:#17202e;margin:36px;font-size:13px;line-height:1.55}" +
      "h1{font-size:20px;margin:0 0 2px} .sub{color:#667;font-size:11px;margin-bottom:22px}" +
      "h2{font-size:14px;border-bottom:2px solid #e3e8f0;padding-bottom:5px;margin:26px 0 10px}" +
      "table{width:100%;border-collapse:collapse;margin:8px 0} th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#667;text-align:left}" +
      "th,td{padding:6px 8px;border-bottom:1px solid #e9edf3} .ok{color:#0ea371;font-weight:600} .bad{color:#dc2626;font-weight:600}" +
      ".kpis{display:flex;gap:26px;margin:14px 0} .kpis div b{display:block;font-size:20px}" +
      ".kpis div span{font-size:10px;color:#667;text-transform:uppercase;letter-spacing:.06em}" +
      "@media print{ .noprint{display:none} }</style></head><body>" +
      '<button class="noprint" style="padding:8px 16px;margin-bottom:18px" onclick="print()">🖨 Print / Save as PDF</button>' +
      bodyHTML + "</body></html>");
    w.document.close();
  };
})();
