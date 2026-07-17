"""
batch_run.py — Single-run script for GitHub Actions/Hugging Face/Serv00.
Sends up to BATCH_SIZE videos then exits.
State (posted_videos.json) is saved locally.
"""

import asyncio
import time
import re
import json
import logging
import os
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError, RetryAfter
from dotenv import load_dotenv
import shutil

# Load environment variables from local .env config
load_dotenv()

# Dynamic path resolution for FFmpeg and FFprobe (highly robust for VPS/Serv00)
FFMPEG_PATH = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "/usr/local/bin/ffprobe"

# ─────────────────────────────────────────────────────────────
#  CONFIG — reads from environment variables (falsy fallback)
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN") or "8815719330:AAG2ZB8Helpzr1OKE65D_JXN19fWuZes9c8"
CHANNEL_ID = os.environ.get("CHANNEL_ID") or "-1003956199030"

CONFIG = {
    "BOT_TOKEN":        BOT_TOKEN,
    "CHANNEL_ID":       CHANNEL_ID,
    "VIDEOS_PER_BATCH": 20,       # send 20 per run for fast continuous uploads
    "MAX_SCAN_PER_RUN": 200,      # scan up to 200 cards to find working ones
    "REQUEST_DELAY":    0.2,

    # database file
    "POSTED_DB":     "posted_videos.json",
    "DOWNLOAD_DIR":  "downloads",

    # ── ADD MORE SITES HERE ───────────────────────────────────
    "SOURCES": [
        {
            "name": "DarkEro",
            "url":  "https://darkero.com/",
            "card_selector":      "h2.entry-title a",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "",
        },
        {
            "name": "SexyVideoIndian",
            "url":  "https://www.sexyvideoindian.com/",
            "card_selector":      "div.video-block a.thumb, div.post-card a, article a",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "div.responsive-player iframe, div.video-player iframe",
        },
        {
            "name": "DesiMMS",
            "url":  "https://www.desimms.com.co/",
            "card_selector":      "div.video-block a.thumb, div.post-card a, article.loop-video a",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "div.responsive-player iframe, div.video-player iframe",
        },
        {
            "name": "DesiTales2",
            "url":  "https://www.desitales2.com/videos/",
            "card_selector":      "div.item a",
            "video_tag_selector": "",
            "iframe_selector":    "",
            "pagination_url":     "https://www.desitales2.com/videos/page/{page}/",
            "no_api":             True,
        },
        {
            "name": "SpicyMMS",
            "url":  "https://www.spicymms.com/",
            "card_selector":      "div.item a, .thumb a",
            "video_tag_selector": "",
            "iframe_selector":    "",
            "pagination_url":     "https://www.spicymms.com/latest-updates/{page}/",
            "no_api":             True,
        },
    ],
}

USER_AGENT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
MP4_REGEX = r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*'

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────────────────────
def load_posted() -> set:
    p = Path(CONFIG["POSTED_DB"])
    if p.exists():
        try:
            return set(json.loads(p.read_text()).get("posted", []))
        except Exception:
            pass
    return set()


def save_posted(ids: set):
    Path(CONFIG["POSTED_DB"]).write_text(
        json.dumps({"posted": list(ids)}, indent=2)
    )
    log.info(f"Saved {len(ids)} posted IDs → {CONFIG['POSTED_DB']}")


# ─────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────
def make_session(referer="") -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
    })
    if referer:
        s.headers["Referer"] = referer
    return s


FREE_PROXIES = []

def get_free_proxies():
    global FREE_PROXIES
    if FREE_PROXIES:
        return FREE_PROXIES
        
    log.info("Fetching free proxy list for firewall bypass...")
    api_url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=yes&anonymity=all"
    try:
        r = requests.get(api_url, timeout=10)
        FREE_PROXIES = [p.strip() for p in r.text.split("\n") if p.strip()]
        log.info(f"Fetched {len(FREE_PROXIES)} free proxies.")
    except Exception as e:
        log.warning(f"Failed to fetch free proxies: {e}")
    return FREE_PROXIES

