import asyncio
import re
from typing import Dict, Any, List, Optional, Tuple
import yt_dlp
from cachetools import TTLCache

# Cache for video info & search results (TTL: 1 hour)
_info_cache = TTLCache(maxsize=1000, ttl=3600)
_search_cache = TTLCache(maxsize=500, ttl=1800)
_playlist_cache = TTLCache(maxsize=200, ttl=1800)

YTDL_OPTS = {
    'format': 'bestaudio[ext=webm][acodec=opus]/bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'skip_download': True,
    'cachedir': False,
    'default_search': 'ytsearch',
    'geo_bypass': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web', 'mweb'],
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    },
}

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
    
    direct_audio_url = None
    direct_audio_ext = "webm"
    audio_formats = []
    http_headers = info.get('http_headers') or YTDL_OPTS['http_headers']

    formats = info.get('formats', [])
    for f in formats:
        acodec = f.get('acodec', 'none')
        if acodec and acodec != 'none':
            audio_formats.append({
                'format_id': f.get('format_id'),
                'ext': f.get('ext'),
                'abr': f.get('abr') or f.get('tbr') or 128,
                'acodec': acodec,
                'asr': f.get('asr') or 48000,
                'filesize': f.get('filesize') or f.get('filesize_approx'),
                'url': f.get('url'),
                'http_headers': f.get('http_headers', http_headers),
            })

    if 'url' in info:
        direct_audio_url = info['url']
        direct_audio_ext = info.get('ext', 'webm')
    elif audio_formats:
        audio_formats.sort(
            key=lambda x: (
                x.get('abr') or 0,
                1 if 'opus' in str(x.get('acodec', '')).lower() else 0
            ),
            reverse=True
        )
        best = audio_formats[0]
        direct_audio_url = best.get('url')
        direct_audio_ext = best.get('ext', 'webm')

    thumbnail = info.get('thumbnail')
    if not thumbnail and info.get('thumbnails'):
        thumbnail = info['thumbnails'][-1].get('url')
    if not thumbnail and info.get('id'):
        thumbnail = f"https://i.ytimg.com/vi/{info.get('id')}/hqdefault.jpg"

    processed = {
        'id': info.get('id'),
        'title': info.get('title', 'Unknown Title'),
        'description': (info.get('description') or '')[:500],
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
    url = url.strip()
    if url in _playlist_cache:
        return _playlist_cache[url]

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract_playlist_sync, url, limit)
    
    if not info:
        raise ValueError("Could not load playlist.")

    entries = info.get('entries', []) or []
    playlist_title = info.get('title', 'YouTube Playlist')
    uploader = info.get('uploader') or info.get('channel') or 'YouTube'

    tracks = []
    for entry in entries[:limit]:
        if not entry:
            continue
        v_id = entry.get('id')
        if not v_id:
            continue
        
        thumb = entry.get('thumbnail')
        if not thumb and entry.get('thumbnails'):
            thumb = entry['thumbnails'][-1].get('url')
        if not thumb:
            thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

        dur = entry.get('duration') or 0
        mins, secs = divmod(dur, 60)
        dur_str = f"{mins}:{secs:02d}"

        tracks.append({
            'id': v_id,
            'title': entry.get('title', 'Unknown Title'),
            'duration': dur,
            'duration_string': dur_str,
            'uploader': entry.get('uploader') or uploader,
            'thumbnail': thumb,
            'url': f"https://www.youtube.com/watch?v={v_id}",
            'webpage_url': f"https://www.youtube.com/watch?v={v_id}"
        })

    result = {
        'title': playlist_title,
        'uploader': uploader,
        'count': len(tracks),
        'tracks': tracks,
        'webpage_url': url
    }
    _playlist_cache[url] = result
    return result

def _search_sync(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    search_opts = {
        **YTDL_OPTS,
        'extract_flat': 'in_playlist',
    }
    with yt_dlp.YoutubeDL(search_opts) as ydl:
        results = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = results.get('entries', []) if results else []
        
        items = []
        for entry in entries:
            if not entry:
                continue
            v_id = entry.get('id')
            thumb = entry.get('thumbnail')
            if not thumb and entry.get('thumbnails'):
                thumb = entry['thumbnails'][-1].get('url')
            if not thumb and v_id:
                thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

            items.append({
                'id': v_id,
                'title': entry.get('title'),
                'duration': entry.get('duration'),
                'duration_string': entry.get('duration_string'),
                'uploader': entry.get('uploader') or entry.get('channel') or "YouTube Artist",
                'thumbnail': thumb,
                'url': f"https://www.youtube.com/watch?v={v_id}",
                'view_count': entry.get('view_count')
            })
        return items

async def search_youtube(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    cache_key = f"{query}:{limit}"
    if cache_key in _search_cache:
        return _search_cache[cache_key]
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _search_sync, query, limit)
    _search_cache[cache_key] = results
    return results
