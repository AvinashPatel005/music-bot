import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any
import discord
from services.youtube import get_video_info, search_youtube, clean_youtube_url, is_playlist, get_playlist_info
from services.spotify import is_spotify_url, parse_spotify_url, get_spotify_track, get_spotify_playlist_or_album
import shutil

logger = logging.getLogger("music_bot.player")

FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
YTDL_PATH = shutil.which("yt-dlp") or "yt-dlp"

FFMPEG_BEFORE_OPTS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5 "
    "-probesize 10M "
    "-analyzeduration 10M "
    "-user_agent \"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\""
)

FFMPEG_AUDIO_OPTS = "-vn"

@dataclass
class Song:
    id: str
    title: str
    url: str
    webpage_url: str
    duration: int
    duration_string: str
    uploader: str
    thumbnail: str
    requester: discord.User | discord.Member
    direct_audio_url: Optional[str] = None
    http_headers: Optional[Dict[str, str]] = None
    search_query: Optional[str] = None
    source_type: str = "youtube"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "webpage_url": self.webpage_url,
            "duration": self.duration,
            "duration_string": self.duration_string,
            "uploader": self.uploader,
            "thumbnail": self.thumbnail,
            "search_query": self.search_query,
            "source_type": self.source_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], requester: discord.User | discord.Member) -> "Song":
        return cls(
            id=data.get("id", ""),
            title=data.get("title", "Unknown Title"),
            url=data.get("url", ""),
            webpage_url=data.get("webpage_url", data.get("url", "")),
            duration=data.get("duration", 0),
            duration_string=data.get("duration_string", "0:00"),
            uploader=data.get("uploader", "Artist"),
            thumbnail=data.get("thumbnail", ""),
            requester=requester,
            search_query=data.get("search_query"),
            source_type=data.get("source_type", "youtube"),
        )

    @classmethod
    async def from_query(cls, query: str, requester: discord.User | discord.Member) -> "Song":
        query = query.strip()
        if not (query.startswith("http://") or query.startswith("https://") or len(query) == 11):
            results = await search_youtube(query, limit=1)
            if not results:
                raise ValueError(f"No results found for: {query}")
            target_url = results[0]["url"]
        else:
            target_url = clean_youtube_url(query)

        info = await get_video_info(target_url)
        return cls(
            id=info.get("id", ""),
            title=info.get("title", "Unknown Title"),
            url=info.get("webpage_url", target_url),
            webpage_url=info.get("webpage_url", target_url),
            duration=info.get("duration", 0),
            duration_string=info.get("duration_string", "0:00"),
            uploader=info.get("uploader", "Unknown Artist"),
            thumbnail=info.get("thumbnail", ""),
            requester=requester,
            direct_audio_url=info.get("direct_audio_url"),
            http_headers=info.get("http_headers"),
            source_type="youtube"
        )

    @classmethod
    async def from_spotify(cls, track_id: str, requester: discord.User | discord.Member) -> "Song":
        loop = asyncio.get_running_loop()
        track_info = await loop.run_in_executor(None, get_spotify_track, track_id)
        
        return cls(
            id=track_id,
            title=track_info["title"],
            url=track_info["url"],
            webpage_url=track_info["url"],
            duration=track_info["duration"],
            duration_string=track_info["duration_string"],
            uploader=track_info["artist"],
            thumbnail=track_info["thumbnail"],
            requester=requester,
            direct_audio_url=None,
            search_query=track_info["search_query"],
            source_type="spotify"
        )

    @classmethod
    async def from_playlist(cls, playlist_url: str, requester: discord.User | discord.Member, limit: int = 100) -> Tuple[Dict[str, Any], List["Song"]]:
        pl_info = await get_playlist_info(playlist_url, limit=limit)
        songs = []
        for t in pl_info.get("tracks", []):
            songs.append(cls(
                id=t["id"],
                title=t["title"],
                url=t["url"],
                webpage_url=t["webpage_url"],
                duration=t["duration"],
                duration_string=t["duration_string"],
                uploader=t["uploader"],
                thumbnail=t["thumbnail"],
                requester=requester,
                direct_audio_url=None,
                source_type="youtube"
            ))
        return pl_info, songs

    @classmethod
    async def from_spotify_collection(cls, item_type: str, item_id: str, requester: discord.User | discord.Member, limit: int = 100) -> Tuple[Dict[str, Any], List["Song"]]:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, get_spotify_playlist_or_album, item_type, item_id, limit)
        songs = []
        for t in data.get("tracks", []):
            songs.append(cls(
                id=t["id"],
                title=t["title"],
                url=t["url"],
                webpage_url=t["url"],
                duration=t["duration"],
                duration_string=t["duration_string"],
                uploader=t["artist"],
                thumbnail=t["thumbnail"],
                requester=requester,
                direct_audio_url=None,
                search_query=t["search_query"],
                source_type="spotify"
            ))
        return data, songs


