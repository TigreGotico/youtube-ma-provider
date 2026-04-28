"""YouTube Music provider for Music Assistant via tutubo + yt-dlp.

Supports:
- Music tracks, albums, artists, playlists (via YouTube Music API)
- Audiobooks (YouTube videos classified as audiobooks)
- Podcasts (YouTube videos classified as podcasts)
- Radio stations (YouTube live streams classified as live radio)
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
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
    Album,
    Artist,
    Audiobook,
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemImage,
    MediaItemType,
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
    ProviderFeature.ARTIST_ALBUMS,
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
    return YouTubeMusicProvider(mass, manifest, config, SUPPORTED_FEATURES)


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


def _artist_mapping(name: str, domain: str) -> ItemMapping:
    return ItemMapping(media_type=MediaType.ARTIST, item_id=name, provider=domain, name=name)


def _provider_mapping(item_id: str, domain: str, instance_id: str) -> ProviderMapping:
    return ProviderMapping(item_id=item_id, provider_domain=domain, provider_instance=instance_id)


def _thumb_url(raw: dict) -> str | None:
    thumbs = raw.get("thumbnails") or raw.get("thumbnail") or []
    if isinstance(thumbs, list) and thumbs:
        return thumbs[-1].get("url")
    return None


# ---------------------------------------------------------------------------
# Music conversions (YouTube Music API — MusicTrack etc.)
# ---------------------------------------------------------------------------

def _to_track(t, domain: str, instance_id: str) -> Track:
    """Convert a tutubo MusicTrack/MusicVideo to a MA Track."""
    watch_url = t.watch_url
    track = Track(
        item_id=watch_url,
        provider=domain,
        name=t.title or "Unknown",
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
        duration=int(t.length or 0),
    )
    artist_name = t.artist or "Unknown"
    raw_artists = getattr(t, "_raw_data", {}).get("artists") or []
    artist_browse_id = raw_artists[0].get("id") if raw_artists else None
    artist_id = f"ytm:artist:{artist_browse_id}" if artist_browse_id else artist_name
    track.artists = UniqueList([
        ItemMapping(media_type=MediaType.ARTIST, item_id=artist_id, provider=domain, name=artist_name)
    ])
    img = _image(t.thumbnail_url, instance_id)
    if img:
        track.metadata.images = UniqueList([img])
    return track


def _to_album(raw: dict, browse_id: str, domain: str, instance_id: str) -> Album:
    item_id = f"ytm:album:{browse_id}"
    title = raw.get("title") or raw.get("name") or "Unknown"
    artists = raw.get("artists") or []
    artist_name = artists[0].get("name") if artists else raw.get("artist") or "Unknown"
    album = Album(
        item_id=item_id,
        provider=domain,
        name=title,
        provider_mappings={_provider_mapping(item_id, domain, instance_id)},
    )
    album.artists = UniqueList([_artist_mapping(artist_name, domain)])
    img = _image(_thumb_url(raw), instance_id)
    if img:
        album.metadata.images = UniqueList([img])
    return album


def _to_artist(raw: dict, browse_id: str, domain: str, instance_id: str) -> Artist:
    item_id = f"ytm:artist:{browse_id}"
    name = raw.get("artist") or raw.get("name") or "Unknown"
    artist = Artist(
        item_id=item_id,
        provider=domain,
        name=name,
        provider_mappings={_provider_mapping(item_id, domain, instance_id)},
    )
    img = _image(_thumb_url(raw), instance_id)
    if img:
        artist.metadata.images = UniqueList([img])
    return artist


def _to_playlist(raw: dict, browse_id: str, domain: str, instance_id: str) -> Playlist:
    item_id = f"ytm:playlist:{browse_id}"
    title = raw.get("title") or raw.get("name") or "Unknown"
    _owner = raw.get("author") or raw.get("artist") or "YouTube Music"
    owner = _owner.get("name", str(_owner)) if isinstance(_owner, dict) else _owner
    pl = Playlist(
        item_id=item_id,
        provider=domain,
        name=title,
        owner=owner,
        is_editable=False,
        provider_mappings={_provider_mapping(item_id, domain, instance_id)},
    )
    img = _image(_thumb_url(raw), instance_id)
    if img:
        pl.metadata.images = UniqueList([img])
    return pl


# ---------------------------------------------------------------------------
# Non-music conversions (VideoPreview — content-type filtered)
# ---------------------------------------------------------------------------

def _video_watch_url(v) -> str:
    vid = getattr(v, "video_id", None) or getattr(v, "videoId", None) or ""
    return f"https://www.youtube.com/watch?v={vid}" if vid else ""


def _to_audiobook(v, domain: str, instance_id: str) -> Audiobook:
    """Convert a tutubo VideoPreview classified as AUDIOBOOK to a MA Audiobook."""
    watch_url = _video_watch_url(v)
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
    """Convert a tutubo VideoPreview classified as LIVE_RADIO to a MA Radio."""
    watch_url = _video_watch_url(v)
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


def _to_podcast(v, domain: str, instance_id: str) -> Podcast:
    """Convert a tutubo VideoPreview classified as PODCAST to a MA Podcast."""
    watch_url = _video_watch_url(v)
    podcast = Podcast(
        item_id=watch_url,
        provider=domain,
        name=getattr(v, "title", None) or "Unknown",
        provider_mappings={_provider_mapping(watch_url, domain, instance_id)},
    )
    img = _image(getattr(v, "thumbnail_url", None), instance_id)
    if img:
        podcast.metadata.images = UniqueList([img])
    return podcast


# ---------------------------------------------------------------------------
# Stream resolution
# ---------------------------------------------------------------------------

def _extract_stream_url(watch_url: str) -> str:
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as err:
        raise ProviderUnavailableError("yt-dlp not installed") from err
    with yt_dlp.YoutubeDL(_YTDLP_OPTS) as ydl:
        info = ydl.extract_info(watch_url, download=False)
        return info.get("url", "")


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class YouTubeMusicProvider(MusicProvider):
    """Music Assistant provider for YouTube Music, audiobooks, podcasts, and radio via tutubo."""

    @property
    def is_streaming_provider(self) -> bool:
        return True

    async def handle_async_init(self) -> None:
        try:
            from tutubo import YoutubeSearch  # noqa: PLC0415
            from tutubo.ytmus import (  # noqa: PLC0415
                MusicTrack, MusicVideo, MusicAlbum, MusicArtist, MusicPlaylist,
                get_album, _get_ytmus,
            )
            from tutubo.content_type import ContentType as TutuboContentType  # noqa: PLC0415
            self._YoutubeSearch = YoutubeSearch
            self._MusicTrack = MusicTrack
            self._MusicVideo = MusicVideo
            self._MusicAlbum = MusicAlbum
            self._MusicArtist = MusicArtist
            self._MusicPlaylist = MusicPlaylist
            self._get_album = get_album
            self._get_ytmus = _get_ytmus
            self._TutuboContentType = TutuboContentType
        except ImportError as err:
            raise ProviderUnavailableError("tutubo not installed") from err
        self._track_cache: dict[str, Track] = {}
        self._album_playlist_map: dict[str, str] = {}

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 10
    ) -> SearchResults:
        result = SearchResults()

        def _do():
            s = self._YoutubeSearch(search_query)
            tracks, albums, artists, playlists = [], [], [], []
            audiobooks, radios, podcasts = [], [], []

            if MediaType.TRACK in media_types:
                for t in s.iterate_music_tracks(max_res=limit):
                    tracks.append(t)

            if MediaType.ALBUM in media_types:
                for a in s.iterate_music_albums(max_res=limit):
                    bid = a._raw_data.get("browseId", "")
                    albums.append((a._raw_data, bid))

            if MediaType.ARTIST in media_types:
                for a in s.iterate_music_artists(max_res=limit):
                    bid = a._raw_data.get("browseId", "")
                    artists.append((a._raw_data, bid))

            if MediaType.PLAYLIST in media_types:
                for p in s.iterate_music_playlists(max_res=limit):
                    bid = p._raw_data.get("browseId", "")
                    playlists.append((p._raw_data, bid))

            if MediaType.AUDIOBOOK in media_types:
                for v in s.iterate_audiobooks(max_res=limit):
                    audiobooks.append(v)

            if MediaType.RADIO in media_types:
                CT = self._TutuboContentType
                for v in s.iterate_by_content_type(CT.LIVE_RADIO, max_res=limit):
                    radios.append(v)

            if MediaType.PODCAST in media_types:
                for v in s.iterate_podcasts(max_res=limit):
                    podcasts.append(v)

            return tracks, albums, artists, playlists, audiobooks, radios, podcasts

        tracks, albums, artists, playlists, audiobooks, radios, podcasts = await asyncio.to_thread(_do)

        result.tracks = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        result.albums = [_to_album(d, bid, self.domain, self.instance_id) for d, bid in albums]
        result.artists = [_to_artist(d, bid, self.domain, self.instance_id) for d, bid in artists]
        result.playlists = [_to_playlist(d, bid, self.domain, self.instance_id) for d, bid in playlists]
        result.audiobooks = [_to_audiobook(v, self.domain, self.instance_id) for v in audiobooks]
        result.radio = [_to_radio(v, self.domain, self.instance_id) for v in radios]
        result.podcasts = [_to_podcast(v, self.domain, self.instance_id) for v in podcasts]

        for t in result.tracks:
            self._track_cache[t.item_id] = t
        for d, bid in albums:
            pl_id = d.get("playlistId", "")
            if bid and pl_id:
                self._album_playlist_map[bid] = pl_id
        return result

    async def browse(self, path: str) -> Sequence[MediaItemType | BrowseFolder]:
        parts = [p for p in path.split("://")[1].split("/") if p] if "://" in path else []
        if not parts:
            return [
                BrowseFolder(item_id="trending", provider=self.domain,
                             path=f"{path}/trending", name="Trending Music"),
                BrowseFolder(item_id="audiobooks", provider=self.domain,
                             path=f"{path}/audiobooks", name="Audiobooks"),
                BrowseFolder(item_id="radio", provider=self.domain,
                             path=f"{path}/radio", name="Live Radio"),
                BrowseFolder(item_id="podcasts", provider=self.domain,
                             path=f"{path}/podcasts", name="Podcasts"),
            ]

        section = parts[0]

        def _do():
            CT = self._TutuboContentType
            if section == "audiobooks":
                s = self._YoutubeSearch("free audiobook")
                return "audiobooks", list(s.iterate_audiobooks(max_res=20))
            if section == "radio":
                s = self._YoutubeSearch("24/7 live radio")
                return "radio", list(s.iterate_by_content_type(CT.LIVE_RADIO, max_res=20))
            if section == "podcasts":
                s = self._YoutubeSearch("podcast episode")
                return "podcasts", list(s.iterate_podcasts(max_res=20))
            # default: trending music
            s = self._YoutubeSearch("trending music")
            return "tracks", list(s.iterate_music_tracks(max_res=20))

        kind, items = await asyncio.to_thread(_do)
        if kind == "audiobooks":
            return [_to_audiobook(v, self.domain, self.instance_id) for v in items]
        if kind == "radio":
            return [_to_radio(v, self.domain, self.instance_id) for v in items]
        if kind == "podcasts":
            return [_to_podcast(v, self.domain, self.instance_id) for v in items]
        return [_to_track(t, self.domain, self.instance_id) for t in items]

    # ------------------------------------------------------------------
    # Music — album / artist / playlist
    # ------------------------------------------------------------------

    async def get_album(self, prov_album_id: str) -> Album:
        browse_id = prov_album_id.split("ytm:album:")[-1]
        playlist_id = self._album_playlist_map.get(browse_id, "")
        data = await asyncio.to_thread(self._get_album, browse_id, playlist_id)
        return _to_album(data, browse_id, self.domain, self.instance_id)

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        browse_id = prov_album_id.split("ytm:album:")[-1]
        playlist_id = self._album_playlist_map.get(browse_id, "")

        def _do():
            data = self._get_album(browse_id, playlist_id)
            return self._MusicAlbum(data).tracks

        tracks = await asyncio.to_thread(_do)
        result = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        for t in result:
            self._track_cache[t.item_id] = t
        return result

    async def get_artist(self, prov_artist_id: str) -> Artist:
        browse_id = prov_artist_id.split("ytm:artist:")[-1]

        def _do():
            ytm = self._get_ytmus()
            data = ytm.get_artist(browse_id)
            data["browseId"] = browse_id
            return data

        data = await asyncio.to_thread(_do)
        return _to_artist(data, browse_id, self.domain, self.instance_id)

    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        browse_id = prov_artist_id.split("ytm:artist:")[-1]

        def _do():
            ytm = self._get_ytmus()
            data = ytm.get_artist(browse_id)
            data["browseId"] = browse_id
            return self._MusicArtist(data).tracks

        tracks = await asyncio.to_thread(_do)
        result = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        for t in result:
            self._track_cache[t.item_id] = t
        return result

    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        browse_id = prov_artist_id.split("ytm:artist:")[-1]

        def _do():
            ytm = self._get_ytmus()
            artist_data = ytm.get_artist(browse_id)
            result = []
            for alb in (artist_data.get("albums", {}).get("results") or []):
                alb_id = alb.get("browseId", "")
                if alb_id:
                    result.append((alb, alb_id))
            return result

        albums = await asyncio.to_thread(_do)
        for d, bid in albums:
            pl_id = d.get("playlistId", "")
            if bid and pl_id:
                self._album_playlist_map[bid] = pl_id
        return [_to_album(d, bid, self.domain, self.instance_id) for d, bid in albums]

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        browse_id = prov_playlist_id.split("ytm:playlist:")[-1]

        def _do():
            ytm = self._get_ytmus()
            data = ytm.get_playlist(browse_id)
            data["browseId"] = browse_id
            return data

        data = await asyncio.to_thread(_do)
        return _to_playlist(data, browse_id, self.domain, self.instance_id)

    async def get_playlist_tracks(self, prov_playlist_id: str, page: int = 0) -> list[Track]:
        browse_id = prov_playlist_id.split("ytm:playlist:")[-1]

        def _do():
            ytm = self._get_ytmus()
            data = ytm.get_playlist(browse_id)
            data["browseId"] = browse_id
            return self._MusicPlaylist(data).tracks

        tracks = await asyncio.to_thread(_do)
        result = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        for t in result:
            self._track_cache[t.item_id] = t
        return result

    # ------------------------------------------------------------------
    # Generic track / stream
    # ------------------------------------------------------------------

    async def get_track(self, prov_track_id: str) -> Track:
        if prov_track_id in self._track_cache:
            return self._track_cache[prov_track_id]

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
        self._track_cache[prov_track_id] = track
        return track

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        stream_url = await asyncio.to_thread(_extract_stream_url, item_id)
        if not stream_url:
            raise MediaNotFoundError(f"Could not resolve stream for: {item_id}")
        return StreamDetails(
            provider=self.domain,
            item_id=item_id,
            audio_format=AudioFormat(content_type=ContentType.UNKNOWN),
            media_type=media_type,
            stream_type=StreamType.HTTP,
            path=stream_url,
            can_seek=True,
            allow_seek=True,
        )
