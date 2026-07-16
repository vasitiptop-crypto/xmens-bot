"""
batch_run.py — Single-run script for GitHub Actions.
Sends up to BATCH_SIZE videos then exits.
State (posted_videos.json) is committed back to the repo by the workflow.
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

# ─────────────────────────────────────────────────────────────
#  CONFIG — reads from GitHub Actions secrets (env vars)
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "8815719330:AAG2ZB8Helpzr1OKE65D_JXN19fWuZes9c8")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003956199030")

CONFIG = {
    "BOT_TOKEN":        BOT_TOKEN,
    "CHANNEL_ID":       CHANNEL_ID,
    "VIDEOS_PER_BATCH": 2,        # send 2 per GitHub Actions run (every 5 min)
    "MAX_SCAN_PER_RUN": 40,       # scan up to 40 cards to find 2 working ones
    "REQUEST_DELAY":    2,

    # posted_videos.json lives in the repo root so Actions can commit it back
    "POSTED_DB":     "posted_videos.json",
    "DOWNLOAD_DIR":  "downloads",

    # ── ADD MORE SITES HERE ───────────────────────────────────
    "SOURCES": [
        {
            "name": "AuntyMazaX",
            "url":  "https://auntymazax30.watch/",
            "card_selector":      "div.video-block a.thumb",
            "video_tag_selector": "video source, source[src]",
            "iframe_selector":    "div.responsive-player iframe, div.video-player iframe",
        },
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
#  SCRAPE LISTING
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
        title = card.get("title") or ""
        if not title:
            parent = card.find_parent("div", class_="video-block")
            if parent:
                infos = parent.find("a", class_="infos")
                if infos:
                    title = infos.get("title") or infos.get_text(strip=True)
        videos.append({"id": vid_id, "url": href,
                       "title": str(title or "Video")[:200], "source": source})
    log.info(f"[{source['name']}] {len(videos)} cards found.")
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
    m = re.findall(MP4_REGEX, str(soup))
    return m[0].strip() if m else None


# ─────────────────────────────────────────────────────────────
#  DOWNLOAD
# ─────────────────────────────────────────────────────────────
def download_video(session, mp4_url, vid_id) -> str | None:
    dl = Path(CONFIG["DOWNLOAD_DIR"])
    dl.mkdir(exist_ok=True)
    dest = dl / f"{vid_id}.mp4"
    try:
        with session.get(mp4_url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    f.write(chunk)
        mb = dest.stat().st_size / 1024 / 1024
        log.info(f"Downloaded {mb:.1f} MB")
        return str(dest)
    except Exception as e:
        log.warning(f"Download failed: {e}")
        dest.unlink(missing_ok=True)
        return None


# ─────────────────────────────────────────────────────────────
#  UPLOAD (no caption)
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
                caption="",
                supports_streaming=True,
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
#  MAIN — one batch then exit (GitHub Actions handles the loop)
# ─────────────────────────────────────────────────────────────
async def main():
    bot    = Bot(token=CONFIG["BOT_TOKEN"])
    posted = load_posted()
    target = CONFIG["VIDEOS_PER_BATCH"]
    sent   = 0

    log.info(f"=== Batch run | target={target} | posted={len(posted)} ===")

    for source in CONFIG["SOURCES"]:
        if sent >= target:
            break
        session  = make_session(referer=source["url"])
        all_vids = scrape_source(session, source)
        new_vids = [v for v in all_vids if v["id"] not in posted]

        for video in new_vids[:CONFIG["MAX_SCAN_PER_RUN"]]:
            if sent >= target:
                break
            log.info(f"[{sent+1}/{target}] {video['title'][:60]}")
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

    log.info(f"=== Done: {sent}/{target} sent ===")


if __name__ == "__main__":
    asyncio.run(main())
