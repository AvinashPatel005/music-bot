# 🎵 YouTube & Spotify Ad-Free Audio Streamer & Discord Music Bot

A high-performance backend application and **Discord Voice Music Bot** built with **FastAPI**, **discord.py**, **MongoDB**, **yt-dlp**, and **ffmpeg** to stream clean, ad-free audio from **YouTube & Spotify** into Discord voice lobbies and web clients.

---

## 🌟 Key Features

- **⏱️ 2-Minute Empty Lobby Auto-Disconnect**: Automatically detects when all human users leave the voice channel and disconnects after 2 minutes of inactivity (cancelling the timer if someone rejoins).
- **💾 Custom Saved Playlists (MongoDB)**: Users can create custom playlists, save songs/queues, view tracks, and instantly play their personal playlists with `/playlist play`.
- **🎙️ Dynamic Voice Lobby Status**: Automatically updates the Discord Voice Channel Status with the currently playing song title and clears it when music stops.
- **🟢 YouTube & Spotify Support**: Supports Spotify tracks, playlists, albums, YouTube songs, and YouTube playlists.
- **🎛️ Minimalist Control Dashboard**: Apple/Spotify-inspired card UI with interactive buttons in chat and auto-card on skip.
- **🔊 48kHz Stereo Processing**: Crystal-clear voice audio pipeline without distortion.

---

## 📁 Custom MongoDB Playlist Commands

| Slash Command | Description |
|---|---|
| `/playlist create <name>` | Create a new saved playlist in MongoDB |
| `/playlist add playlist:<name> [song:<query>]` | Add a YouTube/Spotify song or currently playing track to your saved playlist |
| `/playlist play playlist:<name>` | Instantly load and play your saved playlist in the voice channel |
| `/playlist list` | List all your saved playlists with track counts and total durations |
| `/playlist view playlist:<name>` | View all tracks inside a saved playlist |
| `/playlist remove playlist:<name> <number>` | Remove a track from your playlist by track number |
| `/playlist save_queue <name>` | Save the entire active music queue into a new playlist |
| `/playlist delete playlist:<name>` | Delete a saved playlist from MongoDB |

---

## 🤖 General Player Commands

| Command | Description |
|---|---|
| `/play <query_or_url>` | Play a YouTube or Spotify song/playlist ad-free |
| `/nowplaying` / `/widget` | Show the music control card in chat |
| `/pause` & `/resume` | Pause / resume playback |
| `/skip` | Skip track and post fresh card for next song |
| `/queue` | View current queue list and total duration |
| `/volume <10-100>` | Adjust volume level |
| `/loop` | Toggle repeat on/off |
| `/stop` | Clear queue, reset voice status, and leave voice |
| `/sync` | Instantly refresh and sync all slash commands in this server |

---

## 🚀 How to Run

```bash
source venv/bin/activate
python run_bot.py
```
