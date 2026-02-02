import os
import json
import requests
import feedparser
import time
import re
import random
import warnings 
from datetime import datetime
from slugify import slugify
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- SUPPRESS WARNINGS ---
warnings.filterwarnings("ignore", category=FutureWarning)

# --- CONFIGURATION ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

WEBSITE_URL = "https://football-daily-two.vercel.app" 
INDEXNOW_KEY = "b0c1cebbd6004e1a9e25605cc51b2937" 
GOOGLE_JSON_KEY = os.environ.get("GOOGLE_INDEXING_KEY", "") 

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    # Jangan exit, biarkan error handling yang menangani nanti

AUTHOR_PROFILES = [
    "Dave Harsya (Senior Analyst)", "Sarah Jenkins (Chief Editor)",
    "Luca Romano (Transfer Specialist)", "Marcus Reynolds (Premier League Correspondent)",
    "Elena Petrova (Tactical Expert)", "Ben Foster (footballs Journalist)",
    "Mateo Rodriguez (European Football Analyst)"
]

VALID_CATEGORIES = [
    "Transfer News", 
    "Premier League", 
    "Champions League", 
    "La Liga", 
    "International", 
    "Tactical Analysis"
]

RSS_SOURCES = {
    "US Source": "https://news.google.com/rss/search?q=Sports+News&hl=en-US&gl=US&ceid=US:en",
    "UK Source": "https://news.google.com/rss/search?q=Sports+News&hl=en-GB&gl=GB&ceid=GB:en"
}

FALLBACK_IMAGES = [
    "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=1200&q=80",
    "https://images.unsplash.com/photo-1431324155629-1a6deb1dec8d?auto=format&fit=crop&w=1200&q=80"
]

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"

TARGET_PER_SOURCE = 3 

# --- HELPER FUNCTIONS ---

