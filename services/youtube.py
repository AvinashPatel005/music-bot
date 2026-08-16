import os
import re
import base64
import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
import yt_dlp
from cachetools import TTLCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("music_bot.youtube")

# Cache for video info & search results (TTL: 1 hour)
_info_cache = TTLCache(maxsize=1000, ttl=3600)
_search_cache = TTLCache(maxsize=500, ttl=1800)
_playlist_cache = TTLCache(maxsize=200, ttl=1800)

def get_cookiefile_path() -> Optional[str]:
    """
    Resolves cookie file from environment variable or local file for Render/Cloud deployment.
    """
    env_name = "YOUTUBE_COOKIES" if os.getenv("YOUTUBE_COOKIES") else ("YOUTUBE_COOKIES_BASE64" if os.getenv("YOUTUBE_COOKIES_BASE64") else None)
    cookie_env = os.getenv("YOUTUBE_COOKIES") or os.getenv("YOUTUBE_COOKIES_BASE64")

    if cookie_env:
        cookie_path = "/tmp/youtube_cookies.txt" if os.path.exists("/tmp") else "youtube_cookies.txt"
        try:
            try:
                decoded = base64.b64decode(cookie_env).decode('utf-8')
                if "# Netscape HTTP Cookie File" in decoded or "youtube.com" in decoded:
                    cookie_content = decoded
                else:
                    cookie_content = cookie_env
            except Exception:
                cookie_content = cookie_env
            
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write(cookie_content)
            logger.info("Using YouTube cookies from environment variable '%s' (saved to %s)", env_name, cookie_path)
            return cookie_path
        except Exception as e:
            logger.warning("Failed to parse/write YouTube cookies from environment variable '%s': %s", env_name, e)

    if os.path.exists("cookies.txt"):
        logger.info("Using YouTube cookies from local file 'cookies.txt'")
        return "cookies.txt"

    logger.info("No YouTube cookies detected (neither in env nor in local cookies.txt). Operating without cookies.")
    return None

COOKIE_FILE = get_cookiefile_path()

# YTDL options using the user's cookie with JS challenge solver & multi-client fallback
YTDL_OPTS: Dict[str, Any] = {
    'format': 'bestaudio/ba/b/best',
    'format_sort': ['hasaud', 'acodec', 'abr'],
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'skip_download': True,
    'cachedir': False,
    'geo_bypass': True,
    'remote_components': ['ejs:github'],
    'extractor_args': {
        'youtube': {
            'player_client': ['android_vr', 'android', 'ios', 'web']
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    },
}

if COOKIE_FILE:
    YTDL_OPTS['cookiefile'] = COOKIE_FILE

# Dedicated options for ytsearch queries
SEARCH_OPTS: Dict[str, Any] = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': 'in_playlist',
    'skip_download': True,
    'cachedir': False,
    'default_search': 'ytsearch',
    'remote_components': ['ejs:github'],
    'extractor_args': {
        'youtube': {
            'player_client': ['android_vr', 'android', 'ios', 'web']
        }
    },
}

if COOKIE_FILE:
    SEARCH_OPTS['cookiefile'] = COOKIE_FILE

def clean_youtube_url(url_or_id: str) -> str:
    url_or_id = url_or_id.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return f"https://www.youtube.com/watch?v={url_or_id}"
    return url_or_id

def is_playlist(url: str) -> bool:
    url = url.strip()
    return "playlist?list=" in url or "&list=" in url or "/sets/" in url

def _extract_info_sync(url: str) -> Dict[str, Any]:
    with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)

