import os, time, requests, schedule, anthropic, json, base64, io
import numpy as np
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image

IG_USER_ID   = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN     = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
IMGBB_KEY    = os.environ.get("IMGBB_API_KEY", "")
FB_PAGE_ID   = os.environ.get("FB_PAGE_ID", "100063636817093")
IG_BASE      = "https://graph.instagram.com/v21.0"
FB_BASE      = "https://graph.facebook.com/v21.0"
HISTORY_FILE = "/tmp/published_products.json"

# ─── HISTORIQUE ───────────────────────────────────────────────────────────────

def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_history(history):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[-50:], f)
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")

def already_published(product_url):
    return product_url in load_history()

def mark_as_published(product_url):
    history = load_history()
    if product_url not in history:
        history.append(product_url)
        save_history(history)
    print(f"Produit marque comme publie: {product_url}")

# ─── TRAITEMENT IMAGE ─────────────────────────────────────────────────────────

def trim_white_borders(img, threshold=230, min_band=30):
    """
    Supprime UNIQUEMENT les vraies bandes blanches larges (>30px).
    Ne touche pas aux images sans bandes significatives.
    """
    try:
        arr = np.array(img.convert("L"))
        mask = arr < threshold
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return img

        top    = int(np.argmax(rows))
        bottom = int(len(rows) - np.argmax(rows[::-1]))
        left   = int(np.argmax(cols))
        right  = int(len(cols) - np.argmax(cols[::-1]))

        # Ne rogne QUE si les bandes sont vraiment larges (>30px)
        actual_top    = top    if top    > min_band else 0
        actual_bottom = img.height - bottom if (img.height - bottom) > min_band else img.height
        actual_left   = left   if left   > min_band else 0
        actual_right  = img.width - right if (img.width - right) > min_band else img.width

        if actual_top > 0 or actual_bottom < img.height or actual_left > 0 or actual_right < img.width:
            print(f"  Bandes supprimees (haut:{actual_top} bas:{img.height-actual_bottom} gauche:{actual_left} droite:{img.width-actual_right})")
            return img.crop((actual_left, actual_top, actual_right, actual_bottom))

        return img
    except Exception as e:
        print(f"  Erreur trim: {e}")
        return img

def is_white_background(img, threshold=242, min_ratio=0.35):
    """Detecte si l'image a un fond blanc (produit detouré)"""
    try:
        arr = np.array(img.convert("L"))
        white_pixels = np.sum(arr >= threshold)
        ratio = white_pixels / arr.size
        print(f"  Ratio pixels blancs: {ratio:.2f}")
        return ratio > min_ratio
    except:
        return False

