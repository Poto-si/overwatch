"use strict";

// Apply saved appearance before styles load.
(() => {
  const themeKey = "potosi.agents-dashboard.theme";
  const accentKey = "potosi.agents-dashboard.accent";
  const accents = {mint: "Mint", sky: "Sky", violet: "Violet", amber: "Amber", rose: "Rose"};
  const validTheme = value => ["dark", "light", "system"].includes(value) ? value : "dark";
  const validAccent = value => Object.hasOwn(accents, value) ? value : "mint";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let theme = "dark", accent = "mint";
  try { theme = validTheme(localStorage.getItem(themeKey)); } catch (_) { /* Storage is optional. */ }
  try { accent = validAccent(localStorage.getItem(accentKey)); } catch (_) { /* Storage is optional. */ }

  function apply() {
    const resolved = theme === "system" ? (system.matches ? "dark" : "light") : theme;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.accent = accent;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", resolved);
    document.querySelectorAll('input[name="appearance-theme"]').forEach(input => { input.checked = input.value === theme; });
    document.querySelectorAll('input[name="appearance-accent"]').forEach(input => { input.checked = input.value === accent; });
    document.querySelector("#appearance-menu summary")?.setAttribute("aria-label", "Appearance: " + theme + " theme, " + accents[accent] + " accent");
  }

  function save(key, value) {
    try { localStorage.setItem(key, value); } catch (_) { /* Keep the in-memory choice. */ }
  }

  apply();
  system.addEventListener("change", () => { if (theme === "system") apply(); });
  window.addEventListener("storage", event => {
    if (event.key === themeKey) theme = validTheme(event.newValue);
    else if (event.key === accentKey) accent = validAccent(event.newValue);
    else if (event.key === null) { theme = "dark"; accent = "mint"; }
    else return;
    apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll('input[name="appearance-theme"]').forEach(input => input.addEventListener("change", () => {
      if (!input.checked) return;
      theme = validTheme(input.value);
      apply();
      save(themeKey, theme);
    }));
    document.querySelectorAll('input[name="appearance-accent"]').forEach(input => input.addEventListener("change", () => {
      if (!input.checked) return;
      accent = validAccent(input.value);
      apply();
      save(accentKey, accent);
    }));
    const menus = [...document.querySelectorAll(".appearance-menu")];
    for (const menu of menus) menu.addEventListener("toggle", () => {
      if (menu.open) for (const other of menus) if (other !== menu) other.open = false;
    });
    document.addEventListener("click", event => {
      for (const menu of menus) if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      for (const menu of menus) if (menu.open) {
        menu.open = false;
        menu.querySelector("summary")?.focus();
      }
    });
  }, {once: true});
})();
