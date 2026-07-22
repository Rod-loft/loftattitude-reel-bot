import os, time, requests, schedule, anthropic, json, base64, io, re, random, threading
import numpy as np
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, send_from_directory

IG_USER_ID   = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN     = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
IMGBB_KEY    = os.environ.get("IMGBB_API_KEY", "")
FB_PAGE_ID   = os.environ.get("FB_PAGE_ID", "100063636817093")
FB_TOKEN     = os.environ.get("FB_PAGE_TOKEN", "")
IG_BASE      = "https://graph.instagram.com/v21.0"
FB_BASE      = "https://graph.facebook.com/v21.0"
HISTORY_FILE = "/tmp/published_products.json"
STORY_HISTORY_FILE = os.path.join(os.path.dirname(HISTORY_FILE), "published_stories.json")

# ─── STORIES : MARQUES CIBLEES ─────────────────────────────────────────────────

STORY_BRAND_URLS = [
    "https://www.loftattitude.com/fr/brand/18-camino-a-casa",
    "https://www.loftattitude.com/fr/brand/7-kare-design",
    "https://www.loftattitude.com/fr/brand/62-villeroy-boch",
]
STORY_MIN_PRICE = 100.0
STORY_SLIDE_COUNT = 4
STORY_SLIDE_DURATION = 3.5
STORY_VIDEO_DIR = "/tmp/story_videos"
STORY_SLIDES_DIR = "/tmp/story_slides"
def _resolve_music_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "assets", "story_music.mp3"),
        os.path.join(base_dir, "story_music.mp3"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]

MUSIC_PATH = _resolve_music_path()
os.makedirs(STORY_VIDEO_DIR, exist_ok=True)
os.makedirs(STORY_SLIDES_DIR, exist_ok=True)

# ─── SERVEUR FLASK : HEBERGEMENT DES VIDEOS STORIES ────────────────────────────

flask_app = Flask(__name__)

@flask_app.route("/video/<path:filename>")
def serve_story_video(filename):
    return send_from_directory(STORY_VIDEO_DIR, filename)

@flask_app.route("/health")
def health_check():
    return "OK"

def start_flask_server():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

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

