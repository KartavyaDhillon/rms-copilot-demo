/* icons.js — inline SVG icon set (lucide-style outlines, stroke=currentColor).
   No CDN dependency: LAN machines render these even with restricted internet. */
(function () {
  const P = (d) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + d + "</svg>";
  const ICONS = {
    dashboard: P('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
    play:      P('<polygon points="6 3 20 12 6 21 6 3"/>'),
    history:   P('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 3"/>'),
    chart:     P('<path d="M3 3v18h18"/><path d="M7 15l4-6 4 3 5-8"/>'),
    file:      P('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/>'),
    bot:       P('<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M8 4h8"/><circle cx="9" cy="13.5" r="1"/><circle cx="15" cy="13.5" r="1"/><path d="M9 17h6"/>'),
    calendar:  P('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
    download:  P('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>'),
    folder:    P('<path d="M4 4h5l2 3h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>'),
    mail:      P('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>'),
    bell:      P('<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>'),
    settings:  P('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'),
    search:    P('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
    moon:      P('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
    panel:     P('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>'),
    x:         P('<path d="M18 6 6 18M6 6l12 12"/>'),
    check:     P('<path d="M20 6 9 17l-5-5"/>'),
    alert:     P('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>'),
    info:      P('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'),
    activity:  P('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
    cpu:       P('<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>'),
    disk:      P('<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.5 5.5 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.5A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.5z"/><path d="M6 16h.01M10 16h.01"/>'),
    gauge:     P('<path d="m12 14 4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>'),
    zap:       P('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'),
    refresh:   P('<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>'),
    external:  P('<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/>'),
    terminal:  P('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'),
    copy:      P('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    wifi:      P('<path d="M5 13a10 10 0 0 1 14 0"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M2 9.5a15 15 0 0 1 20 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>'),
    database:  P('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.7-4 3-9 3s-9-1.3-9-3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/>'),
    globe:     P('<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    send:      P('<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>'),
    sparkles:  P('<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z"/>'),
    clock:     P('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
    stop:      P('<rect x="5" y="5" width="14" height="14" rx="2"/>'),
    layers:    P('<path d="m12 2 10 6-10 6L2 8z"/><path d="m2 14 10 6 10-6"/>'),
    eye:       P('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>'),
    wrap:      P('<path d="M3 6h18"/><path d="M3 12h13a3 3 0 1 1 0 6h-4"/><path d="m14 16-2 2 2 2"/><path d="M3 18h6"/>'),
    monitor:   P('<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>'),
  };
  window.RMS = window.RMS || {};
  RMS.icon = (name) => ICONS[name] || ICONS.info;
  RMS.mountIcons = (root) => {
    (root || document).querySelectorAll("i[data-icon]").forEach((el) => {
      el.innerHTML = RMS.icon(el.dataset.icon);
    });
  };
})();