def fetch_soup(session, url):
    # 1. Try direct fetch first
    try:
        r = session.get(url, timeout=8)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"Direct fetch failed for {url}: {e}. Retrying with free proxies...")
        
    # 2. Try proxy fetch fallback
    proxies_list = get_free_proxies()
    if not proxies_list:
        return None
        
    # Try up to 15 proxies
    for p in proxies_list[:15]:
        log.info(f"Trying proxy {p} for {url}...")
        try:
            px = {"http": f"http://{p}", "https": f"http://{p}"}
            # Use direct requests call with proxy to avoid session conflicts
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, proxies=px, timeout=6)
            if r.status_code == 200:
                log.info(f"✅ Proxy fetch succeeded using {p}!")
                return BeautifulSoup(r.text, "html.parser")
            else:
                log.warning(f"Proxy {p} returned status code {r.status_code}")
        except Exception as ex:
            log.debug(f"Proxy {p} failed: {ex}")
            
    log.error(f"All proxies failed to fetch {url}.")
    return None


# ─────────────────────────────────────────────────────────────
#  SCRAPE LISTING
# ─────────────────────────────────────────────────────────────
def scrape_source(session, source, page=1) -> list:
    # Use WordPress REST API for robust, Cloudflare-challenge-free scraping
    api_url = source["url"].rstrip("/") + f"/wp-json/wp/v2/posts?page={page}&per_page=20"
    videos = []
    try:
        if source.get("no_api"):
            raise ValueError("WordPress REST API disabled for this source")
        r = session.get(api_url, timeout=20)
        r.raise_for_status()
        data = r.json()
        for post in data:
            title = post.get("title", {}).get("rendered", "")
            import html
            title = html.unescape(title)
            href = post.get("link", "").strip()
            if not href:
                continue
            vid_id = re.sub(r"[^a-zA-Z0-9_-]", "_",
                            source["name"] + "_" + href.split("//")[-1])[:100]
            videos.append({
                "id": vid_id,
                "url": href,
                "title": title[:200],
                "source": source
            })
    except Exception as e:
        log.warning(f"[{source['name']}] REST API failed for page {page}: {e}. Trying fallback HTML scrape...")
        # Fallback to HTML scraping
        url = source["url"]
        if page > 1:
            if "pagination_url" in source:
                url = source["pagination_url"].format(page=page)
            else:
                url = url.rstrip("/") + f"/page/{page}/"
        soup = fetch_soup(session, url)
        if soup:
            for card in soup.select(source["card_selector"]):
                href = ""
                if card.name != "a":
                    a_tag = card.select_one("a")
                    if a_tag:
                        href = a_tag.get("href", "").strip()
                else:
                    href = card.get("href", "").strip()
                if not href or href == "#":
                    continue
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(source["url"], href)
                vid_id = re.sub(r"[^a-zA-Z0-9_-]", "_",
                                source["name"] + "_" + href.split("//")[-1])[:100]
                
                # Title resolution
                title = ""
                img = card.select_one("img")
                if img:
                    title = img.get("alt") or img.get("title") or ""
                if not title:
                    title = card.get("title") or ""
                if not title:
                    parent = card.find_parent("div", class_="video-block")
                    if parent:
                        infos = parent.find("a", class_="infos")
                        if infos:
                            title = infos.get("title") or infos.get_text(strip=True)
                videos.append({
                    "id": vid_id,
                    "url": href,
                    "title": str(title or "Video")[:200],
                    "source": source
                })
    log.info(f"[{source['name']}] {len(videos)} videos found on page {page}.")
    return videos


