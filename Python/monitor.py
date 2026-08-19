#!/usr/bin/env python3
"""
Python/monitor.py

Monitoring quotidien des sites web listés dans config/list_url.txt.

Pour chaque site :
  1. Vérifie la disponibilité HTTP (statut ONLINE / DOWN + code HTTP).
  2. Charge la page principale avec Playwright + Chromium et prend une capture
     d'écran, qui écrase systématiquement la précédente.
  3. Met à jour data/status.json.

Le script :
  - ne s'interrompt jamais à cause d'un site en erreur (chaque site est isolé
    dans son propre bloc try/except) ;
  - supprime les captures des sites qui ne sont plus dans list_url.txt ;
  - produit des logs clairs sur stdout.

Usage :
    python Python/monitor.py
"""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    SSLError,
    Timeout,
    RequestException,
)

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - clearer error if deps are missing
    print("ERREUR: le paquet 'playwright' n'est pas installé. Voir requirements.txt", file=sys.stderr)
    raise


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "list_url.txt"
SCREENSHOTS_DIR = ROOT_DIR / "screenshots"
STATUS_FILE = ROOT_DIR / "data" / "status.json"

HTTP_TIMEOUT_SECONDS = 15
PLAYWRIGHT_NAV_TIMEOUT_MS = 20_000
SCREENSHOT_VIEWPORT = {"width": 1440, "height": 900}
USER_AGENT = "Mozilla/5.0 (compatible; WebsiteMonitoringDashboard/1.0; +https://github.com)"

# Codes HTTP considérés comme ONLINE. Modifiable facilement ici :
# toute réponse HTTP reçue avec un code < 500 est considérée ONLINE
# (y compris les 4xx, qui indiquent un serveur qui répond).
# Un site est DOWN si : timeout, erreur DNS, erreur de connexion,
# erreur SSL/certificat, ou code HTTP >= 500.
def is_online(http_code: int | None) -> bool:
    if http_code is None:
        return False
    return http_code < 500


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("monitor")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Site:
    name: str
    url: str
    slug: str = field(default="")


@dataclass
class SiteResult:
    name: str
    url: str
    status: str
    http_code: int | None
    screenshot: str
    render_time_ms: int | None
    last_check: str
    error: str | None = None


# --------------------------------------------------------------------------
# config/list_url.txt parsing
# --------------------------------------------------------------------------

URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def parse_list_url(path: Path) -> list[Site]:
    """Lit config/list_url.txt et retourne la liste des sites valides.

    Format attendu par ligne : "Nom du site, URL"
    - lignes vides ignorées
    - lignes commençant par # ignorées (commentaires)
    - lignes invalides ignorées et signalées dans les logs
    """
    if not path.exists():
        log.error("Fichier introuvable : %s", path)
        return []

    sites: list[Site] = []
    seen_slugs: dict[str, int] = {}

    with path.open("r", encoding="utf-8") as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if "," not in line:
                log.warning("Ligne %d invalide (virgule manquante) ignorée : %r", line_number, raw_line.strip())
                continue

            name_part, url_part = line.split(",", 1)
            name = name_part.strip()
            url = url_part.strip()

            if not name or not url:
                log.warning("Ligne %d invalide (nom ou URL vide) ignorée : %r", line_number, raw_line.strip())
                continue

            if not URL_RE.match(url):
                log.warning("Ligne %d invalide (URL non valide) ignorée : %r", line_number, raw_line.strip())
                continue

            slug = slugify(name)
            if slug in seen_slugs:
                seen_slugs[slug] += 1
                slug = f"{slug}-{seen_slugs[slug]}"
                log.warning(
                    "Collision de nom de fichier pour %r, utilisation du slug %r", name, slug
                )
            else:
                seen_slugs[slug] = 0

            sites.append(Site(name=name, url=url, slug=slug))

    log.info("Chargement de config/list_url.txt : %d site(s) valide(s)", len(sites))
    return sites


def slugify(name: str) -> str:
    """Transforme un nom de site en nom de fichier stable et sûr.

    "Au Fil Du Bain" -> "au-fil-du-bain"
    """
    # Décompose les accents (é -> e + accent) puis les supprime
    normalized = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in normalized if not unicodedata.combining(c))

    lowered = without_accents.lower()
    # Remplace tout ce qui n'est pas alphanumérique par un tiret
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = slug.strip("-")
    slug = re.sub(r"-{2,}", "-", slug)

    return slug or "site"


# --------------------------------------------------------------------------
# HTTP check
# --------------------------------------------------------------------------

@dataclass
class HttpCheckResult:
    http_code: int | None
    error: str | None


def check_http(url: str) -> HttpCheckResult:
    """Vérifie la disponibilité HTTP d'une URL.

    Gère explicitement : DNS, connexion, timeout, SSL/certificat, 4xx, 5xx.
    """
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        return HttpCheckResult(http_code=response.status_code, error=None)

    except SSLError as exc:
        return HttpCheckResult(http_code=None, error=f"Erreur HTTPS/certificat : {exc}")

    except Timeout:
        return HttpCheckResult(http_code=None, error="Timeout de connexion")

    except RequestsConnectionError as exc:
        # Regroupe les erreurs DNS et les erreurs de connexion générales
        message = str(exc)
        if isinstance(getattr(exc, "__cause__", None), socket.gaierror) or "Name or service not known" in message:
            return HttpCheckResult(http_code=None, error="Erreur DNS : nom de domaine introuvable")
        return HttpCheckResult(http_code=None, error=f"Erreur de connexion : {exc}")

    except RequestException as exc:
        return HttpCheckResult(http_code=None, error=f"Erreur HTTP inattendue : {exc}")


