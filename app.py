"""
Social Media Video & Audio Download API
Supports 1000+ sites via yt-dlp: YouTube, TikTok, Instagram, Facebook,
X/Twitter, Reddit, Vimeo, Dailymotion, SoundCloud, Twitch & more.

Run:
  pip install -r requirements.txt
  python app.py
  # or: uvicorn app:app --host 0.0.0.0 --port 8000

Docs: http://localhost:8000/docs
Tester UI: http://localhost:8000/
"""

import os
import re
import uuid
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

import yt_dlp
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel, HttpUrl

# ------------------------------------------------------------------ setup

APP_TITLE = "Social Media Video & Audio Download API"
APP_VERSION = "1.2.0"
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "social-dl"
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "7200"))  # 2h default guard

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description="""
Universal downloader API powered by **yt-dlp**.

**Supported:** YouTube, TikTok, Instagram, Facebook, X (Twitter), Reddit,
Vimeo, Dailymotion, SoundCloud, Twitch, Snapchat, Pinterest + 1000 more.

**Flow:**
1. `GET /api/info?url=...` → get title, thumbnail, duration, formats
2. `GET /api/download?url=...&type=video&quality=720` → download file

⚠️ Only download content you have rights to. Respect platform ToS & copyright.

Made with ❤️ by **R4AD Bhai** — [Telegram: @zerox6t9](https://t.me/zerox6t9) • [@Infinity_codex](https://t.me/Infinity_codex)
""",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DownloadRequest(BaseModel):
    url: str
    type: str = "video"      # video | audio
    quality: str = "best"    # best | 2160 | 1440 | 1080 | 720 | 480 | 360 | 240 | lowest
    format: str = "mp4"      # video: mp4 | webm | mkv | best | audio: mp3 | m4a | opus | wav | best
    direct: bool = False     # True = return direct CDN URL JSON instead of file


# ------------------------------------------------------------- helpers

def base_ydl_opts() -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,          # single video only (playlists off by default)
        "socket_timeout": 20,
        "retries": 3,
        "nocheckcertificate": True,
        "prefer_insecure": False,
        "geo_bypass": True,
        # Pretend to be a real browser — helps with TikTok/IG/FB
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
        },
    }


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\x00-\x1f\x7f]', "", name)  # strip control chars
    name = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len] or "download").strip()


