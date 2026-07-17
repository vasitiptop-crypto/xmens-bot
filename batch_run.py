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
from telegram.error import TelegramError
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
    "VIDEOS_PER_BATCH": 10,       # send 10 per Hugging Face run
    "MAX_SCAN_PER_RUN": 100,      # scan up to 100 cards to find 10 working ones
    "REQUEST_DELAY":    0.5,

    # database file
    "POSTED_DB":     "posted_videos.json",
    "DOWNLOAD_DIR":  "downloads",

    # ── ADD MORE SITES HERE ───────────────────────────────────
    "SOURCES": [
        {
            "name": "ViralKand",
            "url":  "https://viralkand.best/",
            "card_selector":      "article.loop-video a",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "div.responsive-player iframe, div.video-player iframe",
        },
        {
            "name": "AllSex",
            "url":  "https://allsex.xxx/",
            "card_selector":      "div.list-videos div.item",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "",
            "pagination_url":     "https://allsex.xxx/latest-updates/{page}/",
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
    ],
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
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
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection":      "keep-alive",
        "Referer":         referer or "https://mydesi.rest/",
    })
    return s


def fetch_soup(session, url):
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"Fetch failed {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
#  SCRAPE LISTING
# ─────────────────────────────────────────────────────────────
def scrape_source(session, source, page=1) -> list:
    # Use WordPress REST API for robust, Cloudflare-challenge-free scraping
    api_url = source["url"].rstrip("/") + f"/wp-json/wp/v2/posts?page={page}&per_page=20"
    videos = []
    try:
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
    # C: page regex
    matches = re.findall(MP4_REGEX, str(soup))
    valid_matches = [m.strip() for m in matches if "_preview" not in m]
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
            if "text/html" in ctype or "text/plain" in ctype:
                log.warning(f"Skipped download: Content-Type is '{ctype}', not a video.")
                return None
                
            # Content-length validation
            clength = int(r.headers.get("Content-Length", 0))
            if clength > 0 and clength < 50 * 1024:
                log.warning(f"Skipped download: file size is too small ({clength} bytes).")
                return None

            with open(dest, "wb") as f:
                for chunk in r.iter_content(2 * 1024 * 1024):
                    f.write(chunk)
                    
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

def compress_video(input_path: str, duration: float) -> str | None:
    """Compresses the video to be just under 49MB using ffmpeg re-encoding."""
    if duration <= 0:
        return None
        
    output_path = input_path.replace(".mp4", "_compressed.mp4")
    # Target size: 48.5 MB to be safe
    target_size_bytes = 48.5 * 1024 * 1024
    
    # Calculate target bitrate (bits per second)
    total_bitrate = (target_size_bytes * 8) / duration
    
    # Reserve 32k for audio (smaller = faster)
    audio_bitrate = 32 * 1024
    video_bitrate = int(total_bitrate - audio_bitrate)
    
    # Ensure video bitrate is reasonable (minimum 100 kbps)
    if video_bitrate < 100 * 1024:
        video_bitrate = 100 * 1024
        
    log.info(f"Compressing {os.path.basename(input_path)} to fit under 49MB (duration={duration:.1f}s, target_bitrate={video_bitrate//1024}k)...")
    
    try:
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-vf", "scale='min(640,iw)':-2",
            "-b:v", f"{video_bitrate}",
            "-maxrate", f"{video_bitrate}",
            "-bufsize", f"{video_bitrate * 2}",
            "-preset", "ultrafast",
            "-threads", "0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level:v", "4.0",
            "-c:a", "aac",
            "-b:a", "32k",
            "-movflags", "+faststart",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if res.returncode == 0 and os.path.exists(output_path):
            compressed_mb = os.path.getsize(output_path) / 1024 / 1024
            log.info(f"Compression completed successfully. Compressed size: {compressed_mb:.1f} MB")
            os.replace(output_path, input_path)
            return input_path
    except Exception as e:
        log.warning(f"ffmpeg compression failed: {e}")
        if os.path.exists(output_path):
            os.unlink(output_path)
    return None

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

    mb = Path(path).stat().st_size / 1024 / 1024
    need_compress = mb > 49
    
    if need_compress:
        log.info(f"File is {mb:.1f} MB (exceeds 49MB limit). Starting compression...")
        duration = metadata.get("duration", 0)
        if duration <= 0:
            # Fallback estimation: Assume a standard average streaming bitrate of 1.2 Mbps (150 KB/s)
            file_size_bytes = Path(path).stat().st_size
            duration = max(10.0, file_size_bytes / (150 * 1024))
            log.info(f"ffprobe failed or returned 0 duration. Estimating duration as {duration:.1f}s based on file size.")
            
        compressed_path = await loop.run_in_executor(None, compress_video, path, duration)
        if compressed_path:
            path = compressed_path
            mb = Path(path).stat().st_size / 1024 / 1024
            metadata = await loop.run_in_executor(None, get_video_metadata, path)
        else:
            log.warning("Compression failed. Skipping file.")
            return False

    # 2. Optimize video for streaming (skip if compress already added faststart)
    if not need_compress:
        path = await loop.run_in_executor(None, optimize_video, path)

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
    except TelegramError as e:
        log.error(f"Telegram error: {e}")
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

    # 2. Try direct URL upload first (async)
    log.info(f"[{video['id']}] Attempting direct URL upload: {mp4[:80]}...")
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
    except TelegramError as e:
        log.warning(f"[{video['id']}] Direct URL upload failed: {e}. Falling back to download-and-upload...")

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
    """Runs main() repeatedly for up to 4.3 minutes (260s) to simulate 24/7 continuous runs on GitHub Actions."""
    start_time = time.time()
    iteration = 1
    total_sent = 0
    
    log.info("Starting continuous uploader loop execution...")
    while time.time() - start_time < 260:
        log.info(f"\n--- Starting Loop Iteration {iteration} (Elapsed: {int(time.time() - start_time)}s) ---")
        sent = await main()
        total_sent += sent
        
        # If no new videos were sent in this run, exit early to save runner minutes
        if sent == 0:
            log.info("No new videos uploaded in this iteration. Exiting early.")
            break
            
        iteration += 1
        log.info("Sleeping 5 seconds before next check...")
        await asyncio.sleep(5)
        
    log.info(f"Continuous uploader run loop finished. Total uploaded: {total_sent}")
    # Write sent count to file for GitHub Actions to read
    try:
        with open("sent_count.txt", "w") as f:
            f.write(str(total_sent))
    except Exception as e:
        log.warning(f"Failed to write sent_count.txt: {e}")


if __name__ == "__main__":
    asyncio.run(main_loop())
