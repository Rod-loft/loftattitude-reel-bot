import os, time, random, re, requests, schedule, anthropic, json, base64, threading
from datetime import datetime
from io import BytesIO
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter
from flask import Flask, send_from_directory

IG_USER_ID = os.environ.get("IG_USER_ID", "17841400937343787")
IG_TOKEN   = os.environ.get("IG_ACCESS_TOKEN", "")
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IG_BASE    = "https://graph.instagram.com/v21.0"

# Fichier d'historique des produits deja publies (persiste entre redemarrages
# si un volume Railway est monte sur /data, sinon reinitialise a chaque deploiement)
STATE_FILE = os.environ.get("STATE_FILE", "./posted.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------------------------------------------------------------------------
# FORMATAGE IMAGES AU FORMAT INSTAGRAM (4:5 portrait)
# Les photos du site n'ont pas toutes le meme ratio. On les retravaille pour
# qu'elles s'affichent correctement sur Instagram, SANS jamais couper le
# produit ni le deformer : l'image complete est centree sur un fond flou
# genere a partir d'elle-meme. Les images formatees sont servies par un
# petit serveur web integre a ce meme service Railway (public si un domaine
# est genere dans Railway > Networking > Generate Domain).
# ---------------------------------------------------------------------------
STATIC_DIR      = os.environ.get("STATIC_DIR", "./static_images")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
PORT            = int(os.environ.get("PORT", "8080"))
TARGET_W, TARGET_H = 1080, 1350  # format 4:5, recommande par Instagram

os.makedirs(STATIC_DIR, exist_ok=True)

flask_app = Flask(__name__)


@flask_app.route("/img/<path:filename>")
def serve_image(filename):
    return send_from_directory(STATIC_DIR, filename)


def run_web_server():
    flask_app.run(host="0.0.0.0", port=PORT)


def format_image_for_instagram(image_url, filename):
    """Telecharge une image, la recadre au format Instagram (4:5) sans
    deformation ni coupe du produit, avec un fond flou en remplissage.
    Sauvegarde localement et retourne l'URL publique."""
    try:
        r = requests.get(image_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")

        # Fond : l'image agrandie pour couvrir tout le cadre, puis floutee
        bg_ratio = max(TARGET_W / img.width, TARGET_H / img.height)
        bg = img.resize((int(img.width * bg_ratio) + 1, int(img.height * bg_ratio) + 1))
        left = (bg.width - TARGET_W) // 2
        top = (bg.height - TARGET_H) // 2
        bg = bg.crop((left, top, left + TARGET_W, top + TARGET_H))
        bg = bg.filter(ImageFilter.GaussianBlur(40))

        # Premier plan : l'image entiere, redimensionnee sans deformation ni coupe
        fg_ratio = min(TARGET_W / img.width, TARGET_H / img.height)
        fg = img.resize((max(1, int(img.width * fg_ratio)), max(1, int(img.height * fg_ratio))))
        fg_x = (TARGET_W - fg.width) // 2
        fg_y = (TARGET_H - fg.height) // 2
        bg.paste(fg, (fg_x, fg_y))

        path = os.path.join(STATIC_DIR, filename)
        bg.save(path, "JPEG", quality=90)

        if not PUBLIC_BASE_URL:
            print("PUBLIC_BASE_URL non configure : impossible de generer une URL publique")
            return None
        return f"{PUBLIC_BASE_URL}/img/{filename}"
    except Exception as e:
        print(f"Erreur formatage image {image_url}: {e}")
        return None

# ---------------------------------------------------------------------------
# MARQUES PRIORITAIRES
# Le bot ne publie plus le "dernier produit" toutes marques confondues :
# il tourne exclusivement sur ces 5 marques, une a une, sans jamais en
# sortir, meme quand tout le stock de ces marques a deja ete publie
# (dans ce cas, un nouveau cycle redemarre sur ces memes 5 marques).
# ---------------------------------------------------------------------------
BRANDS = {
    "richmond-interiors": "66-richmond-interiors",
    "sompex":              "15-sompex",
    "socadis":             "55-socadis",
    "villeroy-boch":       "62-villeroy-boch",
    "kare-design":         "7-kare-design",
}


# ------------------------------------------------------------- historique --

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"posted_urls": []}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")


# ------------------------------------------------------- scraping marques --

# Motif d'URL produit du site, ex:
# https://www.loftattitude.com/fr/chaise/16626-chaise-de-salle-a-manger-oasis-naturel-gwen-8721009431849.html
# Independant des classes CSS (contrairement a .product-miniature qui a change
# de structure sur les pages marque et renvoyait 0 resultat).
PRODUCT_URL_PATTERN = re.compile(
    r"https://www\.loftattitude\.com/fr/[a-z0-9\-]+/\d+-[a-z0-9\-]+\.html"
)


def scrape_brand_products(brand_slug):
    """Recupere les URLs produits de la 1ere page d'une marque (24 produits).
    Extraction par motif d'URL, insensible aux changements de classes CSS."""
    url = f"https://www.loftattitude.com/fr/brand/{brand_slug}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        urls = sorted(set(PRODUCT_URL_PATTERN.findall(r.text)))
        print(f"Marque {brand_slug}: {len(urls)} produits trouves")
        return urls
    except Exception as e:
        print(f"Erreur scraping marque {brand_slug}: {e}")
        return []


def get_product_details(product_url):
    """Recupere nom, prix et image principale d'une fiche produit via les
    balises meta (og:title, product:price:amount, og:image), plus fiables
    que les classes CSS qui varient selon les templates de page."""
    try:
        r = requests.get(product_url, headers=HEADERS, timeout=15)
        html = r.text

        nom_match = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        nom = nom_match.group(1).strip() if nom_match else "Nouveau produit design"
        for suffix in [" | Loft Attitude", " - Loft Attitude"]:
            if nom.endswith(suffix):
                nom = nom[: -len(suffix)]

        price_match = re.search(r'property="product:price:amount"\s+content="([\d.,]+)"', html)
        prix = f"{price_match.group(1)} €" if price_match else ""

        img_match = re.search(r'property="og:image"\s+content="([^"]+)"', html)
        image_url = img_match.group(1) if img_match else ""

        return {"nom": nom, "prix": prix, "image_url": image_url, "url": product_url}
    except Exception as e:
        print(f"Erreur recuperation details produit: {e}")
        return {"nom": "Nouveau produit design", "prix": "", "image_url": "", "url": product_url}


def get_next_priority_product(state):
    """Choisit le prochain produit a publier EXCLUSIVEMENT parmi les 5
    marques prioritaires, en les faisant tourner une a une."""
    posted = set(state.get("posted_urls", []))
    brand_names = list(BRANDS.keys())

    def try_pick(exclude_urls):
        start = len(posted) % len(brand_names)
        ordered = brand_names[start:] + brand_names[:start]
        for brand in ordered:
            print(f"Recherche produit disponible sur la marque '{brand}'...")
            urls = scrape_brand_products(BRANDS[brand])
            candidates = [u for u in urls if u not in exclude_urls]
            if candidates:
                choice = random.choice(candidates)
                print(f"Produit retenu ({brand}): {choice}")
                return get_product_details(choice)
        return None

    product = try_pick(posted)
    if product:
        return product

    # Les 5 marques sont epuisees (tout a deja ete publie au moins une fois) :
    # on relance un cycle sur ces memes marques, sans jamais sortir du perimetre
    print("Toutes les marques prioritaires ont ete publiees au moins une fois. Nouveau cycle.")
    all_brand_urls = set()
    for slug in BRANDS.values():
        all_brand_urls.update(scrape_brand_products(slug))
    posted_outside_brands = posted - all_brand_urls
    return try_pick(posted_outside_brands)


# ------------------------------------------------------------------- bio --

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


# ---------------------------------------------------------------- images --

def get_product_images(product_url):
    """Scrape toutes les images d'une fiche produit"""
    try:
        r = requests.get(product_url, headers=HEADERS, timeout=15)
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


# --------------------------------------------------------------- caption --

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


# ------------------------------------------------------------ publication --

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


# ------------------------------------------------------------------- job --

def daily_job():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{now}] Debut publication Loft Attitude")
    print(f"{'='*50}")

    state = load_state()

    # 1. Produit (exclusivement parmi les 5 marques prioritaires)
    product = get_next_priority_product(state)
    if not product:
        print("Pas de produit disponible dans les marques prioritaires.")
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

    # 4bis. Formatage au format Instagram (4:5, sans coupe ni deformation)
    print("Formatage des images au format Instagram...")
    formatted_images = []
    for i, img_url in enumerate(best_images):
        filename = f"{int(time.time())}_{i}.jpg"
        public_url = format_image_for_instagram(img_url, filename)
        if public_url:
            formatted_images.append(public_url)
    if not formatted_images:
        print("Echec du formatage des images, publication annulee.")
        return

    # 5. Caption
    caption = generate_caption(product)
    print(f"Caption: {len(caption)} caracteres")

    # 6. Publication
    success = publish_carousel(formatted_images, caption)
    print("Reussi !" if success else "Echec.")

    # 7. Historique (uniquement si succes, pour reessayer ce produit sinon)
    if success:
        state.setdefault("posted_urls", []).append(product["url"])
        save_state(state)


if __name__ == "__main__":
    print("Bot Loft Attitude v4 - Marques prioritaires + Lifestyle + Carrousel + Bio + Formatage Instagram")
    print(f"IG_USER_ID: {IG_USER_ID}")
    print(f"Token: {'OK' if IG_TOKEN else 'MANQUANT'}")
    print(f"Claude: {'OK' if CLAUDE_KEY else 'MANQUANT'}")
    print(f"Marques prioritaires: {', '.join(BRANDS.keys())}")
    print(f"URL publique images: {PUBLIC_BASE_URL if PUBLIC_BASE_URL else 'NON CONFIGUREE (a definir dans Railway)'}")
    print("Publication planifiee a 09:00\n")

    threading.Thread(target=run_web_server, daemon=True).start()

    daily_job()
    schedule.every().day.at("09:00").do(daily_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
