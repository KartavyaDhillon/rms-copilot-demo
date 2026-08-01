/* theme.js — the theme engine.
   Everything the user can customize lives in one prefs object persisted to
   localStorage ("rms_ops_prefs") and is applied as data-attributes / CSS custom
   properties on <html>. index.html has a tiny inline copy of applyPrefs() that
   runs before first paint, so there is never a flash of the wrong theme. */
(function () {
  window.RMS = window.RMS || {};

  const KEY = "rms_ops_prefs";

  RMS.THEMES = [
    { id: "dark",     name: "Dark",          bg: "#0b0d12", accent: "#60a5fa" },
    { id: "light",    name: "Light",         bg: "#f4f6fa", accent: "#2f6df6" },
    { id: "amoled",   name: "AMOLED",        bg: "#000000", accent: "#60a5fa" },
    { id: "ocean",    name: "Ocean",         bg: "#06131d", accent: "#2dd4bf" },
    { id: "emerald",  name: "Emerald",       bg: "#081410", accent: "#34d399" },
    { id: "purple",   name: "Purple",        bg: "#0d0916", accent: "#a78bfa" },
    { id: "contrast", name: "High Contrast", bg: "#000000", accent: "#ffd60a" },
  ];

  RMS.ACCENTS = ["#60a5fa", "#2dd4bf", "#34d399", "#a78bfa", "#f472b6", "#fbbf24", "#fb923c", "#f87171"];

  const DEFAULTS = {
    theme: "dark",
    accent: "",        // "" = use the theme's own accent
    fontSize: 14,      // px on <html>
    compact: false,
    radius: 14,        // px
    anim: true,
    sidebar: "full",   // "full" | "rail"
  };

  RMS.prefs = { ...DEFAULTS };
  try { Object.assign(RMS.prefs, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}

  RMS.savePrefs = function (patch) {
    Object.assign(RMS.prefs, patch || {});
    try { localStorage.setItem(KEY, JSON.stringify(RMS.prefs)); } catch (e) {}
    RMS.applyPrefs();
  };

  RMS.resetPrefs = function () {
    RMS.prefs = { ...DEFAULTS };
    try { localStorage.removeItem(KEY); } catch (e) {}
    RMS.applyPrefs();
  };

  RMS.applyPrefs = function () {
    const p = RMS.prefs, el = document.documentElement;
    el.dataset.theme = p.theme;
    if (p.accent) el.style.setProperty("--accent", p.accent);
    else el.style.removeProperty("--accent");
    el.style.setProperty("--radius", (p.radius || 14) + "px");
    el.style.fontSize = (p.fontSize || 14) + "px";
    el.classList.toggle("compact", !!p.compact);
    el.classList.toggle("no-anim", p.anim === false);
    el.classList.toggle("rail", p.sidebar === "rail");
    document.dispatchEvent(new CustomEvent("rms:prefs"));
  };

  RMS.cycleTheme = function () {
    const ids = RMS.THEMES.map((t) => t.id);
    const next = ids[(ids.indexOf(RMS.prefs.theme) + 1) % ids.length];
    RMS.savePrefs({ theme: next });
    return RMS.THEMES.find((t) => t.id === next).name;
  };
})();
