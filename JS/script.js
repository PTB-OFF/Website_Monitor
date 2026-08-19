/**
 * Website Monitoring Dashboard — JS/script.js
 *
 * - Charge data/status.json
 * - Calcule et affiche les statistiques globales
 * - Génère dynamiquement les tuiles de sites (aucun site codé en dur)
 * - Gère le thème clair / sombre avec persistance localStorage
 */

(function () {
  "use strict";

  const STATUS_URL = "data/status.json";
  const THEME_STORAGE_KEY = "monitoring-dashboard-theme";

  const els = {
    sitesGrid: document.getElementById("sites-grid"),
    emptyState: document.getElementById("empty-state"),
    errorState: document.getElementById("error-state"),
    statTotal: document.getElementById("stat-total"),
    statOnline: document.getElementById("stat-online"),
    statDown: document.getElementById("stat-down"),
    statUptime: document.getElementById("stat-uptime"),
    lastUpdateValue: document.getElementById("last-update-value"),
    themeToggle: document.getElementById("theme-toggle"),
  };

  /* ----------------------------- Thème ----------------------------- */

  function getPreferredTheme() {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;

    const prefersDark = window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    return prefersDark ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.body && document.body.setAttribute("data-theme", theme);
  }

  function initTheme() {
    const theme = getPreferredTheme();
    applyTheme(theme);

    els.themeToggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") === "dark"
        ? "dark" : "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem(THEME_STORAGE_KEY, next);
    });
  }

  /* ----------------------------- Utils ----------------------------- */

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
  }

  function formatDate(isoLike) {
    if (!isoLike) return "—";
    // Le format produit par monitor.py est "YYYY-MM-DD HH:MM:SS"
    const normalized = isoLike.replace(" ", "T");
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return escapeHtml(isoLike);

    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    const hh = String(d.getHours()).padStart(2, "0");
    const min = String(d.getMinutes()).padStart(2, "0");
    return `${dd}/${mm}/${yyyy} ${hh}:${min}`;
  }

  /* --------------------------- Rendu stats -------------------------- */

  function renderStats(sites) {
    const total = sites.length;
    const online = sites.filter((s) => s.status === "online").length;
    const down = total - online;
    const uptime = total > 0 ? Math.round((online / total) * 100) : 0;

    els.statTotal.textContent = total;
    els.statOnline.textContent = online;
    els.statDown.textContent = down;
    els.statUptime.textContent = `${uptime}%`;
  }

  /* --------------------------- Rendu tuiles -------------------------- */

  function siteCardTemplate(site) {
    const isOnline = site.status === "online";
    const statusLabel = isOnline ? "ONLINE" : "DOWN";
    const badgeClass = isOnline ? "status-badge--online" : "status-badge--down";
    const dotClass = isOnline ? "pulse-dot--online" : "pulse-dot--down";
    const httpClass = isOnline ? "site-card__http--online" : "site-card__http--down";
    const screenshotClass = isOnline ? "" : " site-card__screenshot--down";

    const name = escapeHtml(site.name);
    const url = escapeHtml(site.url);
    const httpCode = site.http_code != null ? escapeHtml(site.http_code) : "—";
    const lastCheck = formatDate(site.last_check);
    const screenshot = site.screenshot ? escapeHtml(site.screenshot) : "";

    const screenshotMarkup = screenshot
      ? `<img src="${screenshot}" alt="Capture d'écran de ${name}" loading="lazy"
           onerror="this.parentElement.innerHTML='<div class=&quot;site-card__screenshot-fallback&quot;>Capture indisponible</div>';">`
      : `<div class="site-card__screenshot-fallback">Capture indisponible</div>`;

    return `
      <article class="site-card">
        <header class="site-card__header">
          <div>
            <h2 class="site-card__name">${name}</h2>
            <p class="site-card__url">${url}</p>
          </div>
          <span class="status-badge ${badgeClass}">
            <span class="pulse-dot ${dotClass}"></span>${statusLabel}
          </span>
        </header>

        <div class="site-card__screenshot${screenshotClass}">
          ${screenshotMarkup}
        </div>

        <div class="site-card__meta">
          <span>HTTP <span class="mono ${httpClass}">${httpCode}</span></span>
          <span>Vérifié : <span class="mono">${lastCheck}</span></span>
        </div>

        <div class="site-card__footer">
          <a class="site-card__link" href="${site.url}" target="_blank" rel="noopener noreferrer">
            Ouvrir le site
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
              <path d="M15 3h6v6"/><path d="M10 14L21 3"/>
            </svg>
          </a>
        </div>
      </article>`;
  }

  function renderSites(sites) {
    if (!sites.length) {
      els.sitesGrid.innerHTML = "";
      els.emptyState.classList.remove("state-panel--hidden");
      return;
    }
    els.emptyState.classList.add("state-panel--hidden");
    els.sitesGrid.innerHTML = sites.map(siteCardTemplate).join("");
  }

  /* --------------------------- Chargement --------------------------- */

  async function loadStatus() {
    try {
      const response = await fetch(`${STATUS_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      if (!data || !Array.isArray(data.sites)) throw new Error("Format de données invalide");

      els.errorState.classList.add("state-panel--hidden");
      els.lastUpdateValue.textContent = formatDate(data.last_update);
      renderStats(data.sites);
      renderSites(data.sites);
    } catch (err) {
      console.error("Impossible de charger data/status.json :", err);
      els.sitesGrid.innerHTML = "";
      els.emptyState.classList.add("state-panel--hidden");
      els.errorState.classList.remove("state-panel--hidden");
      renderStats([]);
      els.lastUpdateValue.textContent = "—";
    }
  }

  /* ------------------------------ Init ------------------------------ */

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    loadStatus();
  });
})();
