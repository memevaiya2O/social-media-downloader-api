"""
Social Media Video & Audio Download API
Supports 1000+ sites via yt-dlp: YouTube, TikTok, Instagram, Facebook,
X/Twitter, Reddit, Vimeo, Dailymotion, SoundCloud, Twitch & more.

YouTube anti-bot protection (v1.3+):
  Layer 1 — Chrome TLS impersonation (curl_cffi) + multi-client rotation
           (android -> mweb/tv -> web)
  Layer 2 — Automatic Piped + Invidious API fallback (no cookies needed)
  Layer 3 — Optional YOUTUBE_COOKIES env var / cookies.txt (most reliable)

Run:
  pip install -r requirements.txt
  python app.py
  # or: uvicorn app:app --host 0.0.0.0 --port 8000

Docs: http://localhost:8000/docs
Tester UI: http://localhost:8000/
"""

import os
import re
import json
import time
import uuid
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any

import yt_dlp
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel, HttpUrl

# ------------------------------------------------------------------ setup

APP_TITLE = "Social Media Video & Audio Download API"
APP_VERSION = "1.3.0"
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "social-dl"
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "7200"))  # 2h default guard

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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

**YouTube bot-check protection:** Chrome impersonation + alternate player
clients + automatic Piped/Invidious fallback. Optional `YOUTUBE_COOKIES`
env var for maximum reliability.

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
    force_fallback: bool = False  # True = skip yt-dlp, use Piped/Invidious (YouTube only)


# ------------------------------------------------------------- helpers

# Chrome TLS impersonation (via curl_cffi) defeats fingerprint-based
# bot-checks. Mutable dict so we can auto-disable it at runtime if the
# installed yt-dlp/curl_cffi combo ever rejects it.
try:
    import curl_cffi  # noqa: F401
    _IMPERSONATE = {"target": "chrome"}
except ImportError:
    _IMPERSONATE = {"target": None}


def base_ydl_opts() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,          # single video only (playlists off by default)
        "socket_timeout": 20,
        "retries": 3,
        "nocheckcertificate": True,
        "prefer_insecure": False,
        "geo_bypass": True,
        # Pretend to be a real browser — helps with TikTok/IG/FB
        "user_agent": BROWSER_UA,
        "http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if _IMPERSONATE["target"]:
        opts["impersonate"] = _IMPERSONATE["target"]
    proxy = os.getenv("YDL_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy
    return opts


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


def clean_error(msg: str) -> str:
    # yt-dlp errors can be verbose — keep first useful line
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)  # strip ANSI
    lines = [l.strip() for l in msg.splitlines() if l.strip()]
    # drop generic prefix
    cleaned = lines[-1] if lines else msg
    return cleaned[:500]


# ------------------------------------------------- cookies + anti-bot
# YouTube flags datacenter IPs (Render/Railway/VPS) with
# "Sign in to confirm you're not a bot". We fight it three ways:
#   1) Chrome impersonation + alternate YouTube player clients, and
#   2) Piped/Invidious fallback (see section below), and
#   3) optional authenticated cookies via YOUTUBE_COOKIES env var
#      (Render/Railway) or a local cookies.txt file (see README).

COOKIES_PATH = Path(__file__).parent / "cookies.txt"
COOKIES_ENV = "YOUTUBE_COOKIES"
_cached_cookiefile: Optional[str] = None

BOT_ERROR_DETAIL = (
    "YouTube blocked this server's IP with a bot-check ('Sign in to confirm "
    "you're not a bot'). This is very common on Render/Railway free-tier IPs — "
    "your link is fine."
)


def is_bot_error(msg: str) -> bool:
    m = (msg or "").lower()
    return "not a bot" in m or "sign in to confirm" in m


