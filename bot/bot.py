import os
import asyncio
import logging
from typing import Dict, Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("music_bot")

import discord
from discord import app_commands
from discord.ext import commands
from bot.music_player import GuildMusicPlayer, Song
from services.youtube import is_playlist
from services.spotify import is_spotify_url, parse_spotify_url
import services.playlist_manager as pl_mgr

class MusicControlWidgetView(discord.ui.View):
    """
    Sleek, minimalist control dashboard for Discord chat.
    """
    def __init__(self, player: GuildMusicPlayer):
        super().__init__(timeout=None)
        self.player = player
        self._update_button_states()

    def _update_button_states(self):
        if self.player.is_paused:
            self.pause_resume_btn.label = "Resume"
            self.pause_resume_btn.style = discord.ButtonStyle.success
        else:
            self.pause_resume_btn.label = "Pause"
            self.pause_resume_btn.style = discord.ButtonStyle.primary

        if self.player.loop_current:
            self.loop_btn.label = "Loop: ON"
            self.loop_btn.style = discord.ButtonStyle.success
        else:
            self.loop_btn.label = "Loop: OFF"
            self.loop_btn.style = discord.ButtonStyle.secondary

    # --- ROW 1 CONTROLS ---
    @discord.ui.button(label="Pause", style=discord.ButtonStyle.primary, row=0, custom_id="widget_pause_resume")
    async def pause_resume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.player.voice_client or not self.player.current:
            return await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
        
        if self.player.is_paused:
            self.player.resume()
        elif self.player.is_playing:
            self.player.pause()

        self._update_button_states()
        await interaction.response.edit_message(
            embed=self.player.create_card_embed(),
            view=self
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=0, custom_id="widget_skip")
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.player.is_playing and not self.player.is_paused:
            return await interaction.response.send_message("Nothing to skip.", ephemeral=True)
        
        skipped_title = self.player.current.title if self.player.current else "track"
        self.player.skip()
        await interaction.response.send_message(f"Skipped **{skipped_title}**", ephemeral=True)

    @discord.ui.button(label="Loop: OFF", style=discord.ButtonStyle.secondary, row=0, custom_id="widget_loop")
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.loop_current = not self.player.loop_current
        self._update_button_states()
        await interaction.response.edit_message(
            embed=self.player.create_card_embed(),
            view=self
        )

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=0, custom_id="widget_stop")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.player.stop_and_disconnect()
        embed = discord.Embed(
            title="Player Stopped",
            description="Playback stopped and disconnected from voice channel.",
            color=0xef4444
        )
        await interaction.response.edit_message(embed=embed, view=None)

    # --- ROW 2 CONTROLS ---
    @discord.ui.button(label="Vol -10%", style=discord.ButtonStyle.secondary, row=1, custom_id="widget_vol_down")
    async def vol_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_vol = self.player.set_volume(-0.1)
        await interaction.response.edit_message(
            embed=self.player.create_card_embed(),
            view=self
        )

    @discord.ui.button(label="Vol +10%", style=discord.ButtonStyle.secondary, row=1, custom_id="widget_vol_up")
    async def vol_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_vol = self.player.set_volume(0.1)
        await interaction.response.edit_message(
            embed=self.player.create_card_embed(),
            view=self
        )

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, row=1, custom_id="widget_queue")
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.player.queue and not self.player.current:
            return await interaction.response.send_message("Queue is empty.", ephemeral=True)
        
        embed = discord.Embed(title="Current Music Queue", color=0x5865f2)
        if self.player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**[{self.player.current.title}]({self.player.current.webpage_url})** (`{self.player.current.duration_string}`)",
                inline=False
            )
        
        if self.player.queue:
            q_lines = []
            for idx, song in enumerate(self.player.queue[:10], start=1):
                safe_title = song.title[:45] + ("..." if len(song.title) > 45 else "")
                q_lines.append(f"`{idx}.` **[{safe_title}]({song.webpage_url})** (`{song.duration_string}`) - {song.requester.mention}")
            
            q_text = "\n".join(q_lines)
            if len(self.player.queue) > 10:
                q_text += f"\n*...and {len(self.player.queue) - 10} more songs*"
            
            if len(q_text) > 1000:
                q_text = q_text[:990] + "\n*...*"
            embed.add_field(name="Up Next", value=q_text, inline=False)
            
            total_seconds = sum(s.duration for s in self.player.queue)
            mins, secs = divmod(total_seconds, 60)
            hours, mins = divmod(mins, 60)
            dur_str = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"
            embed.set_footer(text=f"Total: {len(self.player.queue)} songs | Duration: {dur_str}")
        else:
            embed.set_footer(text="Queue is empty. Use /play to add songs.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1, custom_id="widget_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._update_button_states()
        await interaction.response.edit_message(
            embed=self.player.create_card_embed(),
            view=self
        )


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.players: Dict[int, GuildMusicPlayer] = {}

    def get_player(self, guild: discord.Guild) -> GuildMusicPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildMusicPlayer(self, guild)
        return self.players[guild.id]

    async def setup_hook(self):
        logger.info("Initializing MongoDB connection...")
        await pl_mgr.init_db()
        logger.info("MongoDB initialized.")

    async def on_ready(self):
        logger.info(f"Bot logged in as: {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="YouTube & Spotify | /play"
            )
        )
        # Background sync to avoid blocking startup on Discord 429 rate limits
        asyncio.create_task(self._sync_commands_safely())

    async def _sync_commands_safely(self):
        try:
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.info(f"Synced slash commands to guild: {guild.name}")
                except Exception as e:
                    logger.debug(f"Guild sync notice ({guild.name}): {e}")
            await self.tree.sync()
            logger.info("Global slash command tree synced.")
        except Exception as e:
            logger.warning(f"Command sync notice: {e}")

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """
        Monitors voice channel membership to auto-disconnect when the lobby is empty for 2 minutes.
        """
        # If the event was triggered by another bot, ignore
        if member.bot and member.id != self.user.id:
            return

        guild = member.guild
        player = self.players.get(guild.id)
        if not player or not player.voice_client or not player.voice_client.channel:
            return

        channel = player.voice_client.channel
        human_members = [m for m in channel.members if not m.bot]

        if len(human_members) == 0:
            player.start_empty_lobby_timer(timeout=120)
        else:
            player.cancel_empty_lobby_timer()

