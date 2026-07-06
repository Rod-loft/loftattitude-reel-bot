import os, time, requests, schedule, anthropic, json, base64, io
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter

IG_USER_ID   = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN     = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
IMGBB_KEY    = os.environ.get("IMGBB_API_KEY", "")
FB_PAGE_ID   = os.environ.get("FB_PAGE_ID", "")
FB_TOKEN     = os.environ.get("FB_PAGE_TOKEN", "")
IG_BASE      = "https://graph.instagram.com/v21.0"
FB_BASE      = "https://graph.facebook.com/v21.0"
HISTORY_FILE = "/tmp/published_products.json"

# ─── HISTORIQUE ───────────────────────────────────────────────────────────────

def load_history():
    """Charge l'historique des produits deja publies"""
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_history(history):
    """Sauvegarde l'historique"""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[-50:], f)  # Garde les 50 derniers
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")

def already_published(product_url):
    """Verifie si un produit a deja ete publie"""
    history = load_history()
    return product_url in history

def mark_as_published(product_url):
    """Marque un produit comme publie"""
    history = load_history()
    if product_url not in history:
        history.append(product_url)
        save_history(history)
    print(f"Produit marque comme publie: {product_url}")

# ─── TRAITEMENT IMAGE ─────────────────────────────────────────────────────────

def crop_to_45(image_bytes):
    """
    Recadre une image au format 4:5 (1080x1350) SANS bandes blanches.
    Utilise un fond flouté si l'image ne remplit pas le cadre.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        target_w, target_h = 1080, 1350
        target_ratio = target_w / target_h  # 0.8

        # Cree le canvas final
        canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))

        # Calcule le ratio de l'image source
        src_ratio = w / h

        if src_ratio > target_ratio:
            # Image trop large : on la met en pleine hauteur et on floute les bords
            new_h = target_h
            new_w = int(new_h * src_ratio)
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)

            # Fond flouté (image etirée + flou fort)
            bg = img.resize((target_w, target_h), Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
            # Assombrit le fond
            from PIL import ImageEnhance
            bg = ImageEnhance.Brightness(bg).enhance(0.5)
            canvas.paste(bg, (0, 0))

            # Centre l'image par dessus
            x = (target_w - new_w) // 2
            canvas.paste(img_resized, (x, 0))

        else:
            # Image trop haute ou carree : on la met en pleine largeur
            new_w = target_w
            new_h = int(new_w / src_ratio)
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)

            if new_h >= target_h:
                # Recadre au centre
                top = (new_h - target_h) // 2
                img_cropped = img_resized.crop((0, top, new_w, top + target_h))
                canvas.paste(img_cropped, (0, 0))
            else:
                # Centre verticalement avec fond flouté
                bg = img.resize((target_w, target_h), Image.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
                from PIL import ImageEnhance
                bg = ImageEnhance.Brightness(bg).enhance(0.5)
                canvas.paste(bg, (0, 0))
                y = (target_h - new_h) // 2
                canvas.paste(img_resized, (0, y))

        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=92)
        return output.getvalue()

    except Exception as e:
        print(f"Erreur recadrage: {e}")
        return image_bytes

def upload_to_imgbb(image_bytes):
    """Upload une image sur imgbb et retourne l'URL publique"""
    try:
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        r = requests.post("https://api.imgbb.com/1/upload", data={
            "key":        IMGBB_KEY,
            "image":      img_b64,
            "expiration": 3600,
        })
        result = r.json()
        if result.get("success"):
            url = result["data"]["url"]
            print(f"Image uploadee: {url}")
            return url
        print(f"Erreur imgbb: {result}")
        return None
    except Exception as e:
        print(f"Erreur upload imgbb: {e}")
        return None