def load_story_history():
    try:
        with open(STORY_HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_story_history(history):
    try:
        with open(STORY_HISTORY_FILE, "w") as f:
            json.dump(history[-200:], f)
    except Exception as e:
        print(f"Erreur sauvegarde historique stories: {e}")

def already_in_story(product_url):
    return product_url in load_story_history()

def mark_as_storied(product_url):
    history = load_story_history()
    if product_url not in history:
        history.append(product_url)
        save_story_history(history)
    print(f"Produit marque comme publie en story: {product_url}")

# ─── TRAITEMENT IMAGE ─────────────────────────────────────────────────────────

def trim_white_borders(img, threshold=230, min_band=30):
    """Supprime uniquement les vraies bandes blanches larges (>30px)"""
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

        # Ne rogne que si les bandes sont vraiment larges
        crop_top    = top    if top    > min_band else 0
        crop_bottom = bottom if (img.height - bottom) > min_band else img.height
        crop_left   = left   if left   > min_band else 0
        crop_right  = right  if (img.width - right) > min_band else img.width

        # Securite : verifie que le crop est valide
        if crop_left >= crop_right or crop_top >= crop_bottom:
            return img

        if crop_top > 0 or crop_bottom < img.height or crop_left > 0 or crop_right < img.width:
            print(f"  Bandes supprimees (haut:{crop_top} bas:{img.height-crop_bottom} gauche:{crop_left} droite:{img.width-crop_right})")
            return img.crop((crop_left, crop_top, crop_right, crop_bottom))

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
    - Lifestyle (fond colore) : AUCUN rognage, juste recadrage 4:5 au centre
    - Detouré (fond blanc)   : rognage bandes, produit entier centré
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        print(f"  Taille originale: {w}x{h}")

        if w <= 0 or h <= 0:
            return image_bytes

        target_w, target_h = 1080, 1350
        target_ratio = target_w / target_h

        if h == 0:
            return image_bytes

        # Detecte d'abord si fond blanc sur l'image originale
        fond_blanc = is_white_background(img)
        print(f"  Type: {'detouré fond blanc' if fond_blanc else 'lifestyle (pas de rognage)'}")

        # Rognage UNIQUEMENT pour les photos detourees sur fond blanc
        if fond_blanc:
            img = trim_white_borders(img)
            w, h = img.size
            if w <= 0 or h <= 0:
                img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                w, h = img.size
            print(f"  Apres rognage: {w}x{h}")
        # Pour les photos lifestyle : on garde l'image telle quelle
        else:
            w, h = img.size

        if h == 0:
            return image_bytes
        src_ratio = w / h

        if fond_blanc:
            # Produit détouré : entier, centré, fond blanc, marge 60px
            canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            margin = 60
            max_w = target_w - margin * 2
            max_h = target_h - margin * 2

            # Securite
            if w == 0 or h == 0:
                return image_bytes

            scale = min(max_w / w, max_h / h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            x = (target_w - new_w) // 2
            y = (target_h - new_h) // 2
            canvas.paste(img_resized, (x, y))
            img_final = canvas
            print(f"  → Produit entier {new_w}x{new_h} sur fond blanc")

        else:
            # Photo lifestyle : recadre en perdant le moins possible
            ratio_diff = abs(src_ratio - target_ratio) / target_ratio if target_ratio > 0 else 1

            if ratio_diff < 0.15:
                # Ratio proche 4:5 : redimensionne direct
                img_final = img.resize((target_w, target_h), Image.LANCZOS)
                print(f"  → Redimensionne direct (ratio proche)")

            elif src_ratio > target_ratio:
                # Paysage : coupe les côtés max 15%
                new_h = target_h
                new_w = max(1, int(new_h * src_ratio))
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                excess = new_w - target_w
                if excess <= 0:
                    # Pas assez large, centre avec fond blanc
                    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                    x = (target_w - new_w) // 2
                    canvas.paste(img_resized, (x, 0))
                    img_final = canvas
                else:
                    max_cut = int(new_w * 0.15)
                    left = min(excess // 2, max_cut)
                    right_crop = left + target_w
                    if right_crop > new_w:
                        right_crop = new_w
                        left = max(0, right_crop - target_w)
                    img_final = img_resized.crop((left, 0, right_crop, target_h))
                print(f"  → Lifestyle paysage")

            else:
                # Portrait : coupe haut/bas max 20%
                new_w = target_w
                new_h = max(1, int(new_w / src_ratio)) if src_ratio > 0 else target_h
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                excess = new_h - target_h
                if excess <= 0:
                    # Pas assez grand, centre avec fond blanc
                    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                    y = (target_h - new_h) // 2
                    canvas.paste(img_resized, (0, y))
                    img_final = canvas
                else:
                    max_cut = int(new_h * 0.20)
                    top = min(excess // 2, max_cut)
                    bottom_crop = top + target_h
                    if bottom_crop > new_h:
                        bottom_crop = new_h
                        top = max(0, bottom_crop - target_h)
                    img_final = img_resized.crop((0, top, target_w, bottom_crop))
                print(f"  → Lifestyle portrait")

        output = io.BytesIO()
        img_final.save(output, format="JPEG", quality=92)
        return output.getvalue()

    except Exception as e:
        print(f"Erreur recadrage: {e}")
        return image_bytes

def crop_to_916(image_bytes):
    """
    Recadre en 9:16 (1080x1920) pour les Stories Instagram :
    - Lifestyle (fond colore) : recadrage centre, perte minimale
    - Detouré (fond blanc)   : produit entier centré sur fond blanc
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return image_bytes

        target_w, target_h = 720, 1280
        target_ratio = target_w / target_h

        fond_blanc = is_white_background(img)
        if fond_blanc:
            img = trim_white_borders(img)
            w, h = img.size
            if w <= 0 or h <= 0:
                img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                w, h = img.size

        if h == 0:
            return image_bytes
        src_ratio = w / h

        if fond_blanc:
            canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            margin_x, margin_y = 80, 260
            max_w = target_w - margin_x * 2
            max_h = target_h - margin_y * 2
            scale = min(max_w / w, max_h / h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            x = (target_w - new_w) // 2
            y = (target_h - new_h) // 2
            canvas.paste(img_resized, (x, y))
            img_final = canvas
        else:
            if src_ratio > target_ratio:
                # Image plus large que 9:16 : on comble en hauteur, on coupe les cotes
                new_h = target_h
                new_w = max(1, int(new_h * src_ratio))
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                x = (new_w - target_w) // 2
                img_final = img_resized.crop((x, 0, x + target_w, target_h))
            else:
                # Image plus haute/étroite que 9:16 : on comble en largeur, on coupe haut/bas
                new_w = target_w
                new_h = max(1, int(new_w / src_ratio)) if src_ratio > 0 else target_h
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                y = max(0, (new_h - target_h) // 2)
                img_final = img_resized.crop((0, y, target_w, min(y + target_h, new_h)))
                if img_final.size != (target_w, target_h):
                    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                    canvas.paste(img_final, (0, (target_h - img_final.height) // 2))
                    img_final = canvas

        output = io.BytesIO()
        img_final.save(output, format="JPEG", quality=92)
        return output.getvalue()
    except Exception as e:
        print(f"Erreur recadrage story: {e}")
        return image_bytes

def _load_story_font(size):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

def add_story_overlay(image_bytes, product):
    """
    Ajoute un bandeau en bas de la story : nom du produit, prix, et appel a l'action.
    Tout est proportionnel a la hauteur de l'image pour rester lisible quelle que soit la resolution.
    Meta ne permet pas de sticker lien cliquable via l'API -> texte incruste a la place.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        band_top = h - int(h * 0.40)
        solid_top = h - int(h * 0.26)
        for y in range(band_top, solid_top):
            ratio = (y - band_top) / max(1, (solid_top - band_top))
            alpha = int(200 * ratio)
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
        draw.rectangle([0, solid_top, w, h], fill=(0, 0, 0, 225))

        name_font  = _load_story_font(max(28, int(h * 0.032)))
        price_font = _load_story_font(max(34, int(h * 0.040)))
        cta_font   = _load_story_font(max(20, int(h * 0.023)))

        name = product.get("nom", "")[:60]
        prix = product.get("prix", "")
        margin_x = int(w * 0.07)

        draw.text((margin_x, h - int(h * 0.195)), name, font=name_font, fill=(255, 255, 255, 255))
        if prix:
            draw.text((margin_x, h - int(h * 0.140)), prix, font=price_font, fill=(255, 255, 255, 255))
        draw.text((margin_x, h - int(h * 0.070)), "Decouvrir -> loftattitude.com", font=cta_font, fill=(225, 225, 225, 255))

        final_img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        output = io.BytesIO()
        final_img.save(output, format="JPEG", quality=92)
        return output.getvalue()
    except Exception as e:
        print(f"Erreur overlay story: {e}")
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
                    {"type": "text", "text": 'Analyse cette image produit. Reponds UNIQUEMENT en JSON: {"lifestyle": true/false, "score": 0-10, "produit_entier": true/false}\nlifestyle=true si photo dans un interieur/ambiance/mise en scene avec le produit visible\nlifestyle=false si fond blanc/uni/produit seul detouré\nproduit_entier=true si on voit le produit en entier, false si cest un detail/zoom/texture\nscore: qualite visuelle Instagram (penalise fortement les zooms sur details/textures, favorise les vues completes du produit)'}
                ]
            }]
        )
        result = json.loads(msg.content[0].text.replace("```json","").replace("```","").strip())
        return result.get("lifestyle", False), result.get("score", 0), result.get("produit_entier", True)
    except Exception as e:
        print(f"Erreur analyse image: {e}")
        return False, 0, True

