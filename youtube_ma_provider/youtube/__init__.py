"""Generic YouTube provider for Music Assistant.

Covers youtube.com — no YouTube Music API, no account required:
- Tracks      : any YouTube video (watch URL as item_id)
- Artists     : YouTube channels (channel URL as item_id); follow to get uploads
- Playlists   : YouTube playlists
- Audiobooks  : videos classified as AUDIOBOOK by tutubo content-type
- Podcasts    : videos classified as PODCAST
- Radio       : live streams classified as LIVE_RADIO or LIVE_NEWS

Stream URLs are resolved at play-time via yt-dlp.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from music_assistant_models.enums import (
    ContentType,
    ImageType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import MediaNotFoundError, ProviderUnavailableError
from music_assistant_models.media_items import (
    Artist,
    AudioFormat,
    Audiobook,
    MediaItemImage,
    Playlist,
    Podcast,
    PodcastEpisode,
    ProviderMapping,
    Radio,
    SearchResults,
    Track,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigEntry, ConfigValueType, ProviderConfig
    from music_assistant_models.provider import ProviderManifest
    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

SUPPORTED_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.ARTIST_TOPTRACKS,
}

_YTDLP_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "format": "bestaudio/best",
    "skip_download": True,
}


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    return YouTubeProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    return ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image(url: str | None, instance_id: str) -> MediaItemImage | None:
    if not url:
        return None
    return MediaItemImage(type=ImageType.THUMB, path=url, provider=instance_id, remotely_accessible=True)


def _provider_mapping(item_id: str, domain: str, instance_id: str) -> ProviderMapping:
    return ProviderMapping(item_id=item_id, provider_domain=domain, provider_instance=instance_id)


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def _to_track(v, domain: str, instance_id: str) -> Track:
    """Convert a tutubo Video or VideoPreview to a MA Track."""
    watch_url = v.watch_url
    track = Track(
        item_id=watch_url,
        provider=domain,
        name=getattr(v, "title", None) or "Unknown",
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
        duration=int(getattr(v, "length", None) or 0),
    )
    img = _image(getattr(v, "thumbnail_url", None), instance_id)
    if img:
        track.metadata.images = UniqueList([img])
    return track


def _to_artist(ch, domain: str, instance_id: str) -> Artist:
    """Convert a tutubo Channel or ChannelPreview to a MA Artist.

    The channel URL is used as item_id so get_artist() can re-hydrate it
    via Channel(url) without any additional lookup.
    """
    channel_url = ch.channel_url
    name = getattr(ch, "channel_name", None) or getattr(ch, "title", None) or "Unknown"
    artist = Artist(
        item_id=channel_url,
        provider=domain,
        name=name,
        provider_mappings={_provider_mapping(channel_url, domain, instance_id)},
    )
    img = _image(getattr(ch, "thumbnail_url", None), instance_id)
    if img:
        artist.metadata.images = UniqueList([img])
    return artist


def _to_playlist(pl, domain: str, instance_id: str) -> Playlist:
    """Convert a tutubo PlaylistPreview to a MA Playlist."""
    playlist_url = pl.playlist_url
    playlist = Playlist(
        item_id=playlist_url,
        provider=domain,
        name=getattr(pl, "title", None) or "Unknown",
        owner="YouTube",
        is_editable=False,
        provider_mappings={_provider_mapping(playlist_url, domain, instance_id)},
    )
    img = _image(getattr(pl, "thumbnail_url", None), instance_id)
    if img:
        playlist.metadata.images = UniqueList([img])
    return playlist


def _to_audiobook(v, domain: str, instance_id: str) -> Audiobook:
    watch_url = v.watch_url
    book = Audiobook(
        item_id=watch_url,
        provider=domain,
        name=getattr(v, "title", None) or "Unknown",
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
        duration=int(getattr(v, "length", None) or 0),
    )
    img = _image(getattr(v, "thumbnail_url", None), instance_id)
    if img:
        book.metadata.images = UniqueList([img])
    return book


def _to_radio(v, domain: str, instance_id: str) -> Radio:
    watch_url = v.watch_url
    radio = Radio(
        item_id=watch_url,
        provider=domain,
        name=getattr(v, "title", None) or "Unknown",
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
    )
    img = _image(getattr(v, "thumbnail_url", None), instance_id)
    if img:
        radio.metadata.images = UniqueList([img])
    return radio


def _to_podcast_episode(v, domain: str, instance_id: str) -> PodcastEpisode:
    """Convert a VideoPreview classified as PODCAST to a MA PodcastEpisode.

    YouTube does not expose podcast show metadata in search results; we use
    the channel name (VideoPreview.author) as the logical show grouping so
    that episodes from the same channel cluster under one stub Podcast.
    """
    watch_url = v.watch_url
    title = v.title or "Unknown"
    show_name = v.author or "YouTube Podcasts"
    show_id = f"yt:podcast:{show_name}"
    stub_show = Podcast(
        item_id=show_id,
        provider=domain,
        name=show_name,
        provider_mappings={_provider_mapping(show_id, domain, instance_id)},
    )
    episode = PodcastEpisode(
        item_id=watch_url,
        provider=domain,
        name=title,
        position=0,
        podcast=stub_show,
        duration=int(getattr(v, "length", None) or 0),
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
    )
    img = _image(getattr(v, "thumbnail_url", None), instance_id)
    if img:
        episode.metadata.images = UniqueList([img])
    return episode


# ---------------------------------------------------------------------------
# Stream resolution
# ---------------------------------------------------------------------------

def _extract_stream_url(watch_url: str) -> str:
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as err:
        raise ProviderUnavailableError("yt-dlp not installed") from err
    try:
        with yt_dlp.YoutubeDL(_YTDLP_OPTS) as ydl:
            info = ydl.extract_info(watch_url, download=False) or {}
            return info.get("url", "")
    except Exception as exc:
        raise MediaNotFoundError(f"yt-dlp could not extract stream for {watch_url}: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class YouTubeProvider(MusicProvider):
    """Music Assistant provider for generic YouTube content.

    Channels map to Artists (follow a channel = follow an artist).
    Playlists map to Playlists.
    All videos map to Tracks regardless of content type; specialised types
    (audiobooks, podcasts, radio) are also surfaced in their respective MA
    media-type slots.
    """

    @property
    def is_streaming_provider(self) -> bool:
        return True

    async def handle_async_init(self) -> None:
        try:
            from tutubo import YoutubeSearch  # noqa: PLC0415
            from tutubo.channel import Channel, Playlist  # noqa: PLC0415
            self._YoutubeSearch = YoutubeSearch
            self._Channel = Channel
            self._Playlist = Playlist
        except ImportError as err:
            raise ProviderUnavailableError("tutubo not installed") from err

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 10
    ) -> SearchResults:
        result = SearchResults()

        def _do():
            tracks, artists, playlists = [], [], []
            audiobooks, radios, podcasts = [], [], []

            s = self._YoutubeSearch(search_query)

            if MediaType.TRACK in media_types:
                for v in s.iterate_videos(max_res=limit):
                    tracks.append(v)

            if MediaType.ARTIST in media_types:
                for ch in s.iterate_channels(max_res=limit):
                    artists.append(ch)

            if MediaType.PLAYLIST in media_types:
                for pl in s.iterate_playlists(max_res=limit):
                    playlists.append(pl)

            if MediaType.AUDIOBOOK in media_types:
                for v in self._YoutubeSearch.for_audiobooks(search_query).iterate_audiobooks(max_res=limit):
                    audiobooks.append(v)

            if MediaType.RADIO in media_types:
                # Use a plain search so the query isn't biased toward either
                # live_radio or live_news; both surface as MA Radio.
                for v in s.iterate_live_radio(max_res=limit):
                    radios.append(v)
                for v in s.iterate_live_news(max_res=limit):
                    radios.append(v)

            if MediaType.PODCAST in media_types:
                for v in self._YoutubeSearch.for_podcasts(search_query).iterate_podcasts(max_res=limit):
                    podcasts.append(v)

            return tracks, artists, playlists, audiobooks, radios, podcasts

        tracks, artists, playlists, audiobooks, radios, podcasts = await asyncio.to_thread(_do)

        result.tracks = [_to_track(v, self.domain, self.instance_id) for v in tracks]
        result.artists = [_to_artist(ch, self.domain, self.instance_id) for ch in artists]
        result.playlists = [_to_playlist(pl, self.domain, self.instance_id) for pl in playlists]
        result.audiobooks = [_to_audiobook(v, self.domain, self.instance_id) for v in audiobooks]
        result.radio = [_to_radio(v, self.domain, self.instance_id) for v in radios]
        result.podcasts = [_to_podcast_episode(v, self.domain, self.instance_id) for v in podcasts]
        return result

    # ------------------------------------------------------------------
    # Artist / channel
    # ------------------------------------------------------------------

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Fetch channel metadata by URL."""
        ch = await asyncio.to_thread(self._Channel, prov_artist_id)
        return _to_artist(ch, self.domain, self.instance_id)

    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        """Return the channel's latest uploads as Tracks."""
        def _do():
            ch = self._Channel(prov_artist_id)
            return list(ch.videos())

        videos = await asyncio.to_thread(_do)
        return [_to_track(v, self.domain, self.instance_id) for v in videos]

    # ------------------------------------------------------------------
    # Playlist
    # ------------------------------------------------------------------

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Fetch playlist metadata by URL."""
        pl = await asyncio.to_thread(self._Playlist, prov_playlist_id)
        return _to_playlist(pl, self.domain, self.instance_id)

    async def get_playlist_tracks(self, prov_playlist_id: str, page: int = 0) -> list[Track]:
        def _do():
            pl = self._Playlist(prov_playlist_id)
            return list(pl.videos())

        videos = await asyncio.to_thread(_do)
        return [_to_track(v, self.domain, self.instance_id) for v in videos]

    # ------------------------------------------------------------------
    # Track (yt-dlp metadata fallback)
    # ------------------------------------------------------------------

    async def get_track(self, prov_track_id: str) -> Track:
        def _meta():
            import yt_dlp  # noqa: PLC0415
            opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(prov_track_id, download=False) or {}
                return info.get("title") or prov_track_id, info.get("thumbnail")

        title, thumb = await asyncio.to_thread(_meta)
        track = Track(
            item_id=prov_track_id,
            provider=self.domain,
            name=title,
            provider_mappings={_provider_mapping(prov_track_id, self.domain, self.instance_id)},
        )
        if thumb:
            track.metadata.images = UniqueList([_image(thumb, self.instance_id)])
        return track

    # ------------------------------------------------------------------
    # Stream
    # ------------------------------------------------------------------

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        stream_url = await asyncio.to_thread(_extract_stream_url, item_id)
        if not stream_url:
            raise MediaNotFoundError(f"Could not resolve stream for: {item_id}")
        # Live streams are not seekable.
        seekable = media_type != MediaType.RADIO
        return StreamDetails(
            provider=self.domain,
            item_id=item_id,
            audio_format=AudioFormat(content_type=ContentType.UNKNOWN),
            media_type=media_type,
            stream_type=StreamType.HTTP,
            path=stream_url,
            can_seek=seekable,
            allow_seek=seekable,
        )
