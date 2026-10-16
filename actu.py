# Récupération de l'actualité (flux RSS) pour les posts automatiques
import html
import random
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

import requests

FLUX = {
    "franceinfo": "https://www.francetvinfo.fr/titres.rss",
    "Le Monde": "https://www.lemonde.fr/rss/une.xml",
    "Libération": "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml",
    "20 Minutes": "https://www.20minutes.fr/feeds/rss-une.xml",
    "L'Humanité": "https://www.humanite.fr/feed",
    "Mediapart": "https://www.mediapart.fr/articles/feed",
    "HuffPost": "https://www.huffingtonpost.fr/rss/all_headline.xml",
}

# Sujets écartés d'office : on ne fait pas de shitpost sur un drame
MOTS_EXCLUS = [
    "mort", "morts", "décès", "décéd", "tué", "tuée", "tués", "meurtre", "assassin", "attentat",
    "terroris", "viol", "pédocrimin", "agression sexuelle", "féminicide", "accident", "crash",
    "incendie", "victime", "otage", "massacre", "génocide", "bombard", "frappe", "suicide",
    "disparu", "noyé", "séisme", "inondation", "deuil", "obsèques", "funérailles", "hommage",
    "cancer", "enfant", "bébé", "nécrolog",
]

ENTETES = {"User-Agent": "Mozilla/5.0 (Marguerite, bot Bluesky)"}


def _nettoyer(texte):
    texte = re.sub(r"<[^>]+>", " ", texte or "")
    return re.sub(r"\s+", " ", html.unescape(texte)).strip()


def _date(item):
    brut = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
    if not brut:
        return None
    try:
        return parsedate_to_datetime(brut)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(brut.replace("Z", "+00:00"))
        except ValueError:
            return None


def _acceptable(titre, resume):
    bas = f"{titre} {resume}".lower()
    return not any(mot in bas for mot in MOTS_EXCLUS)


def articles_recents(deja_vus, heures=24):
    """Articles des dernières `heures`, hors drames et hors liens déjà traités."""
    limite = datetime.now(timezone.utc) - timedelta(hours=heures)
    articles = []
    for source, url in FLUX.items():
        try:
            r = requests.get(url, headers=ENTETES, timeout=10)
            racine = ET.fromstring(r.content)
        except Exception as e:
            print(f"[actu] flux {source} indisponible : {e}")
            continue
        for item in racine.findall(".//item"):
            titre = _nettoyer(item.findtext("title"))
            lien = (item.findtext("link") or "").strip()
            resume = _nettoyer(item.findtext("description"))[:400]
            date = _date(item)
            if not titre or not lien or lien in deja_vus:
                continue
            if date and date.tzinfo and date < limite:
                continue
            if not _acceptable(titre, resume):
                continue
            articles.append({"source": source, "titre": titre, "resume": resume, "lien": lien})
    random.shuffle(articles)
    return articles
