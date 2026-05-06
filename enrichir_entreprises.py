"""
Script d'enrichissement des données entreprises (site web + email).
Reprend là où il s'est arrêté grâce à un fichier de progression.
"""

import re
import time
import json
import logging
import os
import random
import warnings
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ─── Configuration ────────────────────────────────────────────────────────────

INPUT_FILE = "entreprises_44_v3 - 835.xlsx"
OUTPUT_FILE = "enrichi_entreprises_44_v3_835.xlsx"
FALLBACK_FILE = "a_traiter_manuellement_835.xlsx"
PROGRESS_FILE = "enrichissement_progress_835.json"
LOG_FILE = "log_835.txt"

PAUSE_BETWEEN = 2       # secondes entre chaque entreprise
TIMEOUT = 10            # timeout HTTP
MAX_RETRIES = 2

BLACKLIST_SITES = {
    "pagesjaunes.fr", "societe.com", "infogreffe.fr", "pappers.fr",
    "verif.com", "manageo.fr", "bfmtv.com", "lefigaro.fr", "lemonde.fr",
    "linkedin.com", "facebook.com", "twitter.com", "instagram.com",
    "wikipedia.org", "youtube.com", "kompass.com", "europages.fr",
    "annuaire.fr", "annuaire-mairie.fr", "annuaires.fr", "corporate.com",
}

BLACKLIST_EMAILS = {
    "noreply", "no-reply", "exemple", "example", "test@", "admin@",
    "webmaster@", "postmaster@", "bounce@", "mailer-daemon",
    "unsubscribe", "donotreply", "do-not-reply",
}

EMAIL_PRIORITY = ["contact@", "info@", "direction@", "accueil@", "bonjour@",
                  "commercial@", "hello@", "secretariat@"]

