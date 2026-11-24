/* Simple line icons (24x24, drawn for Click). icon("name") returns an <svg> element. */
"use strict";
const ICON_PATHS = {
  "plus": "M12 5v14M5 12h14",
  "x": "M6 6l12 12M18 6L6 18",
  "menu": "M4 7h16M4 12h16M4 17h16",
  "search": "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5",
  "image": "M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 3-3 5 5M15.5 9.5h.01",
  "memory": "M12 4l2.2 4.6L19 9.3l-3.5 3.4.8 4.8L12 15.2l-4.3 2.3.8-4.8L5 9.3l4.8-.7z",
  "shield": "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z",
  "list": "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  "settings": "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-2 1.2L14.8 21h-4l-.3-2.4a7 7 0 0 1-2-1.2l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-2.6L4.6 9.7l2-3.4 1.9.7a7 7 0 0 1 2-1.2L10.8 3h4l.3 2.4a7 7 0 0 1 2 1.2l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 2.6z",
  "pause": "M9 5v14M15 5v14",
  "play": "M7 5l12 7-12 7z",
  "paperclip": "M20 11.5l-8.1 8.1a5 5 0 0 1-7.1-7.1l8.5-8.5a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-2.8-2.8l7.8-7.8",
  "arrow-up": "M12 19V5M6 11l6-6 6 6",
  "stop": "M8 8h8v8H8z",
  "monitor": "M3 5h18v11H3zM8 20h8M12 16v4",
  "pointer": "M5 3l14 7-6 2-2 6z",
  "download": "M12 4v11M7 10l5 5 5-5M5 20h14",
  "refresh": "M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4",
  "pencil": "M15 5l4 4L8 20H4v-4zM13 7l4 4",
  "trash": "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  "check": "M5 12.5l4.5 4.5L19 7.5",
  "alert": "M12 8v5M12 16.5h.01M10.3 4l-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z",
  "globe": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z",
  "file": "M14 3H6v18h12V7zM14 3v4h4",
  "lock": "M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11",
  "info": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5h.01",
  "sparkle": "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  "cpu": "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
  "mouse": "M12 3a6 6 0 0 1 6 6v6a6 6 0 0 1-12 0V9a6 6 0 0 1 6-6zM12 7v3",
  "external": "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
};
const LOGO_SVG = '<svg viewBox="0 0 24 24"><path d="M7 3.5v15l3.6-3.4 2.5 5.4 2.6-1.2-2.5-5.3 5.3-.2z"/></svg>';

function icon(name, cls = "i") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICON_PATHS[name] || ICON_PATHS.info);
  svg.append(path);
  return svg;
}

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach(n => { if (!n.querySelector("svg")) n.prepend(icon(n.dataset.icon)); });
  root.querySelectorAll("[data-logo]").forEach(n => { if (!n.innerHTML) n.innerHTML = LOGO_SVG; });
}