def select_best_images(images, max_images=5):
    if not images:
        return []
    print(f"Analyse IA de {min(len(images), 8)} images...")
    scored = []
    for i, img_url in enumerate(images[:8]):
        print(f"  Image {i+1}...")
        is_lifestyle, score, produit_entier = is_lifestyle_image(img_url)
        # Bonus lifestyle + bonus produit entier, malus si detail/zoom
        final_score = score + (5 if is_lifestyle else 0) + (3 if produit_entier else -4)
        scored.append({"url": img_url, "lifestyle": is_lifestyle, "score": final_score, "entier": produit_entier})
        print(f"  -> Lifestyle: {is_lifestyle}, Entier: {produit_entier}, Score: {final_score}")
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = [item["url"] for item in scored[:max_images]]
    lifestyle_count = sum(1 for item in scored[:max_images] if item["lifestyle"])
    print(f"Selection: {len(best)} images ({lifestyle_count} lifestyle)")
    return best

def parse_price(text):
    try:
        match = re.search(r"(\d[\d\s\u00a0\u202f]*)[,.](\d{2})", text)
        if match:
            integer_part = re.sub(r"\D", "", match.group(1))
            return float(f"{integer_part}.{match.group(2)}")
        digits = re.sub(r"\D", "", text)
        return float(digits) if digits else 0.0
    except Exception:
        return 0.0

