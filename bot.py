import os, time, requests, schedule, json, base64, io, re, random, threading
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
IG_NATIVE_AUDIO_TOKEN = os.environ.get("IG_NATIVE_AUDIO_TOKEN", "")
IMGBB_KEY    = os.environ.get("IMGBB_API_KEY", "")
OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
FB_PAGE_ID   = os.environ.get("FB_PAGE_ID", "100063636817093")
FB_TOKEN     = os.environ.get("FB_PAGE_TOKEN", "")
IG_BASE      = "https://graph.instagram.com/v21.0"
IG_AUDIO_BASE = "https://graph.facebook.com/v22.0"
FB_BASE      = "https://graph.facebook.com/v21.0"
DATA_DIR = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else "/tmp"
HISTORY_FILE = os.path.join(DATA_DIR, "published_products.json")
STORY_HISTORY_FILE = os.path.join(DATA_DIR, "published_stories.json")
REEL_AUDIO_HISTORY_FILE = os.path.join(DATA_DIR, "published_reel_audio.json")
MANUAL_TEST_STATE_FILE = os.path.join(DATA_DIR, "manual_test_state.json")

# Audios Instagram choisis par Loft Attitude. Meta controle leur disponibilite
# pour les comptes professionnels au moment de la publication.
DEFAULT_IG_REEL_AUDIO_IDS = [
    "762806983127007",    # Betterment - Beautiful Life
    "754474975848343",    # marianneemusic - Audio d'origine
    "2006821089449485",   # Diana Ross - Upside Down (Original CHIC Mix)
    "1406057105005252",   # LAVLO - Seasons
    "29249268567994156",  # sohaib_alisher - Audio d'origine
    "467618430436929",    # France Gall - Musique
    "2740222792873277",   # Aoi Teshima - C'est si bon
    "1084359176109196",   # ANOTR, Leven Kali, Erik Bandt - How You Feel
    "1680385156615282",   # Dubdogz, FEZZO, Zaark - How Does It Feel
    "1314173264000559",   # Hinotoriprojekt - La Dolce Riviera
    "934922342851337",    # Cielo - Sunny Afternoon
    "949918737908129",    # DreamPulse Beats - The New Collection
    "1345239764082094",   # Benetti House Bar - Art Deco Hour
]
IG_REEL_AUDIO_IDS = [
    value.strip()
    for value in os.environ.get(
        "IG_REEL_AUDIO_IDS", ",".join(DEFAULT_IG_REEL_AUDIO_IDS)
    ).split(",")
    if value.strip().isdigit()
]

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
STORY_SINGLE_DURATION = 8.0
STORY_MAX_AI_GENERATIONS = 2
REEL_SLIDE_DURATION = 2.5
STORY_VIDEO_DIR = "/tmp/story_videos"
STORY_SLIDES_DIR = "/tmp/story_slides"
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

def load_reel_audio_history():
    try:
        with open(REEL_AUDIO_HISTORY_FILE, "r") as f:
            history = json.load(f)
        return history if isinstance(history, list) else []
    except Exception:
        return []

def save_reel_audio_history(history):
    try:
        with open(REEL_AUDIO_HISTORY_FILE, "w") as f:
            json.dump(history[-50:], f)
    except Exception as e:
        print(f"Erreur sauvegarde historique audio reel: {e}")

def mark_reel_audio_used(audio_id):
    history = [value for value in load_reel_audio_history() if value != audio_id]
    history.append(audio_id)
    save_reel_audio_history(history)


def should_run_manual_test(env_name):
    """Ne consomme qu'une fois un drapeau TEST_*=1, meme apres un redemarrage."""
    enabled = os.environ.get(env_name, "").strip() == "1"
    try:
        with open(MANUAL_TEST_STATE_FILE, "r") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}

    previous = state.get(env_name) == "1"
    new_value = "1" if enabled else "0"
    if state.get(env_name) != new_value:
        state[env_name] = new_value
        try:
            with open(MANUAL_TEST_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"Erreur sauvegarde etat test manuel: {e}")

    if enabled and previous:
        print(f"{env_name}=1 deja consomme - test non relance apres redemarrage.")
    return enabled and not previous

