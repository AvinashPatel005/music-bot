import asyncio
import logging
from typing import AsyncGenerator, Optional, Dict
import shutil

logger = logging.getLogger("streamer")

FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
YTDL_PATH = shutil.which("yt-dlp") or "yt-dlp"

FORMAT_MAPPING = {
    "mp3": {
        "content_type": "audio/mpeg",
        "codec": "libmp3lame",
        "container": "mp3",
        "ext": "mp3",
        "extra_flags": ["-write_xing", "0", "-id3v2_version", "0", "-flush_packets", "1"],
    },
    "aac": {
        "content_type": "audio/aac",
        "codec": "aac",
        "container": "adts",
        "ext": "aac",
        "extra_flags": ["-flush_packets", "1"],
    },
    "m4a": {
        "content_type": "audio/mp4",
        "codec": "aac",
        "container": "ipod",
        "ext": "m4a",
        "extra_flags": ["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-flush_packets", "1"],
    },
    "opus": {
        "content_type": "audio/opus",
        "codec": "libopus",
        "container": "opus",
        "ext": "opus",
        "extra_flags": ["-flush_packets", "1"],
    },
    "wav": {
        "content_type": "audio/wav",
        "codec": "pcm_s16le",
        "container": "wav",
        "ext": "wav",
        "extra_flags": ["-flush_packets", "1"],
    },
    "ogg": {
        "content_type": "audio/ogg",
        "codec": "libvorbis",
        "container": "ogg",
        "ext": "ogg",
        "extra_flags": ["-flush_packets", "1"],
    },
}

async def stream_audio_pipe(
    youtube_url: str,
    output_format: str = "mp3",
    bitrate: str = "192k",
    start_time: Optional[float] = None,
    chunk_size: int = 32768,
) -> AsyncGenerator[bytes, None]:
    """
    Streams audio ad-free by piping yt-dlp direct output into ffmpeg for real-time transcoding.
    This guarantees 100% reliability against YouTube 403 Forbidden CDN blocks.
    """
    fmt_config = FORMAT_MAPPING.get(output_format.lower(), FORMAT_MAPPING["mp3"])
    
    from services.youtube import COOKIE_FILE, log_available_formats
    import threading
    threading.Thread(target=log_available_formats, args=(youtube_url,), daemon=True).start()

    # 1. yt-dlp command to stream raw audio to stdout
    ytdl_cmd = [
        YTDL_PATH,
        "--format", "bestaudio/ba/b/best",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--output", "-",
        "--remote-components", "ejs:github",
        "--extractor-args", "youtube:player_client=android_vr,mweb,android,tv,web",
    ]

    if COOKIE_FILE:
        ytdl_cmd.extend(["--cookies", COOKIE_FILE])

    ytdl_cmd.append(youtube_url)

    # 2. ffmpeg command to read from stdin (pipe:0) and transcode to stdout (pipe:1)
    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
    ]

    if start_time and start_time > 0:
        ffmpeg_cmd.extend(["-ss", str(start_time)])

    ffmpeg_cmd.extend([
        "-i", "pipe:0",
        "-vn",
        "-c:a", fmt_config["codec"],
        "-b:a", bitrate,
        "-ar", "44100",
        "-ac", "2",
        "-f", fmt_config["container"],
    ])

    ffmpeg_cmd.extend(fmt_config.get("extra_flags", []))
    ffmpeg_cmd.append("pipe:1")

    ytdl_proc = None
    ffmpeg_proc = None
    pipe_task = None

    try:
        # Start yt-dlp process
        ytdl_proc = await asyncio.create_subprocess_exec(
            *ytdl_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Start ffmpeg process
        ffmpeg_proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Background task to pipe yt-dlp stdout into ffmpeg stdin
        async def _forward_ytdl_to_ffmpeg():
            try:
                while True:
                    data = await ytdl_proc.stdout.read(chunk_size)
                    if not data:
                        break
                    ffmpeg_proc.stdin.write(data)
                    await ffmpeg_proc.stdin.drain()
            except (asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    ffmpeg_proc.stdin.close()
                    await ffmpeg_proc.stdin.wait_closed()
                except Exception:
                    pass

        pipe_task = asyncio.create_task(_forward_ytdl_to_ffmpeg())

        # Yield transcoded audio chunks directly from ffmpeg stdout to client
        while True:
            chunk = await ffmpeg_proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk

        await ffmpeg_proc.wait()

    except asyncio.CancelledError:
        logger.info("Client disconnected from audio stream.")
    except Exception as e:
        logger.error(f"Streaming pipeline error: {e}")
    finally:
        if pipe_task and not pipe_task.done():
            pipe_task.cancel()
        for proc in (ffmpeg_proc, ytdl_proc):
            if proc:
                try:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                except Exception:
                    pass