def get_cookiefile() -> Optional[str]:
    """Return a cookies file path if available (saved / env var / local file)."""
    global _cached_cookiefile
    if _cached_cookiefile and Path(_cached_cookiefile).exists():
        return _cached_cookiefile
    # 1) cookies saved via POST /api/cookies (or previously from env)
    posted = DOWNLOAD_DIR / "yt-cookies.txt"
    if posted.exists() and posted.stat().st_size > 100:
        _cached_cookiefile = str(posted)
        return _cached_cookiefile
    # 2) env var (for Render / Railway) — paste raw cookies.txt content
    env_cookies = os.getenv(COOKIES_ENV, "").strip().strip('"').strip("'")
    if env_cookies and "youtube.com" in env_cookies.lower():
        posted.write_text(env_cookies + "\n", encoding="utf-8")
        _cached_cookiefile = str(posted)
        return _cached_cookiefile
    # 3) local cookies.txt next to app.py (local dev)
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
            low = last_err.lower()
            # impersonation unsupported by installed libs? disable & retry rest
            if _IMPERSONATE["target"] and ("impersonat" in low or "curl_cffi" in low):
                _IMPERSONATE["target"] = None
                if i < len(profiles) - 1:
                    continue
                break
            if is_bot_error(last_err) and i < len(profiles) - 1:
                continue  # try next client profile
            break
        except Exception as e:
            last_err = str(e)[:500]
            break
    if is_bot_error(last_err):
        raise HTTPException(status_code=502, detail=BOT_ERROR_DETAIL + f" (last error: {last_err[:200]})")
    raise HTTPException(status_code=400, detail=f"Could not fetch this URL: {last_err}")


# --------------------------------------- YouTube cookieless fallback
# When YouTube bot-blocks the server IP, resolve the video through public
# Piped / Invidious API instances instead, then download straight from the
# media CDN (googlevideo) — no login, no cookies needed.

YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch[^#]*[?&]v=|shorts/|embed/|live/|v/|attribution_link\?.*?v%3D)|youtu\.be/)([\w-]{11})"
)

PIPED_REGISTRY = "https://piped.video/api/v1/instances"
INVIDIOUS_REGISTRY = "https://api.invidious.io/instances.json"

PIPED_HARDCODED = [
    "https://pipedapi.adminforge.de",
    "https://pipedapi.leptons.xyz",
]

INVIDIOUS_HARDCODED = [
    "https://invidious.f5.si",  # verified working 2026-10-04
    "https://iv.melmac.space",
    "https://invidious.nerdvpn.de",
    "https://inv.tux.pizza",
]

# errors where retrying via fallback is pointless (gone/private/removed)
_NO_FALLBACK_PATTERNS = (
    "private video", "video unavailable", "has been removed",
    "removed by the uploader", "no longer available", "does not exist",
    "account associated", "copyright", "account terminated",
    "this video is not available", "premium content",
)

_registry_cache: Dict[str, Dict[str, Any]] = {}
_last_fallback_error = ""


def last_fallback_error() -> str:
    return _last_fallback_error


def extract_youtube_id(url: str) -> Optional[str]:
    m = YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def should_try_fallback(exc: HTTPException) -> bool:
    if exc.status_code == 502:  # bot-check → always try fallback
        return True
    if exc.status_code == 400:
        d = (exc.detail or "").lower()
        return not any(p in d for p in _NO_FALLBACK_PATTERNS)
    return False


def final_bot_error() -> HTTPException:
    fb = last_fallback_error()
    detail = (
        "YouTube blocked this server's IP with a bot-check and the automatic "
        "Piped/Invidious fallback also failed. Your link is fine — the server "
        "just can't reach YouTube right now. Fix: add a YOUTUBE_COOKIES env var "
        "with your exported youtube.com cookies (see README section 'Fix: "
        "EASIEST FIX (60s): export youtube.com cookies ('Get cookies.txt LOCALLY' "
        "extension) and paste them in the 🍪 box on / or POST to /api/cookies — "
        "active instantly. Or set YOUTUBE_COOKIES env var and redeploy. "
        "Full guide: README 'Fix: YouTube bot-check'. Verify: /api/health."
    )
    if fb:
        detail += f" (fallback: {fb[:200]})"
    return HTTPException(status_code=502, detail=detail)


