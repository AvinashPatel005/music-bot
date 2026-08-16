import os
import asyncio
import logging
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app.runner")

async def start_discord_bot():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token or token == "your_discord_bot_token_here":
        logger.warning("DISCORD_BOT_TOKEN is not configured. Discord bot will not start.")
        return

    try:
        from bot.bot import bot
        logger.info("Starting Discord bot in background...")
        await bot.start(token)
    except Exception as e:
        logger.error(f"Discord bot encountered an error: {e}")

async def start_web_server():
    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config("main:app", host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info(f"Starting FastAPI Web Server on port {port}...")
    await server.serve()

async def main():
    # Run both the Discord bot and the FastAPI web server concurrently
    await asyncio.gather(
        start_web_server(),
        start_discord_bot(),
        return_exceptions=True
    )

if __name__ == "__main__":
    asyncio.run(main())