import subprocess

class PipedAudioSource(discord.AudioSource):
    """
    Pipes yt-dlp direct output into ffmpeg for real-time 48kHz stereo PCM streaming.
    Completely eliminates YouTube 403 Forbidden errors and direct stream expiration.
    """
    def __init__(self, ytdl_proc: subprocess.Popen, ffmpeg_proc: subprocess.Popen, first_frame: bytes):
        self._ytdl_proc = ytdl_proc
        self._ffmpeg_proc = ffmpeg_proc
        self._stdout = ffmpeg_proc.stdout
        self._first_frame = first_frame
        self._first_read = True

    @classmethod
    def create(cls, url: str) -> "PipedAudioSource":
        from services.youtube import COOKIE_FILE
        ytdl_cmd = [
            YTDL_PATH,
            "--format", "bestaudio/ba/b/best",
            "--quiet",
            "--no-warnings",
            "--buffer-size", "64k",
            "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=android_vr,android,ios,web",
            "-o", "-",
            url
        ]
        if COOKIE_FILE:
            ytdl_cmd.extend(["--cookies", COOKIE_FILE])

        ffmpeg_cmd = [
            FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "2",
            "pipe:1"
        ]

        ytdl_proc = subprocess.Popen(
            ytdl_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=ytdl_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        if ytdl_proc.stdout:
            ytdl_proc.stdout.close()

        # Pre-buffer the initial 48kHz audio frame so discord.py never encounters a premature EOF
        first_frame = ffmpeg_proc.stdout.read(3840)
        return cls(ytdl_proc, ffmpeg_proc, first_frame)

    def read(self) -> bytes:
        if self._first_read:
            self._first_read = False
            return self._first_frame
        
        # Accumulate pipe chunks until an exact 3840-byte 48kHz audio frame is formed
        data = bytearray()
        while len(data) < 3840:
            chunk = self._stdout.read(3840 - len(data))
            if not chunk:
                return bytes(data) if data else b""
            data.extend(chunk)
        return bytes(data)

    def cleanup(self):
        try:
            self._ffmpeg_proc.kill()
        except Exception:
            pass
        try:
            self._ytdl_proc.kill()
        except Exception:
            pass


class GuildMusicPlayer:
    """
    Manages voice connection, queue, and clean card UI for a Discord Guild.
    """
    def __init__(self, bot: discord.Client, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.loop_current: bool = False
        self.volume: float = 0.7
        self.text_channel: Optional[discord.TextChannel] = None
        self.widget_message: Optional[discord.Message] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    @property
    def is_paused(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_paused())

    async def connect(self, voice_channel: discord.VoiceChannel):
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel.id != voice_channel.id:
                await self.voice_client.move_to(voice_channel)
        else:
            self.voice_client = await voice_channel.connect(self_deaf=True)

    def add_to_queue(self, song: Song) -> int:
        self.queue.append(song)
        return len(self.queue)

    def add_multiple_to_queue(self, songs: List[Song]) -> int:
        self.queue.extend(songs)
        return len(self.queue)

    def clear_queue(self):
        self.queue.clear()

    def skip(self):
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()

    def pause(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    def set_volume(self, delta: float) -> int:
        new_vol = max(0.1, min(1.0, self.volume + delta))
        self.volume = round(new_vol, 2)
        if self.voice_client and self.voice_client.source and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self.volume
        return int(self.volume * 100)

    async def _update_voice_channel_status(self, status: Optional[str]):
        """
        Updates the voice lobby status with the currently playing song name.
        Uses both discord.py native set_status and direct Discord REST API endpoint.
        """
        if not self.voice_client or not self.voice_client.channel:
            return
        channel = self.voice_client.channel
        status_text = status[:480] if status else ""

        # Method 1: Discord.py native set_status
        if hasattr(channel, "set_status"):
            try:
                await channel.set_status(status_text if status_text else None)
                logger.info(f"Updated voice channel status to: '{status_text}'")
                return
            except discord.Forbidden:
                logger.warning("Bot is missing 'Set Voice Channel Status' permission in this voice channel.")
            except Exception as e:
                logger.debug(f"channel.set_status fallback needed: {e}")

        # Method 2: Direct Discord REST API (PUT /channels/{channel_id}/voice-status)
        try:
            route = discord.http.Route('PUT', '/channels/{channel_id}/voice-status', channel_id=channel.id)
            await self.bot.http.request(route, json={"status": status_text})
            logger.info(f"Updated voice channel status (REST API) to: '{status_text}'")
        except discord.Forbidden:
            logger.warning("Bot is missing 'Set Voice Channel Status' permission in this voice channel.")
        except Exception as e:
            logger.warning(f"Could not update voice channel status: {e}")

    async def stop_and_disconnect(self):
        self.clear_queue()
        self.current = None
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        asyncio.create_task(self._update_voice_channel_status(None))
        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
            await self.voice_client.disconnect(force=True)
            self.voice_client = None

    async def _create_audio_source(self, song: Song) -> discord.AudioSource:
        target_url = song.webpage_url or song.url
        loop = asyncio.get_running_loop()
        raw_source = await loop.run_in_executor(None, PipedAudioSource.create, target_url)
        return discord.PCMVolumeTransformer(raw_source, volume=self.volume)

    async def play_next(self):
        async with self._lock:
            if not self.voice_client or not self.voice_client.is_connected():
                return

            if self.loop_current and self.current:
                next_song = self.current
            elif self.queue:
                next_song = self.queue.pop(0)
            else:
                self.current = None
                self._start_idle_timer()
                asyncio.create_task(self._update_voice_channel_status(None))
                if self.widget_message:
                    try:
                        embed = discord.Embed(
                            title="Queue Finished",
                            description="Playback completed. Use `/play` to add tracks.",
                            color=0x4b5563
                        )
                        await self.widget_message.edit(embed=embed, view=None)
                    except Exception:
                        pass
                return

            self._cancel_idle_timer()
            self.current = next_song

            try:
                # If song has no direct_audio_url or headers, resolve it
                if not next_song.direct_audio_url:
                    query_target = next_song.search_query if next_song.search_query else (next_song.webpage_url or next_song.title)
                    if next_song.source_type == "spotify" or next_song.search_query:
                        results = await search_youtube(query_target, limit=1)
                        if not results:
                            raise ValueError(f"Could not find audio for: {next_song.title}")
                        info = await get_video_info(results[0]["url"])
                    else:
                        info = await get_video_info(next_song.webpage_url)
                    
                    next_song.direct_audio_url = info.get("direct_audio_url")
                    next_song.http_headers = info.get("http_headers")
                    if not next_song.thumbnail and info.get("thumbnail"):
                        next_song.thumbnail = info.get("thumbnail")

                source = await self._create_audio_source(next_song)

                def _after_play(error):
                    if error:
                        logger.error(f"Playback error in guild {self.guild.id}: {error}")
                    fut = asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        logger.error(f"Error triggering next track: {e}")

                self.voice_client.play(source, after=_after_play)

                # Update Voice Channel Status with song title & artist
                status_text = f"🎵 {next_song.title}" if next_song.title else "🎵 Playing Music"
                asyncio.create_task(self._update_voice_channel_status(status_text))

                # Post a fresh card in chat so the new track is immediately visible
                if self.text_channel:
                    await self.send_fresh_card()

            except Exception as e:
                logger.error(f"Failed to play song '{next_song.title}': {e}")
                if self.text_channel:
                    await self.text_channel.send(f"Error playing **{next_song.title}**: `{str(e)}`")
                await self.play_next()

    async def send_fresh_card(self):
        """
        Sends a fresh new control card in chat upon song start or skip.
        """
        if not self.text_channel or not self.current:
            return

        from bot.bot import MusicControlWidgetView
        embed = self.create_card_embed()
        view = MusicControlWidgetView(self)

        try:
            self.widget_message = await self.text_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send card: {e}")

    async def update_existing_card(self):
        """
        Updates the existing card in-place for fast button interactions.
        """
        if not self.widget_message or not self.current:
            return await self.send_fresh_card()

        from bot.bot import MusicControlWidgetView
        embed = self.create_card_embed()
        view = MusicControlWidgetView(self)

        try:
            await self.widget_message.edit(embed=embed, view=view)
        except Exception:
            await self.send_fresh_card()

    def _start_idle_timer(self, timeout: int = 120):
        self._cancel_idle_timer()
        
        async def _idle_disconnect():
            await asyncio.sleep(timeout)
            if not self.is_playing and len(self.queue) == 0:
                if self.text_channel:
                    await self.text_channel.send("Disconnected from voice channel due to inactivity.")
                await self.stop_and_disconnect()

        self._idle_task = asyncio.create_task(_idle_disconnect())

    def _cancel_idle_timer(self):
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None

    def start_empty_lobby_timer(self, timeout: int = 120):
        """
        Starts a 2-minute timer when all human users have left the voice channel.
        """
        self.cancel_empty_lobby_timer()
        
        async def _empty_disconnect():
            await asyncio.sleep(timeout)
            if self.voice_client and self.voice_client.channel:
                humans = [m for m in self.voice_client.channel.members if not m.bot]
                if len(humans) == 0:
                    if self.text_channel:
                        await self.text_channel.send("👋 Left voice channel because the lobby was empty for 2 minutes.")
                    await self.stop_and_disconnect()

        self._empty_lobby_task = asyncio.create_task(_empty_disconnect())
        logger.info(f"Started 2-minute empty lobby timer for guild {self.guild.id}")

    def cancel_empty_lobby_timer(self):
        """
        Cancels the empty lobby timer when a human user rejoins.
        """
        if hasattr(self, "_empty_lobby_task") and self._empty_lobby_task and not self._empty_lobby_task.done():
            self._empty_lobby_task.cancel()
            self._empty_lobby_task = None
            logger.info(f"Cancelled empty lobby timer for guild {self.guild.id} (user joined)")

    def create_card_embed(self) -> discord.Embed:
        if not self.current:
            return discord.Embed(title="Music Player", description="No track currently playing.", color=0x4b5563)

        # Status
        if self.is_paused:
            status_text = "PAUSED"
            color = 0xf59e0b
        elif self.loop_current:
            status_text = "LOOPING"
            color = 0x10b981
        else:
            status_text = "NOW PLAYING"
            color = 0x1db954 if self.current.source_type == "spotify" else 0x5865f2

        source_label = "Spotify" if self.current.source_type == "spotify" else "YouTube"

        embed = discord.Embed(
            title=f"{status_text} • {source_label}",
            description=f"### [{self.current.title}]({self.current.webpage_url})\n**{self.current.uploader}**",
            color=color
        )

        if self.current.thumbnail:
            embed.set_thumbnail(url=self.current.thumbnail)

        vol_pct = int(self.volume * 100)
        ch_name = self.voice_client.channel.name if (self.voice_client and self.voice_client.channel) else "Voice"
        
        embed.add_field(name="Duration", value=f"`{self.current.duration_string}`", inline=True)
        embed.add_field(name="Volume", value=f"`{vol_pct}%`", inline=True)
        embed.add_field(name="Channel", value=f"`#{ch_name}`", inline=True)

        embed.add_field(name="Requested By", value=self.current.requester.mention, inline=True)
        embed.add_field(name="Loop", value=f"`{'ON' if self.loop_current else 'OFF'}`", inline=True)
        embed.add_field(name="Quality", value="`48kHz Stereo`", inline=True)
        
        if self.queue:
            next_song = self.queue[0]
            safe_next = next_song.title[:38] + ("..." if len(next_song.title) > 38 else "")
            embed.add_field(name="Up Next", value=f"[{safe_next}]({next_song.webpage_url}) (`{len(self.queue)} in queue`)", inline=False)
        else:
            embed.add_field(name="Up Next", value="*Queue is empty*", inline=False)

        embed.set_footer(text="Ad-Free Audio • Use buttons below to control")
        return embed
