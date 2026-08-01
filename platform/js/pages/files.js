/* pages/files.js — File Explorer over the whitelisted pipeline folders + the
   run-log archive. Browse, search, sort, preview (text head, colorized),
   metadata, and save — the backend never accepts raw paths, only registered
   root keys + realpath-checked subpaths. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { roots: [], root: null, sub: "", q: "", sort: "mtime", dir: -1, items: [] };

  function shell() {
    return (
      '<div class="card" style="margin-bottom:12px">' +
      '<div class="root-chips" id="fxRoots" style="margin-bottom:12px"></div>' +
      '<div class="filters">' +
      '<div class="fx-crumbs" id="fxCrumbs"></div>' +
      '<div class="spacer"></div>' +
      '<input class="f-input" id="fxSearch" placeholder="Filter this folder…">' +
      '<select class="f-select" id="fxSort">' +
      '<option value="mtime">Newest first</option><option value="name">Name A→Z</option>' +
      '<option value="size">Largest first</option></select>' +
      '<button class="btn sm ghost" id="fxRefresh">' + RMS.icon("refresh") + "</button></div></div>" +

      '<div class="card" style="padding:0;overflow:hidden">' +
      '<div style="max-height:600px;overflow:auto"><table class="htable"><thead><tr>' +
      "<th>Name</th><th>Size</th><th>Modified</th><th></th></tr></thead>" +
      '<tbody id="fxRows"></tbody></table></div>' +
      '<div class="card-sub" id="fxFoot" style="padding:10px 16px;border-top:1px solid var(--line)"></div></div>'
    );
  }

  function renderRoots() {
    $("#fxRoots").innerHTML = state.roots.map((r) =>
      '<span class="chip' + (state.root === r.key ? " on" : "") + '" data-r="' + r.key + '">' +
      RMS.icon(r.key === "LOGS" ? "terminal" : "folder") + esc(r.label) + "</span>").join("");
    $("#fxRoots").querySelectorAll(".chip").forEach((c) => {
      c.onclick = () => { state.root = c.dataset.r; state.sub = ""; state.q = ""; $("#fxSearch").value = ""; load(); };
    });
  }

  function renderCrumbs() {
    const root = state.roots.find((r) => r.key === state.root);
    const parts = state.sub ? state.sub.split("/") : [];
    let html = '<a data-sub="">' + esc(root ? root.label : "—") + "</a>";
    let acc = "";
    parts.forEach((p) => {
      acc = acc ? acc + "/" + p : p;
      html += '<span class="sep">/</span><a data-sub="' + esc(acc) + '">' + esc(p) + "</a>";
    });
    $("#fxCrumbs").innerHTML = html;
    $("#fxCrumbs").querySelectorAll("a").forEach((a) => {
      a.onclick = () => { state.sub = a.dataset.sub; load(); };
    });
  }

  function sorted() {
    const items = state.items.slice();
    const k = state.sort;
    items.sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;   // folders always first
      if (k === "name") return a.name.localeCompare(b.name) * -state.dir;
      if (k === "size") return ((a.size || 0) - (b.size || 0)) * state.dir;
      return (a.mtime > b.mtime ? 1 : -1) * state.dir;
    });
    return items;
  }

  function render() {
    renderCrumbs();
    const items = sorted();
    $("#fxRows").innerHTML = items.length ? items.map((f, i) =>
      '<tr class="fx-row" data-i="' + i + '"><td><span class="fx-ic' + (f.dir ? " dir" : "") + '">' +
      RMS.icon(f.dir ? "folder" : "file") + "</span><b>" + esc(f.name) + "</b></td>" +
      '<td class="mono">' + (f.dir ? "—" : RMS.fmtBytes(f.size)) + '</td><td class="mono">' + esc(f.mtime) + "</td>" +
      "<td>" + (f.dir ? "" : '<button class="btn sm ghost" data-save="' + i + '" title="Download">' + RMS.icon("download") + "</button>") + "</td></tr>").join("")
      : '<tr><td colspan="4"><div class="bm-empty">Empty folder' + (state.q ? " (or nothing matches the filter)" : "") + ".</div></td></tr>";
    const files = items.filter((f) => !f.dir);
    $("#fxFoot").textContent = items.filter((f) => f.dir).length + " folders · " + files.length + " files · " +
      RMS.fmtBytes(files.reduce((a, f) => a + (f.size || 0), 0));
    $("#fxRows").querySelectorAll("tr[data-i]").forEach((tr) => {
      tr.onclick = (e) => {
        if (e.target.closest("[data-save]")) return;
        const f = items[+tr.dataset.i];
        if (f.dir) { state.sub = state.sub ? state.sub + "/" + f.name : f.name; load(); }
        else RMS.previewFile(state.root, state.sub ? state.sub + "/" + f.name : f.name);
      };
    });
    $("#fxRows").querySelectorAll("[data-save]").forEach((b) => {
      b.onclick = () => {
        const f = items[+b.dataset.save];
        const sub = state.sub ? state.sub + "/" + f.name : f.name;
        location.href = "/ops/file/get?root=" + encodeURIComponent(state.root) + "&sub=" + encodeURIComponent(sub);
      };
    });
  }

  function load() {
    if (!state.root) return;
    renderRoots();
    RMS.api("/ops/files?root=" + encodeURIComponent(state.root) + "&sub=" + encodeURIComponent(state.sub) +
      "&q=" + encodeURIComponent(state.q), { timeout: 20000 })
      .then((d) => { state.items = d.items || []; render(); })
      .catch(() => {});
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.files = {
    title: "Files", crumb: "Browse pipeline folders, preview outputs, save files",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        let t = null;
        $("#fxSearch").oninput = (e) => { state.q = e.target.value.trim(); clearTimeout(t); t = setTimeout(load, 250); };
        $("#fxSort").onchange = (e) => { state.sort = e.target.value; state.dir = e.target.value === "name" ? 1 : -1; render(); };
        $("#fxRefresh").onclick = load;
        booted = true;
      }
      if (!state.roots.length) {
        RMS.api("/ops/file-roots").then((d) => {
          state.roots = d.roots || [];
          if (!state.root && state.roots.length) state.root = state.roots[0].key;
          load();
        }).catch(() => {});
      } else load();
    },
  };
})();