# ─────────────────────────────────────────────────────────────
#  EXTRACT MP4
# ─────────────────────────────────────────────────────────────
def extract_mp4(session, page_url, source) -> str | None:
    soup = fetch_soup(session, page_url)
    if not soup:
        return None
    # A: video tag
    tag = soup.select_one(source.get("video_tag_selector", "video source"))
    if tag:
        src = (tag.get("src") or tag.get("data-src") or "").strip()
        if ".mp4" in src:
            return src
    # B: iframe
    isel = source.get("iframe_selector")
    if isel:
        iframe = soup.select_one(isel)
        if iframe:
            isrc = iframe.get("src", "").strip()
            if isrc:
                try:
                    r = session.get(isrc, timeout=8, headers={"Referer": page_url})
                    m = re.findall(MP4_REGEX, r.text)
                    if m:
                        return m[0].strip()
                except Exception:
                    pass
    # C: page regex (with advanced tokenized URL filtering for KVS)
    matches = re.findall(MP4_REGEX, str(soup))
    valid_matches = []
    for m in matches:
        m_str = m.strip()
        # Filter out preview formats, WebP/JPG screenshots, and preview strings
        if "_preview" in m_str or ".jpg" in m_str or ".png" in m_str or "preview.mp4" in m_str:
            continue
        valid_matches.append(m_str)
        
    # Prioritize URLs containing both "get_file" and "v-acctoken"
    token_matches = [m for m in valid_matches if "v-acctoken" in m]
    if token_matches:
        return token_matches[0]
        
    return valid_matches[0] if valid_matches else None