async def get_video_info(url_or_id: str) -> Dict[str, Any]:
    clean_url = clean_youtube_url(url_or_id)
    
    if clean_url in _info_cache:
        return _info_cache[clean_url]
    
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract_info_sync, clean_url)
    
    if not info:
        raise ValueError("Could not extract video metadata.")
    
    # Extract direct audio URL if available
    direct_audio_url = None
    direct_audio_ext = "webm"
    audio_formats = []
    
    formats = info.get("formats", [])
    
    # 1. Look for audio-only streams
    audio_only = [f for f in formats if f.get("url") and f.get("vcodec") == "none" and f.get("acodec") != "none"]
    
    # 2. Look for any stream with audio
    any_audio = [f for f in formats if f.get("url") and f.get("acodec") != "none"]

    if audio_only:
        opus_formats = [f for f in audio_only if f.get("ext") == "webm" or "opus" in (f.get("acodec") or "")]
        best_format = opus_formats[-1] if opus_formats else audio_only[-1]
        direct_audio_url = best_format.get("url")
        direct_audio_ext = best_format.get("ext", "webm")
    elif any_audio:
        best_format = any_audio[-1]
        direct_audio_url = best_format.get("url")
        direct_audio_ext = best_format.get("ext", "mp4")
    elif formats:
        best_format = formats[-1]
        direct_audio_url = best_format.get("url")
        direct_audio_ext = best_format.get("ext", "mp4")
    else:
        direct_audio_url = info.get("url")

    # Collect available formats summary
    for f in audio_only:
        audio_formats.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "abr": f.get("abr"),
            "acodec": f.get("acodec"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
        })

    # Pick high-res thumbnail
    thumbnails = info.get("thumbnails", [])
    thumbnail = thumbnails[-1].get("url") if thumbnails else info.get("thumbnail")

    # Collect http headers needed for streaming direct url
    http_headers = info.get("http_headers") or {}

    processed = {
        'id': info.get('id'),
        'title': info.get('title', 'Unknown Title'),
        'description': info.get('description', ''),
        'duration': info.get('duration', 0),
        'duration_string': info.get('duration_string') or "0:00",
        'uploader': info.get('uploader') or info.get('channel') or "YouTube Artist",
        'channel_id': info.get('channel_id'),
        'thumbnail': thumbnail,
        'view_count': info.get('view_count', 0),
        'like_count': info.get('like_count', 0),
        'webpage_url': info.get('webpage_url', clean_url),
        'direct_audio_url': direct_audio_url,
        'direct_audio_ext': direct_audio_ext,
        'http_headers': http_headers,
        'available_audio_formats': audio_formats,
    }

    _info_cache[clean_url] = processed
    return processed

def _extract_playlist_sync(url: str, limit: int = 100) -> Dict[str, Any]:
    opts = {
        **YTDL_OPTS,
        'noplaylist': False,
        'extract_flat': 'in_playlist',
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)

async def get_playlist_info(url: str, limit: int = 100) -> Dict[str, Any]:
    if url in _playlist_cache:
        return _playlist_cache[url]

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract_playlist_sync, url, limit)

    if not info:
        raise ValueError("Could not extract playlist metadata.")

    entries = info.get("entries") or []
    tracks = []

    for entry in entries[:limit]:
        if not entry:
            continue
        v_id = entry.get("id")
        v_url = entry.get("url") or f"https://www.youtube.com/watch?v={v_id}"
        if not v_url.startswith("http"):
            v_url = f"https://www.youtube.com/watch?v={v_id}"

        dur = entry.get("duration") or 0
        if dur:
            m, s = divmod(int(dur), 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        else:
            dur_str = "0:00"

        thumbnails = entry.get("thumbnails") or []
        thumb = thumbnails[-1].get("url") if thumbnails else entry.get("thumbnail") or ""

        tracks.append({
            "id": v_id,
            "title": entry.get("title", "Unknown Title"),
            "url": v_url,
            "webpage_url": v_url,
            "duration": dur,
            "duration_string": dur_str,
            "uploader": entry.get("uploader") or entry.get("channel") or info.get("uploader") or "YouTube Artist",
            "thumbnail": thumb,
        })

    processed = {
        "id": info.get("id"),
        "title": info.get("title", "YouTube Playlist"),
        "uploader": info.get("uploader") or info.get("channel") or "Unknown Creator",
        "webpage_url": info.get("webpage_url", url),
        "track_count": len(tracks),
        "tracks": tracks,
    }

    _playlist_cache[url] = processed
    return processed

def _search_sync(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    search_query = f"ytsearch{limit}:{query}"
    with yt_dlp.YoutubeDL(SEARCH_OPTS) as ydl:
        info = ydl.extract_info(search_query, download=False)
        entries = info.get('entries', []) if info else []
        
        results = []
        for e in entries:
            if not e:
                continue
            v_id = e.get('id')
            v_url = e.get('url') or f"https://www.youtube.com/watch?v={v_id}"
            if not v_url.startswith("http"):
                v_url = f"https://www.youtube.com/watch?v={v_id}"
            
            thumbnails = e.get('thumbnails', [])
            thumb = thumbnails[-1].get('url') if thumbnails else e.get('thumbnail', '')
            
            results.append({
                'id': v_id,
                'title': e.get('title', 'Unknown Title'),
                'url': v_url,
                'duration': e.get('duration', 0),
                'duration_string': e.get('duration_string') or "0:00",
                'uploader': e.get('uploader') or e.get('channel') or "YouTube Artist",
                'thumbnail': thumb,
            })
        return results

async def search_youtube(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    if query in _search_cache:
        return _search_cache[query]
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _search_sync, query, limit)
    _search_cache[query] = results
    return results
