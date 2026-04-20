import os
import discord
import asyncio
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import asyncio
from collections import deque


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

SONG_QUEUES = {}

CURRENT_SONG = {}

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents= intents)

GUILD_ID = 1495489083642876067
guild = discord.Object(id = GUILD_ID)

@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} commands")
    print(f"{bot.user} is online")

@bot.event
async def on_message(msg):
    if msg.author.id != bot.user.id:
        await msg.channel.send(f"Got it, {msg.author.mention}")

@bot.tree.command(name="play", description="Play a song or add it to queue")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()
    
    voice_state = interaction.user.voice

    if voice_state is None or voice_state.channel is None:
        await interaction.followup.send("You need to be in a voice channel.")
        return

    voice_channel = voice_state.channel
    
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    if "list=RD" in song_query:
        song_query = song_query.split("&list=")[0]
    
    is_url = song_query.startswith("http://") or song_query.startswith("https://")

    query = song_query if is_url else "ytsearch1: " + song_query

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "noplaylist": False,
        "extract_flat": False,
    }

    results = await search_ytdlp_async(query, ydl_options)

    guild_id = str(interaction.guild_id)
    SONG_QUEUES.setdefault(guild_id, deque())

    entries = results.get("entries")

    is_playlist = results.get("_type") == "playlist"

    if is_playlist and entries:
        valid_entries = [e for e in entries if e]

        if not valid_entries:
            await interaction.followup.send("Playlist is empty.")
            return

        added_count = 0

        for entry in valid_entries[:25]:
            audio_url = entry.get("url") or entry.get("webpage_url")
            title = entry.get("title", "Untitled")

            if audio_url:
                SONG_QUEUES[guild_id].append((audio_url, title))
                added_count += 1

        playlist_name = results.get("title", "Playlist")

        await interaction.followup.send(
            f"Added **{playlist_name}** ({added_count} songs) to queue"
        )

        if not voice_client.is_playing():
            await play_next_song(voice_client, guild_id, interaction.channel)

        return

    if entries:
        first_track = next((e for e in entries if e), None)
    else:
        first_track = results

    if not first_track:
        await interaction.followup.send("Could not extract track.")
        return

    audio_url = first_track.get("url") or first_track.get("webpage_url")
    title = first_track.get("title", "Untitled")

    if not audio_url:
        await interaction.followup.send("Could not extract audio.")
        return

    SONG_QUEUES[guild_id].append((audio_url, title))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: **{title}**")
    else:
        await interaction.followup.send(f"Now playing: **{title}**")
        await play_next_song(voice_client, guild_id, interaction.channel)

@bot.tree.command(name="skip", description="Skips current song")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped current song")
    else:
        await interaction.response.send_message("Nothing is playing to skip")

@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    # Check if something is actually playing
    if not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is currently playing.")
    
    # Pause the track
    voice_client.pause()
    await interaction.response.send_message("Playback paused!")


@bot.tree.command(name="resume", description="Resume the currently paused song.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    # Check if it's actually paused
    if not voice_client.is_paused():
        return await interaction.response.send_message("I’m not paused right now.")
    
    # Resume playback
    voice_client.resume()
    await interaction.response.send_message("Playback resumed!")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    voice_client = interaction.guild.voice_client

    # Check if the bot is in a voice channel
    if not voice_client or not voice_client.is_connected():
        return await interaction.followup.send("I'm not connected to any voice channel.")

    # Clear the guild's queue
    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    # If something is playing or paused, stop it
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    await interaction.followup.send("Stopped playback and disconnected!")
    await voice_client.disconnect()

@bot.tree.command(name="queue", description="Show the current song queue")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    if guild_id not in SONG_QUEUES or len(SONG_QUEUES[guild_id]) == 0:
        await interaction.response.send_message("📭 The queue is empty.")
        return

    queue_list = SONG_QUEUES[guild_id]

    description = ""
    for i, (_, title) in enumerate(queue_list, start=1):
        description += f"**{i}.** {title}\n"

    if len(description) > 1900:
        description = description[:1900] + "\n... (truncated)"
    
    now_playing = CURRENT_SONG.get(guild_id, "Nothing")


    embed = discord.Embed(title="🎶 Music Queue", color=0x00ffcc)
    embed.add_field(name="Now Playing", value=now_playing or "Nothing", inline=False)
    embed.add_field(name="Up Next", value=description or "Empty", inline=False)

    await interaction.response.send_message(embed=embed)

async def play_next_song(voice_client, guild_id, channel):
    
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()
        CURRENT_SONG[guild_id] = title
        ffmpeg_options = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn -c:a libopus -b:a 96k",
        }

        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="C:\\Users\\deilo\\OneDrive\\Documentos\\LunaBot\\bin\\ffmpeg\\ffmpeg.exe")

        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)
        
        voice_client.play(source, after = after_play)
        asyncio.create_task(channel.send(f"🎵 Now playing: **{title}**"))
    else:
        await voice_client.disconnect()

@bot.tree.command(name="salute", description="Salutes user")
async def salute(interaction: discord.Interaction):
    username = interaction.user.mention
    await interaction.response.send_message(f"Salutations, {username}")

@bot.tree.command(name="bye", description="Says good bye user")
async def bye(interaction: discord.Interaction):
    username = interaction.user.mention
    await interaction.response.send_message(f"Good bye, {username}")

bot.run(TOKEN)