def _http_get_json(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_download(url: str, dest: Path, timeout: int = 180) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        total = 0
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        return total
    except urllib.error.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=(f"YouTube's media CDN refused this server (HTTP {e.code}). "
                    "Fix: save YouTube cookies once via POST /api/cookies (see /docs) "
                    "or set the YOUTUBE_COOKIES env var — guide in README 'Fix: YouTube bot-check'."))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=(f"Media download failed ({str(e)[:120]}). "
                    "Fix: save YouTube cookies via POST /api/cookies or YOUTUBE_COOKIES env var."))


def _normalize_base(raw: str) -> str:
    """Registries sometimes list bare domains — force https:// form."""
    u = (raw or "").strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    return u if u.startswith("https://") else ""


def get_piped_bases() -> List[str]:
    now = time.time()
    hit = _registry_cache.get("piped")
    if hit and now - hit["ts"] < 3600 and hit["bases"]:
        return hit["bases"]
    bases: List[str] = [b for b in PIPED_HARDCODED]  # known hosts first
    for x in os.getenv("EXTRA_PIPED", "").split(","):  # user-supplied first
        u = _normalize_base(x)
        if u and u not in bases:
            bases.insert(0, u)
    try:
        data = _http_get_json(PIPED_REGISTRY, timeout=8)
        if isinstance(data, list):
            for inst in data:
                if isinstance(inst, dict):
                    api = _normalize_base(inst.get("api_url") or "")
                    if api and api not in bases:
                        bases.append(api)
    except Exception:
        pass
    bases = bases[:8]
    _registry_cache["piped"] = {"ts": now, "bases": bases}
    return bases


def get_invidious_bases() -> List[str]:
    now = time.time()
    hit = _registry_cache.get("invidious")
    if hit and now - hit["ts"] < 3600 and hit["bases"]:
        return hit["bases"]
    bases: List[str] = [b for b in INVIDIOUS_HARDCODED]  # known-good first
    for x in os.getenv("EXTRA_INVIDIOUS", "").split(","):  # user-supplied first
        u = _normalize_base(x)
        if u and u not in bases:
            bases.insert(0, u)
    try:
        data = _http_get_json(INVIDIOUS_REGISTRY, timeout=8)
        # format: [ ["domain-or-url", {"api": true, ...}], ... ]
        if isinstance(data, list):
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    url, meta = item[0], item[1]
                    if isinstance(meta, dict) and meta.get("api"):
                        u = _normalize_base(str(url))
                        if u and u not in bases:
                            bases.append(u)
    except Exception:
        pass
    bases = bases[:8]
    _registry_cache["invidious"] = {"ts": now, "bases": bases}
    return bases


def _parse_height(text: Any) -> int:
    if not text:
        return 0
    text = str(text)
    m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)  # 1280x720
    if m:
        return int(m.group(2))
    m = re.search(r"(\d{3,4})\s*p", text)  # 720p / 720p60
    if m:
        return int(m.group(1))
    return 0


def _quality_limit(quality: str) -> Optional[int]:
    """None = best, -1 = lowest, else max height."""
    q = (quality or "best").lower().replace("p", "")
    if q in ("best", "max", "highest"):
        return None
    if q in ("lowest", "low", "min", "worst"):
        return -1
    try:
        return int(q)
    except ValueError:
        return None


def _piped_fetch(video_id: str) -> Optional[Dict[str, Any]]:
    global _last_fallback_error
    for base in get_piped_bases()[:4]:
        try:
            data = _http_get_json(f"{base}/streams/{video_id}", timeout=8)
            if isinstance(data, dict) and (data.get("audioStreams") or data.get("videoStreams")):
                return data
            if isinstance(data, dict) and data.get("message"):
                _last_fallback_error = f"piped: {str(data.get('message'))[:150]}"
        except Exception as e:
            _last_fallback_error = f"piped {base}: {str(e)[:150]}"
    return None