def get_story_candidates(n=STORY_SLIDE_COUNT):
    """Parcourt les 3 pages marques ciblees, filtre les produits >100€ pas encore en story,
    et renvoie un mix de n produits differents (marques melangees)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        candidates = []
        for brand_url in STORY_BRAND_URLS:
            base_url = brand_url.split("?")[0]
            for page in range(1, 6):
                url = base_url if page == 1 else f"{base_url}?page={page}"
                r = requests.get(url, headers=headers, timeout=15)
                soup = BeautifulSoup(r.text, "html.parser")
                products = soup.select(".product-miniature")
                print(f"  {base_url.split('/')[-1]} p{page}: {len(products)} produits")
                if not products:
                    break
                for product in products:
                    name_el  = product.select_one(".product-title")
                    link_el  = product.select_one("a")
                    img_el   = product.select_one("img")
                    text_block = product.get_text(" ", strip=True)
                    price_match = re.search(r"(\d[\d\s\u00a0\u202f]{0,6}[,.]\d{2})\s*€", text_block)
                    if not price_match:
                        continue
                    prix_val = parse_price(price_match.group(1))
                    if prix_val < STORY_MIN_PRICE:
                        continue
                    href = link_el.get("href", "") if link_el else ""
                    product_url = href if href.startswith("http") else "https://www.loftattitude.com" + href
                    if not product_url or already_in_story(product_url):
                        continue
                    img_url = img_el.get("data-src") or img_el.get("src") if img_el else ""
                    if img_url and img_url.startswith("/"):
                        img_url = "https://www.loftattitude.com" + img_url
                    if not img_url:
                        continue
                    candidates.append({
                        "nom":       name_el.text.strip() if name_el else "Produit design",
                        "prix":      price_match.group(1).strip() + " €",
                        "prix_val":  prix_val,
                        "image_url": img_url,
                        "url":       product_url,
                    })
                if len(products) < 12:
                    break
        if not candidates:
            print("Aucun candidat story disponible (tout deja publie ou <100€).")
            return []
        # On privilegie les prix eleves tout en melangeant les marques
        candidates.sort(key=lambda p: p["prix_val"], reverse=True)
        top_pool = candidates[:max(n * 4, 12)]
        random.shuffle(top_pool)
        return top_pool[:n]
    except Exception as e:
        print(f"Erreur scraping stories: {e}")
        return []

def build_story_slideshow(products):
    """Construit une video verticale (9:16) enchainant les photos des produits,
    avec overlay nom/prix par slide et musique d'ambiance."""
    from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, afx
    clips = []
    for i, product in enumerate(products):
        image_url = product.get("image_url")
        if not image_url:
            continue
        try:
            r = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code != 200:
                continue
            slide_bytes = crop_to_916(r.content)
            # Plus d'overlay texte/ombre : uniquement l'image du produit
            slide_path = os.path.join(STORY_SLIDES_DIR, f"slide_{i}_{int(time.time())}.jpg")
            with open(slide_path, "wb") as f:
                f.write(slide_bytes)
            clips.append(ImageClip(slide_path).with_duration(STORY_SLIDE_DURATION))
        except Exception as e:
            print(f"Erreur slide {i}: {e}")
    if not clips:
        return None
    try:
        video = concatenate_videoclips(clips, method="compose")
        if os.path.exists(MUSIC_PATH):
            try:
                audio = AudioFileClip(MUSIC_PATH)
                duration = min(video.duration, audio.duration)
                audio = audio.subclipped(0, duration)
                audio = audio.with_effects([afx.AudioFadeOut(1.0)])
                video = video.with_duration(duration).with_audio(audio)
            except Exception as e:
                print(f"Erreur ajout musique: {e}")
        else:
            print(f"Musique introuvable a {MUSIC_PATH}, video sans son.")
        filename = f"story_{int(time.time())}.mp4"
        output_path = os.path.join(STORY_VIDEO_DIR, filename)
        video.write_videofile(
            output_path, fps=15, codec="libx264", audio_codec="aac",
            preset="ultrafast", threads=1, bitrate="1500k", logger=None,
        )
        return filename
    except Exception as e:
        print(f"Erreur encodage video story: {e}")
        return None

