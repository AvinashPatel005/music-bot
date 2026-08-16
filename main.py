from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from routers.audio import router as audio_router

app = FastAPI(
    title="YouTube Ad-Free Audio Streaming API",
    description="High-performance backend API to stream, search, and download clean ad-free audio from YouTube URLs on the fly.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for cross-origin web/mobile player clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include audio endpoints
app.include_router(audio_router)

# Mount static folder for built-in web player test UI
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
async def index():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "YouTube Audio Streaming API is running. Visit /docs for API documentation."}

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "service": "yt-audio-streamer"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
