import os, time, requests, schedule, anthropic, json
from datetime import datetime
from bs4 import BeautifulSoup

# Variables d'environnement (à configurer sur Railway)
IG_USER_ID   = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN     = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
IG_API       = "https://graph.facebook.com/v19.0"

def get_latest_product():
    """Recupere le dernier produit depuis loftattitude.com"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.loftattitude.com/fr/nouveaux-produits", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one(".product-miniature")
        if not product:
            print("Aucun produit trouve sur la page nouveautes")
            return None
        name_el  = product.select_one(".product-title")
        price_el = product.select_one(".price")
        img_el   = product.select_one("img")
        link_el  = product.select_one("a")
        img_url  = img_el.get("data-src") or img_el.get("src") if img_el else ""
        if img_url and img_url.startswith("/"):
            img_url = "https://www.loftattitude.com" + img_url
        return {
            "nom":       name_el.text.strip()  if name_el  else "Nouveau produit design",
            "prix":      price_el.text.strip() if price_el else "",
            "image_url": img_url,
            "url":       "https://www.loftattitude.com" + link_el["href"] if link_el else "https://www.loftattitude.com",
        }
    except Exception as e:
        print(f"Erreur scraping: {e}")
        return None

def generate_caption(product):
    """Genere caption + hashtags avec Claude"""
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system="""Tu es expert marketing Instagram pour "Loft Attitude", boutique de meubles et objets design loft, industriel et contemporain.
Reponds UNIQUEMENT en JSON valide sans markdown:
{"caption":"caption Instagram 120-150 mots avec emojis, storytelling produit, call-to-action lien en bio","hashtags":["25 hashtags pertinents"]}""",
            messages=[{"role": "user", "content":
                f"Produit: {product['nom']}\nPrix: {product['prix']}\nURL: {product['url']}"
            }]
        )
        data = json.loads(msg.content[0].text)
        return data["caption"] + "\n\n" + " ".join(data["hashtags"])
    except Exception as e:
        print(f"Erreur generation caption: {e}")
        return f"Nouvelle arrivee chez Loft Attitude ! Decouvrez {product['nom']} - {product['prix']}\nLien en bio : loftattitude.com\n\n#loftattitude #design #deco #meuble #loftdesign"

def publish_to_instagram(image_url, caption):
    """Publie une image sur Instagram via API Meta Graph"""
    if not image_url:
        print("Pas d'image disponible")
        return False
    if not IG_TOKEN:
        print("IG_ACCESS_TOKEN manquant")
        return False

    # Etape 1 : creer le container media
    print(f"Creation du container media pour: {image_url}")
    r1 = requests.post(f"{IG_API}/{IG_USER_ID}/media", data={
        "image_url": image_url,
        "caption":   caption,
        "access_token": IG_TOKEN,
    })
    result1 = r1.json()
    print(f"Reponse creation: {result1}")

    if "id" not in result1:
        print(f"Erreur creation media: {result1}")
        return False

    creation_id = result1["id"]

    # Etape 2 : attendre que le media soit traite
    print("Attente traitement media (30 secondes)...")
    time.sleep(30)

    # Etape 3 : verifier le statut
    r_check = requests.get(f"{IG_API}/{creation_id}", params={
        "fields": "status_code",
        "access_token": IG_TOKEN
    })
    status = r_check.json().get("status_code", "UNKNOWN")
    print(f"Statut media: {status}")

    if status not in ["FINISHED", "UNKNOWN"]:
        print(f"Media pas pret, statut: {status}")
        return False

    # Etape 4 : publier
    r2 = requests.post(f"{IG_API}/{IG_USER_ID}/media_publish", data={
        "creation_id":  creation_id,
        "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    print(f"Reponse publication: {result2}")

    if "id" in result2:
        print(f"Publie avec succes ! Post ID: {result2['id']}")
        return True
    else:
        print(f"Erreur publication: {result2}")
        return False

def daily_job():
    """Tache quotidienne : scrape + genere + publie"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{now}] Debut publication Loft Attitude")
    print(f"{'='*50}")

    product = get_latest_product()
    if not product:
        print("Impossible de recuperer un produit. Arret.")
        return

    print(f"Produit trouve: {product['nom']}")
    print(f"Prix: {product['prix']}")
    print(f"Image: {product['image_url']}")

    caption = generate_caption(product)
    print(f"\nCaption generee ({len(caption)} caracteres)")

    success = publish_to_instagram(product["image_url"], caption)
    if success:
        print("Publication reussie sur Instagram !")
    else:
        print("Echec de la publication.")

# Lancement
if __name__ == "__main__":
    print("Bot Loft Attitude Instagram demarre")
    print(f"IG_USER_ID: {IG_USER_ID}")
    print(f"Token configure: {'Oui' if IG_TOKEN else 'NON - manquant !'}")
    print(f"Claude API: {'Oui' if CLAUDE_KEY else 'NON - manquant !'}")
    print("Publication planifiee chaque jour a 09:00\n")

    # Test immediat au demarrage
    print("Test de publication immediat...")
    daily_job()

    # Planification quotidienne
    schedule.every().day.at("09:00").do(daily_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