def select_available_instagram_audio():
    """Choisit un audio natif autorise par Meta, sans repetition rapprochee."""
    if not IG_NATIVE_AUDIO_TOKEN or not IG_REEL_AUDIO_IDS:
        return None

    recent = set(load_reel_audio_history()[-5:])
    preferred = [value for value in IG_REEL_AUDIO_IDS if value not in recent]
    fallback = [value for value in IG_REEL_AUDIO_IDS if value in recent]
    random.shuffle(preferred)
    random.shuffle(fallback)

    # Quelques essais suffisent; en cas d'indisponibilite globale, le Reel
    # est publie sans son plutot qu'avec l'ancienne musique locale.
    for audio_id in (preferred + fallback)[:5]:
        try:
            response = requests.get(
                f"{IG_AUDIO_BASE}/{audio_id}",
                params={
                    "user_id": IG_USER_ID,
                    "access_token": IG_NATIVE_AUDIO_TOKEN,
                },
                timeout=20,
            )
            result = response.json()
            if response.ok and result.get("audio_id"):
                label = " - ".join(
                    value for value in (result.get("display_artist"), result.get("title")) if value
                )
                print(f"Audio Instagram retenu: {label or audio_id} ({audio_id})")
                return audio_id
            print(f"Audio Instagram indisponible {audio_id}: {result.get('error', {}).get('message', 'refuse par Meta')}")
        except Exception as e:
            print(f"Erreur verification audio Instagram {audio_id}: {e}")
    return None

# ─── TRAITEMENT IMAGE ─────────────────────────────────────────────────────────

