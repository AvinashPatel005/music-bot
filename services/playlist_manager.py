import os
import time
import logging
from typing import Dict, Any, List, Optional
import motor.motor_asyncio
from pymongo import ASCENDING

logger = logging.getLogger("music_bot.playlists")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB_NAME", "discord_music_bot")

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db = None
_collection = None

def get_collection():
    global _client, _db, _collection
    if _collection is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        _db = _client[DB_NAME]
        _collection = _db["user_playlists"]
    return _collection

async def init_db():
    try:
        col = get_collection()
        await col.create_index([("user_id", ASCENDING), ("name_slug", ASCENDING)], unique=True)
        logger.info("MongoDB Playlist index initialized successfully.")
    except Exception as e:
        logger.warning(f"MongoDB initialization warning: {e}. Check MONGO_URI in .env.")

def _slugify(name: str) -> str:
    return name.strip().lower().replace(" ", "_")

async def create_playlist(
    user_id: int,
    user_name: str,
    name: str,
    songs: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    col = get_collection()
    name_clean = name.strip()
    slug = _slugify(name_clean)

    existing = await col.find_one({"user_id": user_id, "name_slug": slug})
    if existing:
        raise ValueError(f"You already have a playlist named '{name_clean}'.")

    now = int(time.time())
    doc = {
        "user_id": user_id,
        "user_name": user_name,
        "name": name_clean,
        "name_slug": slug,
        "songs": songs or [],
        "created_at": now,
        "updated_at": now,
    }

    result = await col.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc

async def get_playlist(user_id: int, name: str) -> Optional[Dict[str, Any]]:
    col = get_collection()
    slug = _slugify(name)
    doc = await col.find_one({"user_id": user_id, "name_slug": slug})
    return doc

async def list_user_playlists(user_id: int) -> List[Dict[str, Any]]:
    col = get_collection()
    cursor = col.find({"user_id": user_id}).sort("updated_at", -1)
    results = []
    async for doc in cursor:
        results.append(doc)
    return results

async def add_song_to_playlist(user_id: int, name: str, song_dict: Dict[str, Any]) -> Dict[str, Any]:
    col = get_collection()
    slug = _slugify(name)
    
    pl = await get_playlist(user_id, name)
    if not pl:
        raise ValueError(f"Playlist '{name}' not found. Use `/playlist create {name}` first.")

    now = int(time.time())
    await col.update_one(
        {"user_id": user_id, "name_slug": slug},
        {
            "$push": {"songs": song_dict},
            "$set": {"updated_at": now}
        }
    )
    return await get_playlist(user_id, name)

async def add_multiple_songs_to_playlist(user_id: int, name: str, songs: List[Dict[str, Any]]) -> Dict[str, Any]:
    col = get_collection()
    slug = _slugify(name)
    
    pl = await get_playlist(user_id, name)
    if not pl:
        raise ValueError(f"Playlist '{name}' not found.")

    now = int(time.time())
    await col.update_one(
        {"user_id": user_id, "name_slug": slug},
        {
            "$push": {"songs": {"$each": songs}},
            "$set": {"updated_at": now}
        }
    )
    return await get_playlist(user_id, name)

async def remove_song_from_playlist(user_id: int, name: str, index: int) -> Dict[str, Any]:
    col = get_collection()
    slug = _slugify(name)
    pl = await get_playlist(user_id, name)
    if not pl:
        raise ValueError(f"Playlist '{name}' not found.")

    songs = pl.get("songs", [])
    if index < 1 or index > len(songs):
        raise ValueError(f"Invalid song number. Must be between 1 and {len(songs)}.")

    removed = songs.pop(index - 1)
    now = int(time.time())

    await col.update_one(
        {"user_id": user_id, "name_slug": slug},
        {
            "$set": {
                "songs": songs,
                "updated_at": now
            }
        }
    )
    return removed

async def delete_playlist(user_id: int, name: str) -> bool:
    col = get_collection()
    slug = _slugify(name)
    result = await col.delete_one({"user_id": user_id, "name_slug": slug})
    return result.deleted_count > 0
