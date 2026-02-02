import os
import json
import requests
import feedparser
import time
import re
import random
import warnings
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageEnhance

# Library eksternal (pastikan sudah pip install)
from slugify import slugify  # pip install python-slugify
from groq import Groq, RateLimitError # pip install groq

# Google Indexing (Optional)
try:
    from oauth2client.service_account import ServiceAccountCredentials
    from googleapiclient.discovery import build
except ImportError:
    pass

# --- SUPPRESS WARNINGS ---
warnings.filterwarnings("ignore")

# ==========================================
# 1. KONFIGURASI UTAMA
# ==========================================

# API KEYS (Sebaiknya gunakan Environment Variables, tapi ini membaca dari OS atau String)
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "gsk_...") # Ganti jika hardcode
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

# DOMAIN & KUNCI
WEBSITE_URL = "https://football-daily-two.vercel.app"
INDEXNOW_KEY = "b0c1cebbd6004e1a9e25605cc51b2937"
GOOGLE_JSON_KEY = os.environ.get("GOOGLE_INDEXING_KEY", "") # JSON content as string

if not GROQ_API_KEYS:
    # Fallback testing key (HAPUS SAAT PRODUCTION)
    print("⚠️ Warning: No Groq API Key found in environment.")

# TIM PENULIS
AUTHOR_PROFILES = [
    "Dave Harsya (Senior Analyst)", "Sarah Jenkins (Chief Editor)",
    "Luca Romano (Transfer Specialist)", "Marcus Reynolds (Premier League Correspondent)",
    "Elena Petrova (Tactical Expert)", "Ben Foster (Football Journalist)"
]

# KATEGORI RESMI (Sesuai Menu HTML)
VALID_CATEGORIES = [
    "Premier League", 
    "Champions League", 
    "La Liga", 
    "Transfer News", 
    "International", 
    "Tactical Analysis"
]

# SUMBER BERITA
RSS_SOURCES = {
    "US Source": "https://news.google.com/rss/search?q=Football+News+after:24h&hl=en-US&gl=US&ceid=US:en",
    "UK Source": "https://news.google.com/rss/search?q=Premier+League+News+after:24h&hl=en-GB&gl=GB&ceid=GB:en"
}

# FOLDER STRUKTUR
CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"

TARGET_PER_SOURCE = 2  # Jumlah artikel per sumber RSS

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def clean_text_for_yaml(text):
    """
    Sangat Penting: Membersihkan teks agar aman masuk ke Frontmatter YAML.
    Mengubah kutip dua (") menjadi kutip satu (') agar tidak error.
    """
    if not text: return ""
    text = str(text)
    text = text.replace('"', "'")      # Ubah double quote jadi single
    text = text.replace('\n', ' ')     # Hapus enter
    text = text.replace('\\', '')      # Hapus backslash
    text = re.sub(r'\s+', ' ', text).strip() # Hapus spasi berlebih
    return text

