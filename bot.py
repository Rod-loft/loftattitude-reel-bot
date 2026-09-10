import os, time, requests, schedule, anthropic, json, base64, io, re, random, threading
import numpy as np
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    PARIS_TZ = ZoneInfo("Europe/Paris")
except ImportError:
    try:
        import pytz
        PARIS_TZ = pytz.timezone("Europe/Paris")
    except ImportError:
        PARIS_TZ = None
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from flask import Flask, send_from_directory

IG_USER_ID   = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN     = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
IMGBB_KEY    = os.environ.get("IMGBB_API_KEY", "")
OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")
FB_PAGE_ID   = os.environ.get("FB_PAGE_ID", "100063636817093")
FB_TOKEN     = os.environ.get("FB_PAGE_TOKEN", "")
IG_BASE      = "https://graph.instagram.com/v21.0"
FB_BASE      = "https://graph.facebook.com/v21.0"
DATA_DIR = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else "/tmp"
HISTORY_FILE = os.path.join(DATA_DIR, "published_products.json")
STORY_HISTORY_FILE = os.path.join(DATA_DIR, "published_stories.json")

# ─── STORIES : MARQUES CIBLEES ─────────────────────────────────────────────────

STORY_BRAND_URLS = [
    "https://www.loftattitude.com/fr/brand/18-camino-a-casa",
    "https://www.loftattitude.com/fr/brand/7-kare-design",
    "https://www.loftattitude.com/fr/brand/62-villeroy-boch",
    "https://www.loftattitude.com/fr/brand/9-blomus",
    "https://www.loftattitude.com/fr/brand/15-sompex",
    "https://www.loftattitude.com/fr/brand/66-richmond-interiors",
    "https://www.loftattitude.com/fr/brand/55-socadis",
]
STORY_MIN_PRICE = 100.0
STORY_SLIDE_COUNT = 8
STORY_SLIDE_DURATION = 1.7
STORY_MAX_AI_GENERATIONS = 2
REEL_SLIDE_DURATION = 2.5
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

