import urllib.parse
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from services.youtube import get_video_info, search_youtube, clean_youtube_url
from services.streamer import stream_audio_pipe, FORMAT_MAPPING

router = APIRouter(prefix="/api", tags=["Audio Streaming & Info"])

@router.get("/info", summary="Get YouTube Video & Audio Metadata")
async def get_info(url: str = Query(..., description="YouTube video URL or Video ID")):
    """
    Extracts metadata, thumbnails, channel info, and available audio formats for a given YouTube URL.
    """
    try:
        info = await get_video_info(url)
        return {"success": True, "data": info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch video info: {str(e)}")

@router.get("/search", summary="Search YouTube for Tracks/Videos")
async def search(
    q: str = Query(..., description="Search query string"),
    limit: int = Query(10, ge=1, le=50, description="Max number of search results to return")
):
    """
    Searches YouTube and returns list of videos with URLs ready for streaming.
    """
    try:
        results = await search_youtube(q, limit=limit)
        return {"success": True, "count": len(results), "data": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Search failed: {str(e)}")

@router.get("/formats", summary="Get Supported Output Audio Formats")
async def get_formats():
    """
    Returns list of supported audio streaming and transcode formats.
    """
    return {
        "success": True,
        "formats": list(FORMAT_MAPPING.keys()),
        "details": FORMAT_MAPPING
    }

@router.get("/stream", summary="Stream Ad-Free Audio from YouTube URL")
async def stream_audio(
    url: str = Query(..., description="YouTube video URL or Video ID"),
    format: str = Query("mp3", description="Audio format: mp3, aac, opus, m4a, ogg, wav"),
    bitrate: str = Query("192k", description="Audio bitrate: 96k, 128k, 192k, 256k, 320k"),
    start: float = Query(0.0, ge=0.0, description="Start time offset in seconds for seeking"),
):
    """
    Streams clean, ad-free audio extracted from YouTube on the fly.
    Transcodes to selected format (default: mp3 @ 192kbps) in real-time.
    """
    try:
        clean_url = clean_youtube_url(url)
        fmt_key = format.lower()
        if fmt_key not in FORMAT_MAPPING:
            fmt_key = "mp3"
            
        fmt_config = FORMAT_MAPPING[fmt_key]
        
        generator = stream_audio_pipe(
            youtube_url=clean_url,
            output_format=fmt_key,
            bitrate=bitrate,
            start_time=start
        )

        headers = {
            "Accept-Ranges": "none",
            "Content-Type": fmt_config["content_type"],
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
        }

        return StreamingResponse(
            generator,
            media_type=fmt_config["content_type"],
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio streaming error: {str(e)}")

@router.get("/download", summary="Download Ad-Free Audio Track")
async def download_audio(
    url: str = Query(..., description="YouTube video URL or Video ID"),
    format: str = Query("mp3", description="Output format: mp3, m4a, opus, wav"),
    bitrate: str = Query("320k", description="Audio bitrate (default 320k for high quality download)"),
):
    """
    Downloads clean ad-free audio file with proper filename attachment header.
    """
    try:
        clean_url = clean_youtube_url(url)
        info = await get_video_info(clean_url)
        
        fmt_key = format.lower()
        if fmt_key not in FORMAT_MAPPING:
            fmt_key = "mp3"
            
        fmt_config = FORMAT_MAPPING[fmt_key]
        title = info.get("title", "audio_track")
        safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip() or "track"
        filename = f"{safe_filename}.{fmt_config['ext']}"
        encoded_filename = urllib.parse.quote(filename)

        generator = stream_audio_pipe(
            youtube_url=clean_url,
            output_format=fmt_key,
            bitrate=bitrate,
        )

        headers = {
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}",
            "Content-Type": fmt_config["content_type"],
            "Access-Control-Allow-Origin": "*",
        }

        return StreamingResponse(
            generator,
            media_type=fmt_config["content_type"],
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")