def _replace_edge_black_matte_with_white(img, threshold=48):
    """Remplace un fond noir technique relie aux bords par du blanc.

    Cette securite vise les anciens detourages enregistres sans canal alpha.
    Elle ne s'active que si la quasi-totalite du pourtour est noire et si la
    zone noire occupe une part importante de l'image, afin de ne pas modifier
    une vraie photo d'ambiance sombre.
    """
    try:
        rgb = img.convert("RGB")
        arr = np.asarray(rgb)
        if arr.size == 0:
            return rgb

        dark = np.max(arr, axis=2) <= threshold
        perimeter = np.concatenate((dark[0, :], dark[-1, :], dark[:, 0], dark[:, -1]))
        if float(perimeter.mean()) < 0.80 or float(dark.mean()) < 0.18:
            return rgb

        # Le masque binaire permet de ne blanchir que le noir connecte aux
        # bords. Les parties sombres du produit, isolees au centre, sont gardees.
        # copy() rend le buffer PIL modifiable pour ImageDraw.floodfill.
        flood = Image.fromarray(
            np.where(dark, 0, 255).astype(np.uint8), mode="L"
        ).copy()
        width, height = flood.size
        step = max(1, min(width, height) // 40)
        seeds = []
        for x in range(0, width, step):
            seeds.extend(((x, 0), (x, height - 1)))
        for y in range(0, height, step):
            seeds.extend(((0, y), (width - 1, y)))
        seeds.extend(((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)))

        for seed in seeds:
            if flood.getpixel(seed) == 0:
                ImageDraw.floodfill(flood, seed, 128, thresh=0)

        connected = np.asarray(flood) == 128
        if float(connected.mean()) < 0.18:
            return rgb

        # Un tres leger elargissement absorbe le halo noir d'anti-crenelage.
        matte_mask = Image.fromarray((connected.astype(np.uint8) * 255), mode="L")
        matte_mask = matte_mask.filter(ImageFilter.MaxFilter(3))
        white = Image.new("RGB", rgb.size, (255, 255, 255))
        cleaned = Image.composite(white, rgb, matte_mask)
        print(f"  Fond noir technique remplace par du blanc ({connected.mean():.0%} de l'image)")
        return cleaned
    except Exception as e:
        print(f"  Erreur suppression fond noir: {e}")
        return img.convert("RGB")


def _open_rgb_on_white(image_bytes, remove_black_matte=False):
    """Ouvre une image en conservant son alpha, compose sur blanc, puis passe en RGB."""
    source = Image.open(io.BytesIO(image_bytes))
    source = ImageOps.exif_transpose(source)
    has_alpha = "A" in source.getbands() or "transparency" in source.info
    if has_alpha:
        rgba = source.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        img = white.convert("RGB")
        print("  Transparence composee sur fond blanc")
    else:
        img = source.convert("RGB")
    if remove_black_matte:
        img = _replace_edge_black_matte_with_white(img)
    return img


def trim_near_white_edge_bands(
    img, threshold=238, min_coverage=0.82, max_ratio=0.40,
    edge_tolerance_ratio=0.04,
):
    """Rogne les marges blanches techniques proches des quatre bords.

    La detection tolere la compression JPEG, quelques lignes grises au bord et
    des marges pouvant atteindre 40 % de l'image. Une vraie zone claire reste
    conservee si elle n'est pas presque uniforme sur toute la largeur/hauteur.
    """
    try:
        rgb = img.convert("RGB")
        arr = np.asarray(rgb)
        if arr.size == 0:
            return rgb

        arr16 = arr.astype(np.int16)
        channel_min = np.min(arr16, axis=2)
        channel_max = np.max(arr16, axis=2)
        luminance = np.mean(arr16, axis=2)
        near_white = (
            (luminance >= threshold)
            & (channel_min >= threshold - 10)
            & ((channel_max - channel_min) <= 30)
        )
        height, width = near_white.shape

        row_flags = (
            (near_white.mean(axis=1) >= min_coverage)
            & (luminance.mean(axis=1) >= threshold)
        )
        col_flags = (
            (near_white.mean(axis=0) >= min_coverage)
            & (luminance.mean(axis=0) >= threshold)
        )

        def leading_margin(flags, total_length):
            """Renvoie la profondeur d'une bande proche du bord dans flags."""
            max_scan = max(1, int(total_length * max_ratio))
            tolerance = max(3, int(total_length * edge_tolerance_ratio))
            min_run = max(2, int(total_length * 0.002))
            max_gap = max(2, int(total_length * 0.004))
            candidates = np.flatnonzero(flags[:max_scan])
            if candidates.size == 0 or int(candidates[0]) > tolerance:
                return 0

            first = int(candidates[0])
            last = first
            white_count = 0
            gap = 0
            for index in range(first, max_scan):
                if bool(flags[index]):
                    last = index + 1
                    white_count += 1
                    gap = 0
                else:
                    gap += 1
                    if gap > max_gap:
                        break
            if white_count < min_run:
                return 0
            # Inclut les quelques pixels gris eventuels places avant la marge.
            return last

        top = leading_margin(row_flags, height)
        bottom_margin = leading_margin(row_flags[::-1], height)
        left = leading_margin(col_flags, width)
        right_margin = leading_margin(col_flags[::-1], width)

        # Retire aussi les quelques pixels d'anti-crenelage situes juste apres
        # la marge, afin qu'aucun filet blanc ne survive au redimensionnement.
        bleed = max(2, int(min(width, height) * 0.003))
        if top:
            top = min(height, top + bleed)
        if bottom_margin:
            bottom_margin = min(height, bottom_margin + bleed)
        if left:
            left = min(width, left + bleed)
        if right_margin:
            right_margin = min(width, right_margin + bleed)
        bottom = height - bottom_margin
        right = width - right_margin

        # Ne sacrifie jamais la zone centrale si une photo tres claire a ete
        # prise par erreur pour une marge technique.
        if (
            left >= right
            or top >= bottom
            or (bottom - top) < int(height * 0.35)
            or (right - left) < int(width * 0.35)
        ):
            return rgb
        if top or bottom < height or left or right < width:
            print(
                "  Marges blanches supprimees "
                f"(haut:{top} bas:{height-bottom} gauche:{left} droite:{width-right})"
            )
            return rgb.crop((left, top, right, bottom))
        return rgb
    except Exception as e:
        print(f"  Erreur suppression bandes fines: {e}")
        return img.convert("RGB")

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

def _fit_on_white_canvas(image_bytes, target_size, margin_ratio=0.055):
    """Place une photo de produit entiere sur un fond blanc, sans deformation."""
    try:
        # Un simple convert("RGB") transforme les pixels transparents en noir.
        # On compose donc d'abord l'alpha sur blanc et l'on corrige aussi les
        # anciens fichiers dont le fond noir a deja ete enregistre dans l'image.
        img = _open_rgb_on_white(image_bytes, remove_black_matte=True)
        if img.width <= 0 or img.height <= 0:
            return image_bytes

        # Les fichiers du site ont souvent de grandes bordures blanches. On les
        # retire d'abord afin que le produit occupe correctement le nouveau cadre.
        img = trim_white_borders(img)
        w, h = img.size
        if w <= 0 or h <= 0:
            return image_bytes

        target_w, target_h = target_size
        margin_x = max(20, int(target_w * margin_ratio))
        margin_y = max(20, int(target_h * margin_ratio))
        max_w = target_w - 2 * margin_x
        max_h = target_h - 2 * margin_y
        scale = min(max_w / w, max_h / h)
        new_w = max(1, int(w * scale + 0.5))
        new_h = max(1, int(h * scale + 0.5))

        foreground = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", target_size, (255, 255, 255))
        left = (target_w - new_w) // 2
        top = (target_h - new_h) // 2
        canvas.paste(foreground, (left, top))

        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=94, optimize=True)
        print(f"  Produit entier {new_w}x{new_h} centre sur fond blanc")
        return output.getvalue()
    except Exception as e:
        print(f"Erreur mise sur fond blanc: {e}")
        return image_bytes


def crop_to_45(image_bytes):
    """Prepare une publication 4:5 (1080x1350) sur fond blanc."""
    return _fit_on_white_canvas(image_bytes, (1080, 1350), margin_ratio=0.055)


