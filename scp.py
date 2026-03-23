import re
import requests
import json
import os
import sys

# ── Configuration ──────────────────────────────────────────────

SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42"

SEARCH_BODY = {
    "idTool": 42,
    "need_aggregation": True,
    "page": 1,
    "pageSize": 100,
    "sector": None,
    "occupationModes": [],
    "location": [
        {"lon": 2.224122, "lat": 48.902156},
        {"lon": 2.4697602, "lat": 48.8155755}
    ],
    "residence": None,
    "precision": 6,
    "equipment": [],
    "price": {"max": 10000000},
    "area": {"min": 0},
    "adaptedPmr": False,
    "toolMechanism": "flow"
}

COOKIES = {
    "PHPSESSID": os.environ.get("CROUS_PHPSESSID", ""),
    "qpid": os.environ.get("CROUS_QPID", ""),
    "HAPROXYID": os.environ.get("CROUS_HAPROXYID", ""),
}

HEADERS = {
    "Accept": "application/ld+json, application/json",
    "Content-Type": "application/json",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": "https://trouverunlogement.lescrous.fr/tools/42/search?bounds=2.224122_48.902156_2.4697602_48.8155755&locationName=Paris",
    "User-Agent": "Mozilla/5.0"
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
KNOWN_IDS_FILE = "known_ids.json"
ERROR_STATE_FILE = "error_state.json"
ACCOMMODATION_BASE_URL = "https://trouverunlogement.lescrous.fr/tools/42/accommodations"

# ── Résidences prioritaires ─────────────────────────────────────

PRIORITY_KEYWORDS = [
    kw.strip().lower()
    for kw in os.environ.get("PRIORITY_KEYWORDS", "").split(",")
    if kw.strip()
]

def is_priority(listing) -> bool:
    residence = listing.get("residence", {})
    res_label = (residence.get("label") or "").lower()
    name      = (listing.get("label") or "").lower()
    return any(kw in res_label or kw in name for kw in PRIORITY_KEYWORDS)

def is_paris_intramuros(listing) -> bool:
    address = listing.get("residence", {}).get("address") or ""
    match = re.search(r'\b(75\d{3})\b', address)
    return match is not None or address == ""

# ── Exceptions ─────────────────────────────────────────────────

class SessionExpiredError(Exception):
    pass

# ── Fetch ─────────────────────────────────────────────────────

def fetch_listings():
    try:
        response = requests.post(
            SEARCH_URL,
            json=SEARCH_BODY,
            cookies=COOKIES,
            headers=HEADERS,
            timeout=15
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Erreur reseau : {e}")

    if response.status_code in (401, 403):
        raise SessionExpiredError("Session expiree")

    if "discovery/connect" in response.url or "identification" in response.url.lower():
        raise SessionExpiredError("Redirection login")

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")

    try:
        data = response.json()
    except:
        raise SessionExpiredError("Reponse non JSON")

    items = data.get("results", {}).get("items")
    if items is None:
        raise SessionExpiredError("Structure invalide")

    return items

# ── Fichiers ──────────────────────────────────────────────────

def load_json(path, default):
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else default
    except:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

def load_known_ids():
    return set(load_json(KNOWN_IDS_FILE, []))

def save_known_ids(ids):
    save_json(KNOWN_IDS_FILE, list(ids))

def load_error_state():
    return load_json(ERROR_STATE_FILE, {"in_error": False, "error_type": None})

def save_error_state(state):
    save_json(ERROR_STATE_FILE, state)

# ── Discord ───────────────────────────────────────────────────

def _post_to_discord(payload):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"[WARN] Discord : {e}")

def send_discord_new_listing(listing):
    name      = listing.get("label") or "Logement sans nom"
    residence = listing.get("residence", {})
    address   = residence.get("address") or "Adresse inconnue"
    res_label = residence.get("label") or ""
    lid       = listing.get("id", "")
    link      = f"{ACCOMMODATION_BASE_URL}/{lid}" if lid else ACCOMMODATION_BASE_URL

    occupation = listing.get("occupationModes", [])
    if occupation:
        rent     = occupation[0].get("rent", {})
        rent_min = rent.get("min", 0) // 100
        rent_max = rent.get("max", 0) // 100
        prix     = f"{rent_min}€" if rent_min == rent_max else f"{rent_min}-{rent_max}€"
    else:
        prix = "Non renseigné"

    if is_priority(listing):
        payload = {
            "content": "🚨🟢 **PRIORITAIRE — FONCE !** 🟢🚨 @everyone",
            "embeds": [{
                "title": "🟢 Logement prioritaire disponible !",
                "description": "**Ce logement est dans ta liste prioritaire. Ne tarde pas !**",
                "color": 0x2ECC71,
                "fields": [
                    {"name": "🏠 Résidence", "value": f"{name} ({res_label})" if res_label else name, "inline": False},
                    {"name": "📍 Adresse",   "value": address, "inline": False},
                    {"name": "💶 Loyer",     "value": prix, "inline": True},
                    {"name": "🔗 Lien",      "value": f"[Voir le logement]({link})", "inline": False},
                ],
                "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
            }]
        }
        print(f"🚨 PRIORITAIRE : {name}")
    else:
        payload = {
            "embeds": [{
                "title": "Nouveau logement disponible !",
                "color": 0xFF3B30,
                "fields": [
                    {"name": "Nom",     "value": f"{name} ({res_label})" if res_label else name, "inline": False},
                    {"name": "Adresse", "value": address, "inline": False},
                    {"name": "Loyer",   "value": prix, "inline": True},
                    {"name": "Lien",    "value": f"[Voir le logement]({link})", "inline": False},
                ],
                "footer": {"text": "Mon Logement Crous - Surveillance automatique"},
            }]
        }
        print(f"Notification : {name}")

    _post_to_discord(payload)

# ── Main ──────────────────────────────────────────────────────

def main():
    print("Verification des annonces Crous...")

    error_state = load_error_state()

    try:
        listings = fetch_listings()
    except Exception as e:
        print(e)
        if not error_state["in_error"]:
            _post_to_discord({"content": f"Erreur: {e}"})
            save_error_state({"in_error": True, "error_type": "error"})
        sys.exit(1)

    if error_state["in_error"]:
        save_error_state({"in_error": False, "error_type": None})

    listings = [l for l in listings if is_paris_intramuros(l)]

    previous_ids = load_known_ids()
    current_ids  = {str(l["id"]) for l in listings}

    new_ids = current_ids - previous_ids

    if new_ids:
        print(f"{len(new_ids)} nouveaux logements")
        for l in listings:
            if str(l["id"]) in new_ids:
                send_discord_new_listing(l)
    else:
        print("Aucun nouveau logement")

    # sauvegarde robuste
    try:
        save_known_ids(current_ids)
    except Exception as e:
        print(f"Erreur sauvegarde: {e}")

if __name__ == "__main__":
    main()
