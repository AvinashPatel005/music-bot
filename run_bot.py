import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN or TOKEN == "your_discord_bot_token_here":
    print("=" * 60)
    print("❌ ERROR: DISCORD_BOT_TOKEN is not set!")
    print("Please set your Discord Bot Token in the .env file or environment.")
    print("Example:")
    print("  export DISCORD_BOT_TOKEN='YOUR_BOT_TOKEN_HERE'")
    print("  or edit .env file")
    print("=" * 60)
    sys.exit(1)

from bot.bot import bot

if __name__ == "__main__":
    print("Starting Discord Ad-Free Music Bot...")
    bot.run(TOKEN)