def crop_to_916_reel(image_bytes):
    """Prepare une image de Reel 9:16 (1080x1920) sur fond blanc, sans flou."""
    return _fit_on_white_canvas(image_bytes, (1080, 1920), margin_ratio=0.055)

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
        img = _open_rgb_on_white(image_bytes)
        w, h = img.size
        if w <= 0 or h <= 0:
            return image_bytes

        # Les fines bandes techniques doivent etre retirees meme si la photo
        # lifestyle n'a pas globalement un fond blanc.
        img = trim_near_white_edge_bands(img)
        w, h = img.size

        # Retire ensuite les bordures blanches plus larges des images produit.
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

        product_source = Image.open(io.BytesIO(product_image_bytes))
        product_source = ImageOps.exif_transpose(product_source)
        product_has_alpha = (
            "A" in product_source.getbands() or "transparency" in product_source.info
        )
        product_alpha = None
        if product_has_alpha:
            product_rgba = product_source.convert("RGBA")
            alpha_bbox = product_rgba.getchannel("A").getbbox()
            if alpha_bbox:
                product_rgba = product_rgba.crop(alpha_bbox)
            product = product_rgba.convert("RGB")
            product_alpha = product_rgba.getchannel("A")
        else:
            product = _replace_edge_black_matte_with_white(product_source.convert("RGB"))
            product = trim_white_borders(product)
        pw, ph = product.size
        if pw <= 0 or ph <= 0:
            return None

        max_w = int(target_w * 0.62)
        max_h = int(target_h * 0.52)
        scale = min(max_w / pw, max_h / ph)
        new_w, new_h = max(1, int(pw * scale)), max(1, int(ph * scale))
        product_resized = product.resize((new_w, new_h), Image.LANCZOS)

        if product_alpha is not None:
            mask = product_alpha.resize((new_w, new_h), Image.LANCZOS)
        else:
            # Pour les anciens JPEG sans alpha, masque tout ce qui est presque blanc.
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
    """Prepare une image de publication sur fond blanc puis l'heberge."""
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

def _extract_openai_text(response_data):
    """Extrait le premier texte utile d'une reponse de l'API Responses."""
    for item in response_data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return ""


def _openai_json(input_content, schema_name, schema, max_output_tokens=600, instructions=None):
    """Appelle OpenAI et renvoie un objet JSON conforme au schema demande."""
    if not OPENAI_KEY:
        print("OPENAI_API_KEY manquante, appel IA ignore.")
        return None

    payload = {
        "model": OPENAI_MODEL,
        "input": [{"role": "user", "content": input_content}],
        "max_output_tokens": max_output_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    if instructions:
        payload["instructions"] = instructions

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    try:
        response_data = r.json()
    except ValueError:
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: reponse non JSON")
    if r.status_code >= 400:
        error = response_data.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {message or 'erreur inconnue'}")

    response_text = _extract_openai_text(response_data)
    if not response_text:
        raise RuntimeError("OpenAI n'a renvoye aucun texte exploitable")
    return json.loads(response_text)


def is_lifestyle_image(image_url):
    """Analyse une photo produit avec OpenAI pour identifier les vraies mises en scene."""
    try:
        if not OPENAI_KEY:
            print("Erreur analyse image: OPENAI_API_KEY manquante")
            return False, 0, True

        r = get_scrape_session().get(image_url, timeout=15)
        if r.status_code != 200 or not r.content:
            return False, 0, True

        content_type = r.headers.get("content-type", "image/jpeg").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            content_type = "image/jpeg"
        img_b64 = base64.b64encode(r.content).decode("utf-8")
        image_data_url = f"data:{content_type};base64,{img_b64}"

        schema = {
            "type": "object",
            "properties": {
                "lifestyle": {"type": "boolean"},
                "score": {"type": "integer", "minimum": 0, "maximum": 10},
                "produit_entier": {"type": "boolean"},
            },
            "required": ["lifestyle", "score", "produit_entier"],
            "additionalProperties": False,
        }
        prompt = (
            "Analyse cette image produit pour une publication Instagram de mobilier et decoration. "
            "lifestyle=true uniquement si le produit est photographie dans un interieur, une ambiance "
            "ou une vraie mise en scene. lifestyle=false pour un fond blanc ou uni, un produit detoure, "
            "un visuel technique ou un detail de texture. produit_entier=true si le produit principal "
            "est visible en entier. Le score de 0 a 10 mesure la qualite visuelle pour Instagram : "
            "favorise une vue complete, elegante et nette, et penalise fortement les details et zooms."
        )
        result = _openai_json(
            [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_data_url, "detail": "low"},
            ],
            "analyse_image_produit",
            schema,
            max_output_tokens=150,
        )
        if not result:
            return False, 0, True
        return result["lifestyle"], result["score"], result["produit_entier"]
    except Exception as e:
        print(f"Erreur analyse image OpenAI: {e}")
        return False, 0, True

