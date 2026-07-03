import os, time, requests, schedule, anthropic, json, base64
from datetime import datetime
from bs4 import BeautifulSoup

IG_USER_ID = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN   = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IG_BASE    = "https://graph.instagram.com/v21.0"

def update_bio_link(product_url, product_name):
    """Met a jour le lien de la bio Instagram avec l'URL du produit"""
    try:
        bio_text = f"🛋️ Meubles & Objets Design | Style Loft & Industriel\n✨ Nouveau : {product_name[:40]}\n👇 Produit du jour"
        r = requests.post(f"{IG_BASE}/{IG_USER_ID}", data={
            "biography":    bio_text,
            "website":      product_url,
            "access_token": IG_TOKEN,
        })
        result = r.json()
        if "id" in result or "biography" in result:
            print(f"Bio mise a jour avec le lien : {product_url}")
            return True
        else:
            print(f"Erreur mise a jour bio: {result}")
            return False
    except Exception as e:
        print(f"Erreur bio: {e}")
        return False

def get_product_images(product_url):
    """Scrape toutes les images d'une fiche produit"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(product_url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        images = []
        selectors = [
            ".product-images img",
            ".product-cover img",
            ".product-thumbs img",
            "#product-images-large img",
            ".slick-slide img",
            ".owl-item img",
            "[data-image-large-src]",
            ".js-qv-product-cover img",
            ".product-image img",
        ]
        seen = set()
        for sel in selectors:
            for img in soup.select(sel):
                src = img.get("data-src") or img.get("data-image-large-src") or img.get("src") or ""
                if src.startswith("/"):
                    src = "https://www.loftattitude.com" + src
                if src and src not in seen and any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    if not any(skip in src.lower() for skip in ["logo", "icon", "sprite", "thumb", "mini", "cart"]):
                        seen.add(src)
                        src_large = src.replace("-small", "").replace("-medium", "").replace("_small", "").replace("_medium", "")
                        images.append(src_large)
        print(f"Images trouvees: {len(images)}")
        return images
    except Exception as e:
        print(f"Erreur scraping images: {e}")
        return []

def is_lifestyle_image(image_url):
    """Detecte si une image est lifestyle via Claude Vision"""
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        r = requests.get(image_url, timeout=10)
        if r.status_code != 200:
            return False, 0
        img_b64 = base64.b64encode(r.content).decode("utf-8")
        content_type = r.headers.get("content-type", "image/jpeg")
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": img_b64}},
                    {"type": "text", "text": 'Analyse cette image. Reponds UNIQUEMENT en JSON: {"lifestyle": true/false, "score": 0-10}\nlifestyle=true si photo dans un interieur/ambiance/mise en scene\nlifestyle=false si fond blanc/uni/produit seul detouré\nscore: qualite visuelle Instagram'}
                ]
            }]
        )
        result = json.loads(msg.content[0].text.replace("```json", "").replace("```", "").strip())
        return result.get("lifestyle", False), result.get("score", 0)
    except Exception as e:
        print(f"Erreur analyse image: {e}")
        return False, 0

def select_best_images(images, max_images=5):
    """Selectionne les meilleures images en prioritisant le lifestyle"""
    if not images:
        return []
    print(f"Analyse IA de {min(len(images), 8)} images...")
    scored = []
    for i, img_url in enumerate(images[:8]):
        print(f"  Image {i+1}...")
        is_lifestyle, score = is_lifestyle_image(img_url)
        final_score = score + (5 if is_lifestyle else 0)
        scored.append({"url": img_url, "lifestyle": is_lifestyle, "score": final_score})
        print(f"  -> Lifestyle: {is_lifestyle}, Score: {final_score}")
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = [item["url"] for item in scored[:max_images]]
    lifestyle_count = sum(1 for item in scored[:max_images] if item["lifestyle"])
    print(f"Selection: {len(best)} images ({lifestyle_count} lifestyle)")
    return best

def get_latest_product():
    """Recupere le dernier produit depuis loftattitude.com"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.loftattitude.com/fr/nouveaux-produits", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one(".product-miniature")
        if not product:
            print("Aucun produit trouve")
            return None
        name_el  = product.select_one(".product-title")
        price_el = product.select_one(".price")
        img_el   = product.select_one("img")
        link_el  = product.select_one("a")
        img_url = img_el.get("data-src") or img_el.get("src") if img_el else ""
        if img_url and img_url.startswith("/"):
            img_url = "https://www.loftattitude.com" + img_url
        href = link_el.get("href", "") if link_el else ""
        product_url = href if href.startswith("http") else "https://www.loftattitude.com" + href
        return {
            "nom":       name_el.text.strip()  if name_el  else "Nouveau produit design",
            "prix":      price_el.text.strip() if price_el else "",
            "image_url": img_url,
            "url":       product_url or "https://www.loftattitude.com",
        }
    except Exception as e:
        print(f"Erreur scraping produit: {e}")
        return None