def _find_visual_focus(img):
    """Retourne le point d'interet visuel principal sous la forme (x, y), entre 0 et 1."""
    preview = img.copy()
    preview.thumbnail((240, 240), Image.LANCZOS)
    gray_img = preview.convert("L")
    gray = np.asarray(gray_img, dtype=np.float32)
    if gray.size == 0:
        return 0.5, 0.5

    # Les contrastes locaux et les contours donnent une bonne approximation de la
    # zone importante, sans ajouter de dependance a un service d'analyse externe.
    blur_radius = max(2, min(preview.size) // 30)
    smooth = np.asarray(
        gray_img.filter(ImageFilter.GaussianBlur(blur_radius)), dtype=np.float32
    )
    detail = np.abs(gray - smooth)
    grad_y, grad_x = np.gradient(gray)
    saliency = detail + 0.35 * (np.abs(grad_x) + np.abs(grad_y))

    # Un leger biais central stabilise le cadrage sur les photos tres chargees,
    # tout en laissant un produit decentre attirer naturellement le recadrage.
    ph, pw = gray.shape
    yy, xx = np.mgrid[0:ph, 0:pw]
    distance = np.sqrt(
        ((xx - (pw - 1) / 2) / max(1, pw)) ** 2
        + ((yy - (ph - 1) / 2) / max(1, ph)) ** 2
    )
    saliency *= np.clip(1.0 - 0.45 * distance, 0.65, 1.0)
    cutoff = np.percentile(saliency, 60)
    weights = np.maximum(saliency - cutoff, 0)
    total = float(weights.sum())
    if total < 1e-6:
        return 0.5, 0.5

    focus_x = float((weights * xx).sum() / total) / max(1, pw - 1)
    focus_y = float((weights * yy).sum() / total) / max(1, ph - 1)
    return focus_x, focus_y


def _cover_story_frame(img, target_size, focus):
    """Remplit le cadre sans deformation en centrant le recadrage sur le point d'interet."""
    target_w, target_h = target_size
    w, h = img.size
    scale = max(target_w / w, target_h / h)
    new_w = max(target_w, int(w * scale + 0.5))
    new_h = max(target_h, int(h * scale + 0.5))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    focus_x = int(focus[0] * new_w)
    focus_y = int(focus[1] * new_h)
    left = min(max(focus_x - target_w // 2, 0), new_w - target_w)
    top = min(max(focus_y - target_h // 2, 0), new_h - target_h)
    return resized.crop((left, top, left + target_w, top + target_h))


def crop_to_916(image_bytes):
    """
    Prepare une image verticale 9:16 sans deformation ni bandes ajoutees.

    Un recadrage plein ecran guide par le point d'interet est utilise lorsque la
    perte reste moderee (20 % maximum). Si le recadrage couperait trop l'image,
    la photo complete et nette est centree sur un fond plein ecran cree a partir
    de la meme image agrandie et floutee. Le produit reste ainsi entier, sans
    grandes marges blanches, bandeau, degrade sombre ni texte incruste.
    """
    try:
        source = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(source).convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return image_bytes

        # Retire uniquement les bordures blanches techniques eventuelles avant
        # le cadrage ; aucune marge blanche n'est ensuite recreee.
        if is_white_background(img):
            trimmed = trim_white_borders(img)
            if trimmed.size[0] > 0 and trimmed.size[1] > 0:
                img = trimmed
                w, h = img.size

        target_size = (1080, 1920)
        target_w, target_h = target_size
        focus = _find_visual_focus(img)

        cover_scale = max(target_w / w, target_h / h)
        covered_w = w * cover_scale
        covered_h = h * cover_scale
        crop_loss = max(
            (covered_w - target_w) / covered_w,
            (covered_h - target_h) / covered_h,
        )

        if crop_loss <= 0.20:
            # Plein ecran avec un recadrage modere autour du sujet principal.
            img_final = _cover_story_frame(img, target_size, focus)
        else:
            # Le plein ecran couperait trop le produit : fond issu de la meme
            # photo, puis image complete et nette par-dessus, sans bandes blanches.
            background = _cover_story_frame(img, target_size, focus)
            blur_radius = max(24, int(max(target_size) * 0.025))
            background = background.filter(ImageFilter.GaussianBlur(blur_radius))

            scale = min(target_w / w, target_h / h)
            new_w = max(1, int(w * scale + 0.5))
            new_h = max(1, int(h * scale + 0.5))
            foreground = img.resize((new_w, new_h), Image.LANCZOS)
            left = (target_w - new_w) // 2
            top = (target_h - new_h) // 2
            background.paste(foreground, (left, top))
            img_final = background

        output = io.BytesIO()
        img_final.save(output, format="JPEG", quality=94, optimize=True)
        return output.getvalue()
    except Exception as e:
        print(f"Erreur recadrage story/reel: {e}")
        return image_bytes

def generate_scene_background(product_name):
    """Genere un decor d'interieur via OpenAI (le produit n'est PAS demande dans l'image,
    il sera colle par dessus ensuite pour garantir un rendu produit fidele a l'original)."""
    if not OPENAI_KEY:
        return None
    prompt = (
        f"Photographie d'architecture d'interieur professionnelle, style loft/industriel/contemporain haut de gamme, "
        f"pour mettre en valeur un meuble ou objet de decoration de type '{product_name}'. "
        f"Interieur epure, lumiere naturelle douce, sol et mur flous en arriere-plan, ambiance chaleureuse et minimaliste. "
        f"IMPORTANT: la piece doit etre VIDE, sans aucun meuble ni objet au premier plan, "
        f"seulement un decor/arriere-plan sur lequel un produit sera ajoute ensuite. Format vertical."
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-image-1", "prompt": prompt, "size": "1024x1536", "quality": "medium"},
            timeout=90,
        )
        result = r.json()
        b64 = result.get("data", [{}])[0].get("b64_json")
        if not b64:
            print(f"Erreur generation decor IA: {result}")
            return None
        return base64.b64decode(b64)
    except Exception as e:
        print(f"Erreur appel OpenAI: {e}")
        return None

def compose_product_on_scene(product_image_bytes, background_bytes, target_w=900, target_h=1600):
    """Colle le VRAI detourage produit (pixels d'origine, jamais modifies) sur le decor genere,
    avec une ombre portee douce."""
    try:
        bg = Image.open(io.BytesIO(background_bytes)).convert("RGB")
        bg = bg.resize((target_w, target_h), Image.LANCZOS)

        product = Image.open(io.BytesIO(product_image_bytes)).convert("RGB")
        product = trim_white_borders(product)
        pw, ph = product.size
        if pw <= 0 or ph <= 0:
            return None

        max_w = int(target_w * 0.62)
        max_h = int(target_h * 0.52)
        scale = min(max_w / pw, max_h / ph)
        new_w, new_h = max(1, int(pw * scale)), max(1, int(ph * scale))
        product_resized = product.resize((new_w, new_h), Image.LANCZOS)

        # Masque = tout ce qui n'est pas presque blanc, pour un collage propre sans halo
        gray = product_resized.convert("L")
        mask = gray.point(lambda p: 255 if p < 245 else 0)
        mask = mask.filter(ImageFilter.MaxFilter(3))

        pos_x = (target_w - new_w) // 2
        pos_y = int(target_h * 0.60) - new_h // 2

        # Ombre portee douce sous le produit
        shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.ellipse(
            [pos_x + new_w * 0.10, pos_y + new_h - int(new_h * 0.06),
             pos_x + new_w * 0.90, pos_y + new_h + int(new_h * 0.10)],
            fill=(0, 0, 0, 110),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        composed = Image.alpha_composite(bg.convert("RGBA"), shadow).convert("RGB")

        composed.paste(product_resized, (pos_x, pos_y), mask)

        output = io.BytesIO()
        composed.save(output, format="JPEG", quality=94)
        return output.getvalue()
    except Exception as e:
        print(f"Erreur composition mise en scene: {e}")
        return None

def generate_ai_lifestyle_image(product_image_url, product_name):
    """Orchestration complete : decor IA + collage du vrai produit + upload, renvoie une URL publique."""
    if not OPENAI_KEY:
        return None
    try:
        r = get_scrape_session().get(product_image_url, timeout=15)
        if r.status_code != 200:
            return None
        product_bytes = r.content
        background_bytes = generate_scene_background(product_name)
        if not background_bytes:
            return None
        composed = compose_product_on_scene(product_bytes, background_bytes)
        if not composed:
            return None
        public_url = upload_to_imgbb(composed)
        if public_url:
            print(f"Mise en scene IA generee: {public_url}")
        return public_url
    except Exception as e:
        print(f"Erreur generation mise en scene IA: {e}")
        return None

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
        r = get_scrape_session().get(image_url, timeout=15)
        if r.status_code != 200:
            return None
        return upload_to_imgbb(crop_to_45(r.content))
    except Exception as e:
        print(f"Erreur traitement: {e}")
        return None

# ─── SCRAPING ─────────────────────────────────────────────────────────────────

BOT_BYPASS_KEY = os.environ.get("BOT_BYPASS_KEY", "")
_scrape_session = None

def get_scrape_session():
    """Session partagee avec des en-tetes proches d'un vrai navigateur, persistance
    des cookies, et un en-tete secret pour la regle de contournement Cloudflare."""
    global _scrape_session
    if _scrape_session is None:
        _scrape_session = requests.Session()
        _scrape_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        if BOT_BYPASS_KEY:
            _scrape_session.headers["X-Loft-Bot-Key"] = BOT_BYPASS_KEY
    return _scrape_session

def get_product_images(product_url):
    try:
        session = get_scrape_session()
        r = session.get(product_url, timeout=15)
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
            return False, 0, True
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

def select_best_images(images, max_images=5, product_name=""):
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
        session = get_scrape_session()
        candidates = []
        seen_urls = set()
        for brand_url in STORY_BRAND_URLS:
            base_url = brand_url.split("?")[0]
            for page in range(1, 21):
                url = base_url if page == 1 else f"{base_url}?page={page}"
                time.sleep(random.uniform(1.0, 2.5))
                r = session.get(url, timeout=15)
                soup = BeautifulSoup(r.text, "html.parser")
                products = soup.select(".product-miniature")
                if not products and page == 1:
                    print(f"  {base_url.split('/')[-1]} p1: 0 produits, nouvelle tentative dans 5s...")
                    time.sleep(5)
                    r = session.get(url, timeout=15)
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
                    price_match = re.search(r"(\d[\d\u00a0\u202f]{0,4}[,.]\d{2})\s*€", text_block)
                    if not price_match:
                        continue
                    prix_val = parse_price(price_match.group(1))
                    if prix_val < STORY_MIN_PRICE:
                        continue
                    href = link_el.get("href", "") if link_el else ""
                    product_url = href if href.startswith("http") else "https://www.loftattitude.com" + href
                    if not product_url or already_in_story(product_url) or product_url in seen_urls:
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
                        "marque":    base_url.rstrip("/").split("/")[-1],
                    })
                    seen_urls.add(product_url)
                if len(products) < 12:
                    break
        if not candidates:
            print("Aucun candidat story disponible (tout deja publie ou <100€).")
            return []
        # Un produit par marque en priorite, pour ne jamais laisser une seule marque
        # monopoliser la story, puis on complete avec un pool melange (prix eleves prioritaires)
        by_brand = {}
        for c in candidates:
            by_brand.setdefault(c["marque"], []).append(c)
        for brand_list in by_brand.values():
            random.shuffle(brand_list)

        selected = []
        selected_urls = set()
        brands_order = list(by_brand.keys())
        random.shuffle(brands_order)
        for brand in brands_order:
            if len(selected) >= n:
                break
            pick = by_brand[brand][0]
            selected.append(pick)
            selected_urls.add(pick["url"])

        remaining = [c for c in candidates if c["url"] not in selected_urls]
        remaining.sort(key=lambda p: p["prix_val"], reverse=True)
        remaining = remaining[:max((n - len(selected)) * 4, 12)]
        random.shuffle(remaining)
        for c in remaining:
            if len(selected) >= n:
                break
            selected.append(c)

        random.shuffle(selected)
        return selected
    except Exception as e:
        print(f"Erreur scraping stories: {e}")
        return []

def build_story_slideshow(products):
    """Construit une video verticale (9:16) enchainant les photos des produits,
    sans texte incruste, avec une musique d'ambiance."""
    from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, afx
    clips = []
    for i, product in enumerate(products):
        image_url = product.get("image_url")
        if not image_url:
            continue
        try:
            r = get_scrape_session().get(image_url, timeout=15)
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
            preset="ultrafast", threads=1, bitrate="3000k", logger=None,
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

def find_lifestyle_image_for_product(product):
    """
    Cherche la meilleure photo lifestyle pour un produit donne.
    1. Scrape toutes les images de la fiche produit
    2. Analyse chaque image avec Claude Vision
    3. Retourne la meilleure photo lifestyle (score le plus eleve)
    4. Si aucune photo lifestyle trouvee, retourne None (jamais de detouree)
    """
    # Recupere toutes les images de la fiche produit
    all_images = get_product_images(product["url"])
    if not all_images:
        # Fallback : essaie l'image miniature du listing
        all_images = [product["image_url"]] if product.get("image_url") else []
    if not all_images:
        print(f"  Aucune image trouvee pour: {product['nom']}")
        return None

    print(f"  Analyse de {min(len(all_images), 6)} images pour trouver une photo lifestyle...")
    best_url = None
    best_score = -1

    for img_url in all_images[:6]:  # Max 6 images analysees par produit
        try:
            is_lifestyle, score, produit_entier = is_lifestyle_image(img_url)
            print(f"    {img_url[-50:]} -> lifestyle={is_lifestyle}, score={score}, entier={produit_entier}")
            if is_lifestyle and score > best_score:
                best_score = score
                best_url = img_url
        except Exception as e:
            print(f"    Erreur analyse image: {e}")
            continue

    if best_url:
        print(f"  Meilleure photo lifestyle trouvee (score={best_score}): {best_url[-60:]}")
    else:
        print(f"  Aucune photo lifestyle trouvee pour: {product['nom']} — produit ignore")
    return best_url

def publish_instagram_story_image(image_url):
    """Publie une image unique en Story Instagram."""
    if not image_url or not IG_TOKEN:
        return False
    r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "image_url":  image_url,
        "media_type": "STORIES",
        "access_token": IG_TOKEN,
    })
    result1 = r1.json()
    if "id" not in result1:
        print(f"Erreur creation story image: {result1}")
        return False
    time.sleep(15)
    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id":  result1["id"],
        "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    if "id" in result2:
        print(f"Story image Instagram OK ! ID: {result2['id']}")
        return True
    print(f"Erreur publication story image: {result2}")
    return False

def story_job():
    """
    Publie UNE seule Story Instagram avec UNE photo lifestyle.
    Regles :
    - 1 photo par Story, jamais de diaporama ou montage
    - Uniquement des photos lifestyle (interieur, ambiance) — jamais de fond blanc
    - Si aucune photo lifestyle dispo pour un produit, on essaie le suivant
    - Aucun bandeau, degrade, nom, prix ou texte incruste sur l'image
    - Ne leve jamais d'exception pour ne pas bloquer le bot
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'-'*50}\n[{now}] Story Loft Attitude (1 photo lifestyle)\n{'-'*50}")
        print(f"Historique stories: {len(load_story_history())} produits deja publies")

        # Recupere un pool de candidats (produits >100€ pas encore en story)
        pool = get_story_candidates(20)  # Large pool pour avoir le choix
        if not pool:
            print("Pas de candidats story disponibles.")
            return

        # Cherche le premier produit avec une vraie photo lifestyle
        selected_product = None
        selected_image_url = None

        for candidate in pool:
            print(f"\n-> Candidat: {candidate['nom']} | {candidate['prix']}")
            lifestyle_url = find_lifestyle_image_for_product(candidate)
            if lifestyle_url:
                selected_product = candidate
                selected_image_url = lifestyle_url
                break  # On a trouve notre produit, on s'arrete
            else:
                print(f"  Passe au produit suivant.")
                continue

        if not selected_product or not selected_image_url:
            print("Aucun produit avec photo lifestyle disponible pour cette story.")
            return

        print(f"\nProduit retenu: {selected_product['nom']}")
        print(f"Photo lifestyle: {selected_image_url[-70:]}")

        # Prepare uniquement la photo lifestyle en 9:16, sans aucun overlay
        try:
            r = get_scrape_session().get(selected_image_url, timeout=15)
            if r.status_code != 200 or not r.content:
                print("Echec telechargement image story.")
                return
            story_img = crop_to_916(r.content)
        except Exception as e:
            print(f"Erreur preparation image story: {e}")
            return

        # Upload sur imgbb
        public_url = upload_to_imgbb(story_img)
        if not public_url:
            print("Echec upload image story.")
            return

        # Publie la Story
        ok = publish_instagram_story_image(public_url)
        if ok:
            mark_as_storied(selected_product["url"])
        print(f"Story: {'OK' if ok else 'ECHEC'}")

    except Exception as e:
        print(f"Erreur story_job (ignoree, le bot continue): {e}")

def get_next_product():
    try:
        session = get_scrape_session()
        base_url = "https://www.loftattitude.com/fr/nouveaux-produits"
        for page in range(1, 21):
            url = base_url if page == 1 else f"{base_url}?page={page}"
            time.sleep(random.uniform(1.0, 2.5))
            r = session.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.select(".product-miniature")
            if not products and page == 1:
                print("Page 1: 0 produits, nouvelle tentative dans 5s...")
                time.sleep(5)
                r = session.get(url, timeout=15)
                soup = BeautifulSoup(r.text, "html.parser")
                products = soup.select(".product-miniature")
            if not products:
                break
            print(f"Page {page}: {len(products)} produits trouves")
            for product in products:
                name_el = product.select_one(".product-title")
                img_el  = product.select_one("img")
                link_el = product.select_one("a")
                text_block = product.get_text(" ", strip=True)
                price_match = re.search(r"(\d[\d\u00a0\u202f]{0,4}[,.]\d{2})\s*€", text_block)
                if not price_match:
                    continue
                prix_val = parse_price(price_match.group(1))
                if prix_val < STORY_MIN_PRICE:
                    continue
                img_url = img_el.get("data-src") or img_el.get("src") if img_el else ""
                if img_url and img_url.startswith("/"):
                    img_url = "https://www.loftattitude.com" + img_url
                href = link_el.get("href", "") if link_el else ""
                product_url = href if href.startswith("http") else "https://www.loftattitude.com" + href
                if not product_url:
                    continue
                if already_published(product_url):
                    continue
                print(f"Nouveau produit: {name_el.text.strip() if name_el else 'Inconnu'} | {price_match.group(1).strip()} €")
                return {
                    "nom":       name_el.text.strip() if name_el else "Nouveau produit design",
                    "prix":      price_match.group(1).strip() + " €",
                    "image_url": img_url,
                    "url":       product_url,
                }
            if len(products) < 12:
                break
        print("Aucun produit disponible (tout deja publie ou <100€).")
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
    best_images = select_best_images(all_images, max_images=5, product_name=product["nom"])
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

def build_reel_video(image_urls):
    """Construit un Reel (9:16) a partir des photos du produit, avec la musique d'ambiance, sans overlay."""
    from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, afx
    clips = []
    for i, image_url in enumerate(image_urls):
        try:
            r = get_scrape_session().get(image_url, timeout=15)
            if r.status_code != 200:
                continue
            slide_bytes = crop_to_916(r.content)
            slide_path = os.path.join(STORY_SLIDES_DIR, f"reel_{i}_{int(time.time())}.jpg")
            with open(slide_path, "wb") as f:
                f.write(slide_bytes)
            clips.append(ImageClip(slide_path).with_duration(REEL_SLIDE_DURATION))
        except Exception as e:
            print(f"Erreur slide reel {i}: {e}")
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
                print(f"Erreur ajout musique reel: {e}")
        else:
            print(f"Musique introuvable a {MUSIC_PATH}, reel sans son.")
        filename = f"reel_{int(time.time())}.mp4"
        output_path = os.path.join(STORY_VIDEO_DIR, filename)
        video.write_videofile(
            output_path, fps=15, codec="libx264", audio_codec="aac",
            preset="ultrafast", threads=1, bitrate="3000k", logger=None,
        )
        return filename
    except Exception as e:
        print(f"Erreur encodage reel: {e}")
        return None

def publish_instagram_reel(video_url, caption):
    if not video_url or not IG_TOKEN:
        return False
    r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
        "video_url": video_url, "media_type": "REELS", "caption": caption, "access_token": IG_TOKEN,
    })
    result1 = r1.json()
    if "id" not in result1:
        print(f"Erreur creation reel: {result1}")
        return False
    creation_id = result1["id"]
    for attempt in range(20):
        time.sleep(10)
        status_r = requests.get(f"{IG_BASE}/{creation_id}", params={
            "fields": "status_code", "access_token": IG_TOKEN,
        })
        status = status_r.json().get("status_code")
        print(f"Statut reel: {status} (tentative {attempt + 1})")
        if status == "FINISHED":
            break
        if status == "ERROR":
            print("Erreur traitement reel Meta.")
            return False
    else:
        print("Timeout traitement reel.")
        return False
    r2 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media_publish", data={
        "creation_id": creation_id, "access_token": IG_TOKEN,
    })
    result2 = r2.json()
    if "id" in result2:
        print(f"Reel Instagram OK ! ID: {result2['id']}")
        return True
    print(f"Erreur publication reel: {result2}")
    return False