def select_best_images(images, max_images=5, product_name=""):
    """Selectionne en priorite les vues produit detourees pour les posts et Reels."""
    if not images:
        return []
    print(f"Analyse IA de {min(len(images), 8)} images...")
    scored = []
    for i, img_url in enumerate(images[:8]):
        print(f"  Image {i+1}...")
        is_lifestyle, score, produit_entier = is_lifestyle_image(img_url)
        # Pour les publications et Reels, priorite au produit entier sur fond
        # blanc. Les Stories disposent de leur propre selection lifestyle.
        final_score = score + (3 if produit_entier else -4)
        scored.append({"url": img_url, "lifestyle": is_lifestyle, "score": final_score, "entier": produit_entier})
        print(f"  -> Lifestyle: {is_lifestyle}, Entier: {produit_entier}, Score: {final_score}")
    scored.sort(key=lambda x: (not x["lifestyle"], x["entier"], x["score"]), reverse=True)
    detourees = [item for item in scored if not item["lifestyle"]]
    selection = detourees[:max_images] if detourees else scored[:max_images]
    best = [item["url"] for item in selection]
    lifestyle_count = sum(1 for item in selection if item["lifestyle"])
    print(f"Selection: {len(best)} images ({len(best) - lifestyle_count} detourees, {lifestyle_count} lifestyle)")
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
    if black_friday_active():
        return [p for p in black_friday_products_today() if not already_in_story(p['url'])][:n]
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
    sans texte incruste et sans piste audio locale."""
    from moviepy import ImageClip, concatenate_videoclips
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
            slide_duration = STORY_SINGLE_DURATION if len(products) == 1 else STORY_SLIDE_DURATION
            clips.append(ImageClip(slide_path).with_duration(slide_duration))
        except Exception as e:
            print(f"Erreur slide {i}: {e}")
    if not clips:
        return None
    try:
        video = concatenate_videoclips(clips, method="compose")
        print("Story generee sans musique locale.")
        filename = f"story_{int(time.time())}.mp4"
        output_path = os.path.join(STORY_VIDEO_DIR, filename)
        video.write_videofile(
            output_path, fps=24, codec="libx264", audio=False,
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
    2. Analyse chaque image avec OpenAI Vision
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
        print(f"  Aucune photo lifestyle trouvee pour: {product['nom']} - produit ignore")
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
    """Publie une Story lifestyle sans texte ni musique locale."""
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
                _campaign_visual_context.product = candidate
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

        # L'API Meta ne permet pas de choisir une musique Instagram pour une
        # Story. On fabrique donc une courte video 9:16 silencieuse, sans texte.
        story_product = dict(selected_product)
        story_product["image_url"] = selected_image_url
        filename = build_story_slideshow([story_product])
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        ig_ok = False
        fb_ok = False
        if filename and base_url:
            video_url = f"{base_url}/video/{filename}"
            print(f"Story video hebergee: {video_url}")
            print("\n--- INSTAGRAM STORY ---")
            ig_ok = publish_instagram_story_video(video_url)
            print("Instagram Story: OK" if ig_ok else "Instagram Story: ECHEC")
            print("\n--- FACEBOOK STORY ---")
            fb_ok = publish_facebook_story_video(video_url)
            print("Facebook Story: OK" if fb_ok else "Facebook Story: ECHEC")
        else:
            print("Video Story indisponible; repli sur une image Instagram sans musique.")
            try:
                r = get_scrape_session().get(selected_image_url, timeout=15)
                if r.status_code != 200 or not r.content:
                    print("Echec telechargement image story.")
                    return
                public_url = upload_to_imgbb(crop_to_916(r.content))
                if public_url:
                    ig_ok = publish_instagram_story_image(public_url)
                    fb_ok = publish_facebook_story_image(public_url)
            except Exception as e:
                print(f"Erreur repli image story: {e}")

        if ig_ok:
            mark_as_storied(selected_product["url"])
        print(f"Story: Instagram={'OK' if ig_ok else 'ECHEC'} | Facebook={'OK' if fb_ok else 'ECHEC'}")

    except Exception as e:
        print(f"Erreur story_job (ignoree, le bot continue): {e}")

def get_next_product():
    if black_friday_active():
        return next((p for p in black_friday_products_today() if not already_published(p['url'])), None)
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
        schema = {
            "type": "object",
            "properties": {
                "caption": {"type": "string"},
                "hashtags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 25,
                    "maxItems": 25,
                },
            },
            "required": ["caption", "hashtags"],
            "additionalProperties": False,
        }
        instructions = (
            'Tu es expert marketing Instagram pour "Loft Attitude", boutique de meubles et objets '
            "design loft, industriel et contemporain. Ecris en francais une legende elegante de 120 "
            "a 150 mots avec quelques emojis et un storytelling centre sur le produit et son ambiance. "
            "Termine toujours la legende par cette phrase exacte : Retrouvez ce produit via le lien en "
            "bio 👆 loftattitude.com. Fournis aussi exactement 25 hashtags pertinents."
        )
        data = _openai_json(
            f"Produit: {product['nom']}\nPrix: {product['prix']}\nURL: {product['url']}",
            "legende_instagram",
            schema,
            max_output_tokens=800,
            instructions=instructions,
        )
        if not data:
            raise RuntimeError("Aucune legende renvoyee par OpenAI")
        hashtags = []
        for tag in data["hashtags"]:
            tag = re.sub(r"\s+", "", str(tag).strip())
            if tag and not tag.startswith("#"):
                tag = "#" + tag
            if tag:
                hashtags.append(tag)
        return data["caption"].strip() + "\n\n" + " ".join(hashtags)
    except Exception as e:
        print(f"Erreur caption OpenAI: {e}")
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
    _campaign_visual_context.product = product or {}
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
    """Construit un Reel 9:16 silencieux; Meta ajoutera l'audio natif Instagram."""
    from moviepy import ImageClip, concatenate_videoclips
    clips = []
    for i, image_url in enumerate(image_urls):
        try:
            r = get_scrape_session().get(image_url, timeout=15)
            if r.status_code != 200:
                continue
            slide_bytes = crop_to_916_reel(r.content)
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
        print("Reel genere sans musique locale; ajout audio natif demande a Instagram.")
        filename = f"reel_{int(time.time())}.mp4"
        output_path = os.path.join(STORY_VIDEO_DIR, filename)
        video.write_videofile(
            output_path, fps=24, codec="libx264", audio=False,
            preset="ultrafast", threads=1, bitrate="3000k", logger=None,
        )
        return filename
    except Exception as e:
        print(f"Erreur encodage reel: {e}")
        return None