def _invidious_fetch(video_id: str) -> Optional[Dict[str, Any]]:
    global _last_fallback_error
    for base in get_invidious_bases()[:4]:
        try:
            data = _http_get_json(f"{base}/api/v1/videos/{video_id}", timeout=8)
            if isinstance(data, dict) and (data.get("formatStreams") or data.get("adaptiveFormats")):
                return data
            if isinstance(data, dict) and data.get("error"):
                _last_fallback_error = f"invidious: {str(data.get('error'))[:150]}"
        except Exception as e:
            _last_fallback_error = f"invidious {base}: {str(e)[:150]}"
    return None


def _norm_piped(video_id: str, d: Dict[str, Any]) -> Dict[str, Any]:
    audio: List[Dict[str, Any]] = []
    video: List[Dict[str, Any]] = []
    for s in d.get("audioStreams") or []:
        url = s.get("url")
        if not url:
            continue
        codec = (s.get("codec") or "").lower()
        mime = (s.get("mimeType") or "").lower()
        ext = "webm" if ("opus" in codec or "opus" in mime or "webm" in mime) else "m4a"
        try:
            abr = int(s.get("bitrate") or 0) // 1000
        except (ValueError, TypeError):
            abr = 0
        audio.append({"url": url, "ext": ext, "abr": abr, "codec": codec or mime})
    for s in d.get("videoStreams") or []:
        url = s.get("url")
        if not url:
            continue
        mime = (s.get("mimeType") or "").lower()
        ext = "webm" if "webm" in mime else "mp4"
        try:
            h = int(s.get("height") or 0) or _parse_height(s.get("quality") or "")
        except (ValueError, TypeError):
            h = 0
        video.append({
            "url": url, "ext": ext, "height": h,
            "progressive": not bool(s.get("videoOnly")),
            "codec": s.get("codec") or "", "quality": s.get("quality") or "",
        })
    audio.sort(key=lambda x: x["abr"], reverse=True)
    video.sort(key=lambda x: x["height"], reverse=True)
    try:
        duration = int(d.get("duration") or 0) or None
    except (ValueError, TypeError):
        duration = None
    upload = str(d.get("uploadDate") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", upload)
    meta = {
        "source": "piped", "video_id": video_id,
        "title": d.get("title") or f"youtube-{video_id}",
        "description": d.get("description") or "",
        "uploader": d.get("uploader"),
        "uploader_url": ("https://youtube.com" + d["uploaderUrl"]) if d.get("uploaderUrl") else None,
        "thumbnail": d.get("thumbnailUrl"),
        "duration": duration,
        "view_count": d.get("views"),
        "like_count": d.get("likes"),
        "upload_date": f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else None,
        "is_live": bool(d.get("livestream")),
    }
    return {"meta": meta, "audio": audio, "video": video}


def _norm_invidious(video_id: str, d: Dict[str, Any]) -> Dict[str, Any]:
    audio: List[Dict[str, Any]] = []
    video: List[Dict[str, Any]] = []
    for s in d.get("adaptiveFormats") or []:
        url = s.get("url")
        if not url:
            continue
        typ = (s.get("type") or "").lower()
        container = (s.get("container") or "").lower()
        if typ.startswith("audio/"):
            try:
                abr = int(s.get("bitrate") or 0) // 1000
            except (ValueError, TypeError):
                abr = 0
            ext = container if container in ("m4a", "webm", "mp3", "ogg", "mp4") else (
                "webm" if "webm" in typ else "m4a")
            audio.append({"url": url, "ext": ext, "abr": abr, "codec": typ})
        elif typ.startswith("video/"):
            h = _parse_height(s.get("size") or s.get("resolution") or s.get("qualityLabel") or "")
            ext = container if container in ("mp4", "webm") else (
                "webm" if "webm" in typ else "mp4")
            video.append({"url": url, "ext": ext, "height": h,
                          "progressive": False, "codec": typ, "quality": ""})
    for s in d.get("formatStreams") or []:
        url = s.get("url")
        if not url:
            continue
        h = _parse_height(s.get("resolution") or s.get("qualityLabel") or s.get("quality") or "")
        container = (s.get("container") or "mp4").lower()
        ext = container if container in ("mp4", "webm") else "mp4"
        video.append({"url": url, "ext": ext, "height": h,
                      "progressive": True, "codec": s.get("type") or "",
                      "quality": s.get("qualityLabel") or ""})
    audio.sort(key=lambda x: x["abr"], reverse=True)
    video.sort(key=lambda x: x["height"], reverse=True)
    try:
        duration = int(d.get("lengthSeconds") or 0) or None
    except (ValueError, TypeError):
        duration = None
    thumbs = d.get("videoThumbnails") or []
    thumbs = sorted([t for t in thumbs if isinstance(t, dict) and t.get("url")],
                    key=lambda t: t.get("width") or 0, reverse=True)
    pub = d.get("published")
    try:
        upload_date = time.strftime("%Y%m%d", time.gmtime(int(pub))) if pub else None
    except (ValueError, TypeError):
        upload_date = None
    author_id = d.get("authorId")
    meta = {
        "source": "invidious", "video_id": video_id,
        "title": d.get("title") or f"youtube-{video_id}",
        "description": d.get("description") or "",
        "uploader": d.get("author"),
        "uploader_url": f"https://www.youtube.com/channel/{author_id}" if author_id else None,
        "thumbnail": thumbs[0]["url"] if thumbs else None,
        "duration": duration,
        "view_count": d.get("viewCount"),
        "like_count": d.get("likeCount"),
        "upload_date": upload_date,
        "is_live": bool(d.get("liveNow")),
    }
    return {"meta": meta, "audio": audio, "video": video}


def _resolve_fallback(video_id: str) -> Optional[Dict[str, Any]]:
    """Try Piped, then Invidious. Returns normalized media or None."""
    global _last_fallback_error
    d = _piped_fetch(video_id)
    if d:
        try:
            n = _norm_piped(video_id, d)
            if n["audio"] or n["video"]:
                return n
            _last_fallback_error = "piped: no streams in response"
        except Exception as e:
            _last_fallback_error = f"piped parse: {str(e)[:150]}"
    d = _invidious_fetch(video_id)
    if d:
        try:
            n = _norm_invidious(video_id, d)
            if n["audio"] or n["video"]:
                return n
            _last_fallback_error = "invidious: no streams in response"
        except Exception as e:
            _last_fallback_error = f"invidious parse: {str(e)[:150]}"
    return None


def _duration_string(dur: Optional[int]) -> Optional[str]:
    if not dur:
        return None
    if dur >= 3600:
        return f"{dur // 3600}:{(dur % 3600) // 60:02d}:{dur % 60:02d}"
    return f"{dur // 60}:{dur % 60:02d}"


def _norm_to_info(n: Dict[str, Any]) -> Dict[str, Any]:
    """Convert normalized fallback media to a yt-dlp-like info dict."""
    m = n["meta"]
    dur = m.get("duration")
    formats: List[Dict[str, Any]] = []
    for i, v in enumerate(n["video"]):
        formats.append({
            "format_id": f"fb-v{i}-{v['height']}p",
            "ext": v["ext"],
            "resolution": f"{v['height']}p" if v["height"] else "video",
            "height": v["height"] or None, "fps": None,
            "vcodec": v.get("codec") or "avc1",
            "acodec": "mp4a.40.2" if v["progressive"] else "none",
            "filesize": None, "tbr": None, "protocol": "https",
        })
    for i, a in enumerate(n["audio"]):
        formats.append({
            "format_id": f"fb-a{i}-{a['abr']}k", "ext": a["ext"],
            "resolution": "audio only", "height": None, "fps": None,
            "vcodec": "none", "acodec": a.get("codec") or "audio",
            "filesize": None, "tbr": a["abr"] or None, "protocol": "https",
        })
    return {
        "id": m["video_id"],
        "extractor_key": f"youtube (via {m['source']} fallback)",
        "title": m["title"], "description": m.get("description") or "",
        "uploader": m.get("uploader"), "uploader_url": m.get("uploader_url"),
        "thumbnail": m.get("thumbnail"), "duration": dur,
        "duration_string": _duration_string(dur),
        "view_count": m.get("view_count"), "like_count": m.get("like_count"),
        "comment_count": None, "upload_date": m.get("upload_date"),
        "webpage_url": f"https://www.youtube.com/watch?v={m['video_id']}",
        "is_live": m.get("is_live"), "ext": "mp4",
        "formats": formats, "fallback_source": m["source"],
    }


def try_fallback_info(video_id: str) -> Optional[Dict[str, Any]]:
    n = _resolve_fallback(video_id)
    return _norm_to_info(n) if n else None


def _ffmpeg_run(args: List[str], timeout: int = 300):
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       timeout=timeout, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {(e.stderr or '')[-300:]}")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed")


