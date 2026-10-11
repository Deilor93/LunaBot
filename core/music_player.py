import asyncio
import json
import os
from collections import deque
import discord
import yt_dlp

FFMPEG_PATH = "C:\\Users\\deilo\\OneDrive\\Documentos\\LunaBot\\bin\\ffmpeg\\ffmpeg.exe"
QUEUE_SAVE_PATH = "saved_queues.json"
QUEUE_MASTER_PATH = "saved_queues_master.json"

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -c:a libopus -b:a 96k",
}

YDL_OPTIONS_SINGLE = {
    "format": "bestaudio[abr<=96]/bestaudio",
    "youtube_include_dash_manifest": False,
    "youtube_include_hls_manifest": False,
    "noplaylist": True,
    "extract_flat": False,
    "extractor_args": {"youtube": {"js_runtimes": ["nodejs"]}},
    "cookiefile": "cookies.txt",
    "source_address": "0.0.0.0",
    "sleep_interval": 1,
}

YDL_OPTIONS_PLAYLIST = {
    "format": "bestaudio[abr<=96]/bestaudio",
    "youtube_include_dash_manifest": False,
    "youtube_include_hls_manifest": False,
    "noplaylist": False,
    "extract_flat": "in_playlist",
    "extractor_args": {"youtube": {"js_runtimes": ["nodejs"]}},
    "cookiefile": "cookies.txt",
    "source_address": "0.0.0.0",
    "sleep_interval": 1,
}


def _extract(query: str, ydl_opts: dict):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


async def _resolve_audio_url(webpage_url: str) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _extract(webpage_url, YDL_OPTIONS_SINGLE)),
            timeout=30.0
        )
        audio_url = result.get("url")
        title = result.get("title", "Untitled")
        return (audio_url, title) if audio_url else None
    except asyncio.TimeoutError:
        print(f"[ERROR] Timed out resolving: {webpage_url}")
        return None


def load_saved_queues() -> dict:
    """Load all saved queues from disk."""
    if not os.path.exists(QUEUE_SAVE_PATH):
        return {}
    try:
        with open(QUEUE_SAVE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load saved queues: {e}")
        return {}


def save_queues_to_disk(all_queues: dict):
    """Save all guild queues to disk."""
    try:
        with open(QUEUE_SAVE_PATH, "w") as f:
            json.dump(all_queues, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Failed to save queues: {e}")


class MusicPlayer:
    """Holds queue and playback state for a single guild."""

    # Shared across all instances so any player can trigger a full save
    _all_players: dict[str, "MusicPlayer"] = {}

    def __init__(self, voice_client: discord.VoiceClient, channel: discord.TextChannel, guild_id: str):
        self.voice_client = voice_client
        self.channel = channel
        self.guild_id = guild_id
        self.queue: deque[tuple[str, str]] = deque()
        self.master_list: list[tuple[str, str]] = []
        self.current: str | None = None
        self.current_track: tuple[str, str] | None = None
        MusicPlayer._all_players[guild_id] = self

    @property
    def is_playing(self) -> bool:
        return self.voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.voice_client.is_paused()

    def add_to_queue(self, webpage_url: str, title: str):
        """Queue a track by its webpage URL. Audio URL is resolved lazily at play time."""
        self.queue.append((webpage_url, title))
        self.master_list.append((webpage_url, title))
        self._save()

    def _save(self):
        """Save full queue to disk. Called only when adding songs."""
        data = {}
        for guild_id, player in MusicPlayer._all_players.items():
            data[guild_id] = [
                {"url": url, "title": title}
                for url, title in player.master_list
            ]
        try:
            with open(QUEUE_MASTER_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save queue: {e}")

    @classmethod
    def load_queue_for_guild(cls, guild_id: str) -> list[tuple[str, str]]:
        if not os.path.exists(QUEUE_MASTER_PATH):
            return []
        try:
            with open(QUEUE_MASTER_PATH, "r") as f:
                saved = json.load(f)
            return [(e["url"], e["title"]) for e in saved.get(guild_id, [])]
        except Exception as e:
            print(f"[ERROR] Failed to load queue: {e}")
            return []

    async def add_and_play(self, webpage_url: str, title: str, bot_loop: asyncio.AbstractEventLoop):
        self.add_to_queue(webpage_url, title)
        if not self.is_playing and not self.is_paused:
            await self.play_next(bot_loop)

    async def play_next(self, bot_loop: asyncio.AbstractEventLoop):
        if not self.queue:
            self.current = None
            asyncio.create_task(self.channel.send("✅ Queue finished!"))
            return

        webpage_url, title = self.queue.popleft()

        asyncio.create_task(self.channel.send(f"⏳ Loading **{title}**..."))
        result = await _resolve_audio_url(webpage_url)
        if not result:
            print(f"[ERROR] Could not resolve {webpage_url}, skipping.")
            await self.play_next(bot_loop)
            return

        audio_url, resolved_title = result
        self.current = resolved_title
        self.current_track = (webpage_url, resolved_title)

        source = discord.FFmpegOpusAudio(
            audio_url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS
        )

        def after_play(error):
            if error:
                print(f"[ERROR] Playback error for {title}: {error}")
            asyncio.run_coroutine_threadsafe(
                self.play_next(bot_loop), bot_loop
            )

        self.voice_client.play(source, after=after_play)
        asyncio.create_task(self.channel.send(f"🎵 Now playing: **{resolved_title}**"))

    async def play_alert(self, alert_path: str, bot_loop: asyncio.AbstractEventLoop):
        if not self.voice_client or not self.voice_client.is_connected():
            return

        interrupted_track = self.current_track

        if self.is_playing or self.is_paused:
            self.voice_client.stop()

        alert_source = discord.FFmpegOpusAudio(
            alert_path,
            executable=FFMPEG_PATH,
            options="-vn -c:a libopus -b:a 96k",
        )

        def after_alert(error):
            if error:
                print(f"[ERROR] Alert playback error: {error}")
            if interrupted_track:
                self.queue.appendleft(interrupted_track)
            asyncio.run_coroutine_threadsafe(
                self.play_next(bot_loop), bot_loop
            )

        self.voice_client.play(alert_source, after=after_alert)