def publish_instagram_reel(video_url, caption):
    if not video_url or not IG_TOKEN:
        return False
    audio_id = select_available_instagram_audio()
    publish_base = IG_BASE
    publish_token = IG_TOKEN
    result1 = {}

    if audio_id:
        native_audio_data = {
            "video_url": video_url,
            "media_type": "REELS",
            "caption": caption,
            "audio_configuration": json.dumps({
                "audio_id": audio_id,
                "audio_volume": 100,
                # La video source est silencieuse : seule la piste Instagram
                # selectionnee dans la rotation doit etre audible.
                "video_volume": 0,
            }),
            "access_token": IG_NATIVE_AUDIO_TOKEN,
        }
        native_response = requests.post(
            f"{IG_AUDIO_BASE}/{IG_USER_ID}/media", data=native_audio_data, timeout=30
        )
        result1 = native_response.json()
        if "id" in result1:
            publish_base = IG_AUDIO_BASE
            publish_token = IG_NATIVE_AUDIO_TOKEN
        else:
            print(f"Audio natif refuse, publication du Reel sans son: {result1}")
            audio_id = None

    if "id" not in result1:
        r1 = requests.post(f"{IG_BASE}/{IG_USER_ID}/media", data={
            "video_url": video_url,
            "media_type": "REELS",
            "caption": caption,
            "access_token": IG_TOKEN,
        }, timeout=30)
        result1 = r1.json()
    if "id" not in result1:
        print(f"Erreur creation reel: {result1}")
        return False
    creation_id = result1["id"]
    for attempt in range(20):
        time.sleep(10)
        status_r = requests.get(f"{publish_base}/{creation_id}", params={
            "fields": "status_code", "access_token": publish_token,
        }, timeout=20)
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
    r2 = requests.post(f"{publish_base}/{IG_USER_ID}/media_publish", data={
        "creation_id": creation_id, "access_token": publish_token,
    }, timeout=30)
    result2 = r2.json()
    if "id" in result2:
        print(f"Reel Instagram OK ! ID: {result2['id']}")
        if audio_id:
            mark_reel_audio_used(audio_id)
        return True
    print(f"Erreur publication reel: {result2}")
    return False