def _ffmpeg_convert_audio(src: Path, out: Path, fmt: str):
    if fmt == "mp3":
        args = ["-i", str(src), "-vn", "-codec:a", "libmp3lame", "-q:a", "4", str(out)]
    elif fmt == "wav":
        args = ["-i", str(src), "-vn", "-codec:a", "pcm_s16le", str(out)]
    elif fmt == "opus":
        args = ["-i", str(src), "-vn", "-codec:a", "libopus", "-b:a", "128k", str(out)]
    else:  # m4a / aac
        args = ["-i", str(src), "-vn", "-codec:a", "aac", "-b:a", "192k", str(out)]
    _ffmpeg_run(args)


def _ffmpeg_merge(v: Path, a: Path, out: Path):
    _ffmpeg_run(["-i", str(v), "-i", str(a), "-c", "copy", "-shortest", str(out)])


def _ffmpeg_remux(src: Path, out: Path):
    _ffmpeg_run(["-i", str(src), "-c", "copy", str(out)])


def _pick_video(streams: List[Dict[str, Any]], limit: Optional[int]) -> Optional[Dict[str, Any]]:
    if not streams:
        return None
    if limit == -1:
        return sorted(streams, key=lambda x: x["height"])[0]
    if limit:
        under = [v for v in streams if v["height"] and v["height"] <= limit]
        pool = under or streams
        return sorted(pool, key=lambda x: x["height"], reverse=True)[0]
    return sorted(streams, key=lambda x: x["height"], reverse=True)[0]


