/* pages/ask.js — the AI Assistant workspace.
   (Filename note: this module used to be assistant.js — renamed because
   ad-blocker/privacy filter lists commonly block script URLs containing
   "assistant"/"chat", which made the file silently fail to load in browsers
   with such extensions while every sibling module loaded fine.)

   Not a chat demo: every question goes to /ops/ask, which packs the LIVE
   platform state (today's runs, health, due windows, optionally a specific
   log tail) into the model's context, so answers are grounded in what is
   actually happening on this server. Falls back to deterministic answers if
   no GROQ_API_KEY is configured. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false, busy = false, attachedLog = null;

  const QUICK = [
    ["sparkles", "Summarize today"],
    ["alert", "What failed today and why?"],
    ["play", "What should I run next?"],
    ["clock", "Which runs are still pending?"],
    ["mail", "How many emails went out today?"],
    ["terminal", "Explain the latest failed log"],
  ];

  function shell() {
    return (
      '<div class="chat-wrap">' +
      '<div class="chat-thread" id="chatThread">' +
      '<div class="msg ai"><div class="msg-av">AI</div><div><div class="msg-body">Hello — I\'m the RMS Copilot assistant. I can see today\'s runs, failures, schedules and system health on this server.\n\nAsk me anything about operations, or use a quick action below. To have me explain a specific log, open it from Execution History and press "Ask AI", or use the attach chip here.</div>' +
      '<div class="msg-meta">operational assistant · answers grounded in live platform state</div></div></div>' +
      "</div>" +
      '<div>' +
      '<div class="chat-quick" id="chatQuick">' +
      QUICK.map(([ic, q], i) => '<span class="chip" data-q="' + i + '">' + RMS.icon(ic) + esc(q) + "</span>").join("") +
      '<span class="chip" id="chipAttach">' + RMS.icon("terminal") + " Attach latest failed log</span>" +
      '<span class="chip" id="chipClear">' + RMS.icon("x") + " Clear</span>" +
      "</div>" +
      '<div id="attachBar" hidden style="margin-bottom:8px"></div>' +
      '<div class="chat-input"><textarea id="chatBox" rows="1" placeholder="Ask about today\'s runs, failures, schedules…"></textarea>' +
      '<button class="btn primary" id="chatSend">' + RMS.icon("send") + " Ask</button></div>" +
      "</div></div>"
    );
  }

  function addMsg(role, text, meta) {
    const th = $("#chatThread");
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = '<div class="msg-av">' + (role === "ai" ? "AI" : "You") + "</div>" +
      '<div><div class="msg-body"></div>' + (meta ? '<div class="msg-meta">' + meta + "</div>" : "") + "</div>";
    div.querySelector(".msg-body").textContent = text;
    th.appendChild(div);
    th.scrollTop = th.scrollHeight;
    return div;
  }

  function setAttach(entry) {
    attachedLog = entry;
    const bar = $("#attachBar");
    if (!entry) { bar.hidden = true; bar.innerHTML = ""; return; }
    bar.hidden = false;
    bar.innerHTML = '<span class="chip on">' + RMS.icon("terminal") + " Context: " + esc(entry.file) +
      ' <span id="attachX" style="margin-left:4px;cursor:pointer">✕</span></span>';
    $("#attachX").onclick = () => setAttach(null);
  }

  async function send(q) {
    if (busy || !q.trim()) return;
    busy = true;
    addMsg("user", q.trim());
    $("#chatBox").value = "";
    const thinking = addMsg("ai", "…", "thinking");
    thinking.querySelector(".msg-body").innerHTML = '<span class="spinner"></span>';
    try {
      const body = { question: q.trim() };
      if (attachedLog) { body.logdir = attachedLog.logdir; body.file = attachedLog.file; }
      const r = await fetch("/ops/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      thinking.querySelector(".msg-body").textContent = d.answer || "No answer.";
      thinking.querySelector(".msg-meta").textContent =
        (d.computed ? "⚡ computed live from the output file · " : d.ai ? "✦ AI (Groq) · " : "rule-based · ") +
        new Date().toTimeString().slice(0, 5) + (attachedLog ? " · with log context" : "");
      // citations — real rows from the knowledge index, never model-generated
      if (d.sources && d.sources.length) {
        const ICO = { code: "terminal", output: "file", process: "layers", stats: "chart", client: "bot", log: "history" };
        const srcDiv = document.createElement("div");
        srcDiv.className = "chat-quick";
        srcDiv.style.padding = "6px 0 0";
        srcDiv.innerHTML = d.sources.map((s, i) => '<span class="chip" data-s="' + i + '" title="' + esc(s.detail || "") + '">' +
          RMS.icon(ICO[s.type] || "info") + esc(s.label) + "</span>").join("");
        srcDiv.hidden = true;   // clean answer first — sources behind a button
        const tog = document.createElement("div");
        tog.className = "chat-quick";
        tog.style.padding = "6px 0 0";
        tog.innerHTML = '<span class="chip">' + RMS.icon("search") + " View sources (" + d.sources.length + ")</span>";
        tog.firstElementChild.onclick = () => { srcDiv.hidden = !srcDiv.hidden; };
        const metaEl = thinking.querySelector(".msg-meta");
        metaEl.parentNode.insertBefore(tog, metaEl);
        metaEl.parentNode.insertBefore(srcDiv, metaEl);
        srcDiv.querySelectorAll(".chip[data-s]").forEach((c) => {
          c.onclick = () => {
            const s = d.sources[+c.dataset.s];
            if (s.type === "output" && s.root) RMS.previewFile(s.root, s.sub);
            else RMS.toast("info", s.label, s.detail || s.type);
          };
        });
      }
    } catch (e) {
      thinking.querySelector(".msg-body").textContent = "Could not reach the assistant — is the backend running?";
      thinking.querySelector(".msg-meta").textContent = "error";
    }
    busy = false;
    $("#chatThread").scrollTop = $("#chatThread").scrollHeight;
  }

  function latestFailed() {
    return (RMS._histCache || RMS._histCache30 || []).find((e) => e.status === "failed") || null;
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.assistant = {
    title: "AI Assistant", crumb: "Operational copilot — grounded in live platform state",
    keepAlive: true,   // keep the conversation when navigating away
    mount(el) {
      if (booted) {
        if (RMS._pendingAsk) { const q = RMS._pendingAsk; RMS._pendingAsk = null; send(q); }
        return;
      }
      el.innerHTML = shell();
      RMS.mountIcons(el);
      const box = $("#chatBox");
      const auto = () => { box.style.height = "auto"; box.style.height = Math.min(130, box.scrollHeight) + "px"; };
      box.oninput = auto;
      box.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(box.value); } };
      $("#chatSend").onclick = () => send(box.value);
      $("#chatQuick").querySelectorAll(".chip[data-q]").forEach((c) => {
        c.onclick = () => {
          const q = QUICK[+c.dataset.q][1];
          if (q.includes("latest failed log")) {
            const f = latestFailed();
            if (f) setAttach(f); else { RMS.toast("info", "No failed runs found", "Nothing failed recently 🎉"); return; }
          }
          send(q);
        };
      });
      $("#chipAttach").onclick = () => {
        const f = latestFailed();
        if (f) { setAttach(f); RMS.toast("ok", "Log attached as context", f.file); }
        else RMS.toast("info", "No failed runs found");
      };
      $("#chipClear").onclick = () => {
        $("#chatThread").querySelectorAll(".msg").forEach((m, i) => { if (i) m.remove(); });
        setAttach(null);
      };
      // warm history cache for "latest failed" actions
      RMS.api("/ops/history?days=2", { quiet: true }).then((d) => { RMS._histCache = d.items || []; }).catch(() => {});
      RMS._assistantSend = send;   // lets other pages hand a question over (Ask AI buttons, palette)
      booted = true;
      if (RMS._pendingAsk) { const q = RMS._pendingAsk; RMS._pendingAsk = null; setTimeout(() => send(q), 200); }
    },
  };
})();