def _publish_facebook_vertical_video(video_url, edge, label, description=""):
    """Publie une video verticale via le protocole START/upload/FINISH de Meta."""
    if not FB_PAGE_ID or not FB_TOKEN:
        print("Facebook non configure - FB_PAGE_ID ou FB_PAGE_TOKEN manquant")
        return False
    try:
        start = requests.post(
            f"{FB_BASE}/{FB_PAGE_ID}/{edge}",
            data={"upload_phase": "start", "access_token": FB_TOKEN},
            timeout=30,
        ).json()
        video_id = start.get("video_id")
        upload_url = start.get("upload_url")
        if not video_id or not upload_url:
            print(f"Erreur initialisation {label} Facebook: {start}")
            return False

        upload_response = requests.post(
            upload_url,
            headers={"Authorization": f"OAuth {FB_TOKEN}", "file_url": video_url},
            timeout=120,
        )
        upload_result = upload_response.json()
        if not upload_response.ok or not upload_result.get("success"):
            print(f"Erreur upload {label} Facebook: {upload_result}")
            return False

        finish_data = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "access_token": FB_TOKEN,
        }
        if description:
            finish_data["description"] = description
        finish = requests.post(
            f"{FB_BASE}/{FB_PAGE_ID}/{edge}", data=finish_data, timeout=30
        ).json()
        if finish.get("success") or finish.get("post_id"):
            print(f"{label} Facebook OK ! video_id={video_id}")
            return True
        print(f"Erreur publication {label} Facebook: {finish}")
        return False
    except Exception as e:
        print(f"Erreur {label} Facebook: {e}")
        return False


def publish_facebook_reel(video_url, caption):
    return _publish_facebook_vertical_video(
        video_url, "video_reels", "Reel", description=caption
    )


def publish_facebook_story_video(video_url):
    return _publish_facebook_vertical_video(video_url, "video_stories", "Story")


def publish_facebook_story_image(image_url):
    """Repli Facebook pour une Story image lorsque la video ne peut pas etre creee."""
    if not FB_PAGE_ID or not FB_TOKEN:
        return False
    try:
        photo = requests.post(
            f"{FB_BASE}/{FB_PAGE_ID}/photos",
            data={
                "url": image_url,
                "published": "false",
                "access_token": FB_TOKEN,
            },
            timeout=30,
        ).json()
        photo_id = photo.get("id")
        if not photo_id:
            print(f"Erreur upload photo Story Facebook: {photo}")
            return False
        result = requests.post(
            f"{FB_BASE}/{FB_PAGE_ID}/photo_stories",
            data={"photo_id": photo_id, "access_token": FB_TOKEN},
            timeout=30,
        ).json()
        if result.get("success") or result.get("post_id"):
            print(f"Story image Facebook OK ! photo_id={photo_id}")
            return True
        print(f"Erreur publication Story image Facebook: {result}")
        return False
    except Exception as e:
        print(f"Erreur Story image Facebook: {e}")
        return False

def reel_job():
    """Ne doit jamais lever d'exception : un echec ici ne doit pas arreter le bot."""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'='*50}\n[{now}] Debut Reel Loft Attitude\n{'='*50}")
        product = get_next_product()
        _campaign_visual_context.product = product or {}
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

def black_friday_active():
    now = datetime.now(PARIS_TZ) if PARIS_TZ else datetime.now()
    return '2026-10-31' <= now.strftime('%Y-%m-%d') <= '2026-11-30'


def black_friday_products_today():
    import csv
    now = datetime.now(PARIS_TZ) if PARIS_TZ else datetime.now()
    offset = (now.date() - datetime(2026, 10, 31).date()).days * 8
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'richmond_black_friday_2026.csv')
    # Fail closed: never substitute products outside the approved campaign list.
    with open(path, encoding='utf-8-sig', newline='') as source:
        rows = list(csv.DictReader(source, delimiter=';'))
    if len(rows) != 244 or len({r['ID produit'] for r in rows}) != 244:
        raise ValueError('Liste Black Friday attendue: 244 produits uniques')
    products = []
    for row in rows[max(0, offset):max(0, offset) + 8]:
        products.append({
            'nom': row['Produit'], 'prix': row['Prix Black Friday TTC EUR'] + ' EUR',
            'prix_val': float(row['Prix Black Friday TTC EUR'].replace(',', '.')),
            'url': row['URL'], 'image_url': '', 'marque': 'richmond-interiors',
            'reference': row['Référence Richmond'],
        })
    return products


_campaign_visual_context = threading.local()