def _fallback_download_media(
    n: Dict[str, Any], dtype: str, quality: str, fmt: str, workdir: Path,
) -> Path:
    m = n["meta"]
    dur = m.get("duration") or 0
    if dur and dur > MAX_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Video too long ({dur // 60} min). Max allowed is {MAX_DURATION_SECONDS // 60} min."
        )
    safe = sanitize_filename(m["title"]) or m["video_id"]
    limit = _quality_limit(quality)
    fmt = (fmt or "").lower()

    if dtype == "audio":
        if not n["audio"]:
            raise HTTPException(status_code=502, detail="Fallback has no audio streams for this video.")
        a = n["audio"][0]
        src = workdir / f"{safe}_fb.{a['ext']}"
        _http_download(a["url"], src)
        if fmt in ("mp3", "wav", "opus", "aac", "m4a") and ffmpeg_available():
            target_ext = "opus" if fmt == "opus" else fmt
            if src.suffix.lower() == f".{target_ext}":
                return src
            out = workdir / f"{safe}.{target_ext}"
            _ffmpeg_convert_audio(src, out, fmt)
            src.unlink(missing_ok=True)
            return out
        return src

    # ---- video ----
    prog = [v for v in n["video"] if v["progressive"]]
    v = _pick_video(prog, limit)
    if v:
        dest = workdir / f"{safe}_fb.{v['ext']}"
        _http_download(v["url"], dest)
        if fmt in ("mp4", "webm", "mkv") and dest.suffix.lower() != f".{fmt}" and ffmpeg_available():
            out = workdir / f"{safe}.{fmt}"
            _ffmpeg_remux(dest, out)
            dest.unlink(missing_ok=True)
            return out
        return dest

    # no progressive stream → merge best video-only + best audio
    vo = [x for x in n["video"] if not x["progressive"]]
    if not vo or not n["audio"]:
        raise HTTPException(status_code=502, detail="Fallback has no downloadable video streams.")
    if not ffmpeg_available():
        v = _pick_video(vo, limit) or vo[0]
        dest = workdir / f"{safe}_fb_videoonly.{v['ext']}"
        _http_download(v["url"], dest)
        return dest
    v = _pick_video(vo, limit) or vo[0]
    a = n["audio"][0]
    vf = workdir / f"v_fb.{v['ext']}"
    af = workdir / f"a_fb.{a['ext']}"
    _http_download(v["url"], vf)
    _http_download(a["url"], af)
    out_ext = fmt if fmt in ("mp4", "webm", "mkv") else "mp4"
    out = workdir / f"{safe}.{out_ext}"
    _ffmpeg_merge(vf, af, out)
    vf.unlink(missing_ok=True)
    af.unlink(missing_ok=True)
    return out


def try_fallback_download(
    video_id: str, dtype: str, quality: str, fmt: str, workdir: Path,
) -> Optional[Path]:
    n = _resolve_fallback(video_id)
    if not n:
        return None
    return _fallback_download_media(n, dtype, quality, fmt, workdir)


def fallback_direct_response(video_id: str, dtype: str, quality: str, fmt: str) -> Dict[str, Any]:
    n = _resolve_fallback(video_id)
    if not n:
        raise final_bot_error()
    limit = _quality_limit(quality)
    if dtype == "audio" and n["audio"]:
        s = n["audio"][0]
        return {
            "title": n["meta"]["title"],
            "platform": f"youtube (via {n['meta']['source']} fallback)",
            "ext": s["ext"], "direct_url": s["url"],
            "via_fallback": n["meta"]["source"],
            "expires_note": "Direct URLs expire quickly — use immediately.",
        }
    pool = [v for v in n["video"] if v["progressive"]] or n["video"]
    if not pool:
        raise HTTPException(status_code=502, detail="Fallback has no video streams.")
    s = _pick_video(pool, limit) or pool[0]
    return {
        "title": n["meta"]["title"],
        "platform": f"youtube (via {n['meta']['source']} fallback)",
        "ext": s["ext"], "direct_url": s["url"],
        "via_fallback": n["meta"]["source"],
        "expires_note": "Direct URLs expire quickly — use immediately.",
    }


def extract_info(url: str, force_fallback: bool = False) -> Dict[str, Any]:
    vid = extract_youtube_id(url)
    if force_fallback and vid:
        fb = try_fallback_info(vid)
        if fb is not None:
            return fb
        raise HTTPException(
            status_code=502,
            detail="Forced fallback failed: " + (last_fallback_error() or "no working Piped/Invidious instance")[:300])
    try:
        return extract_with_fallback(url, download=False)
    except HTTPException as e:
        if vid and should_try_fallback(e):
            fb = try_fallback_info(vid)
            if fb is not None:
                return fb
            raise final_bot_error()
        raise


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


def download_to_file(url: str, dtype: str, quality: str, fmt: str,
                     force_fallback: bool = False) -> Path:
    """Download media to a temp file. Returns path to the file."""
    job_id = uuid.uuid4().hex[:12]
    workdir = DOWNLOAD_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(workdir / "%(title).80s [%(id)s].%(ext)s")

    dtype = dtype.lower()
    fmt = fmt.lower()

    vid = extract_youtube_id(url)
    if force_fallback and vid:
        try:
            fb_path = try_fallback_download(vid, dtype, quality, fmt, workdir)
        except HTTPException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        if fb_path is not None:
            return fb_path
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=502,
            detail="Forced fallback failed: " + (last_fallback_error() or "no working Piped/Invidious instance")[:300])

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
        if vid and should_try_fallback(e):
            try:
                fb_path = try_fallback_download(vid, dtype, quality, fmt, workdir)
            except HTTPException:
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            if fb_path is not None:
                return fb_path
            shutil.rmtree(workdir, ignore_errors=True)
            raise final_bot_error()
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
        "youtube_fallback": "piped + invidious (auto)",
        "impersonate": _IMPERSONATE["target"],
        "youtube_mode": "multi-client fallback (android → mweb/tv → default)"
                        + (" + cookies ✅" if get_cookiefile() else " (no cookies)"),
    }


