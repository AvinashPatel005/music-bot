import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple
import requests
from cachetools import TTLCache

logger = logging.getLogger("music_bot.spotify")
_spotify_cache = TTLCache(maxsize=500, ttl=3600)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def is_spotify_url(url: str) -> bool:
    url = url.strip()
    return "spotify.com" in url or "spotify.link" in url or url.startswith("spotify:")

def parse_spotify_url(url: str) -> Optional[Tuple[str, str]]:
    """
    Parses any Spotify URL (including international /intl-xx/ and query parameters)
    or URI into (item_type, item_id).
    """
    url = url.strip()
    
    # Handle Spotify URI e.g. spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
    uri_match = re.search(r'spotify:(track|playlist|album):([a-zA-Z0-9]+)', url, re.IGNORECASE)
    if uri_match:
        return uri_match.group(1).lower(), uri_match.group(2)
    
    # Handle URL e.g. open.spotify.com/(intl-xx/)?(track|playlist|album)/[id]
    url_match = re.search(r'/(track|playlist|album)/([a-zA-Z0-9]+)', url, re.IGNORECASE)
    if url_match:
        return url_match.group(1).lower(), url_match.group(2)
        
    return None

def _extract_from_embed_html(item_type: str, item_id: str) -> Optional[Dict[str, Any]]:
    """
    Scrapes the public Spotify embed page (__NEXT_DATA__ JSON).
    This works without requiring any API keys or tokens.
    """
    embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
    try:
        res = requests.get(embed_url, headers=HEADERS, timeout=6)
        if res.status_code != 200:
            return None

        # Look for __NEXT_DATA__ script tag
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">({.*?})</script>', res.text)
        if not match:
            return None

        data = json.loads(match.group(1))
        entity = (
            data.get("props", {})
            .get("pageProps", {})
            .get("state", {})
            .get("data", {})
            .get("entity", {})
        )
        if not entity:
            return None

        title = entity.get("name") or entity.get("title") or f"Spotify {item_type.capitalize()}"
        subtitle = entity.get("subtitle") or entity.get("author") or "Spotify"
        
        # Cover art
        cover_art = ""
        if entity.get("coverArt", {}).get("sources"):
            cover_art = entity["coverArt"]["sources"][0].get("url", "")
        elif entity.get("visualIdentity", {}).get("image"):
            cover_art = entity["visualIdentity"]["image"][0].get("url", "")

        # Extract tracks
        raw_tracks = entity.get("trackList") or entity.get("tracks") or []
        parsed_tracks = []

        for idx, t in enumerate(raw_tracks):
            t_name = t.get("title") or t.get("name") or "Unknown Song"
            t_artist = t.get("subtitle") or t.get("artist") or subtitle
            dur_ms = t.get("duration") or t.get("duration_ms") or 0
            dur_sec = dur_ms // 1000 if dur_ms > 1000 else dur_ms
            mins, secs = divmod(dur_sec, 60)
            dur_str = f"{mins}:{secs:02d}"

            t_uri = t.get("uri") or ""
            t_id = t_uri.split(":")[-1] if ":" in t_uri else f"{item_id}_{idx}"

            parsed_tracks.append({
                "id": t_id,
                "title": t_name,
                "artist": t_artist,
                "search_query": f"{t_name} {t_artist} audio",
                "duration": dur_sec,
                "duration_string": dur_str,
                "thumbnail": cover_art,
                "url": f"https://open.spotify.com/track/{t_id}"
            })

        # If single track entity
        if item_type == "track" and not parsed_tracks:
            dur_ms = entity.get("duration") or 0
            dur_sec = dur_ms // 1000 if dur_ms > 1000 else dur_ms
            mins, secs = divmod(dur_sec, 60)
            parsed_tracks.append({
                "id": item_id,
                "title": title,
                "artist": subtitle,
                "search_query": f"{title} {subtitle} audio",
                "duration": dur_sec,
                "duration_string": f"{mins}:{secs:02d}",
                "thumbnail": cover_art,
                "url": f"https://open.spotify.com/track/{item_id}"
            })

        return {
            "type": item_type,
            "title": title,
            "author": subtitle,
            "thumbnail": cover_art,
            "count": len(parsed_tracks),
            "tracks": parsed_tracks,
            "url": f"https://open.spotify.com/{item_type}/{item_id}"
        }

    except Exception as e:
        logger.error(f"Failed to scrape Spotify embed for {item_type}/{item_id}: {e}")
        return None

def get_spotify_track(track_id: str) -> Dict[str, Any]:
    cache_key = f"track:{track_id}"
    if cache_key in _spotify_cache:
        return _spotify_cache[cache_key]

    # 1. Try public embed extraction
    embed_data = _extract_from_embed_html("track", track_id)
    if embed_data and embed_data.get("tracks"):
        res = embed_data["tracks"][0]
        _spotify_cache[cache_key] = res
        return res

    # 2. Fallback to Spotify oEmbed
    try:
        oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
        res = requests.get(oembed_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            title_raw = data.get("title", "Spotify Track")
            author = data.get("author_name", "Spotify Artist")
            thumb = data.get("thumbnail_url", "")
            
            result = {
                "type": "track",
                "id": track_id,
                "title": title_raw,
                "artist": author,
                "search_query": f"{title_raw} {author} audio",
                "duration": 0,
                "duration_string": "0:00",
                "thumbnail": thumb,
                "url": f"https://open.spotify.com/track/{track_id}"
            }
            _spotify_cache[cache_key] = result
            return result
    except Exception:
        pass

    # Generic fallback
    return {
        "type": "track",
        "id": track_id,
        "title": f"Spotify Track ({track_id})",
        "artist": "Spotify",
        "search_query": f"Spotify track {track_id} audio",
        "duration": 0,
        "duration_string": "0:00",
        "thumbnail": "",
        "url": f"https://open.spotify.com/track/{track_id}"
    }

def get_spotify_playlist_or_album(item_type: str, item_id: str, limit: int = 100) -> Dict[str, Any]:
    cache_key = f"{item_type}:{item_id}"
    if cache_key in _spotify_cache:
        return _spotify_cache[cache_key]

    # 1. Try public embed extraction
    embed_data = _extract_from_embed_html(item_type, item_id)
    if embed_data and embed_data.get("tracks"):
        embed_data["tracks"] = embed_data["tracks"][:limit]
        embed_data["count"] = len(embed_data["tracks"])
        _spotify_cache[cache_key] = embed_data
        return embed_data

    # 2. Fallback to oEmbed for basic title/thumbnail
    title = f"Spotify {item_type.capitalize()}"
    author = "Spotify"
    thumb = ""
    try:
        oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/{item_type}/{item_id}"
        res = requests.get(oembed_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            title = data.get("title", title)
            thumb = data.get("thumbnail_url", "")
            author = data.get("author_name", author)
    except Exception:
        pass

    result = {
        "type": item_type,
        "title": title,
        "author": author,
        "thumbnail": thumb,
        "count": 0,
        "tracks": [],
        "url": f"https://open.spotify.com/{item_type}/{item_id}"
    }
    return result