def black_friday_badge(image_bytes, story=False):
    if not black_friday_active():
        return image_bytes
    from PIL import ImageFont
    img = _open_rgb_on_white(image_bytes)
    draw = ImageDraw.Draw(img)
    def font(size):
        try:
            return ImageFont.truetype('DejaVuSans-Bold.ttf', size)
        except OSError:
            return ImageFont.load_default(size=size)

    # Coordonnees adaptees aux formats 9:16 et 4:5, hors des controles Instagram.
    top = 230 if story else 90
    right = img.width - 40
    left = right - 420
    bottom = top + (220 if story else 175)
    draw.rounded_rectangle((left, top, right, bottom), radius=24,
                           fill=(20, 20, 20), outline=(205, 173, 109), width=3)
    lines = ['BLACK FRIDAY', 'Jusqu\u2019a -50 %' if story else '-40 %']
    if story:
        lines.append('Offres Black Friday')
        lines.append('Lien dans notre bio')
    else:
        lines.append('loftattitude.com')
    draw.multiline_text(((left+right)/2, (top+bottom)/2), '\n'.join(lines),
                        font=font(28), fill='white', anchor='mm', align='center', spacing=12)

    product = getattr(_campaign_visual_context, 'product', {})
    if not story and product.get('nom'):
        import textwrap
        diameter = 330
        x, y = 40, img.height - diameter - 260
        draw.ellipse((x, y, x+diameter, y+diameter), fill=(20, 20, 20),
                     outline=(205, 173, 109), width=3)
        label = product['nom']
        reference = product.get('reference', '')
        chosen = None
        for size in range(25, 11, -1):
            candidate_font = font(size)
            wrapped = textwrap.wrap(label, width=max(14, int(440 / size)))
            text = '\n'.join(wrapped + ([f'Ref. {reference}'] if reference else []))
            bounds = draw.multiline_textbbox((0, 0), text, font=candidate_font, spacing=7)
            if bounds[2]-bounds[0] <= diameter*0.76 and bounds[3]-bounds[1] <= diameter*0.66:
                chosen = (text, candidate_font)
                break
        if chosen:
            draw.multiline_text((x+diameter/2, y+diameter/2), chosen[0], font=chosen[1],
                                fill='white', anchor='mm', align='center', spacing=7)
    output = io.BytesIO()
    img.save(output, format='JPEG', quality=94)
    return output.getvalue()


_story_crop_without_badge = crop_to_916
_reel_crop_without_badge = crop_to_916_reel
_post_crop_without_badge = crop_to_45

def crop_to_916(image_bytes):
    return black_friday_badge(_story_crop_without_badge(image_bytes), story=True)

def crop_to_916_reel(image_bytes):
    return black_friday_badge(_reel_crop_without_badge(image_bytes))

def crop_to_45(image_bytes):
    return black_friday_badge(_post_crop_without_badge(image_bytes))


if __name__ == "__main__":
    print("Bot Loft Attitude v18 - Stories lifestyle / Posts et Reels sur fond blanc")
    print(f"Stockage historique: {DATA_DIR} {'(persistant)' if DATA_DIR == '/data' else '(NON persistant - volume /data absent)'}")
    print(f"IG_USER_ID:  {IG_USER_ID}")
    print(f"FB_PAGE_ID:  {FB_PAGE_ID}")
    print(f"IMGBB:       {'OK' if IMGBB_KEY else 'MANQUANT'}")
    print(f"Token IG:    {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Audio IG:    {'NATIF' if IG_NATIVE_AUDIO_TOKEN else 'LOCAL (IG_NATIVE_AUDIO_TOKEN manquant)'}")
    print(f"OpenAI:      {'OK' if OPENAI_KEY else 'MANQUANT'} ({OPENAI_MODEL})")
    print("Publication feed/reel planifiee a 09:00 (alternee un jour sur deux)")
    print("Stories planifiees a 12:30, 19:30\n")
    threading.Thread(target=start_flask_server, daemon=True).start()
    print(f"Serveur video demarre sur le port {os.environ.get('PORT', 8080)}\n")
    print("Aucune publication automatique au redemarrage; attente des horaires planifies.")
    if should_run_manual_test("TEST_STORY_NOW"):
        print("\nTEST_STORY_NOW=1 detecte -> declenchement story manuel\n")
        story_job()
    if should_run_manual_test("TEST_REEL_NOW"):
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
    schedule.every().day.at('09:00', 'Europe/Paris').do(daily_dispatch_job)
    def campaign_story_slot():
        if black_friday_active():
            story_job()

    def regular_story_slot():
        if not black_friday_active():
            story_job()

    for hour in ('09:30', '10:30', '11:30', '12:30', '14:00', '15:30', '17:00', '19:00'):
        schedule.every().day.at(hour, 'Europe/Paris').do(campaign_story_slot)
    for hour in ('10:00', '13:00', '17:00'):
        schedule.every().day.at(hour, 'Europe/Paris').do(regular_story_slot)
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Erreur boucle planificateur (ignoree): {e}")
        time.sleep(60)
