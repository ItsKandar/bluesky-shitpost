# Récupération de l'actualité (flux RSS) pour les posts automatiques
import html
import html.entities
import random
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

import requests

# Sources prioritaires : un article pas encore traité, publié depuis moins de N jours,
# passe avant toute autre actualité.
PRIORITAIRES = {
    "Angle Gauche": ("https://anglegauche.fr/rss.php", 14),
}

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


def _xml_valide(contenu):
    """Remplace les entités HTML (&nbsp;…) que certains flux mettent à tort dans le XML."""
    texte = contenu.decode("utf-8", "replace") if isinstance(contenu, bytes) else contenu
    xml_ok = {"amp", "lt", "gt", "quot", "apos"}
    return re.sub(r"&([a-zA-Z][a-zA-Z0-9]*);",
                  lambda m: m.group(0) if m.group(1) in xml_ok or m.group(1) not in html.entities.name2codepoint
                  else f"&#{html.entities.name2codepoint[m.group(1)]};", texte).encode("utf-8")


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


def _image_rss(item):
    for el in item.iter():
        url = el.get("url") or ""
        if el.tag.endswith(("content", "thumbnail", "enclosure")) and url.startswith("http"):
            if el.get("type", "image").startswith("image") or el.get("medium") == "image":
                return url
    return None


def image_article(article, max_octets=950_000):
    """Image de l'article (flux RSS, sinon balise og:image de la page), en octets, ou None."""
    url = article.get("image")
    try:
        if not url:
            page = requests.get(article["lien"], headers=ENTETES, timeout=10).text
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page) or \
                re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
            url = html.unescape(m.group(1)) if m else None
        if not url:
            return None
        r = requests.get(url, headers=ENTETES, timeout=10)
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) <= max_octets:
            return r.content
    except Exception as e:
        print(f"[actu] image indisponible : {e}")
    return None


def _acceptable(titre, resume):
    bas = f"{titre} {resume}".lower()
    return not any(mot in bas for mot in MOTS_EXCLUS)


def _lire_flux(source, url, deja_vus, limite):
    try:
        r = requests.get(url, headers=ENTETES, timeout=10)
        racine = ET.fromstring(_xml_valide(r.content))
    except Exception as e:
        print(f"[actu] flux {source} indisponible : {e}")
        return []
    articles = []
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
        articles.append({"source": source, "titre": titre, "resume": resume, "lien": lien,
                         "image": _image_rss(item), "date": date})
    return articles


def articles_recents(deja_vus, heures=24):
    """Articles à traiter : d'abord ceux des sources prioritaires (du plus récent au plus
    ancien), puis les autres flux des dernières `heures` dans un ordre aléatoire.
    Drames et liens déjà traités sont écartés."""
    maintenant = datetime.now(timezone.utc)
    prioritaires = []
    for source, (url, jours) in PRIORITAIRES.items():
        prioritaires += _lire_flux(source, url, deja_vus, maintenant - timedelta(days=jours))
    prioritaires.sort(key=lambda a: a["date"] or maintenant, reverse=True)
    autres = []
    for source, url in FLUX.items():
        autres += _lire_flux(source, url, deja_vus, maintenant - timedelta(hours=heures))
    random.shuffle(autres)
    return prioritaires + autres