class CookiesRequest(BaseModel):
    cookies: str  # raw Netscape cookies.txt content (must include youtube.com)


@app.post("/api/cookies")
def save_cookies(body: CookiesRequest):
    """Save YouTube cookies pasted from 'Get cookies.txt LOCALLY' export.

    Active immediately until next redeploy. For persistence also/instead
    set the YOUTUBE_COOKIES env var. Cookies are never returned by any API.
    """
    global _cached_cookiefile
    text = (body.cookies or "").strip()
    if "youtube.com" not in text.lower() or text.count("\n") < 2 or "\t" not in text:
        raise HTTPException(status_code=400, detail=(
            "Invalid cookies: paste the FULL export from the 'Get cookies.txt LOCALLY' "
            "extension while on youtube.com (Netscape format, many tab-separated lines)."))
    (DOWNLOAD_DIR / "yt-cookies.txt").write_text(text + "\n", encoding="utf-8")
    _cached_cookiefile = None
    ok = bool(get_cookiefile())
    return {"saved": ok, "youtube_cookies": ok,
            "note": "Cookies active until next redeploy. For persistence set YOUTUBE_COOKIES env var."}


@app.delete("/api/cookies")
def delete_cookies():
    """Remove cookies saved via POST /api/cookies."""
    global _cached_cookiefile
    try:
        (DOWNLOAD_DIR / "yt-cookies.txt").unlink(missing_ok=True)
    except Exception:
        pass
    _cached_cookiefile = None
    return {"saved": False, "youtube_cookies": bool(get_cookiefile())}


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
    force_fallback: bool = Query(False, description="Skip yt-dlp, use Piped/Invidious (YouTube only)"),
):
    """Get metadata (title, thumbnail, duration, uploader) without downloading."""
    info = extract_info(url, force_fallback=force_fallback)
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
    if info.get("fallback_source"):
        result["fallback_source"] = info["fallback_source"]
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
    force_fallback: bool = Query(False, description="Skip yt-dlp, use Piped/Invidious (YouTube only)"),
):
    return handle_download(background_tasks, url, type, quality, format, direct, force_fallback)