bot = MusicBot()

@bot.tree.command(name="sync", description="Instantly refresh and sync all bot slash commands in this server")
async def sync_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(f"Instantly synced **{len(synced)} slash commands** to this server! All commands are ready.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Sync error: {str(e)}", ephemeral=True)

async def _send_player_card(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    player.text_channel = interaction.channel

    if not player.current:
        return await interaction.response.send_message(
            "Nothing is currently playing. Use `/play <song>` to start playing.",
            ephemeral=True
        )

    embed = player.create_card_embed()
    view = MusicControlWidgetView(player)
    await interaction.response.send_message(embed=embed, view=view)
    player.widget_message = await interaction.original_response()


@bot.tree.command(name="play", description="Play a YouTube/Spotify song, album, or playlist in voice channel")
@app_commands.describe(query="YouTube or Spotify link, song name, or search query")
async def play_cmd(interaction: discord.Interaction, query: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message(
            "You must be connected to a voice channel to use `/play`.",
            ephemeral=True
        )

    query = query.strip().strip("<>")
    if not interaction.response.is_done():
        await interaction.response.defer()
    player = bot.get_player(interaction.guild)
    player.text_channel = interaction.channel

    # 1. Connect to voice channel
    try:
        await player.connect(interaction.user.voice.channel)
    except Exception as e:
        return await interaction.followup.send(f"Failed to connect to voice channel: `{str(e)}`")

    # 2. Check for Spotify URL
    if is_spotify_url(query):
        parsed = parse_spotify_url(query)
        if parsed:
            item_type, item_id = parsed
            try:
                if item_type in ("playlist", "album"):
                    sp_data, songs = await Song.from_spotify_collection(item_type, item_id, requester=interaction.user, limit=100)
                    if not songs:
                        return await interaction.followup.send("No valid tracks found in this Spotify collection.")

                    was_idle = (not player.is_playing and not player.is_paused)
                    if was_idle:
                        player.queue.append(songs[0])
                        if len(songs) > 1:
                            player.add_multiple_to_queue(songs[1:])
                        await player.play_next()
                    else:
                        player.add_multiple_to_queue(songs)

                    embed = discord.Embed(
                        title=f"Loaded Spotify {item_type.capitalize()}",
                        description=f"**[{sp_data['title']}]({sp_data['url']})**",
                        color=0x1db954
                    )
                    if sp_data.get("thumbnail"):
                        embed.set_thumbnail(url=sp_data["thumbnail"])
                    embed.add_field(name="Tracks Added", value=f"`{len(songs)} songs`", inline=True)
                    embed.add_field(name="By", value=f"`{sp_data['author']}`", inline=True)
                    embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
                    embed.set_footer(text="Spotify Ad-Free Audio")
                    
                    await interaction.followup.send(embed=embed)
                    return

                elif item_type == "track":
                    song = await Song.from_spotify(item_id, requester=interaction.user)
                    if not player.is_playing and not player.is_paused:
                        player.queue.append(song)
                        await player.play_next()
                        embed = player.create_card_embed()
                        view = MusicControlWidgetView(player)
                        msg = await interaction.followup.send(embed=embed, view=view)
                        player.widget_message = msg
                    else:
                        pos = player.add_to_queue(song)
                        embed = discord.Embed(
                            title="Added Spotify Track to Queue",
                            description=f"**[{song.title}]({song.webpage_url})**\nby **{song.uploader}**",
                            color=0x1db954
                        )
                        if song.thumbnail:
                            embed.set_thumbnail(url=song.thumbnail)
                        embed.add_field(name="Position in Queue", value=f"`#{pos}`", inline=True)
                        embed.add_field(name="Duration", value=f"`{song.duration_string}`", inline=True)
                        embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
                        embed.set_footer(text="Spotify Ad-Free Audio")
                        await interaction.followup.send(embed=embed)

                        if player.widget_message:
                            try:
                                await player.widget_message.edit(
                                    embed=player.create_card_embed(),
                                    view=MusicControlWidgetView(player)
                                )
                            except Exception:
                                pass
                    return

            except Exception as e:
                return await interaction.followup.send(f"Error loading Spotify track: `{str(e)}`")

    # 3. Check for YouTube Playlist
    if is_playlist(query):
        try:
            pl_info, songs = await Song.from_playlist(query, requester=interaction.user, limit=100)
            if not songs:
                return await interaction.followup.send("No valid songs found in this playlist.")

            was_idle = (not player.is_playing and not player.is_paused)
            if was_idle:
                player.queue.append(songs[0])
                if len(songs) > 1:
                    player.add_multiple_to_queue(songs[1:])
                await player.play_next()
            else:
                player.add_multiple_to_queue(songs)

            embed = discord.Embed(
                title="Loaded YouTube Playlist",
                description=f"**[{pl_info['title']}]({pl_info['webpage_url']})**",
                color=0x5865f2
            )
            embed.add_field(name="Tracks Added", value=f"`{len(songs)} songs`", inline=True)
            embed.add_field(name="Playlist By", value=f"`{pl_info['uploader']}`", inline=True)
            embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
            embed.set_footer(text="YouTube Playlist")
            
            await interaction.followup.send(embed=embed)
            return

        except Exception as e:
            return await interaction.followup.send(f"Could not load playlist: `{str(e)}`")

    # 4. Standard YouTube Search / Video URL
    try:
        song = await Song.from_query(query, requester=interaction.user)
    except Exception as e:
        return await interaction.followup.send(f"Could not find track: `{str(e)}`")

    if not player.is_playing and not player.is_paused:
        player.queue.append(song)
        await player.play_next()
        
        embed = player.create_card_embed()
        view = MusicControlWidgetView(player)
        msg = await interaction.followup.send(embed=embed, view=view)
        player.widget_message = msg
    else:
        pos = player.add_to_queue(song)
        embed = discord.Embed(
            title="Added to Queue",
            description=f"**[{song.title}]({song.webpage_url})**",
            color=0x5865f2
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        embed.add_field(name="Position", value=f"`#{pos}`", inline=True)
        embed.add_field(name="Duration", value=f"`{song.duration_string}`", inline=True)
        embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
        await interaction.followup.send(embed=embed)
        
        if player.widget_message:
            try:
                await player.widget_message.edit(
                    embed=player.create_card_embed(),
                    view=MusicControlWidgetView(player)
                )
            except Exception:
                pass


@bot.tree.command(name="widget", description="Show the music control dashboard card in the chat")
async def widget_cmd(interaction: discord.Interaction):
    await _send_player_card(interaction)


@bot.tree.command(name="nowplaying", description="Show details and controls for the current track")
async def np_cmd(interaction: discord.Interaction):
    await _send_player_card(interaction)


@bot.tree.command(name="pause", description="Pause the currently playing track")
async def pause_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    if player.pause():
        await interaction.response.send_message("Audio paused.")
        await player.update_existing_card()
    else:
        await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)


@bot.tree.command(name="resume", description="Resume playback if paused")
async def resume_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    if player.resume():
        await interaction.response.send_message("Playback resumed.")
        await player.update_existing_card()
    else:
        await interaction.response.send_message("Audio is not paused.", ephemeral=True)


@bot.tree.command(name="skip", description="Skip to the next song in the queue")
async def skip_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    if not player.is_playing and not player.is_paused:
        return await interaction.response.send_message("Nothing is currently playing to skip.", ephemeral=True)

    current_title = player.current.title if player.current else "track"
    player.skip()
    await interaction.response.send_message(f"Skipped **{current_title}**.")


@bot.tree.command(name="queue", description="Show the current music queue")
async def queue_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    if not player.current and not player.queue:
        return await interaction.response.send_message("The queue is currently empty.", ephemeral=True)

    embed = discord.Embed(title="Current Music Queue", color=0x5865f2)
    if player.current:
        embed.add_field(
            name="Now Playing",
            value=f"**[{player.current.title}]({player.current.webpage_url})** (`{player.current.duration_string}`)",
            inline=False
        )

    if player.queue:
        q_lines = []
        for idx, s in enumerate(player.queue[:10], start=1):
            safe_title = s.title[:45] + ("..." if len(s.title) > 45 else "")
            q_lines.append(f"`{idx}.` **[{safe_title}]({s.webpage_url})** (`{s.duration_string}`) - {s.requester.mention}")
        
        if len(player.queue) > 10:
            q_lines.append(f"\n*...and {len(player.queue) - 10} more songs*")
        
        q_text = "\n".join(q_lines)
        if len(q_text) > 1000:
            q_text = q_text[:990] + "\n*...*"
            
        embed.add_field(name="Up Next", value=q_text, inline=False)
        total_seconds = sum(s.duration for s in player.queue)
        mins, secs = divmod(total_seconds, 60)
        hours, mins = divmod(mins, 60)
        duration_str = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"
        embed.set_footer(text=f"Total: {len(player.queue)} songs | Duration: {duration_str}")
    else:
        embed.set_footer(text="Queue is empty. Use /play to add more tracks.")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="volume", description="Adjust player volume (10% - 100%)")
@app_commands.describe(percent="Volume percentage (10 to 100)")
async def volume_cmd(interaction: discord.Interaction, percent: int):
    player = bot.get_player(interaction.guild)
    percent = max(10, min(100, percent))
    player.volume = percent / 100.0
    if player.voice_client and player.voice_client.source and isinstance(player.voice_client.source, discord.PCMVolumeTransformer):
        player.voice_client.source.volume = player.volume
    await interaction.response.send_message(f"Volume set to **{percent}%**.")
    await player.update_existing_card()


@bot.tree.command(name="stop", description="Stop music, clear the queue, and leave the voice channel")
async def stop_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    await player.stop_and_disconnect()
    await interaction.response.send_message("Music stopped and disconnected from voice channel.")


@bot.tree.command(name="loop", description="Toggle looping for the current song")
async def loop_cmd(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    player.loop_current = not player.loop_current
    status = "Enabled" if player.loop_current else "Disabled"
    await interaction.response.send_message(f"Looping for current track is now **{status}**.")
    await player.update_existing_card()


# =====================================================================
# 📁 MONGODB CUSTOM PLAYLIST COMMAND GROUP
# =====================================================================

playlist_group = app_commands.Group(name="playlist", description="Create, save, view, and play custom MongoDB playlists")

async def _playlist_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        playlists = await pl_mgr.list_user_playlists(interaction.user.id)
        return [
            app_commands.Choice(name=f"{pl['name']} ({len(pl.get('songs', []))} songs)", value=pl['name'])
            for pl in playlists if current.lower() in pl['name'].lower()
        ][:25]
    except Exception:
        return []


@playlist_group.command(name="create", description="Create a new saved playlist in MongoDB")
@app_commands.describe(name="Unique name for your playlist")
async def pl_create_cmd(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    try:
        doc = await pl_mgr.create_playlist(
            user_id=interaction.user.id,
            user_name=str(interaction.user),
            name=name
        )
        embed = discord.Embed(
            title="Playlist Created",
            description=f"Created playlist **{doc['name']}** in MongoDB.\nAdd songs with `/playlist add playlist:{doc['name']} song:<name_or_url>`",
            color=0x10b981
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@playlist_group.command(name="add", description="Add a song or current track to a saved playlist")
@app_commands.describe(
    playlist="Name of your saved playlist",
    song="YouTube/Spotify URL or song name (leave blank to add currently playing song)"
)
@app_commands.autocomplete(playlist=_playlist_autocomplete)
async def pl_add_cmd(interaction: discord.Interaction, playlist: str, song: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    player = bot.get_player(interaction.guild)

    # 1. Resolve song to add
    song_to_add: Optional[Song] = None
    if song:
        query = song.strip().strip("<>")
        try:
            if is_spotify_url(query):
                parsed = parse_spotify_url(query)
                if parsed and parsed[0] == "track":
                    song_to_add = await Song.from_spotify(parsed[1], requester=interaction.user)
                elif parsed and parsed[0] in ("playlist", "album"):
                    # Add all songs from collection
                    _, songs = await Song.from_spotify_collection(parsed[0], parsed[1], requester=interaction.user)
                    songs_dict = [s.to_dict() for s in songs]
                    await pl_mgr.add_multiple_songs_to_playlist(interaction.user.id, playlist, songs_dict)
                    return await interaction.followup.send(f"Added **{len(songs)} tracks** from Spotify to playlist **{playlist}**.", ephemeral=True)
            elif is_playlist(query):
                _, songs = await Song.from_playlist(query, requester=interaction.user)
                songs_dict = [s.to_dict() for s in songs]
                await pl_mgr.add_multiple_songs_to_playlist(interaction.user.id, playlist, songs_dict)
                return await interaction.followup.send(f"Added **{len(songs)} tracks** from YouTube Playlist to playlist **{playlist}**.", ephemeral=True)
            
            if not song_to_add:
                song_to_add = await Song.from_query(query, requester=interaction.user)
        except Exception as e:
            return await interaction.followup.send(f"Failed to find song '{query}': `{str(e)}`", ephemeral=True)
    else:
        if player.current:
            song_to_add = player.current
        else:
            return await interaction.followup.send(
                f"Nothing is currently playing. To add a song to **{playlist}**, please provide the song name: `/playlist add playlist:{playlist} song:Song Name Here`",
                ephemeral=True
            )

    # 2. Add song to MongoDB
    try:
        updated_pl = await pl_mgr.add_song_to_playlist(interaction.user.id, playlist, song_to_add.to_dict())
        embed = discord.Embed(
            title="Song Added to Playlist",
            description=f"Added **[{song_to_add.title}]({song_to_add.webpage_url})** to **{updated_pl['name']}** (`{len(updated_pl['songs'])} total songs`).",
            color=0x10b981
        )
        if song_to_add.thumbnail:
            embed.set_thumbnail(url=song_to_add.thumbnail)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@playlist_group.command(name="play", description="Instantly load and play your saved MongoDB playlist")
@app_commands.describe(playlist="Playlist name to play")
@app_commands.autocomplete(playlist=_playlist_autocomplete)
async def pl_play_cmd(interaction: discord.Interaction, playlist: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message(
            "You must be connected to a voice channel to use `/playlist play`.",
            ephemeral=True
        )

    await interaction.response.defer()
    player = bot.get_player(interaction.guild)
    player.text_channel = interaction.channel

    # 1. Connect
    try:
        await player.connect(interaction.user.voice.channel)
    except Exception as e:
        return await interaction.followup.send(f"Failed to connect to voice channel: `{str(e)}`")

    # 2. Fetch from MongoDB
    try:
        pl_data = await pl_mgr.get_playlist(interaction.user.id, playlist)
        if not pl_data:
            return await interaction.followup.send(f"Playlist **{playlist}** not found in MongoDB. Use `/playlist list` to see your playlists.")

        raw_songs = pl_data.get("songs", [])
        if not raw_songs:
            return await interaction.followup.send(f"Playlist **{pl_data['name']}** is empty. Add songs with `/playlist add playlist:{playlist} song:<song>`.")

        songs = [Song.from_dict(s, requester=interaction.user) for s in raw_songs]
        was_idle = (not player.is_playing and not player.is_paused)

        if was_idle:
            player.queue.append(songs[0])
            if len(songs) > 1:
                player.add_multiple_to_queue(songs[1:])
            await player.play_next()
        else:
            player.add_multiple_to_queue(songs)

        embed = discord.Embed(
            title="Playing Saved Playlist",
            description=f"Loaded **{pl_data['name']}** from MongoDB with **{len(songs)} tracks**.",
            color=0x5865f2
        )
        embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
        embed.set_footer(text="MongoDB Custom Playlist")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")


@playlist_group.command(name="list", description="List all your saved playlists in MongoDB")
async def pl_list_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        playlists = await pl_mgr.list_user_playlists(interaction.user.id)
        if not playlists:
            return await interaction.followup.send("You have no saved playlists in MongoDB. Create one with `/playlist create <name>`.", ephemeral=True)

        embed = discord.Embed(title=f"Saved Playlists for {interaction.user.display_name}", color=0x5865f2)
        for idx, pl in enumerate(playlists, start=1):
            songs = pl.get("songs", [])
            total_sec = sum(s.get("duration", 0) for s in songs)
            mins, secs = divmod(total_sec, 60)
            hours, mins = divmod(mins, 60)
            dur_str = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"

            embed.add_field(
                name=f"{idx}. {pl['name']}",
                value=f"`{len(songs)} tracks` • Total: `{dur_str}`\nPlay: `/playlist play {pl['name']}`",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error listing playlists: {str(e)}", ephemeral=True)


@playlist_group.command(name="view", description="View tracks in a saved playlist")
@app_commands.describe(playlist="Playlist name")
@app_commands.autocomplete(playlist=_playlist_autocomplete)
async def pl_view_cmd(interaction: discord.Interaction, playlist: str):
    await interaction.response.defer(ephemeral=True)
    try:
        pl_data = await pl_mgr.get_playlist(interaction.user.id, playlist)
        if not pl_data:
            return await interaction.followup.send(f"Playlist **{playlist}** not found.", ephemeral=True)

        songs = pl_data.get("songs", [])
        if not songs:
            return await interaction.followup.send(f"Playlist **{pl_data['name']}** is empty.", ephemeral=True)

        embed = discord.Embed(title=f"Playlist: {pl_data['name']}", color=0x5865f2)
        lines = []
        for idx, s in enumerate(songs[:15], start=1):
            safe_title = s.get("title", "Track")[:45]
            lines.append(f"`{idx}.` **[{safe_title}]({s.get('webpage_url', '')})** (`{s.get('duration_string', '0:00')}`) - {s.get('uploader', 'Artist')}")
        
        if len(songs) > 15:
            lines.append(f"\n*...and {len(songs) - 15} more tracks*")

        embed.description = "\n".join(lines)
        total_sec = sum(s.get("duration", 0) for s in songs)
        mins, secs = divmod(total_sec, 60)
        hours, mins = divmod(mins, 60)
        dur_str = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"
        embed.set_footer(text=f"Total: {len(songs)} tracks | Duration: {dur_str}")

        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@playlist_group.command(name="remove", description="Remove a track from your saved playlist by number")
@app_commands.describe(playlist="Playlist name", number="Track number from /playlist view")
@app_commands.autocomplete(playlist=_playlist_autocomplete)
async def pl_remove_cmd(interaction: discord.Interaction, playlist: str, number: int):
    await interaction.response.defer(ephemeral=True)
    try:
        removed = await pl_mgr.remove_song_from_playlist(interaction.user.id, playlist, number)
        await interaction.followup.send(f"Removed track `#{number}` (**{removed.get('title', 'Song')}**) from playlist **{playlist}**.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@playlist_group.command(name="save_queue", description="Save the currently active voice queue into a new playlist")
@app_commands.describe(name="Name for the new playlist")
async def pl_save_queue_cmd(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    player = bot.get_player(interaction.guild)

    songs_to_save: List[Dict[str, Any]] = []
    if player.current:
        songs_to_save.append(player.current.to_dict())
    for s in player.queue:
        songs_to_save.append(s.to_dict())

    if not songs_to_save:
        return await interaction.followup.send("There are no songs in the current queue to save.", ephemeral=True)

    try:
        doc = await pl_mgr.create_playlist(
            user_id=interaction.user.id,
            user_name=str(interaction.user),
            name=name,
            songs=songs_to_save
        )
        embed = discord.Embed(
            title="Queue Saved to Playlist",
            description=f"Saved **{len(songs_to_save)} tracks** from the current queue into **{doc['name']}** in MongoDB.\nPlay anytime with `/playlist play {doc['name']}`",
            color=0x10b981
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


@playlist_group.command(name="delete", description="Delete a saved playlist from MongoDB")
@app_commands.describe(name="Playlist name to delete")
async def pl_delete_cmd(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    try:
        success = await pl_mgr.delete_playlist(interaction.user.id, name)
        if success:
            await interaction.followup.send(f"Deleted playlist **{name}** from MongoDB.", ephemeral=True)
        else:
            await interaction.followup.send(f"Playlist **{name}** was not found.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)


# Register the playlist command group into the bot's tree
bot.tree.add_command(playlist_group)
