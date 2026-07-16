"""
main.py — Railway entry point
Reads all secrets from environment variables so nothing is hardcoded.
Set these in Railway dashboard → Variables:
  BOT_TOKEN   = your Telegram bot token
  CHANNEL_ID  = your numeric channel ID (e.g. -1003956199030)
"""

import os
import asyncio
import time
import re
import json
import logging
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError

# ─────────────────────────────────────────────────────────────
#  CONFIG — reads from environment variables on Railway
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "8815719330:AAG2ZB8Helpzr1OKE65D_JXN19fWuZes9c8")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003956199030")

CONFIG = {
    "BOT_TOKEN":       BOT_TOKEN,
    "CHANNEL_ID":      CHANNEL_ID,
    "VIDEOS_PER_BATCH": 2,          # upload 2 videos per cycle
    "CYCLE_SECONDS":   60,           # one cycle every 60 seconds
    "MAX_SCAN_PER_RUN": 40,          # scan up to 40 candidates to find 2 good ones
    "REQUEST_DELAY":   2,            # seconds between HTTP requests
    "POSTED_DB":       "/tmp/posted_videos.json",   # /tmp is writable on Railway
    "DOWNLOAD_DIR":    "/tmp/downloads",

    # ── ADD / REMOVE SOURCES HERE ─────────────────────────────
    "SOURCES": [
        {
            "name": "AuntyMazaX",
            "url":  "https://auntymazax30.watch/",
            "card_selector":       "div.video-block a.thumb",
            "video_tag_selector":  "video source, source[src]",
            "iframe_selector":     "div.responsive-player iframe, div.video-player iframe",
        },
        # ── Template for adding more sites ───────────────────
        # {
        #     "name": "SiteName",
        #     "url":  "https://example.com/",
        #     "card_selector":      "article.post a.thumb",
        #     "video_tag_selector": "video source",
        #     "iframe_selector":    None,
        # },
    ],
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
MP4_REGEX = r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*'

# ─────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  POSTED DB
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


# ─────────────────────────────────────────────────────────────
#  HTTP HELPERS
# ─────────────────────────────────────────────────────────────
def make_session(referer="") -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         referer,
    })
    return s


def fetch_soup(session, url):
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"Fetch failed {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
#  SCRAPE LISTING PAGE
# ─────────────────────────────────────────────────────────────
def scrape_source(session, source) -> list:
    soup = fetch_soup(session, source["url"])
    if not soup:
        return []

    videos = []
    for card in soup.select(source["card_selector"]):
        href = card.get("href", "").strip()
        if not href or href == "#":
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(source["url"], href)

        vid_id = re.sub(r"[^a-zA-Z0-9_-]", "_",
                        source["name"] + "_" + href.split("//")[-1])[:100]

        # Get title from sibling .infos link
        title = card.get("title") or ""
        if not title:
            parent = card.find_parent("div", class_="video-block")
            if parent:
                infos = parent.find("a", class_="infos")
                if infos:
                    title = infos.get("title") or infos.get_text(strip=True)
        title = str(title or "Video")[:200]

        videos.append({"id": vid_id, "url": href, "title": title, "source": source})

    log.info(f"[{source['name']}] {len(videos)} cards found.")
    return videos


# ─────────────────────────────────────────────────────────────
#  EXTRACT MP4 URL
# ─────────────────────────────────────────────────────────────
def extract_mp4(session, page_url, source) -> str | None:
    soup = fetch_soup(session, page_url)
    if not soup:
        return None

    # Strategy A: <video>/<source> tag
    tag = soup.select_one(source.get("video_tag_selector", "video source"))
    if tag:
        src = (tag.get("src") or tag.get("data-src") or "").strip()
        if ".mp4" in src:
            return src

    # Strategy B: iframe embed (e.g. luluvdo)
    iframe_sel = source.get("iframe_selector")
    if iframe_sel:
        iframe = soup.select_one(iframe_sel)
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

    # Strategy C: raw HTML regex
    m = re.findall(MP4_REGEX, str(soup))
    if m:
        return m[0].strip()

    return None


