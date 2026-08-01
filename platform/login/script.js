/* ═══════════════════════════════════════════════════════════════════════════
   RMS Copilot — Sign-in deck behaviour
   ─ Depth-projected starfield (z-perspective), parallax vs cursor
   ─ 3D card tilt + cursor-tracked sheen
   ─ Live HUD: NSE/MCX state from IST clock; runs-today from /ops/summary
   ─ Auth against /auth/login (real backend) with loading → success/shake
   All effects: transform/opacity only, ~30fps cap, off under reduced-motion.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fine = window.matchMedia("(pointer: fine)").matches;   // no tilt on touch

  /* ── THEME (persisted; nudges the platform's own pref so the console
        matches after sign-in) ─────────────────────────────────────────── */
  var THEME_KEY = "rms_login_theme";
  var PLATFORM_MAP = { midnight: "dark", ocean: "ocean", nebula: "purple",
                       emerald: "emerald", light: "light" };

  function applyTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    try {
      localStorage.setItem(THEME_KEY, name);
      var prefs = {};
      try { prefs = JSON.parse(localStorage.getItem("rms_ops_prefs") || "{}"); } catch (e) {}
      prefs.theme = PLATFORM_MAP[name] || "dark";
      localStorage.setItem("rms_ops_prefs", JSON.stringify(prefs));
    } catch (e) {}
    document.querySelectorAll(".tp-dot").forEach(function (d) {
      d.setAttribute("aria-checked", d.dataset.theme === name ? "true" : "false");
    });
  }
  document.querySelectorAll(".tp-dot").forEach(function (d) {
    d.addEventListener("click", function () { applyTheme(d.dataset.theme); });
  });
  applyTheme((function () {
    try { return localStorage.getItem(THEME_KEY) || "midnight"; } catch (e) { return "midnight"; }
  })());

  document.addEventListener("keydown", function (e) {
    if (e.altKey && (e.key === "t" || e.key === "T")) {
      var order = ["midnight", "ocean", "nebula", "emerald", "light"];
      var cur = document.documentElement.getAttribute("data-theme") || "midnight";
      applyTheme(order[(order.indexOf(cur) + 1) % order.length]);
    }
  });

  /* ── ENV BADGE / YEAR / CLOCK ──────────────────────────────────────────── */
  var env = document.body.getAttribute("data-env") || "DEV";
  var badge = document.getElementById("envBadge");
  if (badge) { badge.textContent = env; badge.setAttribute("data-env", env); }
  var yearEl = document.getElementById("copyYear");
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());

  function tickClock() {
    var now = new Date();
    var c = document.getElementById("liveClock");
    var d = document.getElementById("liveDate");
    if (c) c.textContent = now.toLocaleTimeString("en-IN", { hour12: false });
    if (d) d.textContent = now.toLocaleDateString("en-IN",
      { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  }
  tickClock(); setInterval(tickClock, 1000);

  /* ── TYPEWRITER — cycles the three things the console actually does.
     Types at ~45ms/char, holds, deletes faster, moves on. Under reduced
     motion it just shows the first phrase, static. ─────────────────────── */
  (function typewriter() {
    var el = document.getElementById("typeLine");
    if (!el) return;
    var PHRASES = ["Margin monitoring", "Exchange automation", "AI-assisted operations"];
    if (reduceMotion) { el.textContent = PHRASES[0]; return; }
    var pi = 0, ci = PHRASES[0].length, deleting = false;

    function step() {
      var full = PHRASES[pi];
      if (!deleting) {
        ci++;
        el.textContent = full.slice(0, ci);
        if (ci >= full.length) { deleting = true; setTimeout(step, 2100); return; }
        setTimeout(step, 42 + Math.random() * 40);
      } else {
        ci--;
        el.textContent = full.slice(0, ci);
        if (ci <= 0) { deleting = false; pi = (pi + 1) % PHRASES.length; setTimeout(step, 350); return; }
        setTimeout(step, 22);
      }
    }
    setTimeout(step, 2400);           // let the entrance sequence finish first
  })();

  /* ── boot flicker on the HUD values once they render ─────────────────── */
  if (!reduceMotion) {
    setTimeout(function () {
      document.querySelectorAll(".hud-v").forEach(function (el, i) {
        setTimeout(function () { el.classList.add("boot"); }, i * 110);
      });
    }, 700);
  }

  /* ── HUD: market state (derived from local clock — the desk runs on IST) ── */
  function setMarket(idV, idSub, openH, openM, closeH, closeM) {
    var v = document.getElementById(idV);
    if (!v) return;
    var now = new Date();
    var mins = now.getHours() * 60 + now.getMinutes();
    var isWeekday = now.getDay() >= 1 && now.getDay() <= 5;
    var open = isWeekday && mins >= openH * 60 + openM && mins < closeH * 60 + closeM;
    v.innerHTML = '<span class="pulse ' + (open ? "ok" : "off") + '"></span> ' +
                  (open ? "OPEN" : "CLOSED");
  }
  function tickMarkets() {
    setMarket("hudNse", "hudNseSub", 9, 15, 15, 30);
    setMarket("hudMcx", "hudMcxSub", 9, 0, 23, 30);
  }
  tickMarkets(); setInterval(tickMarkets, 30000);

  /* HUD: runs today — REAL number from the console, not a decoration.
     If the backend can't say, the card admits it rather than inventing one. */
  (function hudRuns() {
    var v = document.getElementById("hudRuns");
    var s = document.getElementById("hudRunsSub");
    if (!v) return;
    fetch("/ops/summary").then(function (r) { return r.json(); }).then(function (d) {
      var n = null;
      if (d && typeof d.runs_today === "number") n = d.runs_today;
      else if (d && d.kpis && typeof d.kpis.runs_today === "number") n = d.kpis.runs_today;
      else if (d && d.summary && typeof d.summary.runs_today === "number") n = d.summary.runs_today;
      if (n !== null) {
        if (s) s.textContent = "pipeline runs completed today";
        // count up 0 → n over ~700ms — numbers that arrive feel measured,
        // numbers that just appear feel hardcoded
        if (reduceMotion || n === 0) { v.textContent = String(n); }
        else {
          var t0 = performance.now();
          (function cnt(now) {
            var p = Math.min(1, (now - t0) / 700);
            v.textContent = String(Math.round(n * (1 - Math.pow(1 - p, 3))));
            if (p < 1) requestAnimationFrame(cnt);
          })(t0);
        }
      } else {
        v.textContent = "●";
        if (s) s.textContent = "console reachable — live";
      }
    }).catch(function () {
      v.textContent = "—";
      if (s) s.textContent = "console not reachable";
    });
  })();

  /* ── FLOW FIELD ───────────────────────────────────────────────────────
     Replaces the starfield. Particles ride an invisible wind — a smooth
     time-varying vector field built from layered sines (cheap curl-noise
     stand-in) — and are drawn as SHORT STROKES, not dots. The canvas is
     never fully cleared; each frame lays a translucent wash of the page
     background over the last, so every particle drags a fading light trail.
     That trail persistence is what makes it feel alive.

     Extras:
       · the field slowly rotates over minutes, so it never loops visibly
       · particles bend AWAY from the cursor (the wind parts around you)
       · a bright comet streaks through every few seconds
       · depth: each particle has z → thicker/brighter/faster when near
     Cost control: ~120 strokes/frame, 30fps cap, pauses when tab hidden.
     A MutationObserver resets the canvas on theme switch so old-coloured
     trails can't smear into the new palette. */
  var canvas = document.getElementById("bgField");
  var mouseX = 0, mouseY = 0;              // -1..1, shared with parallax/tilt
  var mousePX = -9999, mousePY = -9999;    // pixel-space, for the wind gap

  if (canvas && !reduceMotion) {
    var ctx = canvas.getContext("2d");
    var W = 0, H = 0, ps = [], running = true, last = 0;
    var N = Math.min(130, Math.floor(window.innerWidth / 12));
    var FRAME_MS = 33;
    var comet = null, nextComet = 2500;

    function themeVals() {
      var cs = getComputedStyle(document.documentElement);
      return {
        col: (cs.getPropertyValue("--particle").trim() || "130,170,255"),
        light: document.documentElement.getAttribute("data-theme") === "light"
      };
    }
    var TH = themeVals();

    function size() {
      W = canvas.width = innerWidth; H = canvas.height = innerHeight;
      ctx.clearRect(0, 0, W, H);
    }
    function spawn(p) {
      p = p || {};
      p.x = Math.random() * W; p.y = Math.random() * H;
      p.z = .25 + Math.random() * .75;       // depth 0.25..1
      p.life = 200 + Math.random() * 400;    // frames until respawn (avoids ruts)
      return p;
    }
    function seed() { ps = []; for (var i = 0; i < N; i++) ps.push(spawn()); }

    /* the wind: layered sines → smooth rotating angle field */
    function field(x, y, t) {
      return (Math.sin(x * 0.0016 + t * .21) +
              Math.cos(y * 0.0014 - t * .17) +
              Math.sin((x + y) * 0.0007 + t * .11)) * 1.15;
    }

    function frame(ts) {
      if (!running) return;
      requestAnimationFrame(frame);
      if (ts - last < FRAME_MS) return;
      last = ts;
      var t = ts / 1000;

      // translucent wash = fading trails. destination-out erases uniformly,
      // which works on every theme without knowing the bg colour.
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = "rgba(0,0,0,.075)";
      ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";

      ctx.lineCap = "round";
      for (var i = 0; i < ps.length; i++) {
        var p = ps[i];
        var ang = field(p.x, p.y, t);

        // the wind parts around the cursor
        var dx = p.x - mousePX, dy = p.y - mousePY;
        var d2 = dx * dx + dy * dy;
        if (d2 < 180 * 180) {
          var push = 1 - Math.sqrt(d2) / 180;
          ang += Math.atan2(dy, dx) * 0 + push * 1.6 * (dx * Math.cos(ang) + dy * Math.sin(ang) > 0 ? 1 : -1);
        }

        var sp = (0.7 + p.z * 1.9);
        var nx = p.x + Math.cos(ang) * sp;
        var ny = p.y + Math.sin(ang) * sp;

        ctx.strokeStyle = "rgba(" + TH.col + "," + (0.10 + p.z * 0.34).toFixed(3) + ")";
        ctx.lineWidth = 0.6 + p.z * 1.5;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(nx, ny);
        ctx.stroke();

        p.x = nx; p.y = ny;
        if (--p.life <= 0 || p.x < -20 || p.x > W + 20 || p.y < -20 || p.y > H + 20) spawn(p);
      }

      // the comet — one bright traveller every few seconds
      nextComet -= FRAME_MS;
      if (!comet && nextComet <= 0) {
        var fromLeft = Math.random() < .5;
        comet = { x: fromLeft ? -30 : W + 30, y: H * (.15 + Math.random() * .5),
                  vx: (fromLeft ? 1 : -1) * (7 + Math.random() * 5),
                  vy: 1.2 + Math.random() * 1.6, life: 140 };
        nextComet = 3500 + Math.random() * 4000;
      }
      if (comet) {
        var c = comet;
        ctx.strokeStyle = "rgba(" + TH.col + ",.85)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(c.x, c.y);
        ctx.lineTo(c.x + c.vx * 3.2, c.y + c.vy * 3.2);
        ctx.stroke();
        c.x += c.vx; c.y += c.vy;
        if (--c.life <= 0 || c.x < -60 || c.x > W + 60 || c.y > H + 60) comet = null;
      }

      ctx.globalCompositeOperation = "source-over";
    }

    size(); seed();
    requestAnimationFrame(frame);
    addEventListener("resize", function () { size(); seed(); });
    document.addEventListener("visibilitychange", function () {
      running = !document.hidden;
      if (running) requestAnimationFrame(frame);
    });
    // theme switch → fresh canvas + new colour, no cross-palette smears
    new MutationObserver(function () { TH = themeVals(); ctx.clearRect(0, 0, W, H); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    document.addEventListener("pointermove", function (e) {
      mousePX = e.clientX; mousePY = e.clientY;
    });
  }

  /* ── CURSOR → PARALLAX LAYERS + CARD TILT + SHEEN ─────────────────────── */
  var card = document.getElementById("loginCard");
  var sheen = document.getElementById("cardSheen");
  var typing = false;                       // tilt off while typing — precision beats show

  if (fine && !reduceMotion) {
    var pending = false;
    document.addEventListener("pointermove", function (e) {
      mouseX = (e.clientX / innerWidth) * 2 - 1;
      mouseY = (e.clientY / innerHeight) * 2 - 1;
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        // parallax layers
        document.querySelectorAll(".plx").forEach(function (el) {
          el.style.setProperty("--px", (-mouseX).toFixed(3));
          el.style.setProperty("--py", (-mouseY).toFixed(3));
          el.style.setProperty("--plx-f", el.dataset.plx || 14);
        });
        // card tilt (subtle: ±4°) + sheen position
        if (card && !typing) {
          var r = card.getBoundingClientRect();
          var cx = (e.clientX - r.left) / r.width;
          var cy = (e.clientY - r.top) / r.height;
          var inside = cx >= -0.3 && cx <= 1.3 && cy >= -0.3 && cy <= 1.3;
          card.style.setProperty("--rx", inside ? ((cx - .5) * 8).toFixed(2) : 0);
          card.style.setProperty("--ry", inside ? ((.5 - cy) * 8).toFixed(2) : 0);
          if (sheen && inside) {
            sheen.style.setProperty("--mx", (cx * 100).toFixed(1) + "%");
            sheen.style.setProperty("--my", (cy * 100).toFixed(1) + "%");
          }
        }
      });
    });
  }

  /* ── CURSOR GLOW TRAIL — lerps toward the pointer so it trails softly.
     Its own rAF loop, transform-only, stops when the tab is hidden. ─────── */
  (function cursorGlow() {
    var g = document.getElementById("cursorGlow");
    if (!g || !fine || reduceMotion) { if (g) g.remove(); return; }
    var tx = innerWidth / 2, ty = innerHeight / 2, x = tx, y = ty, live = false;
    document.addEventListener("pointermove", function (e) {
      tx = e.clientX; ty = e.clientY;
      if (!live) { live = true; g.style.opacity = "1"; }
    });
    (function loop() {
      if (!document.hidden) {
        x += (tx - x) * 0.09;          // the lag IS the effect
        y += (ty - y) * 0.09;
        g.style.transform = "translate3d(" + x.toFixed(1) + "px," + y.toFixed(1) + "px,0)";
      }
      requestAnimationFrame(loop);
    })();
  })();

  /* ── PASSWORD SHOW/HIDE ───────────────────────────────────────────────── */
  var pw = document.getElementById("password");
  var eye = document.getElementById("eyeBtn");
  if (eye && pw) {
    eye.addEventListener("click", function () {
      var show = pw.type === "password";
      pw.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", String(show));
      eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
      eye.querySelector(".eye-open").style.display = show ? "none" : "";
      eye.querySelector(".eye-closed").style.display = show ? "" : "none";
      pw.focus();
    });
  }

  /* ── CAPS LOCK ────────────────────────────────────────────────────────── */
  var caps = document.getElementById("capsWarn");
  if (pw && caps) {
    function capsCheck(e) {
      if (typeof e.getModifierState !== "function") return;
      caps.hidden = !e.getModifierState("CapsLock");
    }
    pw.addEventListener("keydown", capsCheck);
    pw.addEventListener("keyup", capsCheck);
    pw.addEventListener("blur", function () { caps.hidden = true; });
  }

  /* tilt pauses while either input is focused — a moving target is hostile
     to someone trying to type a password */
  ["empId", "password"].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("focus", function () {
      typing = true;
      if (card) { card.style.setProperty("--rx", 0); card.style.setProperty("--ry", 0); }
    });
    el.addEventListener("blur", function () { typing = false; });
  });

  /* ── REMEMBER ME (employee id only, never the password) ───────────────── */
  var empId = document.getElementById("empId");
  var remember = document.getElementById("rememberMe");
  try {
    var savedId = localStorage.getItem("rms_login_empid");
    if (savedId && empId) { empId.value = savedId; if (remember) remember.checked = true; }
  } catch (e) {}

  /* ── TOASTS ───────────────────────────────────────────────────────────── */
  function toast(kind, msg, ms) {
    var host = document.getElementById("toasts");
    if (!host) return;
    var el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.innerHTML = '<span class="t-ic">' +
      (kind === "ok" ? "✓" : kind === "err" ? "✕" : "ℹ") + "</span><span></span>";
    el.lastElementChild.textContent = msg;
    host.appendChild(el);
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 260);
    }, ms || 4200);
  }

  /* ── RIPPLE ───────────────────────────────────────────────────────────── */
  var btn = document.getElementById("signinBtn");
  if (btn && !reduceMotion) {
    btn.addEventListener("pointerdown", function (e) {
      var r = btn.getBoundingClientRect();
      var d = Math.max(r.width, r.height);
      var s = document.createElement("span");
      s.className = "ripple";
      s.style.width = s.style.height = d + "px";
      s.style.left = (e.clientX - r.left - d / 2) + "px";
      s.style.top = (e.clientY - r.top - d / 2) + "px";
      btn.appendChild(s);
      setTimeout(function () { s.remove(); }, 600);
    });
  }

  /* ── SUBMIT: loading morph → success tick, or card shake ──────────────── */
  var form = document.getElementById("loginForm");
  var msg = document.getElementById("formMsg");

  function setLoading(on) {
    if (!btn) return;
    btn.disabled = on;
    btn.classList.toggle("loading", on);
  }
  function fail(text) {
    setLoading(false);
    if (msg) msg.textContent = text;
    if (card && !reduceMotion) {
      card.classList.remove("shake");
      void card.offsetWidth;               // restart the animation
      card.classList.add("shake");
    }
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (msg) msg.textContent = "";

      var id = (empId && empId.value || "").trim();
      var pass = (pw && pw.value) || "";
      if (!id)   { fail("Enter your Employee ID."); empId && empId.focus(); return; }
      if (!pass) { fail("Enter your password.");    pw && pw.focus();       return; }

      try {
        if (remember && remember.checked) localStorage.setItem("rms_login_empid", id);
        else localStorage.removeItem("rms_login_empid");
      } catch (err) {}

      setLoading(true);

      fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ employee_id: id, password: pass })
      }).then(function (r) {
        if (r.status === 404 || r.status === 405) throw { notWired: true };
        return r.json();
      }).then(function (d) {
        if (d && d.ok) {
          btn.classList.remove("loading");
          btn.classList.add("success");
          if (d.must_change) {
            // Still on the shared default — secure the account before going in.
            currentId = id; currentPw = pass;
            setTimeout(showChangeScreen, 650);
          } else {
            toast("ok", "Welcome, " + (d.display_name || id) + " — loading console…", 2500);
            setTimeout(function () { window.location.href = d.redirect || "/"; }, 700);
          }
        } else {
          fail((d && d.error) || "Invalid Employee ID or password.");
        }
      }).catch(function (err) {
        if (err && err.notWired) {
          setLoading(false);
          var m = "Authentication backend is not enabled on this server yet.";
          if (msg) msg.textContent = m;
          toast("info", m, 6000);
        } else {
          fail("Could not reach the server — is the console backend running?");
        }
      });
    });
  }

  /* ══ FIRST-LOGIN PASSWORD CHANGE ══════════════════════════════════════
     Shown in place of the sign-in form when the account is still on the
     shared default. Held in memory only for the length of this exchange —
     nothing about the password is persisted client-side. */
  var currentId = "", currentPw = "";
  var changeForm = document.getElementById("changeForm");
  var changeBtn = document.getElementById("changeBtn");
  var changeMsg = document.getElementById("changeMsg");
  var newPw = document.getElementById("newPw");
  var newPw2 = document.getElementById("newPw2");
  var pwBar = document.getElementById("pwBar");

  function showChangeScreen() {
    if (form) form.hidden = true;
    if (changeForm) changeForm.hidden = false;
    var t = document.querySelector(".card-title");
    var s = document.querySelector(".card-sub");
    if (t) t.textContent = "Set your password";
    if (s) s.textContent = "You are signed in with the shared default — choose your own to continue.";
    if (newPw) newPw.focus();
  }

  /* strength: length + variety. Advisory only — the server enforces the
     minimum, this just gives honest feedback while typing. */
  function scorePw(v) {
    var s = 0;
    if (v.length >= 8) s++;
    if (v.length >= 12) s++;
    if (/[a-z]/.test(v) && /[A-Z]/.test(v)) s++;
    if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) s++;
    return Math.min(4, s);
  }
  if (newPw && pwBar) {
    newPw.addEventListener("input", function () {
      var s = scorePw(newPw.value);
      pwBar.style.width = (s * 25) + "%";
      pwBar.setAttribute("data-s", String(s));
    });
  }

  if (changeForm) {
    changeForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (changeMsg) changeMsg.textContent = "";
      var a = (newPw && newPw.value) || "", b = (newPw2 && newPw2.value) || "";

      if (a.length < 8) { changeMsg.textContent = "At least 8 characters."; newPw.focus(); return; }
      if (a !== b)      { changeMsg.textContent = "The two passwords don't match."; newPw2.focus(); return; }

      changeBtn.disabled = true;
      changeBtn.classList.add("loading");

      fetch("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ employee_id: currentId, current_password: currentPw,
                               new_password: a })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) {
          changeBtn.classList.remove("loading");
          changeBtn.classList.add("success");
          toast("ok", "Password set — loading console…", 2200);
          // The roster sheet is a best-effort update; if it was open in Excel
          // the password change still SUCCEEDED, so say what actually happened
          // rather than implying failure.
          if (d.sheet && d.sheet.ok === false && d.sheet.error) {
            toast("info", "Note: roster sheet not updated (" + d.sheet.error + "). "
                        + "Your new password is active regardless.", 7000);
          }
          setTimeout(function () { window.location.href = "/"; }, 900);
        } else {
          changeBtn.disabled = false;
          changeBtn.classList.remove("loading");
          if (changeMsg) changeMsg.textContent = (d && d.error) || "Could not set the password.";
        }
      }).catch(function () {
        changeBtn.disabled = false;
        changeBtn.classList.remove("loading");
        if (changeMsg) changeMsg.textContent = "Could not reach the server.";
      });
    });
  }

  /* ── FORGOT PASSWORD ──────────────────────────────────────────────────── */
  var forgot = document.getElementById("forgotLink");
  if (forgot) {
    forgot.addEventListener("click", function () {
      toast("info",
        "Password resets are handled by the RMS tech desk — contact the administrator.",
        6000);
    });
  }

})();