def publish_facebook_reel(video_url, caption):
    if not FB_PAGE_ID or not FB_TOKEN:
        print("Facebook non configure - FB_PAGE_ID ou FB_PAGE_TOKEN manquant")
        return False
    try:
        r = requests.post(f"{FB_BASE}/{FB_PAGE_ID}/videos", data={
            "file_url": video_url, "description": caption, "access_token": FB_TOKEN,
        })
        result = r.json()
        if "id" in result:
            print(f"Facebook video OK ! ID: {result['id']}")
            return True
        print(f"Erreur Facebook video: {result}")
        return False
    except Exception as e:
        print(f"Erreur Facebook video: {e}")
        return False

def reel_job():
    """Ne doit jamais lever d'exception : un echec ici ne doit pas arreter le bot."""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'='*50}\n[{now}] Debut Reel Loft Attitude\n{'='*50}")
        product = get_next_product()
        if not product:
            print("Pas de nouveau produit pour le reel aujourd'hui.")
            return
        print(f"Produit reel: {product['nom']} | {product['prix']}")
        all_images = get_product_images(product["url"]) if product["url"] else []
        if not all_images and product["image_url"]:
            all_images = [product["image_url"]]
        best_images = select_best_images(all_images, max_images=6, product_name=product["nom"])
        if not best_images:
            print("Pas d'images pour le reel.")
            return
        filename = build_reel_video(best_images)
        if not filename:
            print("Echec generation video reel.")
            return
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url:
            print("PUBLIC_BASE_URL manquant, impossible d'heberger le reel.")
            return
        video_url = f"{base_url}/video/{filename}"
        print(f"Reel heberge: {video_url}")
        caption = generate_caption(product)
        print("\n--- INSTAGRAM REEL ---")
        ig_ok = publish_instagram_reel(video_url, caption)
        print("Instagram: OK" if ig_ok else "Instagram: ECHEC")
        print("\n--- FACEBOOK REEL ---")
        fb_ok = publish_facebook_reel(video_url, caption)
        print("Facebook: OK" if fb_ok else "Facebook: ECHEC")
        if ig_ok:
            mark_as_published(product["url"])
        print(f"\nResultat reel: Instagram={'OK' if ig_ok else 'ECHEC'} | Facebook={'OK' if fb_ok else 'ECHEC'}")
    except Exception as e:
        print(f"Erreur reel_job (ignoree, le bot continue): {e}")