@app.post("/api/download")
def download_post(body: DownloadRequest, background_tasks: BackgroundTasks):
    return handle_download(
        background_tasks, body.url, body.type, body.quality, body.format,
        body.direct, body.force_fallback,
    )


def handle_download(
    background_tasks: BackgroundTasks,
    url: str, dtype: str, quality: str, fmt: str, direct: bool,
    force_fallback: bool = False,
):
    dtype = (dtype or "video").lower()
    if dtype not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="type must be 'video' or 'audio'")

    if direct:
        # Return a direct CDN URL without downloading to server
        selector = build_format_selector(dtype, quality, fmt)
        vid = extract_youtube_id(url)
        if force_fallback and vid:
            return fallback_direct_response(vid, dtype, quality, fmt)
        try:
            info = extract_with_fallback(url, extra={"format": selector}, download=False)
        except HTTPException as e:
            if vid and should_try_fallback(e):
                return fallback_direct_response(vid, dtype, quality, fmt)
            raise
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
    path = download_to_file(url, dtype, quality, fmt, force_fallback=force_fallback)
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
    force_fallback: bool = Query(False, description="Skip yt-dlp, use Piped/Invidious (YouTube only)"),
):
    """Shortcut: download video as MP4. /api/mp4?url=...&quality=720"""
    return handle_download(background_tasks, url, "video", quality, "mp4", False, force_fallback)


@app.get("/api/mp3")
def shortcut_mp3(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    force_fallback: bool = Query(False, description="Skip yt-dlp, use Piped/Invidious (YouTube only)"),
):
    """Shortcut: download audio as MP3 (falls back to M4A if no ffmpeg). /api/mp3?url=..."""
    fmt = "mp3" if ffmpeg_available() else "best"
    return handle_download(background_tasks, url, "audio", "best", fmt, False, force_fallback)


# ------------------------------------------------------------------ run

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