def generate_caption(product):
    """Genere caption + hashtags avec Claude"""
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system='Tu es expert marketing Instagram pour "Loft Attitude", boutique de meubles et objets design loft, industriel et contemporain. Reponds UNIQUEMENT en JSON valide sans markdown: {"caption":"caption Instagram 120-150 mots avec emojis, storytelling produit, ambiance design, termine TOUJOURS par : Retrouvez ce produit via le lien en bio 👆 loftattitude.com","hashtags":["25 hashtags pertinents"]}',
            messages=[{"role": "user", "content": f"Produit: {product['nom']}\nPrix: {product['prix']}\nURL: {product['url']}"}]
        )
        data = json.loads(msg.content[0].text.replace("```json", "").replace("```", "").strip())
        return data["caption"] + "\n\n" + " ".join(data["hashtags"])
    except Exception as e:
        print(f"Erreur caption: {e}")
        return f"Nouvelle arrivee chez Loft Attitude ! {product['nom']} - {product['prix']}\nRetrouvez ce produit via le lien en bio 👆 loftattitude.com\n\n#loftattitude #design #deco #meuble #loftdesign"

def publish_carousel(images, caption):
    """Publie un carrousel ou image simple"""
    if not images:
        print("Pas d'images")
        return False
    if not IG_TOKEN:
        print("Token manquant")
        return False
    if len(images) == 1:
        return publish_single(images[0], caption)

    print(f"Carrousel avec {len(images)} images...")
    children_ids = []
    for i, img_url in enumerate(images):
        print(f"  Container image {i+1}...")
        r = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
            "image_url":        img_url,
            "is_carousel_item": "true",
            "access_token":     IG_TOKEN,
        })
        result = r.json()
        if "id" in result:
            children_ids.append(result["id"])
        else:
            print(f"  Erreur: {result}")

    if not children_ids:
        return False

    print(f"{len(children_ids)} containers crees. Attente 30s...")
    time.sleep(30)

    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "media_type":   "CAROUSEL",
        "children":     ",".join(children_ids),
        "caption":      caption,
        "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    print(f"Container carrousel: {result2}")
    if "id" not in result2:
        return False

    time.sleep(10)
    r3 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id":  result2["id"],
        "access_token": IG_TOKEN,
    })
    result3 = r3.json()
    if "id" in result3:
        print(f"Carrousel publie ! ID: {result3['id']}")
        return True
    print(f"Erreur: {result3}")
    return False

def publish_single(image_url, caption):
    """Publie une image simple"""
    r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "image_url":    image_url,
        "caption":      caption,
        "access_token": IG_TOKEN,
    })
    result1 = r1.json()
    if "id" not in result1:
        print(f"Erreur: {result1}")
        return False
    time.sleep(30)
    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id":  result1["id"],
        "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    if "id" in result2:
        print(f"Publie ! ID: {result2['id']}")
        return True
    return False

def daily_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{now}] Debut publication Loft Attitude")
    print(f"{'='*50}")

    # 1. Produit
    product = get_latest_product()
    if not product:
        print("Pas de produit disponible.")
        return
    print(f"Produit: {product['nom']}")
    print(f"URL: {product['url']}")

    # 2. Mise a jour de la bio avec le lien produit
    print("Mise a jour de la bio Instagram...")
    update_bio_link(product["url"], product["nom"])

    # 3. Images
    all_images = get_product_images(product["url"]) if product["url"] else []
    if not all_images and product["image_url"]:
        all_images = [product["image_url"]]

    # 4. Selection lifestyle
    best_images = select_best_images(all_images, max_images=5)
    if not best_images:
        print("Pas d'images.")
        return

    # 5. Caption
    caption = generate_caption(product)
    print(f"Caption: {len(caption)} caracteres")

    # 6. Publication
    success = publish_carousel(best_images, caption)
    print("Reussi !" if success else "Echec.")

if __name__ == "__main__":
    print("Bot Loft Attitude v3 - Lifestyle + Carrousel + Bio")
    print(f"IG_USER_ID: {IG_USER_ID}")
    print(f"Token: {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude: {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print("Publication planifiee a 09:00\n")
    daily_job()
    schedule.every().day.at("09:00").do(daily_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
