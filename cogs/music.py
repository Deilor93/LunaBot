import asyncio
import discord
from discord import player
import yt_dlp
from discord import app_commands
from discord.ext import commands

from core.music_player import MusicPlayer, YDL_OPTIONS_SINGLE, YDL_OPTIONS_PLAYLIST




def _extract(query: str, ydl_opts: dict):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


async def search_ytdlp_async(query: str, ydl_opts: dict):
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _extract(query, ydl_opts)),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        print(f"[ERROR] yt-dlp timed out for query: {query}")
        return {}

class QueueView(discord.ui.View):
    def __init__(self, snapshot: list, current: str, per_page: int = 10):
        super().__init__(timeout=60)
        self.snapshot = snapshot
        self.current = current
        self.per_page = per_page
        self.page = 0
        self.total_pages = max(1, -(-len(snapshot) // per_page))  # ceiling division

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        page_entries = self.snapshot[start:end]

        lines = [
            f"**{start + i + 1}.** {title}"
            for i, (_, title) in enumerate(page_entries)
        ]
        description = "\n".join(lines) or "Empty"

        embed = discord.Embed(title="🎶 Music Queue", color=0x00FFCC)
        embed.add_field(name="Now Playing", value=self.current or "Nothing", inline=False)
        embed.add_field(name="Up Next", value=description, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} · {len(self.snapshot)} songs")
        return embed

    def update_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

class Music(commands.Cog):
    """All music-related slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._players: dict[str, MusicPlayer] = {}

    def get_player(self, guild_id: str) -> MusicPlayer | None:
        return self._players.get(guild_id)

    async def get_or_create_player(
        self,
        guild_id: str,
        voice_channel: discord.VoiceChannel,
        text_channel: discord.TextChannel,
        current_voice_client: discord.VoiceClient | None,
    ) -> MusicPlayer:
        if current_voice_client is None:
            vc = await voice_channel.connect()
        elif voice_channel != current_voice_client.channel:
            await current_voice_client.move_to(voice_channel)
            vc = current_voice_client
        else:
            vc = current_voice_client

        if guild_id not in self._players:
            self._players[guild_id] = MusicPlayer(vc, text_channel, guild_id)
        else:
            self._players[guild_id].voice_client = vc

        return self._players[guild_id]

    # ── /play ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Play a song or add it to queue")
    @app_commands.describe(song_query="Search query or URL")
    async def play(self, interaction: discord.Interaction, song_query: str):
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("You need to be in a voice channel.")

        guild_id = str(interaction.guild_id)
        voice_channel = interaction.user.voice.channel
        player = await self.get_or_create_player(
            guild_id,
            voice_channel,
            interaction.channel,
            interaction.guild.voice_client,
        )
        print(f"[DEBUG] Player created/fetched for guild {guild_id}")

        is_url = song_query.startswith(("http://", "https://"))
        is_playlist_url = is_url and ("PL" in song_query or "/sets/" in song_query)
        print(f"[DEBUG] is_url={is_url}, is_playlist_url={is_playlist_url}, query='{song_query}'")

        # ── Branch 1: playlist URL ─────────────────────────────────────────────
        if is_playlist_url:
            # First, get the flat list of entries quickly
            results = await search_ytdlp_async(song_query, YDL_OPTIONS_PLAYLIST)
            entries = results.get("entries") or []
            valid = [e for e in entries if e][:25]
            print(f"[DEBUG] Playlist entries found: {len(valid)}")

            if not valid:
                return await interaction.followup.send("Playlist is empty or couldn't be loaded.")

            playlist_name = results.get("title", "Playlist")
            added_count = 0

            for entry in valid:
                webpage_url = entry.get("webpage_url") or entry.get("url")
                title = entry.get("title", "Untitled")
                if webpage_url:
                    player.add_to_queue(webpage_url, title)
                    added_count += 1

            await interaction.followup.send(
                f"Added **{playlist_name}** ({added_count} songs) to queue."
            )
            if not player.is_playing and not player.is_paused:
                await player.play_next(self.bot.loop)
            return

        # ── Branch 2: single video URL or search query ─────────────────────────
        query = song_query if is_url else f"ytsearch1:{song_query}"  # ← no space after colon
        print(f"[DEBUG] Searching with query: '{query}'")

        results = await search_ytdlp_async(query, YDL_OPTIONS_SINGLE)
        print(f"[DEBUG] search returned keys: {list(results.keys()) if results else 'empty'}")

        entries = results.get("entries")
        track = next((e for e in entries if e), None) if entries else results
        print(f"[DEBUG] track title={track.get('title') if track else None}")

        if not track:
            return await interaction.followup.send("Could not find that track.")

        audio_url = track.get("url") or track.get("webpage_url")
        title = track.get("title", "Untitled")
        print(f"[DEBUG] Final title={title}, has_url={bool(audio_url)}")

        if not audio_url:
            return await interaction.followup.send("Could not extract audio URL.")

        if player.is_playing or player.is_paused:
            await interaction.followup.send(f"Added to queue: **{title}**")
        else:
            await interaction.followup.send(f"Now playing: **{title}**")

        webpage_url = track.get("webpage_url") or track.get("url")
        await player.add_and_play(webpage_url, title, self.bot.loop)

    # ── /skip ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped!")
        else:
            await interaction.response.send_message("Nothing is playing to skip.")

    # ── /pause ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("I'm not in a voice channel.")
        if not vc.is_playing():
            return await interaction.response.send_message("Nothing is playing.")
        vc.pause()
        await interaction.response.send_message("⏸️ Paused!")

    # ── /resume ────────────────────────────────────────────────────────────────

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("I'm not in a voice channel.")
        if not vc.is_paused():
            return await interaction.response.send_message("I'm not paused right now.")
        vc.resume()
        await interaction.response.send_message("▶️ Resumed!")

    # ── /stop ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            return await interaction.followup.send("I'm not in a voice channel.")

        guild_id = str(interaction.guild_id)
        player = self.get_player(guild_id)
        if player:
            self._players.pop(guild_id, None)

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await interaction.followup.send("⏹️ Stopped and disconnected!")
        await vc.disconnect()
        
    # ── /queue ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        guild_id = str(interaction.guild_id)
        player = self.get_player(guild_id)

        if not player or not player.queue:
            return await interaction.followup.send("📭 The queue is empty.")

        try:
            snapshot = list(player.queue)  # ← pass the raw snapshot of tuples
            view = QueueView(snapshot, player.current)
            view.update_buttons()
            await interaction.followup.send(embed=view.build_embed(), view=view)

        except Exception as e:
            print(f"[ERROR] queue command crashed: {e}")
            await interaction.followup.send("Something went wrong displaying the queue.")

    # --/resume_queue

    @app_commands.command(name="resume_queue", description="Resume the saved queue from the last session")
    async def resume_queue(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("You need to be in a voice channel.")

        guild_id = str(interaction.guild_id)
        print(f"[DEBUG] resume_queue: looking for guild_id={guild_id}")
        saved = MusicPlayer.load_queue_for_guild(guild_id)
        print(f"[DEBUG] resume_queue: found {len(saved)} entries")

        if not saved:
            return await interaction.followup.send("No saved queue found for this server.")

        voice_channel = interaction.user.voice.channel
        player = await self.get_or_create_player(
            guild_id,
            voice_channel,
            interaction.channel,
            interaction.guild.voice_client,
        )

        if player.queue:
            return await interaction.followup.send("There's already an active queue. Use `/stop` first.")

        for webpage_url, title in saved:
            player.queue.append((webpage_url, title))
            player.master_list.append((webpage_url, title))

        await interaction.followup.send(f"▶️ Resuming saved queue ({len(saved)} songs)!")
        await player.play_next(self.bot.loop)

async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))