def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(title, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    # Simpan dengan trailing slash sesuai standar Hugo
    memory[title] = f"/{slug}/"
    if len(memory) > 60:
        memory = dict(list(memory.items())[-60:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_formatted_internal_links():
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return ""
    if len(items) > 4: items = random.sample(items, 4)
    formatted_links = []
    for title, url in items:
        # Format list Markdown
        formatted_links.append(f"- [{title}]({url})")
    return "\n".join(formatted_links)

# ==========================================
# 3. ENGINE GAMBAR (POLLINATIONS)
# ==========================================

def download_and_optimize_image(query, filename):
    if not filename.endswith(".webp"):
        filename = filename.rsplit(".", 1)[0] + ".webp"
    
    # Cek jika gambar sudah ada
    if os.path.exists(f"{IMAGE_DIR}/{filename}"):
        return f"/images/{filename}"

    # Prompt Engineering untuk Gambar
    base_prompt = f"{query} soccer match action, dynamic angle, 8k resolution, stadium background, cinematic lighting, photorealistic, sports photography"
    safe_prompt = base_prompt.replace(" ", "%20")[:300]
    
    print(f"      🎨 Generating Image for: {query[:25]}...")

    for attempt in range(2):
        seed = random.randint(1, 999999)
        # Menggunakan model flux-realism untuk hasil lebih nyata
        image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1200&height=675&nologo=true&model=flux-realism&seed={seed}&enhance=true"
        
        try:
            response = requests.get(image_url, timeout=45)
            if response.status_code == 200 and "image" in response.headers.get("content-type", ""):
                img = Image.open(BytesIO(response.content)).convert("RGB")
                
                # Optimasi Gambar
                img = img.resize((1200, 675), Image.Resampling.LANCZOS)
                img = ImageEnhance.Sharpness(img).enhance(1.2)
                img = ImageEnhance.Color(img).enhance(1.1)

                output_path = f"{IMAGE_DIR}/{filename}"
                img.save(output_path, "WEBP", quality=80, method=6)
                print(f"      📸 Image Saved: {filename}")
                return f"/images/{filename}" 
        except Exception as e:
            time.sleep(2)
    
    # Fallback Image jika gagal
    print("      ⚠️ Image gen failed, using fallback.")
    return "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=1200&q=80"

# ==========================================
# 4. ENGINE ARTIKEL (GROQ AI)
# ==========================================

def get_groq_article_seo(title, summary, link, internal_links_block, author_name):
    valid_cats_str = ", ".join(VALID_CATEGORIES)
    
    system_prompt = f"""
    You are {author_name}, a professional sports journalist for 'Football Daily'.
    
    TASK: Write a viral news article based on the provided news snippet.
    
    OUTPUT FORMAT (Strict JSON Only):
    {{
        "title": "A Catchy, Clickbait-style Headline (Max 60 chars, NO Markdown)",
        "description": "Engaging meta description (Max 150 chars, NO quotes)",
        "category": "Select ONE exact category from: [{valid_cats_str}]",
        "main_keyword": "Main Player or Team Name",
        "lsi_keywords": ["keyword1", "keyword2", "keyword3"],
        "image_alt": "Descriptive Alt Text for the featured image",
        "content": "The full article content in Markdown format"
    }}

    CONTENT WRITING GUIDELINES:
    1. **Structure**: 
       - Opening Paragraph (Hook)
       - `{{< ad >}}` (MUST INSERT HERE)
       - Detailed Body Paragraphs with H2 (##) Subtitles
       - Key Stats Table (Markdown) if applicable
       - `{{< ad >}}` (MUST INSERT HERE AGAIN)
       - Conclusion
    2. **Tone**: Professional, energetic, and factual.
    3. **Ads**: You MUST insert the shortcode `{{< ad >}}` exactly twice in the content.
    4. **Links**: Add the provided internal links under a `### Read More` section at the very bottom.
    
    INTERNAL LINKS TO ADD AT BOTTOM:
    {internal_links_block}
    """

    user_prompt = f"News: {title}\nSummary: {summary}\nOriginal Link: {link}"

    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        try:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=6000,
                response_format={"type": "json_object"} # PENTING: Mencegah JSON error
            )
            return completion.choices[0].message.content
        except RateLimitError:
            print("      ⏳ Rate Limit hit, switching key...")
            continue
        except Exception as e: 
            print(f"      ⚠️ AI Error: {e}")
            continue
            
    return None

# ==========================================
# 5. ENGINE INDEXING
# ==========================================

def submit_to_indexnow(url):
    try:
        endpoint = "https://api.indexnow.org/indexnow"
        host = WEBSITE_URL.replace("https://", "").replace("http://", "")
        data = {
            "host": host,
            "key": INDEXNOW_KEY,
            "keyLocation": f"https://{host}/{INDEXNOW_KEY}.txt",
            "urlList": [url]
        }
        requests.post(endpoint, json=data, headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=10)
        print(f"      🚀 IndexNow Submitted")
    except: pass

def submit_to_google(url):
    if not GOOGLE_JSON_KEY: return
    try:
        creds_dict = json.loads(GOOGLE_JSON_KEY)
        SCOPES = ["https://www.googleapis.com/auth/indexing"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
        service = build("indexing", "v3", credentials=credentials)
        body = {"url": url, "type": "URL_UPDATED"}
        service.urlNotifications().publish(body=body).execute()
        print(f"      🚀 Google Indexing Submitted")
    except Exception as e:
        pass

# ==========================================
# 6. MAIN PROCESS
# ==========================================

def main():
    print("🔥 STARTING AUTOMATION ENGINE...")
    
    # Buat folder jika belum ada
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    total_new = 0

    for source_name, rss_url in RSS_SOURCES.items():
        print(f"\n📡 Fetching RSS: {source_name}...")
        try:
            feed = feedparser.parse(rss_url)
        except:
            print("   ❌ Failed to fetch RSS")
            continue

        if not feed.entries: continue

        source_count = 0
        for entry in feed.entries:
            if source_count >= TARGET_PER_SOURCE: break

            # 1. Siapkan Judul & Slug
            raw_title = entry.title.split(" - ")[0]
            slug = slugify(raw_title, max_length=60, word_boundary=True)
            filename = f"{slug}.md"
            file_path = f"{CONTENT_DIR}/{filename}"

            # 2. Cek Duplikat
            if os.path.exists(file_path): 
                print(f"   ⏭️  Skipped (Exists): {slug}")
                continue

            current_author = random.choice(AUTHOR_PROFILES)
            print(f"   🤖 Processing: {raw_title[:40]}...")
            
            # 3. Generate Artikel via AI
            links_block = get_formatted_internal_links()
            json_response = get_groq_article_seo(raw_title, entry.summary, entry.link, links_block, current_author)
            
            if not json_response: continue

            # 4. Parsing JSON
            try:
                data = json.loads(json_response)
            except json.JSONDecodeError:
                print("      ❌ JSON Parsing Failed. Skipping.")
                continue

            # 5. Validasi Data
            cat = data.get('category', 'International')
            if cat not in VALID_CATEGORIES: cat = 'International'
            
            title = clean_text_for_yaml(data.get('title', raw_title))
            desc = clean_text_for_yaml(data.get('description', entry.summary))
            alt_text = clean_text_for_yaml(data.get('image_alt', title))
            
            # Keyword Image
            keyword_img = data.get('main_keyword') or title
            img_path = download_and_optimize_image(keyword_img, f"{slug}.webp")
            
            # Tags ke Format JSON String
            tags_list = data.get('lsi_keywords', [])
            tags_json = json.dumps(tags_list)
            
            # Tanggal ISO 8601
            pub_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")

            # 6. Tulis ke File .md
            # Perhatikan: {{< ad >}} sudah ada di dalam data['content'] karena instruksi prompt
            md_content = f"""---
title: "{title}"
date: {pub_date}
lastmod: {pub_date}
author: "{current_author}"
categories: ["{cat}"]
tags: {tags_json}
featured_image: "{img_path}"
featured_image_alt: "{alt_text}"
description: "{desc}"
slug: "{slug}"
url: "/{slug}/"
draft: false
---

{data.get('content', '')}

---
*Source: Analysis by {current_author} based on reports from [Original Source]({entry.link}).*
"""

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            
            # 7. Post-Processing
            save_link_to_memory(title, slug)
            print(f"   ✅ Published: {filename}")
            
            full_url = f"{WEBSITE_URL}/{slug}/"
            submit_to_indexnow(full_url)
            submit_to_google(full_url)
            
            source_count += 1
            total_new += 1
            
            # Delay agar tidak kena ban IP/API limit
            time.sleep(5)

    print(f"\n🎉 DONE! Total Articles Created: {total_new}")

if __name__ == "__main__":
    main()