PAGES_TO_SCRAPE = [
    "", "/contact", "/nous-contacter", "/mentions-legales",
    "/about", "/a-propos", "/coordonnees", "/contactez-nous",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

EMAIL_REGEX = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─── Helpers HTTP ─────────────────────────────────────────────────────────────

_ddg_session: requests.Session | None = None


def get_ddg_session() -> requests.Session:
    global _ddg_session
    if _ddg_session is None:
        _ddg_session = requests.Session()
        _ddg_session.headers.update(HEADERS)
        try:
            _ddg_session.get("https://html.duckduckgo.com/html/", timeout=TIMEOUT)
        except Exception:
            pass
    return _ddg_session


def get_with_retry(url: str, **kwargs) -> requests.Response | None:
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(1.5 ** attempt)
            else:
                log.debug(f"Échec {url}: {e}")
    return None


def is_blacklisted(url: str) -> bool:
    return any(b in url for b in BLACKLIST_SITES)


# ─── Étape 1 : Trouver le site web ────────────────────────────────────────────

def site_depuis_api_gouv(siren: str) -> str | None:
    url = f"https://recherche-entreprises.api.gouv.fr/search?q={siren}"
    r = get_with_retry(url)
    if not r:
        return None
    try:
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        res = results[0]
        # Chercher dans tous les champs possibles
        for candidate in [
            res.get("site_web"),
            res.get("complements", {}).get("site_internet"),
            res.get("complements", {}).get("site_web"),
            res.get("siege", {}).get("site_web"),
        ]:
            if candidate and str(candidate).strip():
                return str(candidate).strip()
    except Exception:
        pass
    return None


def site_depuis_duckduckgo(nom: str, ville: str) -> str | None:
    query = f"{nom} {ville} site officiel"
    session = get_ddg_session()
    try:
        r = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "b": "", "kl": "fr-fr", "df": ""},
            timeout=TIMEOUT,
        )
    except Exception as e:
        log.debug(f"DDG POST échoué: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    def est_url_propre(url: str) -> bool:
        """Rejette les URLs de tracking, pubs et redirections DDG."""
        PATTERNS_REJETES = (
            "duckduckgo.com", "bing.com/aclick", "y.js?", "clickserve",
            "dartserch", "redirect", "doubleclick", "ad_domain",
        )
        return (
            url.startswith("http")
            and not is_blacklisted(url)
            and not any(p in url for p in PATTERNS_REJETES)
        )

    # Premier résultat non blacklisté parmi les liens de résultats
    for a in soup.select("a.result__a"):
        href = a.get("href", "").strip()
        if not href.startswith("http"):
            href = "https://" + href
        if est_url_propre(href):
            return href

    # Fallback : URLs affichées dans .result__url (texte propre sans tracking)
    for a in soup.select(".result__url"):
        href = a.get_text(strip=True)
        if not href.startswith("http"):
            href = "https://" + href
        if est_url_propre(href):
            return href

    return None


def trouver_site(siren: str, nom: str, ville: str) -> tuple[str | None, str]:
    site = site_depuis_api_gouv(siren)
    if site:
        return site, "api_gouv"
    site = site_depuis_duckduckgo(nom, ville)
    if site:
        return site, "duckduckgo"
    return None, "aucune"


# ─── Étape 2 : Scraper les emails ─────────────────────────────────────────────

def normaliser_url(base: str, chemin: str) -> str:
    base = base.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return base + chemin


def est_email_valide(email: str) -> bool:
    email_lower = email.lower()
    if any(b in email_lower for b in BLACKLIST_EMAILS):
        return False
    domaine = email_lower.split("@")[-1]
    if domaine in {"example.com", "exemple.fr", "test.fr", "test.com", "domain.com", "email.com"}:
        return False
    return True


def extraire_emails_texte(texte: str) -> list[str]:
    emails = EMAIL_REGEX.findall(texte)
    return [e for e in emails if est_email_valide(e)]


def prioriser_email(emails: list[str]) -> tuple[str | None, str]:
    if not emails:
        return None, ""
    for prefix in EMAIL_PRIORITY:
        for e in emails:
            if e.lower().startswith(prefix):
                return e, "prioritaire"
    return emails[0], "premier_trouve"


def scraper_emails_site(site_url: str) -> tuple[str | None, str]:
    """Retourne (email, source_page)."""
    if not site_url.startswith("http"):
        site_url = "https://" + site_url

    tous_emails: dict[str, list[str]] = {}  # source -> emails

    for chemin in PAGES_TO_SCRAPE:
        url = normaliser_url(site_url, chemin)
        r = get_with_retry(url, allow_redirects=True)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # Supprimer scripts/styles
            for tag in soup(["script", "style"]):
                tag.decompose()
            texte = soup.get_text(separator=" ")
            emails = extraire_emails_texte(texte)
            if emails:
                source_label = chemin.strip("/") or "accueil"
                tous_emails[source_label] = emails
        except Exception:
            continue
        time.sleep(0.5)

    if not tous_emails:
        return None, ""

    # Priorité aux pages de contact
    for page_prioritaire in ["contact", "nous-contacter", "contactez-nous", "coordonnees"]:
        if page_prioritaire in tous_emails:
            email, _ = prioriser_email(tous_emails[page_prioritaire])
            if email:
                return email, page_prioritaire

    # Sinon mention légales puis accueil
    for page in ["mentions-legales", "accueil"]:
        if page in tous_emails:
            email, _ = prioriser_email(tous_emails[page])
            if email:
                return email, page

    # Dernier recours
    all_emails = [e for lst in tous_emails.values() for e in lst]
    email, _ = prioriser_email(all_emails)
    source = list(tous_emails.keys())[0] if tous_emails else ""
    return email, source


# ─── Progression ──────────────────────────────────────────────────────────────

def charger_progression() -> dict:
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def sauvegarder_progression(prog: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    df = pd.read_excel(INPUT_FILE, dtype=str)
    df = df.fillna("")

    col_nom = "Nom de l'entreprise"
    col_siren = "SIREN"
    col_ville = "Ville"

    # S'assurer que les colonnes de sortie existent
    for col in ["Site_Web", "Source_Site", "Email_Contact", "Source_Email"]:
        if col not in df.columns:
            df[col] = ""

    progression = charger_progression()
    total = len(df)

    for idx, row in df.iterrows():
        siren = str(row[col_siren]).strip()
        nom = str(row[col_nom]).strip()
        ville = str(row[col_ville]).strip()
        label = f"[{idx+1}/{total}] {nom}"

        # Reprendre là où on s'est arrêté
        if siren in progression and progression[siren].get("done"):
            data = progression[siren]
            df.at[idx, "Site_Web"] = data.get("site", "")
            df.at[idx, "Source_Site"] = data.get("source_site", "")
            df.at[idx, "Email_Contact"] = data.get("email", "")
            df.at[idx, "Source_Email"] = data.get("source_email", "")
            log.info(f"{label} | REPRISE (déjà traité)")
            continue

        log.info(f"{label} | Traitement en cours...")

        # Étape 1 : site web
        site, source_site = trouver_site(siren, nom, ville)
        log.info(f"  Site: {site or 'non trouvé'} ({source_site})")

        # Étape 2 : email
        email, source_email = ("", "")
        if site:
            email, source_email = scraper_emails_site(site)
            log.info(f"  Email: {email or 'non trouvé'} ({source_email})")
        else:
            log.info("  Email: ignoré (pas de site)")

        df.at[idx, "Site_Web"] = site or ""
        df.at[idx, "Source_Site"] = source_site
        df.at[idx, "Email_Contact"] = email or ""
        df.at[idx, "Source_Email"] = source_email

        progression[siren] = {
            "done": True,
            "nom": nom,
            "site": site or "",
            "source_site": source_site,
            "email": email or "",
            "source_email": source_email,
        }
        sauvegarder_progression(progression)

        # Sauvegarde intermédiaire tous les 10
        if (idx + 1) % 10 == 0:
            df.to_excel(OUTPUT_FILE, index=False)
            log.info(f"  Sauvegarde intermédiaire ({idx+1}/{total})")

        time.sleep(PAUSE_BETWEEN + random.uniform(0, 1))

    # Export final
    df.to_excel(OUTPUT_FILE, index=False)
    log.info(f"\nFichier enrichi sauvegardé : {OUTPUT_FILE}")

    # Fichier fallback (sans email)
    col_adresse = [c for c in df.columns if "adresse" in c.lower()][0]
    sans_email = df[df["Email_Contact"].str.strip() == ""][[
        col_nom, col_adresse, col_ville, "Code postal",
        col_siren, "Site_Web", "Source_Site"
    ]].copy()
    if not sans_email.empty:
        sans_email.to_excel(FALLBACK_FILE, index=False)
        log.info(f"Fichier manuel sauvegardé : {FALLBACK_FILE} ({len(sans_email)} entreprises)")

    # Résumé
    total_sites = (df["Site_Web"].str.strip() != "").sum()
    total_emails = (df["Email_Contact"].str.strip() != "").sum()
    log.info(f"\n=== RÉSUMÉ ===")
    log.info(f"Total entreprises : {total}")
    log.info(f"Sites trouvés     : {total_sites} ({total_sites/total*100:.0f}%)")
    log.info(f"Emails trouvés    : {total_emails} ({total_emails/total*100:.0f}%)")


if __name__ == "__main__":
    main()
