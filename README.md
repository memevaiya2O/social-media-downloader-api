<div align="center">

![Banner](https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=200&section=header&text=Social%20Downloader%20API&fontSize=55&fontColor=fff&animation=fadeIn&fontAlignY=38&desc=1000%2B%20Sites%20%E2%80%A2%20Video%20%2B%20Audio%20%E2%80%A2%20One%20Powerful%20API&descAlignY=62)

[![Python](https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-powered-red?style=for-the-badge&logo=youtube)](https://github.com/yt-dlp/yt-dlp)
[![License](https://img.shields.io/badge/License-MIT-gold?style=for-the-badge)](LICENSE)

[![Deploy to Render](https://img.shields.io/badge/Deploy_to-Render-46E3B7?style=for-the-badge&logo=render)](https://render.com/docs/blueprint-spec)
[![Deploy on Railway](https://img.shields.io/badge/Deploy_on-Railway-0B0D0F?style=for-the-badge&logo=railway)](https://railway.com)

**The ultimate open-source API to fetch info & download video/audio from any social media platform.**

[🚀 Live Demo](#-one-click-deploy) • [📡 API Docs](#-api-endpoints) • [💡 Examples](#-usage-examples) • [👑 Credits](#-credits)

</div>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🌍 **1000+ Sites** | YouTube, TikTok, Instagram, Facebook, X/Twitter, Reddit, Vimeo, SoundCloud & more |
| 🎬 **Video Download** | MP4 / WebM / MKV — Best, 4K, 1080p, 720p, 480p, 360p or Lowest |
| 🎵 **Audio Download** | MP3 / M4A / OPUS / WAV extraction with ffmpeg |
| 🔍 **Metadata API** | Title, thumbnail, uploader, views, likes, duration, qualities — no download needed |
| ⚡ **Direct CDN URLs** | Get expiring direct links as JSON (`direct=true`) — no server storage |
| 🎨 **Built-in Web UI** | Beautiful tester interface at `/` — paste link & download |
| 📚 **Auto Docs** | Interactive Swagger UI at `/docs` |
| 🐳 **Docker Ready** | One-click deploy on **Render** & **Railway** with ffmpeg included |
| 🛡️ **Safe Guards** | Duration limits, filename sanitizing, auto temp-file cleanup |
| 🌐 **CORS Enabled** | Call it directly from any website or mobile app |

---

## 🎯 Supported Platforms

<div align="center">

| Platform | Video | Audio | Notes |
|:--------:|:-----:|:-----:|:------|
| ▶ **YouTube** | ✅ | ✅ | Videos, Shorts, Music |
| 🎵 **TikTok** | ✅ | ✅ | Videos, no watermark* |
| 📸 **Instagram** | ✅ | ✅ | Reels, Posts, Stories (public) |
| 📘 **Facebook** | ✅ | ✅ | Videos, Watch, Reels |
| 𝕏 **X / Twitter** | ✅ | ✅ | Videos, GIFs, Spaces |
| 👽 **Reddit** | ✅ | ✅ | v.redd.it + hosted video |
| 🎬 **Vimeo** | ✅ | ✅ | Public videos |
| ☁ **SoundCloud** | ➖ | ✅ | Tracks, playlists |
| 🟣 **Twitch** | ✅ | ✅ | VODs & Clips |
| 📌 **Pinterest** | ✅ | ➖ | Video pins |
| 👻 **Snapchat** | ✅ | ➖ | Spotlight |
| ➕ **+1000 more** | ✅ | ✅ | Full list via yt-dlp |

*\*watermark depends on source availability*

</div>

---

## 🚀 One-Click Deploy

### Render.com (Free)

1. Fork / push this repo to your GitHub
2. Go to [render.com](https://render.com) → **New → Blueprint**
3. Select this repo — `render.yaml` auto-configures everything
4. Click **Apply** → Done! Your API is live at `https://<name>.onrender.com`

> Uses Docker runtime → **ffmpeg included** → full MP3 support ✅

### Railway.com (Free trial)

**Option A — Dashboard:**
1. Go to [railway.com](https://railway.com) → **New Project → Deploy from Repo**
2. Select this repo — `railway.toml` + `Dockerfile` auto-detected
3. Railway assigns a public URL → Done! 🎉

**Option B — CLI:**
```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

### 🐳 Docker (any VPS)

```bash
docker build -t social-dl-api .
docker run -p 8000:8000 -e MAX_DURATION_SECONDS=3600 social-dl-api
```

### 💻 Local

```bash
git clone https://github.com/memevaiya2O/social-media-downloader-api.git
cd social-media-downloader-api
pip install -r requirements.txt
python app.py
# → UI: http://localhost:8000/   Docs: http://localhost:8000/docs
```

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|:------:|----------|-------------|
| `GET` | `/` | 🎨 Web tester UI |
| `GET` | `/docs` | 📚 Swagger docs |
| `GET` | `/api/health` | 💚 Health check |
| `GET` | `/api/supported` | 🌍 Supported platforms list |
| `GET` | `/api/info?url=...` | 🔍 Metadata (add `&include_formats=true`) |
| `GET` | `/api/formats?url=...` | 🎞 All downloadable formats |
| `GET`/`POST` | `/api/download` | ⬇ Download file or get CDN URL |
| `GET` | `/api/mp4?url=...&quality=720` | ⚡ Shortcut: MP4 video |
| `GET` | `/api/mp3?url=...` | ⚡ Shortcut: MP3 audio |

### Parameters — `/api/download`

| Param | Options | Default |
|-------|---------|---------|
| `url` | Any post / video URL | *required* |
| `type` | `video` / `audio` | `video` |
| `quality` | `best` `2160` `1440` `1080` `720` `480` `360` `240` `lowest` | `best` |
| `format` | video: `mp4` `webm` `mkv` `best` · audio: `mp3` `m4a` `opus` `wav` `best` | `mp4` |
| `direct` | `true` → JSON with CDN URL · `false` → file download | `false` |

---

## 💡 Usage Examples

**cURL — get info:**
```bash
curl "https://YOUR-APP.onrender.com/api/info?url=https://www.tiktok.com/@user/video/123"
```

**cURL — download 720p video:**
```bash
curl -OJ "https://YOUR-APP.onrender.com/api/download?url=<URL>&type=video&quality=720&format=mp4"
```

**cURL — download MP3:**
```bash
curl -OJ "https://YOUR-APP.onrender.com/api/mp3?url=<URL>"
```

**cURL — direct CDN link (JSON, no server download):**
```bash
curl "https://YOUR-APP.onrender.com/api/download?url=<URL>&type=video&quality=720&direct=true"
```

**JavaScript:**
```js
const api = "https://YOUR-APP.onrender.com";
// 1. fetch info
const info = await fetch(`${api}/api/info?url=${encodeURIComponent(link)}`).then(r => r.json());
console.log(info.title, info.thumbnail);
// 2. trigger download
window.open(`${api}/api/download?url=${encodeURIComponent(link)}&type=video&quality=720&format=mp4`);
```

**Python:**
```python
import requests
api = "https://YOUR-APP.onrender.com"
r = requests.get(f"{api}/api/download", params={"url": link, "type": "audio", "format": "mp3"})
open("song.mp3", "wb").write(r.content)
```

---

## 🛠 Fix: YouTube "Sign in to confirm you're not a bot"

**Why it happens:** YouTube flags datacenter IPs (Render, Railway, most VPS providers) and demands bot verification for anonymous requests. Your link is fine — the *server IP* is blocked.

**Already built-in ✅:** every request auto-retries through multiple YouTube clients (`android_music → android → mweb → tv → web`). Mobile/TV API endpoints usually bypass the IP check, so most links work with **zero setup**.

**If it still fails, add cookies (2 minutes):**

1. Desktop Chrome/Edge → install the **"Get cookies.txt LOCALLY"** extension
2. Go to `youtube.com` (logged in — preferably a spare/throwaway account) → click Export → copy **all** text
3. **Render:** Dashboard → your service → **Environment** → add `YOUTUBE_COOKIES` = pasted text → Save (auto-redeploys)
   **Railway:** service → **Variables** → add `YOUTUBE_COOKIES` → redeploys automatically
   **Local:** save the text as `cookies.txt` next to `app.py`
4. Open `/api/health` → `"youtube_cookies": true` means it worked ✅

> ⚠️ Never commit `cookies.txt` (it's gitignored). Use a spare Google account, and re-export if errors return after weeks/months (cookies expire).

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Server port (auto-injected by Render/Railway) |
| `MAX_DURATION_SECONDS` | `3600` | Reject videos longer than this |
| `YOUTUBE_COOKIES` | *(empty)* | Paste exported youtube.com cookies → fixes bot-check (see below) |

---

## 📁 Project Structure

```
social-media-downloader-api/
├── 🐍 app.py              # Main API (FastAPI + yt-dlp)
├── 🎨 static/index.html   # Web tester UI
├── 🐳 Dockerfile          # Python 3.12 + ffmpeg image
├── ☁️ render.yaml         # Render Blueprint deploy config
├── 🚂 railway.toml        # Railway deploy config
├── 📄 Procfile            # Fallback process file
├── 📦 requirements.txt    # Python dependencies
├── 🧪 test_api.py         # Smoke test script
└── 📖 README.md           # You are here
```

---

## ⚠️ Disclaimer

> This tool is for **personal & educational use only**. Only download content you own or have rights to. Respect each platform's Terms of Service and copyright laws. The author is not responsible for misuse.

---

<div align="center">

## 👑 Credits

<img src="https://capsule-render.vercel.app/api?type=rect&color=gradient&customColorList=6,11,20&height=90&section=header&text=R4AD%20BHAI&fontSize=45&fontColor=fff&animation=blinking&fontAlignY=45" width="100%"/>

### Created with ❤️ by **R 4 A D Bhai**

[![Telegram 1](https://img.shields.io/badge/Telegram-@zerox6t9-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/zerox6t9)
[![Telegram 2](https://img.shields.io/badge/Telegram-@Infinity_codex-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/Infinity_codex)

**📩 Contact:** [t.me/zerox6t9](https://t.me/zerox6t9) • [t.me/Infinity_codex](https://t.me/Infinity_codex)

⭐ **If you like this project, give it a star!** ⭐

![Footer](https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=120&section=footer)

</div>