def publish_instagram_story_video(video_url):
    if not video_url or not IG_TOKEN:
        return False
    r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "video_url": video_url, "media_type": "STORIES", "access_token": IG_TOKEN,
    })
    result1 = r1.json()
    if "id" not in result1:
        print(f"Erreur creation story video: {result1}")
        return False
    creation_id = result1["id"]
    for attempt in range(20):
        time.sleep(10)
        status_r = requests.get(f"{IG_BASE}/{creation_id}", params={
            "fields": "status_code", "access_token": IG_TOKEN,
        })
        status = status_r.json().get("status_code")
        print(f"Statut video story: {status} (tentative {attempt + 1})")
        if status == "FINISHED":
            break
        if status == "ERROR":
            print("Erreur traitement video Meta.")
            return False
    else:
        print("Timeout traitement video story.")
        return False
    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id": creation_id, "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    if "id" in result2:
        print(f"Story video Instagram OK ! ID: {result2['id']}")
        return True
    print(f"Erreur publication story video: {result2}")
    return False

def story_job():
    """Ne doit jamais lever d'exception : un echec ici ne doit pas arreter le bot
    ni empecher les publications feed planifiees."""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'-'*50}\n[{now}] Story Loft Attitude\n{'-'*50}")
        products = get_story_candidates(STORY_SLIDE_COUNT)
        if not products:
            print("Pas de candidats story.")
            return
        for p in products:
            print(f"Slide: {p['nom']} | {p['prix']}")
        filename = build_story_slideshow(products)
        if not filename:
            print("Echec generation video story.")
            return
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url:
            print("PUBLIC_BASE_URL manquant, impossible d'heberger la video.")
            return
        video_url = f"{base_url}/video/{filename}"
        print(f"Video hebergee: {video_url}")
        ok = publish_instagram_story_video(video_url)
        if ok:
            for p in products:
                mark_as_storied(p["url"])
        print(f"Story: {'OK' if ok else 'ECHEC'}")
    except Exception as e:
        print(f"Erreur story_job (ignoree, le bot continue): {e}")

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
    if not FB_PAGE_ID or not FB_TOKEN:
        print("Facebook non configure - FB_PAGE_ID ou FB_PAGE_TOKEN manquant")
        return False
    try:
        if not FB_TOKEN:
            print("FB_PAGE_TOKEN manquant dans les variables")
            return False
        page_token = FB_TOKEN
        print(f"Publication Facebook sur {FB_PAGE_ID} avec FB_PAGE_TOKEN...")
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
    print("\nTraitement images...")
    processed_urls = []
    for i, img_url in enumerate(best_images):
        print(f"  Image {i+1}: {img_url[:70]}...")
        public_url = process_image(img_url)
        processed_urls.append(public_url if public_url else img_url)
    processed_urls = [u for u in processed_urls if u]
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
    print("Bot Loft Attitude v15 - Lifestyle sans rognage / Detouré rognage+centrage")
    print(f"IG_USER_ID:  {IG_USER_ID}")
    print(f"FB_PAGE_ID:  {FB_PAGE_ID}")
    print(f"IMGBB:       {'OK' if IMGBB_KEY else 'MANQUANT'}")
    print(f"Token IG:    {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude:      {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print("Publication feed planifiee a 09:00")
    print("Stories planifiees a 11:00, 14:00, 17:00, 20:00\n")
    threading.Thread(target=start_flask_server, daemon=True).start()
    print(f"Serveur video demarre sur le port {os.environ.get('PORT', 8080)}\n")
    try:
        daily_job()
    except Exception as e:
        print(f"Erreur daily_job au demarrage (ignoree): {e}")
    if os.environ.get("TEST_STORY_NOW") == "1":
        print("\nTEST_STORY_NOW=1 detecte -> declenchement story manuel\n")
        story_job()
    schedule.every().day.at("09:00").do(daily_job)
    schedule.every().day.at("11:00").do(story_job)
    schedule.every().day.at("14:00").do(story_job)
    schedule.every().day.at("17:00").do(story_job)
    schedule.every().day.at("20:00").do(story_job)
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Erreur boucle planificateur (ignoree): {e}")
        time.sleep(60)