# ─────────────────────────────────────────────────────────────
#  DOWNLOAD
# ─────────────────────────────────────────────────────────────
def download_video(session, mp4_url, vid_id) -> str | None:
    dl = Path(CONFIG["DOWNLOAD_DIR"])
    dl.mkdir(exist_ok=True)
    dest = dl / f"{vid_id}.mp4"
    log.info(f"Downloading from: {mp4_url[:120]}")
    try:
        with session.get(mp4_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            
            # Content-type validation
            ctype = r.headers.get("Content-Type", "").lower()
            if "video" not in ctype:
                log.warning(f"Skipped download: Content-Type is '{ctype}', not a video.")
                return None
                
            # Content-length validation
            clength = int(r.headers.get("Content-Length", 0))
            if clength > 0:
                if clength < 50 * 1024:
                    log.warning(f"Skipped download: file size is too small ({clength} bytes).")
                    return None
                if clength > 50 * 1024 * 1024:
                    log.warning(f"Skipped download: file size exceeds 50MB limit ({clength / 1024 / 1024:.1f} MB).")
                    return None

            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(2 * 1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > 50 * 1024 * 1024:
                        log.warning("Skipped download: downloaded content size exceeded 50MB during stream.")
                        dest.unlink(missing_ok=True)
                        return None
                    
        # File size verification
        actual_size = dest.stat().st_size
        if actual_size < 50 * 1024:
            log.warning(f"Skipped download: final file size too small ({actual_size} bytes).")
            dest.unlink(missing_ok=True)
            return None

        mb = actual_size / 1024 / 1024
        log.info(f"Downloaded {mb:.1f} MB successfully.")
        return str(dest)
    except Exception as e:
        log.warning(f"Download failed: {e}")
        dest.unlink(missing_ok=True)
        return None


import subprocess

def get_video_metadata(path: str) -> dict:
    """Uses ffprobe to extract duration, width, and height."""
    try:
        cmd = [
            FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                    duration = float(stream.get("duration", 0) or data.get("format", {}).get("duration", 0))
                    return {"width": width, "height": height, "duration": int(duration)}
    except Exception as e:
        log.warning(f"ffprobe failed: {e}")
    return {}

def optimize_video(input_path: str) -> str:
    """Relocates the moov atom to the beginning of the MP4 file for fast start streaming."""
    output_path = input_path.replace(".mp4", "_optimized.mp4")
    try:
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-c", "copy", "-movflags", "+faststart",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if res.returncode == 0 and os.path.exists(output_path):
            log.info(f"Relocated moov atom successfully: {os.path.basename(output_path)}")
            os.replace(output_path, input_path)
            return input_path
    except Exception as e:
        log.warning(f"ffmpeg failed: {e}")
        if os.path.exists(output_path):
            os.unlink(output_path)
    return input_path


# ─────────────────────────────────────────────────────────────
#  UPLOAD (no caption)
# ─────────────────────────────────────────────────────────────
async def send_video(bot: Bot, path: str) -> bool:
    # 1. Get video metadata
    loop = asyncio.get_running_loop()
    metadata = await loop.run_in_executor(None, get_video_metadata, path)
    log.info(f"Original video metadata: {metadata}")

    # Optimize video for streaming
    path = await loop.run_in_executor(None, optimize_video, path)

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with open(path, "rb") as f:
                await bot.send_video(
                    chat_id=CONFIG["CHANNEL_ID"],
                    video=f,
                    caption="",
                    supports_streaming=True,
                    width=metadata.get("width"),
                    height=metadata.get("height"),
                    duration=metadata.get("duration"),
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                )
            log.info("✅ Upload OK")
            return True
        except RetryAfter as e:
            wait_time = e.retry_after + 2
            log.warning(f"Telegram rate limit hit (RetryAfter). Sleeping for {wait_time}s before attempt {attempt+2}...")
            await asyncio.sleep(wait_time)
        except TelegramError as e:
            log.error(f"Telegram error: {e}")
            break
    return False


# ─────────────────────────────────────────────────────────────
#  CONCURRENT WORKER FOR VPS/CLOUD RUN
# ─────────────────────────────────────────────────────────────
compress_semaphore = asyncio.Semaphore(2)

async def process_and_upload_video(bot: Bot, session, video: dict) -> bool:
    loop = asyncio.get_running_loop()
    
    # 1. Extract MP4 URL (synchronous network request, run in executor)
    mp4 = await loop.run_in_executor(None, extract_mp4, session, video["url"], video["source"])
    if not mp4:
        log.warning(f"[{video['id']}] No MP4 URL extracted — skipping.")
        return False

    # 2. Try direct URL upload first (async) with rate-limit retry
    max_attempts = 3
    for attempt in range(max_attempts):
        log.info(f"[{video['id']}] Attempting direct URL upload (Attempt {attempt+1}/{max_attempts}): {mp4[:80]}...")
        try:
            await bot.send_video(
                chat_id=CONFIG["CHANNEL_ID"],
                video=mp4,
                caption="",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
            log.info(f"[{video['id']}] ✅ Direct URL upload succeeded!")
            return True
        except RetryAfter as e:
            wait_time = e.retry_after + 2
            log.warning(f"[{video['id']}] Rate limit hit! Sleeping for {wait_time}s before retry...")
            await asyncio.sleep(wait_time)
        except TelegramError as e:
            log.warning(f"[{video['id']}] Direct URL upload failed: {e}. Falling back to download-and-upload...")
            break

    # 3. Fallback to download, compress (controlled by semaphore), and upload
    async with compress_semaphore:
        log.info(f"[{video['id']}] Downloading video for local processing...")
        path = await loop.run_in_executor(None, download_video, session, mp4, video["id"])
        if not path:
            log.warning(f"[{video['id']}] Download failed — skipping.")
            return False

        try:
            ok = await send_video(bot, path)
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception as ex:
                    log.warning(f"Failed to remove local file {path}: {ex}")
        return ok


async def main():
    bot    = Bot(token=CONFIG["BOT_TOKEN"])
    posted = load_posted()
    target = CONFIG["VIDEOS_PER_BATCH"]
    sent   = 0

    log.info(f"=== Batch run | target={target} | already posted={len(posted)} ===")
    log.info(f"Sources: {[s['name'] for s in CONFIG['SOURCES']]}")

    # Build a combined pool from ALL sources, round-robin style
    source_queues = {}
    for source in CONFIG["SOURCES"]:
        session = make_session(referer=source["url"])
        
        # Scrape page 1
        all_vids = scrape_source(session, source, page=1)
        new_vids = [v for v in all_vids if v["id"] not in posted]
        
        # If we need more videos, traverse older pages to find unique, unposted ones
        page = 2
        while len(new_vids) < target and page <= 1000:
            log.info(f"[{source['name']}] Only {len(new_vids)} new videos on page {page-1}. Scraping page {page} for older unposted videos...")
            time.sleep(CONFIG["REQUEST_DELAY"])
            page_vids = scrape_source(session, source, page=page)
            if not page_vids:
                break
            new_page_vids = [v for v in page_vids if v["id"] not in posted]
            new_vids.extend(new_page_vids)
            page += 1

        log.info(f"[{source['name']}] Total unique unposted videos found: {len(new_vids)}.")

        source_queues[source["name"]] = {
            "session": session,
            "queue": new_vids[:CONFIG["MAX_SCAN_PER_RUN"]],
        }

    # Round-robin: collect candidates up to MAX_SCAN_PER_RUN from each source
    source_names = list(source_queues.keys())
    candidates = []
    attempts = 0
    max_attempts = CONFIG["MAX_SCAN_PER_RUN"] * len(source_names)

    while len(candidates) < CONFIG["MAX_SCAN_PER_RUN"] and attempts < max_attempts:
        made_progress = False
        for sname in source_names:
            q = source_queues[sname]["queue"]
            sess = source_queues[sname]["session"]
            if not q:
                continue
            video = q.pop(0)
            attempts += 1
            made_progress = True
            candidates.append((video, sess))
        if not made_progress:
            break

    log.info(f"Collected {len(candidates)} candidates. Processing in parallel batches of 10...")

    # Process candidates in parallel batches of 10
    batch_size = 10
    for i in range(0, len(candidates), batch_size):
        if sent >= target:
            break

        current_batch = candidates[i:i+batch_size]
        log.info(f"\n=== Processing parallel batch of {len(current_batch)} videos (Uploaded: {sent}/{target}) ===")

        # Create concurrent upload tasks
        tasks = []
        for video, sess in current_batch:
            tasks.append(process_and_upload_video(bot, sess, video))

        # Run batch concurrently
        results = await asyncio.gather(*tasks)

        # Record successes
        for (video, sess), success in zip(current_batch, results):
            if success:
                posted.add(video["id"])
                sent += 1

        # Save progress back to database after each batch
        save_posted(posted)

    log.info(f"=== Done: {sent}/{target} videos sent ===")
    return sent


async def main_loop():
    """Runs main() repeatedly for up to 25 minutes (1500s) to maximize uploads per GitHub Actions run.
    Self-chains via workflow dispatch so videos upload continuously 24/7."""
    MAX_RUN_SECONDS = 1500  # 25 minutes — leave 5 min buffer for git commit + chaining
    MAX_EMPTY_RETRIES = 3   # retry this many times before giving up when no videos found
    
    start_time = time.time()
    iteration = 1
    total_sent = 0
    consecutive_empty = 0
    
    log.info("Starting continuous uploader loop (25 min max)...")
    
    while time.time() - start_time < MAX_RUN_SECONDS:
        # Rotate source order each iteration so we don't always hammer the same site first
        rotation = (iteration - 1) % len(CONFIG["SOURCES"])
        CONFIG["SOURCES"] = CONFIG["SOURCES"][rotation:] + CONFIG["SOURCES"][:rotation]
        
        elapsed = int(time.time() - start_time)
        remaining = MAX_RUN_SECONDS - (time.time() - start_time)
        log.info(f"\n--- Iteration {iteration} | Elapsed: {elapsed}s | Remaining: {int(remaining)}s | Total uploaded: {total_sent} ---")
        
        sent = await main()
        total_sent += sent
        
        if sent == 0:
            consecutive_empty += 1
            if consecutive_empty >= MAX_EMPTY_RETRIES:
                log.info(f"No videos found for {MAX_EMPTY_RETRIES} consecutive iterations. Stopping loop.")
                break
            backoff = 10 * consecutive_empty  # 10s, 20s, 30s
            log.info(f"No videos this iteration ({consecutive_empty}/{MAX_EMPTY_RETRIES} empty). "
                     f"Retrying in {backoff}s...")
            await asyncio.sleep(backoff)
        else:
            consecutive_empty = 0  # reset on success
            log.info("Sleeping 5 seconds before next iteration...")
            await asyncio.sleep(5)
        
        iteration += 1
    
    log.info(f"=== Continuous uploader finished. Total uploaded: {total_sent} across {iteration - 1} iterations ===")
    
    # Write sent count to file for GitHub Actions self-chaining step
    try:
        with open("sent_count.txt", "w") as f:
            f.write(str(total_sent))
    except Exception as e:
        log.warning(f"Failed to write sent_count.txt: {e}")


if __name__ == "__main__":
    asyncio.run(main_loop())