def daily_dispatch_job():
    """Alterne carrousel un jour, Reel le lendemain (base sur le jour de l'annee, stable aux redemarrages)."""
    try:
        day_of_year = datetime.now().timetuple().tm_yday
        if day_of_year % 2 == 0:
            print("Jour pair -> publication carrousel")
            daily_job()
        else:
            print("Jour impair -> publication Reel")
            reel_job()
    except Exception as e:
        print(f"Erreur dispatch quotidien (ignoree): {e}")

if __name__ == "__main__":
    print("Bot Loft Attitude v16 - Stories lifestyle 9:16 sans overlay")
    print(f"Stockage historique: {DATA_DIR} {'(persistant)' if DATA_DIR == '/data' else '(NON persistant - volume /data absent)'}")
    print(f"IG_USER_ID:  {IG_USER_ID}")
    print(f"FB_PAGE_ID:  {FB_PAGE_ID}")
    print(f"IMGBB:       {'OK' if IMGBB_KEY else 'MANQUANT'}")
    print(f"Token IG:    {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude:      {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print("Publication feed/reel planifiee a 09:00 (alternee un jour sur deux)")
    print("Stories planifiees a 12:30, 19:30\n")
    threading.Thread(target=start_flask_server, daemon=True).start()
    print(f"Serveur video demarre sur le port {os.environ.get('PORT', 8080)}\n")
    try:
        daily_dispatch_job()
    except Exception as e:
        print(f"Erreur dispatch au demarrage (ignoree): {e}")
    if os.environ.get("TEST_STORY_NOW") == "1":
        print("\nTEST_STORY_NOW=1 detecte -> declenchement story manuel\n")
        story_job()
    if os.environ.get("TEST_REEL_NOW") == "1":
        print("\nTEST_REEL_NOW=1 detecte -> declenchement reel manuel\n")
        reel_job()
    # Railway tourne en UTC. Paris = UTC+1 en hiver, UTC+2 en ete (heure d'ete).
    # On compense : si on veut publier a 9h Paris heure d'ete, on programme a 7h UTC.
    # Horaires UTC correspondant aux heures Paris (heure d'ete, CEST = UTC+2) :
    #   Paris 08h30 -> UTC 06h30
    #   Paris 09h00 -> UTC 07h00  (feed/reel quotidien)
    #   Paris 10h00 -> UTC 08h00  (story matin)
    #   Paris 13h00 -> UTC 11h00  (story midi)
    #   Paris 17h00 -> UTC 15h00  (story apres-midi)
    #   Paris 20h00 -> UTC 18h00  (story soir)
    # En heure d'hiver (CET = UTC+1), ajouter 1h a chaque horaire UTC.
    # Le bot utilise schedule sans timezone, donc on programme en UTC.
    schedule.every().day.at("07:00").do(daily_dispatch_job)   # 09h Paris heure ete
    schedule.every().day.at("08:00").do(story_job)             # 10h Paris heure ete
    schedule.every().day.at("11:00").do(story_job)             # 13h Paris heure ete
    schedule.every().day.at("15:00").do(story_job)             # 17h Paris heure ete
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Erreur boucle planificateur (ignoree): {e}")
        time.sleep(60)