def crop_to_45(image_bytes):
    """
    Recadre en 4:5 (1080x1350) intelligemment :
    - Lifestyle → recadre au centre en conservant un max de l'image
    - Détouré (fond blanc) → produit ENTIER centré avec marge, rien coupé
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        print(f"  Taille originale: {img.size}")

        # Supprime uniquement les vraies grandes bandes blanches
        img = trim_white_borders(img)
        print(f"  Apres rognage: {img.size}")

        target_w, target_h = 1080, 1350
        target_ratio = target_w / target_h  # 0.8
        w, h = img.size
        src_ratio = w / h

        fond_blanc = is_white_background(img)
        print(f"  Type: {'detouré fond blanc' if fond_blanc else 'lifestyle/couleur'}")

        if fond_blanc:
            # Produit détouré : ENTIER, centré, marge confortable, rien coupé
            canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            margin = 60  # marge de 60px de chaque côté
            max_w = target_w - margin * 2
            max_h = target_h - margin * 2
            scale = min(max_w / w, max_h / h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            x = (target_w - new_w) // 2
            y = (target_h - new_h) // 2
            canvas.paste(img_resized, (x, y))
            img_final = canvas
            print(f"  → Produit entier {new_w}x{new_h} sur fond blanc")

        else:
            # Photo lifestyle : recadre intelligemment
            # Si l'image est proche du ratio 4:5, on la redimensionne sans couper
            ratio_diff = abs(src_ratio - target_ratio) / target_ratio

            if ratio_diff < 0.15:
                # Ratio proche de 4:5 (<15% d'écart) : juste redimensionner
                img_final = img.resize((target_w, target_h), Image.LANCZOS)
                print(f"  → Lifestyle: redimensionne direct (ratio proche 4:5)")

            elif src_ratio > target_ratio:
                # Image paysage : coupe les côtés (max 15% de chaque côté)
                new_h = target_h
                new_w = int(new_h * src_ratio)
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                # Coupe max 15% de chaque côté
                max_cut = int(new_w * 0.15)
                left = min((new_w - target_w) // 2, max_cut)
                # Si ça ne suffit pas, on ajoute fond blanc sur les côtés
                if new_w - 2 * left < target_w:
                    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                    x = (target_w - new_w) // 2
                    canvas.paste(img_resized, (x, 0))
                    img_final = canvas
                else:
                    img_final = img_resized.crop((left, 0, left + target_w, target_h))
                print(f"  → Lifestyle paysage: coupe {left}px de chaque côté")

            else:
                # Image portrait/carrée : coupe haut/bas (max 20% de chaque côté)
                new_w = target_w
                new_h = int(new_w / src_ratio)
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                max_cut = int(new_h * 0.20)
                top = min((new_h - target_h) // 2, max_cut)
                if top < 0: top = 0
                bottom = top + target_h
                if bottom > new_h:
                    # Pas assez grand, on centre avec fond blanc
                    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                    y = (target_h - new_h) // 2
                    canvas.paste(img_resized, (0, y))
                    img_final = canvas
                else:
                    img_final = img_resized.crop((0, top, target_w, bottom))
                print(f"  → Lifestyle portrait: coupe {top}px haut/bas")

        output = io.BytesIO()
        img_final.save(output, format="JPEG", quality=92)
        return output.getvalue()

    except Exception as e:
        print(f"Erreur recadrage: {e}")
        return image_bytes

def upload_to_imgbb(image_bytes):
    try:
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        r = requests.post("https://api.imgbb.com/1/upload", data={
            "key": IMGBB_KEY, "image": img_b64, "expiration": 3600,
        })
        result = r.json()
        if result.get("success"):
            url = result["data"]["url"]
            print(f"  Uploadee: {url}")
            return url
        print(f"  Erreur imgbb: {result}")
        return None
    except Exception as e:
        print(f"  Erreur upload: {e}")
        return None

def process_image(image_url):
    try:
        r = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            return None
        return upload_to_imgbb(crop_to_45(r.content))
    except Exception as e:
        print(f"Erreur traitement: {e}")
        return None

# ─── SCRAPING ─────────────────────────────────────────────────────────────────

def get_product_images(product_url):
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
                        src_large = src.replace("-small","").replace("-medium","").replace("_small","").replace("_medium","")
                        images.append(src_large)
        print(f"Images trouvees: {len(images)}")
        return images
    except Exception as e:
        print(f"Erreur scraping images: {e}")
        return []

def is_lifestyle_image(image_url):
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
        result = json.loads(msg.content[0].text.replace("```json","").replace("```","").strip())
        return result.get("lifestyle", False), result.get("score", 0)
    except Exception as e:
        print(f"Erreur analyse image: {e}")
        return False, 0

def select_best_images(images, max_images=5):
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
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.loftattitude.com/fr/nouveaux-produits", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.select(".product-miniature")
        if not products:
            print("Aucun produit trouve")
            return None
        print(f"{len(products)} produits trouves")
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
            if already_published(product_url):
                print(f"Deja publie: {product_url}")
                continue
            print(f"Nouveau produit: {name_el.text.strip() if name_el else 'Inconnu'}")
            return {
                "nom":       name_el.text.strip()  if name_el  else "Nouveau produit design",
                "prix":      price_el.text.strip() if price_el else "",
                "image_url": img_url,
                "url":       product_url,
            }
        print("Tous les produits ont deja ete publies !")
        return None
    except Exception as e:
        print(f"Erreur scraping: {e}")
        return None

def generate_caption(product):
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system='Tu es expert marketing Instagram pour "Loft Attitude", boutique de meubles et objets design loft, industriel et contemporain. Reponds UNIQUEMENT en JSON valide sans markdown: {"caption":"caption Instagram 120-150 mots avec emojis, storytelling produit, ambiance design, termine TOUJOURS par : Retrouvez ce produit via le lien en bio 👆 loftattitude.com","hashtags":["25 hashtags pertinents"]}',
            messages=[{"role": "user", "content": f"Produit: {product['nom']}\nPrix: {product['prix']}\nURL: {product['url']}"}]
        )
        data = json.loads(msg.content[0].text.replace("```json","").replace("```","").strip())
        return data["caption"] + "\n\n" + " ".join(data["hashtags"])
    except Exception as e:
        print(f"Erreur caption: {e}")
        return f"Nouvelle arrivee chez Loft Attitude ! {product['nom']} - {product['prix']}\nRetrouvez ce produit via le lien en bio 👆 loftattitude.com\n\n#loftattitude #design #deco #meuble #loftdesign"

def publish_instagram(images_urls, caption):
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
    print(f"Erreur: {result3}")
    return False

def publish_facebook(images_urls, caption, product_url):
    if not FB_PAGE_ID or not IG_TOKEN:
        print("Facebook non configure")
        return False
    try:
        print(f"Publication Facebook sur {FB_PAGE_ID}...")
        r_pages = requests.get(f"{FB_BASE}/me/accounts", params={"access_token": IG_TOKEN})
        pages = r_pages.json().get("data", [])
        page_token = None
        for page in pages:
            if page.get("id") == FB_PAGE_ID:
                page_token = page.get("access_token")
                print(f"Token Page trouve: {page.get('name')}")
                break
        if not page_token:
            print(f"Token Page non trouve. Pages: {[p.get('name') for p in pages]}")
            return False
        if len(images_urls) == 1:
            r = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/photos", data={
                "url": images_urls[0], "caption": caption + f"\n\n🔗 {product_url}",
                "access_token": page_token,
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
                "url": img_url, "published": "false", "access_token": page_token,
            })
            result = r.json()
            if "id" in result:
                photo_ids.append({"media_fbid": result["id"]})
        if not photo_ids:
            return False
        r2 = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/feed", data={
            "message": caption + f"\n\n🔗 {product_url}",
            "attached_media": json.dumps(photo_ids),
            "access_token": page_token,
        })
        result2 = r2.json()
        if "id" in result2:
            print(f"Facebook OK ! ID: {result2['id']}")
            return True
        print(f"Erreur Facebook: {result2}")
        return False
    except Exception as e:
        print(f"Erreur Facebook: {e}")
        return False

def daily_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{now}] Debut publication Loft Attitude")
    print(f"{'='*50}")
    product = get_next_product()
    if not product:
        print("Pas de nouveau produit aujourd'hui.")
        return
    print(f"Produit: {product['nom']} | Prix: {product['prix']}")
    all_images = get_product_images(product["url"]) if product["url"] else []
    if not all_images and product["image_url"]:
        all_images = [product["image_url"]]
    best_images = select_best_images(all_images, max_images=5)
    if not best_images:
        print("Pas d'images.")
        return
    print("\nTraitement images (smart crop v11)...")
    processed_urls = []
    for i, img_url in enumerate(best_images):
        print(f"  Image {i+1}: {img_url[:70]}...")
        public_url = process_image(img_url)
        processed_urls.append(public_url if public_url else img_url)
    if not processed_urls:
        return
    print(f"{len(processed_urls)} images pretes")
    caption = generate_caption(product)
    print(f"Caption: {len(caption)} caracteres")
    print("\n--- INSTAGRAM ---")
    ig_ok = publish_instagram(processed_urls, caption)
    print("Instagram: OK" if ig_ok else "Instagram: ECHEC")
    print("\n--- FACEBOOK ---")
    fb_ok = publish_facebook(processed_urls, caption, product["url"])
    print("Facebook: OK" if fb_ok else "Facebook: ECHEC")
    if ig_ok:
        mark_as_published(product["url"])
    print(f"\nResultat: Instagram={'OK' if ig_ok else 'ECHEC'} | Facebook={'OK' if fb_ok else 'ECHEC'}")

if __name__ == "__main__":
    print("Bot Loft Attitude v11 - Recadrage intelligent, produit jamais coupe")
    print(f"IG_USER_ID:  {IG_USER_ID}")
    print(f"FB_PAGE_ID:  {FB_PAGE_ID}")
    print(f"IMGBB:       {'OK' if IMGBB_KEY else 'MANQUANT'}")
    print(f"Token IG:    {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude:      {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print("Publication planifiee a 09:00\n")
    daily_job()
    schedule.every().day.at("09:00").do(daily_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