def parse_upload_date(raw: Optional[str]) -> Optional[str]:
    # yt-dlp gives YYYYMMDD
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def simplify_format(f: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a yt-dlp format dict to what clients actually need."""
    return {
        "format_id": f.get("format_id"),
        "ext": f.get("ext"),
        "resolution": f.get("resolution") or (
            f"{f.get('width')}x{f.get('height')}" if f.get("width") else f.get("format_note")
        ),
        "height": f.get("height"),
        "fps": f.get("fps"),
        "vcodec": f.get("vcodec"),
        "acodec": f.get("acodec"),
        "filesize": f.get("filesize") or f.get("filesize_approx"),
        "tbr": f.get("tbr"),
        "protocol": f.get("protocol"),
        "has_video": f.get("vcodec") not in (None, "none"),
        "has_audio": f.get("acodec") not in (None, "none"),
    }


# ------------------------------------------------- cookies + anti-bot
# YouTube flags datacenter IPs (Render/Railway/VPS) with
# "Sign in to confirm you're not a bot". We fight it two ways:
#   1) rotate through alternate YouTube player clients (mobile/TV API
#      endpoints don't enforce the same IP check), and
#   2) optional authenticated cookies via YOUTUBE_COOKIES env var
#      (Render/Railway) or a local cookies.txt file (see README).

COOKIES_PATH = Path(__file__).parent / "cookies.txt"
COOKIES_ENV = "YOUTUBE_COOKIES"
_cached_cookiefile: Optional[str] = None

BOT_ERROR_DETAIL = (
    "YouTube blocked this server's IP with a bot-check ('Sign in to confirm "
    "you're not a bot'). This is very common on Render/Railway free-tier IPs — "
    "your link is fine. The API already auto-retried with alternate YouTube "
    "clients. Fix: add a YOUTUBE_COOKIES env var with your exported "
    "youtube.com cookies (see README section 'Fix: YouTube bot-check'), then "
    "redeploy. Check /api/health to confirm cookies loaded."
)


def is_bot_error(msg: str) -> bool:
    m = (msg or "").lower()
    return "not a bot" in m or "sign in to confirm" in m


def get_cookiefile() -> Optional[str]:
    """Return a cookies file path if available (env var or local file)."""
    global _cached_cookiefile
    if _cached_cookiefile and Path(_cached_cookiefile).exists():
        return _cached_cookiefile
    # 1) env var (for Render / Railway) — paste raw cookies.txt content
    env_cookies = os.getenv(COOKIES_ENV, "").strip().strip('"').strip("'")
    if env_cookies and "youtube.com" in env_cookies.lower():
        p = DOWNLOAD_DIR / "yt-cookies.txt"
        p.write_text(env_cookies + "\n", encoding="utf-8")
        _cached_cookiefile = str(p)
        return _cached_cookiefile
    # 2) local cookies.txt next to app.py (local dev)
    if COOKIES_PATH.exists() and COOKIES_PATH.stat().st_size > 100:
        _cached_cookiefile = str(COOKIES_PATH)
        return _cached_cookiefile
    return None


def ydl_profiles() -> List[Dict[str, Any]]:
    """Ordered option-overlays to try (YouTube anti-bot fallback chain)."""
    cookiefile = get_cookiefile()
    android_args = {"youtube": {
        "player_client": ["android_music", "android", "web"],
        "player_skip": ["configs"],
    }}
    alt_args = {"youtube": {
        "player_client": ["mweb", "tv", "web"],
        "player_skip": ["configs", "webpage"],
    }}
    profiles: List[Dict[str, Any]] = []
    if cookiefile:
        profiles.append({"cookiefile": cookiefile})  # authenticated first
        profiles.append({"cookiefile": cookiefile, "extractor_args": android_args})
    profiles.append({"extractor_args": android_args})  # hardened default
    profiles.append({"extractor_args": alt_args})      # alternate clients
    profiles.append({} if not cookiefile else {"cookiefile": cookiefile})  # stock
    return profiles


def extract_with_fallback(
    url: str,
    extra: Optional[Dict[str, Any]] = None,
    download: bool = False,
) -> Dict[str, Any]:
    """Run yt-dlp, auto-retrying through anti-bot client profiles."""
    profiles = ydl_profiles()
    last_err = "unknown error"
    for i, profile in enumerate(profiles):
        opts = base_ydl_opts()
        if extra:
            opts.update(extra)
        for k, v in profile.items():
            if k == "extractor_args" and isinstance(opts.get(k), dict):
                merged = dict(opts[k])
                merged.update(v)
                opts[k] = merged
            else:
                opts[k] = v
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
            if not info:
                last_err = "No media found at this URL."
                continue
            if info.get("_type") == "playlist" and info.get("entries"):
                info = info["entries"][0]
            return info
        except HTTPException:
            raise
        except yt_dlp.utils.DownloadError as e:
            last_err = clean_error(str(e))
            if is_bot_error(last_err) and i < len(profiles) - 1:
                continue  # try next client profile
            break
        except Exception as e:
            last_err = str(e)[:500]
            break
    if is_bot_error(last_err):
        raise HTTPException(status_code=502, detail=BOT_ERROR_DETAIL + f" (last error: {last_err[:200]})")
    raise HTTPException(status_code=400, detail=f"Could not fetch this URL: {last_err}")


def extract_info(url: str) -> Dict[str, Any]:
    return extract_with_fallback(url, download=False)


def clean_error(msg: str) -> str:
    # yt-dlp errors can be verbose — keep first useful line
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)  # strip ANSI
    lines = [l.strip() for l in msg.splitlines() if l.strip()]
    # drop generic prefix
    cleaned = lines[-1] if lines else msg
    return cleaned[:500]


def build_format_selector(dtype: str, quality: str, fmt: str) -> str:
    """Build a yt-dlp -f format selector string."""
    dtype = dtype.lower()
    quality = quality.lower().replace("p", "")
    fmt = fmt.lower()

    if dtype == "audio":
        return "bestaudio/best"

    # ---- video ----
    if quality in ("best", "max", "highest"):
        h = ""
    elif quality in ("lowest", "low", "min", "worst"):
        return f"worstvideo+worstaudio/worst" if fmt in ("best", "") else f"worst[ext={fmt}]/worst"
    else:
        try:
            h = f"[height<={int(quality)}]"
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid quality '{quality}'. Use best, 1080, 720, 480, 360 or lowest.")

    if fmt in ("best", "", "any"):
        return f"bestvideo{h}+bestaudio/best{h}/best"
    if fmt in ("mp4", "m4a"):
        # H.264 + AAC in mp4 is most compatible
        return f"bestvideo{h}[ext=mp4]+bestaudio[ext=m4a]/bestvideo{h}+bestaudio/best{h}/best"
    if fmt == "webm":
        return f"bestvideo{h}[ext=webm]+bestaudio[ext=webm]/bestvideo{h}+bestaudio/best{h}/best"
    return f"bestvideo{h}+bestaudio/best{h}/best"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def download_to_file(url: str, dtype: str, quality: str, fmt: str) -> Path:
    """Download media to a temp file. Returns path to the file."""
    job_id = uuid.uuid4().hex[:12]
    workdir = DOWNLOAD_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(workdir / "%(title).80s [%(id)s].%(ext)s")

    dtype = dtype.lower()
    fmt = fmt.lower()
    selector = build_format_selector(dtype, quality, fmt)

    dl_extra: Dict[str, Any] = {
        "format": selector,
        "outtmpl": outtmpl,
        "noplaylist": True,
    }
    if dtype == "video" and fmt in ("mp4", "webm", "mkv"):
        dl_extra["merge_output_format"] = fmt

    # audio conversion needs ffmpeg
    if dtype == "audio" and fmt in ("mp3", "wav", "opus", "aac"):
        if not ffmpeg_available():
            # gracefully fall back to native container (usually m4a/webm)
            fmt = "best"
        else:
            dl_extra["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt,
                "preferredquality": "192",
            }]

    try:
        info = extract_with_fallback(url, extra=dl_extra, download=True)
    except HTTPException as e:
        shutil.rmtree(workdir, ignore_errors=True)
        # keep the friendly bot-check message, prefix anything else
        if e.status_code == 502:
            raise
        raise HTTPException(status_code=e.status_code, detail=f"Download failed: {e.detail}")

    # duration guard
    dur = info.get("duration") or 0
    if dur and dur > MAX_DURATION_SECONDS:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"Video too long ({dur//60} min). Max allowed is {MAX_DURATION_SECONDS//60} min."
        )

    # find downloaded file (ignore partial/temp files from retries)
    files = [p for p in workdir.iterdir()
             if p.is_file() and p.suffix.lower() not in (".part", ".ytdl", ".temp", ".tmp")]
    if not files:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Download finished but no file was produced.")
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return files[0]


def cleanup_path(path: Path):
    try:
        parent = path.parent
        if parent.exists() and parent.name != DOWNLOAD_DIR.name:
            shutil.rmtree(parent, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        pass


def media_type_for(path: Path, dtype: str) -> str:
    ext = path.suffix.lower().lstrip(".")
    if dtype == "audio" and ext == "mp4":
        return "audio/mp4"  # audio-only MP4 (no-ffmpeg fallback)
    return {
        "mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
        "mov": "video/quicktime", "flv": "video/x-flv",
        "mp3": "audio/mpeg", "m4a": "audio/mp4", "opus": "audio/opus",
        "ogg": "audio/ogg", "wav": "audio/wav", "aac": "audio/aac",
        "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    }.get(ext, "video/mp4" if dtype == "video" else "audio/mpeg")


# ----------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    """Serve the built-in tester UI."""
    ui = Path(__file__).parent / "static" / "index.html"
    if ui.exists():
        return ui.read_text(encoding="utf-8")
    return HTMLResponse(
        f"<h2>{APP_TITLE} v{APP_VERSION}</h2>"
        "<p>API is running. Open <a href='/docs'>/docs</a> for interactive docs.</p>"
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "ffmpeg": ffmpeg_available(),
        "engine": f"yt-dlp {yt_dlp.version.__version__}",
        "youtube_cookies": bool(get_cookiefile()),
        "youtube_mode": "multi-client fallback (android → mweb/tv → default)"
                        + (" + cookies ✅" if get_cookiefile() else " (no cookies)"),
    }


@app.get("/api/supported")
def supported():
    """List popular supported platforms (yt-dlp supports 1000+ in total)."""
    return {
        "count": "1000+ sites via yt-dlp",
        "popular": [
            {"name": "YouTube", "domains": ["youtube.com", "youtu.be"], "video": True, "audio": True},
            {"name": "TikTok", "domains": ["tiktok.com", "vt.tiktok.com"], "video": True, "audio": True},
            {"name": "Instagram", "domains": ["instagram.com"], "video": True, "audio": True, "note": "Reels, posts, stories (public)"},
            {"name": "Facebook", "domains": ["facebook.com", "fb.watch"], "video": True, "audio": True},
            {"name": "X / Twitter", "domains": ["x.com", "twitter.com"], "video": True, "audio": True},
            {"name": "Reddit", "domains": ["reddit.com", "v.redd.it"], "video": True, "audio": True},
            {"name": "Vimeo", "domains": ["vimeo.com"], "video": True, "audio": True},
            {"name": "Dailymotion", "domains": ["dailymotion.com"], "video": True, "audio": True},
            {"name": "SoundCloud", "domains": ["soundcloud.com"], "video": False, "audio": True},
            {"name": "Twitch", "domains": ["twitch.tv"], "video": True, "audio": True, "note": "VODs & clips"},
            {"name": "Snapchat", "domains": ["snapchat.com"], "video": True, "audio": False},
            {"name": "Pinterest", "domains": ["pinterest.com"], "video": True, "audio": False},
            {"name": "LinkedIn", "domains": ["linkedin.com"], "video": True, "audio": False},
            {"name": "Tumblr", "domains": ["tumblr.com"], "video": True, "audio": True},
            {"name": "Rumble", "domains": ["rumble.com"], "video": True, "audio": True},
            {"name": "Bilibili", "domains": ["bilibili.tv", "bilibili.com"], "video": True, "audio": True},
        ],
    }


@app.get("/api/info")
def get_info(
    url: str = Query(..., description="Social media post / video URL"),
    include_formats: bool = Query(False, description="Include full format list"),
):
    """Get metadata (title, thumbnail, duration, uploader) without downloading."""
    info = extract_info(url)
    formats = [simplify_format(f) for f in info.get("formats", []) or []]

    # pick best thumbnail
    thumb = info.get("thumbnail")
    if not thumb and info.get("thumbnails"):
        thumbs = info["thumbnails"]
        thumb = (thumbs[-1] or {}).get("url")

    # quick download suggestions
    webpage = info.get("webpage_url") or url
    result = {
        "id": info.get("id"),
        "platform": info.get("extractor_key") or info.get("extractor"),
        "title": info.get("title"),
        "description": (info.get("description") or "")[:500],
        "uploader": info.get("uploader") or info.get("channel") or info.get("artist"),
        "uploader_url": info.get("uploader_url") or info.get("channel_url"),
        "thumbnail": thumb,
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "upload_date": parse_upload_date(info.get("upload_date")),
        "webpage_url": webpage,
        "is_live": info.get("is_live"),
        "ext": info.get("ext"),
        "quick_download": {
            "video_best": f"/api/download?url={webpage}&type=video&quality=best",
            "video_720p": f"/api/download?url={webpage}&type=video&quality=720",
            "video_480p": f"/api/download?url={webpage}&type=video&quality=480",
            "audio_mp3": f"/api/download?url={webpage}&type=audio&format=mp3",
            "audio_best": f"/api/download?url={webpage}&type=audio&format=best",
        },
    }
    if include_formats:
        result["formats"] = formats
        result["format_count"] = len(formats)
    else:
        # light summary: available heights
        heights = sorted({f["height"] for f in formats if f.get("height")}, reverse=True)
        result["available_qualities"] = [f"{h}p" for h in heights] or ["default"]
    return result


@app.get("/api/formats")
def get_formats(url: str = Query(..., description="Social media post / video URL")):
    """List all downloadable formats for a URL."""
    info = extract_info(url)
    formats = [simplify_format(f) for f in info.get("formats", []) or []]
    return {
        "title": info.get("title"),
        "platform": info.get("extractor_key") or info.get("extractor"),
        "format_count": len(formats),
        "formats": formats,
    }


@app.get("/api/download")
def download_get(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="Social media post / video URL"),
    type: str = Query("video", description="video or audio"),
    quality: str = Query("best", description="best, 2160, 1080, 720, 480, 360, lowest"),
    format: str = Query("mp4", description="video: mp4/webm/mkv/best · audio: mp3/m4a/opus/wav/best"),
    direct: bool = Query(False, description="True = return direct CDN URL as JSON instead of file"),
):
    return handle_download(background_tasks, url, type, quality, format, direct)


@app.post("/api/download")
def download_post(body: DownloadRequest, background_tasks: BackgroundTasks):
    return handle_download(
        background_tasks, body.url, body.type, body.quality, body.format, body.direct
    )


def handle_download(
    background_tasks: BackgroundTasks,
    url: str, dtype: str, quality: str, fmt: str, direct: bool,
):
    dtype = (dtype or "video").lower()
    if dtype not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="type must be 'video' or 'audio'")

    if direct:
        # Return a direct CDN URL without downloading to server
        selector = build_format_selector(dtype, quality, fmt)
        info = extract_with_fallback(url, extra={"format": selector}, download=False)
        direct_url = info.get("url")
        if not direct_url and info.get("requested_formats"):
            # merged formats → return both parts
            parts = [f.get("url") for f in info["requested_formats"] if f.get("url")]
            return {"title": info.get("title"), "direct_urls": parts, "note": "Merged format: video+audio parts"}
        if not direct_url:
            raise HTTPException(status_code=500, detail="No direct URL found for this media.")
        return {
            "title": info.get("title"),
            "platform": info.get("extractor_key") or info.get("extractor"),
            "ext": info.get("ext"),
            "direct_url": direct_url,
            "expires_note": "Direct URLs expire quickly — use immediately.",
        }

    # Default: download to server then stream file to client
    path = download_to_file(url, dtype, quality, fmt)
    background_tasks.add_task(cleanup_path, path)
    filename = sanitize_filename(path.stem) + path.suffix
    # NOTE: don't set Content-Disposition manually — non-ASCII titles
    # (e.g. hindi songs with ｜) crash latin-1 header encoding.
    # Starlette's `filename=` handles RFC 5987 UTF-8 encoding for us.
    return FileResponse(
        path,
        media_type=media_type_for(path, dtype),
        filename=filename,
    )


# ---- convenience shortcuts ----

@app.get("/api/mp4")
def shortcut_mp4(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    quality: str = Query("720"),
):
    """Shortcut: download video as MP4. /api/mp4?url=...&quality=720"""
    return handle_download(background_tasks, url, "video", quality, "mp4", False)


@app.get("/api/mp3")
def shortcut_mp3(background_tasks: BackgroundTasks, url: str = Query(...)):
    """Shortcut: download audio as MP3 (falls back to M4A if no ffmpeg). /api/mp3?url=..."""
    fmt = "mp3" if ffmpeg_available() else "best"
    return handle_download(background_tasks, url, "audio", "best", fmt, False)


# ------------------------------------------------------------------ run

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
