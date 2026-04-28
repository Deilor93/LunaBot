import asyncio
from collections import deque
import discord
import yt_dlp

FFMPEG_PATH = "C:\\Users\\deilo\\OneDrive\\Documentos\\LunaBot\\bin\\ffmpeg\\ffmpeg.exe"

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
}


def _extract(query: str, ydl_opts: dict):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


async def _resolve_audio_url(webpage_url: str) -> tuple[str, str] | None:
    """Fully resolve a webpage URL into a playable audio URL."""
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


class MusicPlayer:
    """Holds queue and playback state for a single guild."""

    def __init__(self, voice_client: discord.VoiceClient, channel: discord.TextChannel):
        self.voice_client = voice_client
        self.channel = channel
        # Each entry is (url, title, is_resolved)
        # is_resolved=False means url is a webpage_url that needs full extraction
        self.queue: deque[tuple[str, str, bool]] = deque()
        self.current: str | None = None

    @property
    def is_playing(self) -> bool:
        return self.voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.voice_client.is_paused()

    def clear(self):
        self.queue.clear()
        self.current = None

    def add_to_queue(self, url: str, title: str, resolved: bool = True):
        """
        Queue a track. Pass resolved=False for playlist entries
        that still need a full yt-dlp extract before playing.
        """
        self.queue.append((url, title, resolved))

    async def add_and_play(self, audio_url: str, title: str, bot_loop: asyncio.AbstractEventLoop):
        self.add_to_queue(audio_url, title, resolved=True)
        if not self.is_playing and not self.is_paused:
            await self.play_next(bot_loop)

    async def play_next(self, bot_loop: asyncio.AbstractEventLoop):
        if not self.queue:
            await self.voice_client.disconnect()
            return

        url, title, resolved = self.queue.popleft()

        # Lazy resolution: if this is a webpage_url, extract the real audio URL now
        if not resolved:
            asyncio.create_task(self.channel.send(f"⏳ Loading **{title}**..."))
            result = await _resolve_audio_url(url)
            if not result:
                print(f"[ERROR] Could not resolve {url}, skipping.")
                await self.play_next(bot_loop)
                return
            url, title = result

        self.current = title

        source = discord.FFmpegOpusAudio(
            url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS
        )

        def after_play(error):
            if error:
                print(f"[ERROR] Playback error for {title}: {error}")
            asyncio.run_coroutine_threadsafe(
                self.play_next(bot_loop), bot_loop
            )

        self.voice_client.play(source, after=after_play)
        asyncio.create_task(self.channel.send(f"🎵 Now playing: **{title}**"))