# --------------------------------------------------------------------------
# Screenshots (Playwright)
# --------------------------------------------------------------------------

@dataclass
class ScreenshotResult:
    error: str | None
    render_time_ms: int | None


def take_screenshot(browser, url: str, destination: Path) -> ScreenshotResult:
    """Charge la page principale, mesure le temps de rendu complet et
    enregistre une capture d'écran.

    Le "temps de rendu complet" est mesuré entre le début de la navigation
    et l'événement `load` de la page (DOM + ressources + styles + images
    chargés) — c'est le même instant qui déclenche la capture d'écran.

    Écrase toujours la capture précédente au même chemin.
    """
    context = None
    try:
        context = browser.new_context(
            viewport=SCREENSHOT_VIEWPORT,
            user_agent=USER_AGENT,
            ignore_https_errors=False,
        )
        page = context.new_page()
        page.set_default_navigation_timeout(PLAYWRIGHT_NAV_TIMEOUT_MS)
        page.set_default_timeout(PLAYWRIGHT_NAV_TIMEOUT_MS)

        start = time.perf_counter()
        page.goto(url, wait_until="load")
        render_time_ms = round((time.perf_counter() - start) * 1000)

        destination.parent.mkdir(parents=True, exist_ok=True)
        # Écrit vers un fichier temporaire puis remplace pour un "overwrite" atomique
        tmp_path = destination.with_suffix(destination.suffix + ".tmp")
        page.screenshot(path=str(tmp_path), full_page=False)
        tmp_path.replace(destination)
        return ScreenshotResult(error=None, render_time_ms=render_time_ms)

    except PlaywrightTimeoutError:
        return ScreenshotResult(error="Timeout Playwright lors du chargement de la page", render_time_ms=None)
    except PlaywrightError as exc:
        return ScreenshotResult(error=f"Erreur Playwright : {exc}", render_time_ms=None)
    except Exception as exc:  # garde-fou : ne jamais interrompre le traitement des autres sites
        return ScreenshotResult(error=f"Erreur inattendue lors de la capture : {exc}", render_time_ms=None)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Cleanup: remove screenshots for sites no longer in the config
# --------------------------------------------------------------------------

def cleanup_orphan_screenshots(current_slugs: set[str]) -> None:
    if not SCREENSHOTS_DIR.exists():
        return

    for path in SCREENSHOTS_DIR.iterdir():
        if not path.is_file():
            continue
        slug = path.stem
        if slug not in current_slugs:
            try:
                path.unlink()
                log.info("Capture obsolète supprimée : %s", path.name)
            except OSError as exc:
                log.warning("Impossible de supprimer %s : %s", path.name, exc)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def process_site(browser, site: Site) -> SiteResult:
    log.info("Vérification de %s (%s)", site.name, site.url)
    check_time = now_str()
    screenshot_rel_path = f"screenshots/{site.slug}.png"
    screenshot_abs_path = ROOT_DIR / screenshot_rel_path

    try:
        http_result = check_http(site.url)
    except Exception as exc:  # ne doit jamais arrêter le monitoring des autres sites
        log.error("Erreur inattendue lors de la vérification HTTP de %s : %s", site.name, exc)
        http_result = HttpCheckResult(http_code=None, error=str(exc))

    online = is_online(http_result.http_code)
    status = "online" if online else "down"

    if http_result.error:
        log.warning("%s -> DOWN (%s)", site.name, http_result.error)
    else:
        log.info("%s -> %s (HTTP %s)", site.name, status.upper(), http_result.http_code)

    screenshot_result = take_screenshot(browser, site.url, screenshot_abs_path)
    if screenshot_result.error:
        log.warning("Capture indisponible pour %s : %s", site.name, screenshot_result.error)
    else:
        log.info("%s -> rendu complet en %d ms", site.name, screenshot_result.render_time_ms)

    return SiteResult(
        name=site.name,
        url=site.url,
        status=status,
        http_code=http_result.http_code,
        screenshot=screenshot_rel_path,
        render_time_ms=screenshot_result.render_time_ms,
        last_check=check_time,
        error=http_result.error or screenshot_result.error,
    )


def main() -> int:
    log.info("=== Démarrage du monitoring ===")

    sites = parse_list_url(CONFIG_FILE)
    if not sites:
        log.warning("Aucun site valide trouvé dans %s. status.json sera vide.", CONFIG_FILE)

    results: list[SiteResult] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for site in sites:
                try:
                    result = process_site(browser, site)
                except Exception as exc:
                    # Filet de sécurité ultime : une erreur sur un site ne doit
                    # jamais interrompre le traitement des autres.
                    log.error("Échec complet du traitement de %s : %s", site.name, exc)
                    result = SiteResult(
                        name=site.name,
                        url=site.url,
                        status="down",
                        http_code=None,
                        screenshot=f"screenshots/{site.slug}.png",
                        render_time_ms=None,
                        last_check=now_str(),
                        error=str(exc),
                    )
                results.append(result)
        finally:
            browser.close()

    # Nettoyage des captures orphelines
    current_slugs = {site.slug for site in sites}
    cleanup_orphan_screenshots(current_slugs)

    # Génération de status.json
    status_data = {
        "last_update": now_str(),
        "sites": [
            {
                "name": r.name,
                "url": r.url,
                "status": r.status,
                "http_code": r.http_code,
                "screenshot": r.screenshot,
                "render_time_ms": r.render_time_ms,
                "last_check": r.last_check,
            }
            for r in results
        ],
    }

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(status_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    online_count = sum(1 for r in results if r.status == "online")
    down_count = len(results) - online_count
    log.info(
        "=== Monitoring terminé : %d site(s), %d ONLINE, %d DOWN ===",
        len(results), online_count, down_count,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