def process_image(image_url):
    """Telecharge, recadre en 4:5 et uploade sur imgbb"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(image_url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        cropped = crop_to_45(r.content)
        return upload_to_imgbb(cropped)
    except Exception as e:
        print(f"Erreur traitement image: {e}")
        return None

# ─── SCRAPING ─────────────────────────────────────────────────────────────────

def get_product_images(product_url):
    """Scrape toutes les images d'une fiche produit"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(product_url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        images = []
        selectors = [
            ".product-images img", ".product-cover img", ".product-thumbs img",
            "#product-images-large img", ".slick-slide img", ".owl-item img",
            "[data-image-large-src]", ".js-qv-product-cover img", ".product-image img",
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

def get_next_product():
    """
    Recupere le prochain produit NON encore publie depuis loftattitude.com.
    Parcourt la liste des nouveautes jusqu'a trouver un produit inédit.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.loftattitude.com/fr/nouveaux-produits", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.select(".product-miniature")

        if not products:
            print("Aucun produit trouve sur la page nouveautes")
            return None

        print(f"{len(products)} produits trouves sur la page")

        for product in products:
            name_el  = product.select_one(".product-title")
            price_el = product.select_one(".price")
            img_el   = product.select_one("img")
            link_el  = product.select_one("a")

            img_url = img_el.get("data-src") or img_el.get("src") if img_el else ""
            if img_url and img_url.startswith("/"):
                img_url = "https://www.loftattitude.com" + img_url

            href = link_el.get("href", "") if link_el else ""
            product_url = href if href.startswith("http") else "https://www.loftattitude.com" + href

            if not product_url:
                continue

            # Verifie si ce produit a deja ete publie
            if already_published(product_url):
                print(f"Deja publie, on passe: {product_url}")
                continue

            # Nouveau produit trouve !
            print(f"Nouveau produit: {name_el.text.strip() if name_el else 'Inconnu'}")
            return {
                "nom":       name_el.text.strip()  if name_el  else "Nouveau produit design",
                "prix":      price_el.text.strip() if price_el else "",
                "image_url": img_url,
                "url":       product_url,
            }

        print("Tous les produits de la page ont deja ete publies !")
        return None

    except Exception as e:
        print(f"Erreur scraping produit: {e}")
        return None

# ─── CAPTION ──────────────────────────────────────────────────────────────────

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

# ─── PUBLICATION ──────────────────────────────────────────────────────────────

def publish_instagram(images_urls, caption):
    """Publie sur Instagram (simple ou carrousel)"""
    if not images_urls or not IG_TOKEN:
        return False

    if len(images_urls) == 1:
        r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
            "image_url": images_urls[0], "caption": caption, "access_token": IG_TOKEN,
        })
        result1 = r1.json()
        if "id" not in result1:
            print(f"Erreur Instagram: {result1}")
            return False
        time.sleep(30)
        r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
            "creation_id": result1["id"], "access_token": IG_TOKEN,
        })
        result2 = r2.json()
        if "id" in result2:
            print(f"Instagram OK ! ID: {result2['id']}")
            return True
        return False

    children_ids = []
    for i, img_url in enumerate(images_urls):
        print(f"  Container Instagram {i+1}...")
        r = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
            "image_url": img_url, "is_carousel_item": "true", "access_token": IG_TOKEN,
        })
        result = r.json()
        if "id" in result:
            children_ids.append(result["id"])
        else:
            print(f"  Erreur: {result}")

    if not children_ids:
        return False

    print(f"{len(children_ids)} containers. Attente 30s...")
    time.sleep(30)

    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "media_type": "CAROUSEL", "children": ",".join(children_ids),
        "caption": caption, "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    if "id" not in result2:
        print(f"Erreur carrousel: {result2}")
        return False

    time.sleep(10)
    r3 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id": result2["id"], "access_token": IG_TOKEN,
    })
    result3 = r3.json()
    if "id" in result3:
        print(f"Instagram carrousel OK ! ID: {result3['id']}")
        return True
    print(f"Erreur publication: {result3}")
    return False

def publish_facebook(images_urls, caption, product_url):
    """Publie sur la Page Facebook"""
    if not FB_PAGE_ID or not FB_TOKEN:
        print("Facebook non configure - ignore")
        return False
    try:
        print("Publication Facebook...")
        if len(images_urls) == 1:
            r = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/photos", data={
                "url": images_urls[0],
                "caption": caption + f"\n\n🔗 {product_url}",
                "access_token": FB_TOKEN,
            })
            result = r.json()
            if "id" in result:
                print(f"Facebook OK ! ID: {result['id']}")
                return True
            print(f"Erreur Facebook: {result}")
            return False

        photo_ids = []
        for i, img_url in enumerate(images_urls):
            print(f"  Upload Facebook photo {i+1}...")
            r = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/photos", data={
                "url": img_url, "published": "false", "access_token": FB_TOKEN,
            })
            result = r.json()
            if "id" in result:
                photo_ids.append({"media_fbid": result["id"]})

        if not photo_ids:
            return False

        r2 = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/feed", data={
            "message": caption + f"\n\n🔗 {product_url}",
            "attached_media": json.dumps(photo_ids),
            "access_token": FB_TOKEN,
        })
        result2 = r2.json()
        if "id" in result2:
            print(f"Facebook OK avec {len(photo_ids)} photos ! ID: {result2['id']}")
            return True
        print(f"Erreur Facebook: {result2}")
        return False
    except Exception as e:
        print(f"Erreur Facebook: {e}")
        return False

# ─── JOB PRINCIPAL ────────────────────────────────────────────────────────────

def daily_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{now}] Debut publication Loft Attitude")
    print(f"{'='*50}")

    # 1. Prochain produit non publie
    product = get_next_product()
    if not product:
        print("Pas de nouveau produit a publier aujourd'hui.")
        return
    print(f"Produit: {product['nom']} | Prix: {product['prix']}")
    print(f"URL: {product['url']}")

    # 2. Images de la fiche produit
    all_images = get_product_images(product["url"]) if product["url"] else []
    if not all_images and product["image_url"]:
        all_images = [product["image_url"]]

    # 3. Selection lifestyle par IA
    best_images = select_best_images(all_images, max_images=5)
    if not best_images:
        print("Pas d'images.")
        return

    # 4. Recadrage 4:5 sans bandes blanches + upload imgbb
    print("\nRecadrage 4:5 et upload...")
    processed_urls = []
    for i, img_url in enumerate(best_images):
        print(f"  Traitement image {i+1}...")
        public_url = process_image(img_url)
        if public_url:
            processed_urls.append(public_url)
        else:
            processed_urls.append(img_url)

    if not processed_urls:
        print("Pas d'images traitees.")
        return

    print(f"{len(processed_urls)} images pretes (4:5, sans bandes)")

    # 5. Caption
    caption = generate_caption(product)
    print(f"Caption: {len(caption)} caracteres")

    # 6. Publication Instagram
    print("\n--- INSTAGRAM ---")
    ig_ok = publish_instagram(processed_urls, caption)
    print("Instagram: OK" if ig_ok else "Instagram: ECHEC")

    # 7. Publication Facebook
    print("\n--- FACEBOOK ---")
    fb_ok = publish_facebook(processed_urls, caption, product["url"])
    print("Facebook: OK" if fb_ok else "Facebook: ECHEC ou non configure")

    # 8. Marquer comme publie SEULEMENT si Instagram a reussi
    if ig_ok:
        mark_as_published(product["url"])

    print(f"\nResultat: Instagram={'OK' if ig_ok else 'ECHEC'} | Facebook={'OK' if fb_ok else 'ECHEC'}")

# ─── LANCEMENT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Bot Loft Attitude v5 - Anti-doublon + 4:5 sans bandes + Lifestyle + Carrousel")
    print(f"IG_USER_ID:  {IG_USER_ID}")
    print(f"IMGBB:       {'OK' if IMGBB_KEY else 'MANQUANT'}")
    print(f"FB_PAGE_ID:  {FB_PAGE_ID or 'non configure'}")
    print(f"Token IG:    {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude:      {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print("Publication planifiee a 09:00\n")
    daily_job()
    schedule.every().day.at("09:00").do(daily_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
