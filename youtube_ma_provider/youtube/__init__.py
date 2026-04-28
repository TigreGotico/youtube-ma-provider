"""Generic YouTube provider for Music Assistant.

Covers youtube.com: audiobooks, podcasts, and live radio/news.
Uses tutubo's YoutubeSearch with content-type filtering — no YouTube Music
API, no account required.  Stream URLs are resolved via yt-dlp.
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
    AudioFormat,
    Audiobook,
    MediaItemImage,
    Podcast,
    PodcastEpisode,
    ProviderMapping,
    Radio,
    SearchResults,
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
    """Music Assistant provider for YouTube audiobooks, podcasts, and live radio."""

    @property
    def is_streaming_provider(self) -> bool:
        return True

    async def handle_async_init(self) -> None:
        try:
            from tutubo import YoutubeSearch  # noqa: PLC0415
            self._YoutubeSearch = YoutubeSearch
        except ImportError as err:
            raise ProviderUnavailableError("tutubo not installed") from err

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 10
    ) -> SearchResults:
        result = SearchResults()

        def _do():
            audiobooks, radios, podcasts = [], [], []

            if MediaType.AUDIOBOOK in media_types:
                s = self._YoutubeSearch.for_audiobooks(search_query)
                for v in s.iterate_audiobooks(max_res=limit):
                    audiobooks.append(v)

            if MediaType.RADIO in media_types:
                # Live radio and live news both surface as MA Radio.
                s = self._YoutubeSearch.for_live_news(search_query)
                for v in s.iterate_live_radio(max_res=limit):
                    radios.append(v)
                for v in s.iterate_live_news(max_res=limit):
                    radios.append(v)

            if MediaType.PODCAST in media_types:
                s = self._YoutubeSearch.for_podcasts(search_query)
                for v in s.iterate_podcasts(max_res=limit):
                    podcasts.append(v)

            return audiobooks, radios, podcasts

        audiobooks, radios, podcasts = await asyncio.to_thread(_do)

        result.audiobooks = [_to_audiobook(v, self.domain, self.instance_id) for v in audiobooks]
        result.radio = [_to_radio(v, self.domain, self.instance_id) for v in radios]
        result.podcasts = [_to_podcast_episode(v, self.domain, self.instance_id) for v in podcasts]
        return result

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