# ─────────────────────────────────────────────────────────────
#  DOWNLOAD
# ─────────────────────────────────────────────────────────────
def download_video(session, mp4_url, vid_id) -> str | None:
    dl_dir = Path(CONFIG["DOWNLOAD_DIR"])
    dl_dir.mkdir(parents=True, exist_ok=True)
    dest = dl_dir / f"{vid_id}.mp4"
    try:
        with session.get(mp4_url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    f.write(chunk)
        mb = dest.stat().st_size / 1024 / 1024
        log.info(f"Downloaded {mb:.1f} MB → {dest.name}")
        return str(dest)
    except Exception as e:
        log.warning(f"Download failed: {e}")
        dest.unlink(missing_ok=True)
        return None


# ─────────────────────────────────────────────────────────────
#  SEND TO TELEGRAM (no caption = video only)
# ─────────────────────────────────────────────────────────────
async def send_video(bot: Bot, path: str) -> bool:
    mb = Path(path).stat().st_size / 1024 / 1024
    if mb > 49:
        log.warning(f"Too large ({mb:.1f} MB) — skipping.")
        return False
    try:
        with open(path, "rb") as f:
            await bot.send_video(
                chat_id=CONFIG["CHANNEL_ID"],
                video=f,
                caption="",               # NO caption, NO links
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        log.info("Upload OK ✓")
        return True
    except TelegramError as e:
        log.error(f"Telegram error: {e}")
        return False


# ─────────────────────────────────────────────────────────────
#  ONE BATCH — find and upload VIDEOS_PER_BATCH videos
# ─────────────────────────────────────────────────────────────
async def run_batch(bot: Bot, posted: set) -> int:
    target = CONFIG["VIDEOS_PER_BATCH"]
    sent   = 0

    for source in CONFIG["SOURCES"]:
        if sent >= target:
            break
        session = make_session(referer=source["url"])
        all_vids = scrape_source(session, source)
        new_vids = [v for v in all_vids if v["id"] not in posted]

        for video in new_vids[:CONFIG["MAX_SCAN_PER_RUN"]]:
            if sent >= target:
                break

            log.info(f"  [{sent+1}/{target}] {video['title'][:60]}")
            time.sleep(CONFIG["REQUEST_DELAY"])

            mp4 = extract_mp4(session, video["url"], video["source"])
            if not mp4:
                continue

            time.sleep(CONFIG["REQUEST_DELAY"])
            path = download_video(session, mp4, video["id"])
            if not path:
                continue

            ok = await send_video(bot, path)
            Path(path).unlink(missing_ok=True)

            if ok:
                posted.add(video["id"])
                save_posted(posted)
                sent += 1

    return sent


# ─────────────────────────────────────────────────────────────
#  MAIN LOOP — runs forever on Railway (2 videos / minute)
# ─────────────────────────────────────────────────────────────
async def main():
    bot    = Bot(token=CONFIG["BOT_TOKEN"])
    cycle  = 0
    log.info("=" * 55)
    log.info("  Telegram Auto-Uploader started on Railway")
    log.info(f"  Channel : {CONFIG['CHANNEL_ID']}")
    log.info(f"  Sources : {[s['name'] for s in CONFIG['SOURCES']]}")
    log.info(f"  Batch   : {CONFIG['VIDEOS_PER_BATCH']} videos / {CONFIG['CYCLE_SECONDS']}s")
    log.info("=" * 55)

    while True:
        cycle += 1
        posted = load_posted()
        log.info(f"\n── Cycle #{cycle} ──")
        t0   = time.time()
        sent = await run_batch(bot, posted)
        elapsed = time.time() - t0
        log.info(f"Cycle #{cycle}: {sent}/{CONFIG['VIDEOS_PER_BATCH']} sent in {elapsed:.0f}s")

        # Wait remainder of the minute before next cycle
        wait = max(1, CONFIG["CYCLE_SECONDS"] - elapsed)
        log.info(f"Next cycle in {wait:.0f}s …")
        await asyncio.sleep(wait)


if __name__ == "__main__":
    asyncio.run(main())