def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(title, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    memory[title] = f"/{slug}/"
    if len(memory) > 50:
        memory = dict(list(memory.items())[-50:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_formatted_internal_links():
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return ""
    if len(items) > 3: items = random.sample(items, 3)
    formatted_links = []
    for title, url in items:
        formatted_links.append(f"- [{title}]({url})")
    return "\n".join(formatted_links)

def clean_text_for_yaml(text):
    """Membersihkan teks agar aman masuk ke YAML Frontmatter"""
    if not text: return ""
    text = str(text)
    text = text.replace('"', "'") # Ubah double quote jadi single
    text = text.replace('\n', ' ') 
    text = text.replace('\\', '')
    return re.sub(r'\s+', ' ', text).strip()

# --- IMAGE ENGINE (DIKEMBALIKAN SESUAI KODE ASLI ANDA) ---
def download_and_optimize_image(query, filename):
    if not filename.endswith(".webp"):
        filename = filename.rsplit(".", 1)[0] + ".webp"
    
    # Cek exist
    if os.path.exists(f"{IMAGE_DIR}/{filename}"):
        return f"/images/{filename}"

    # Prompt sesuai kode awal Anda
    base_prompt = f"{query} footballs action photography, stadium atmosphere, 8k resolution, highly detailed, photorealistic, cinematic lighting, sharp focus"
    safe_prompt = base_prompt.replace(" ", "%20")[:250]
    
    print(f"      🎨 Generating HQ Image: {base_prompt[:40]}...")

    for attempt in range(3):
        seed = random.randint(1, 999999)
        # URL sesuai kode awal Anda
        image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1280&height=720&nologo=true&model=flux-realism&seed={seed}&enhance=true"
        
        try:
            response = requests.get(image_url, timeout=120)
            if response.status_code == 200:
                if "image" not in response.headers.get("content-type", ""):
                    time.sleep(2); continue

                img = Image.open(BytesIO(response.content)).convert("RGB")
                # Resize sesuai kode awal Anda
                img = img.resize((1200, 675), Image.Resampling.LANCZOS)
                
                # Enhance sesuai kode awal Anda
                enhancer_sharp = ImageEnhance.Sharpness(img)
                img = enhancer_sharp.enhance(1.3)
                enhancer_color = ImageEnhance.Color(img)
                img = enhancer_color.enhance(1.1)

                output_path = f"{IMAGE_DIR}/{filename}"
                # Save params sesuai kode awal Anda
                img.save(output_path, "WEBP", quality=75, method=6, optimize=True)
                
                print(f"      📸 HQ Image Saved: {filename}")
                return f"/images/{filename}" 

        except Exception as e:
            time.sleep(5)
    
    print("      ❌ Image failed after 3 attempts. Using Fallback.")
    return random.choice(FALLBACK_IMAGES)

# --- INDEXING ENGINE ---
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
        requests.post(endpoint, json=data, headers={'Content-Type': 'application/json; charset=utf-8'})
        print(f"      🚀 IndexNow Submitted")
    except: pass

def submit_to_google(url):
    # Import di dalam fungsi untuk menghindari error jika library tidak ada
    try:
        from oauth2client.service_account import ServiceAccountCredentials
        from googleapiclient.discovery import build
        
        if not GOOGLE_JSON_KEY: return
        creds_dict = json.loads(GOOGLE_JSON_KEY)
        SCOPES = ["https://www.googleapis.com/auth/indexing"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
        service = build("indexing", "v3", credentials=credentials)
        body = {"url": url, "type": "URL_UPDATED"}
        service.urlNotifications().publish(body=body).execute()
        print(f"      🚀 Google Indexing Submitted")
    except Exception as e:
        pass

# --- AI WRITER ENGINE (DIPERBAIKI UNTUK JSON DAN IKLAN) ---
def get_groq_article_seo(title, summary, link, internal_links_block, author_name):
    valid_cats_str = ", ".join(VALID_CATEGORIES)
    
    # Prompt diperbaiki agar:
    # 1. Output JSON valid (mengatasi error parsing).
    # 2. Menyisipkan {{< ad >}} secara otomatis.
    system_prompt = f"""
    You are {author_name} for 'football Daily'.
    
    TASK: Write a 1000+ word viral article based on the news.
    
    # CRITICAL INSTRUCTION FOR HEADERS (H2/H3):
    - **NEVER** use generic headers like "Analysis", "Conclusion".
    - **ALWAYS** write unique, catchy headers.
    
    # CRITICAL INSTRUCTION FOR ADS:
    - You MUST insert the exact code `{{< ad >}}` exactly TWICE in the article body.
    - Once after the introduction.
    - Once before the conclusion.
    
    # CRITICAL INSTRUCTION FOR CATEGORY:
    You must classify this news into EXACTLY ONE of these categories: [{valid_cats_str}].
    
    OUTPUT FORMAT (Strict JSON):
    {{
        "title": "Headline (No Markdown)",
        "description": "Meta description (No quotes)",
        "category": "CHOOSE_FROM_LIST_ABOVE",
        "main_keyword": "Entity Name",
        "lsi_keywords": ["keyword1"],
        "image_alt": "Descriptive text for image",
        "content": "Markdown content here including {{< ad >}}"
    }}

    # INTERNAL LINKS BLOCK:
    ### Read More
    {internal_links_block}
    """

    user_prompt = f"News: {title}\nSummary: {summary}\nLink: {link}\nWrite it now."

    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        try:
            print(f"      🤖 AI Writing (Unique Headers & Categorizing)...")
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.8,
                max_tokens=7500,
                response_format={"type": "json_object"} # FORCE JSON OBJECT
            )
            return completion.choices[0].message.content
        except RateLimitError: continue
        except BadRequestError as e:
            if "organization_restricted" in str(e):
                print(f"      ⚠️ Critical: API Key Rejected (Organization Restricted).")
            continue
        except Exception as e: 
            print(f"      ⚠️ Error: {e}")
            continue
            
    return None

# --- MAIN LOOP ---
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    total_generated = 0

    for source_name, rss_url in RSS_SOURCES.items():
        print(f"\n📡 Fetching Source: {source_name}...")
        try:
            feed = feedparser.parse(rss_url)
        except: continue
        
        if not feed or not feed.entries: continue

        cat_success_count = 0
        for entry in feed.entries:
            if cat_success_count >= TARGET_PER_SOURCE: break

            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title, max_length=60, word_boundary=True)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"): continue

            current_author = random.choice(AUTHOR_PROFILES)
            print(f"   🔥 Processing: {clean_title[:40]}... (Author: {current_author})")
            
            links_block = get_formatted_internal_links()
            
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, links_block, current_author)
            
            if not raw_response:
                print("      ❌ Failed to get AI response (Check API Key). Skipping.")
                continue

            try:
                data = json.loads(raw_response)
            except:
                print("      ❌ JSON Parsing Failed. Skipping.")
                continue

            # Validasi Kategori
            if data.get('category') not in VALID_CATEGORIES:
                data['category'] = "International"

            # Image Gen (Menggunakan Fungsi Asli Anda)
            img_name = f"{slug}.webp"
            keyword_for_image = data.get('main_keyword') or clean_title
            final_img = download_and_optimize_image(keyword_for_image, img_name)
            
            # Data Cleaning untuk YAML
            title = clean_text_for_yaml(data.get('title', clean_title))
            desc = clean_text_for_yaml(data.get('description', entry.summary))
            alt_text = clean_text_for_yaml(data.get('image_alt', clean_title))
            
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
            tags_list = data.get('lsi_keywords', [])
            if data.get('main_keyword'): tags_list.append(data['main_keyword'])
            tags_str = json.dumps(tags_list)
            
            md = f"""---
title: "{title}"
date: {date}
lastmod: {date}
author: "{current_author}"
categories: ["{data['category']}"]
tags: {tags_str}
featured_image: "{final_img}"
featured_image_alt: "{alt_text}"
description: "{desc}"
slug: "{slug}"
url: "/{slug}/"
draft: false
---

{data.get('content', '')}

---
*Source: Analysis by {current_author} based on international reports and [Original Story]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            if 'title' in data: save_link_to_memory(title, slug)
            
            print(f"   ✅ Published: {filename} (Category: {data['category']})")
            
            full_url = f"{WEBSITE_URL}/{slug}/"
            submit_to_indexnow(full_url)
            submit_to_google(full_url)
            
            cat_success_count += 1
            total_generated += 1
            time.sleep(5)

    print(f"\n🎉 DONE! Total: {total_generated}")

if __name__ == "__main__":
    main()
