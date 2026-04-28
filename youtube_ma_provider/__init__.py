"""YouTube Music provider for Music Assistant via tutubo + yt-dlp."""

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
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemImage,
    MediaItemType,
    Playlist,
    ProviderMapping,
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


def _image(url: str | None, instance_id: str) -> MediaItemImage | None:
    if not url:
        return None
    return MediaItemImage(type=ImageType.THUMB, path=url, provider=instance_id, remotely_accessible=True)


def _artist_mapping(name: str, domain: str) -> ItemMapping:
    return ItemMapping(media_type=MediaType.ARTIST, item_id=name, provider=domain, name=name)


def _to_track(t, domain: str, instance_id: str) -> Track:
    watch_url = t.watch_url
    track = Track(
        item_id=watch_url,
        provider=domain,
        name=t.title or "Unknown",
        provider_mappings={
            ProviderMapping(item_id=watch_url, provider_domain=domain, provider_instance=instance_id)
        },
        duration=int(t.length or 0),
    )
    artist_name = t.artist or "Unknown"
    # Use canonical ytm:artist:{browseId} if the raw data carries it
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


def _thumb_url(raw: dict) -> str | None:
    """Extract best thumbnail URL from a ytmusicapi dict (search or API response)."""
    thumbs = raw.get("thumbnails") or raw.get("thumbnail") or []
    if isinstance(thumbs, list) and thumbs:
        return thumbs[-1].get("url")
    return None


def _to_album(raw: dict, browse_id: str, domain: str, instance_id: str) -> Album:
    item_id = f"ytm:album:{browse_id}"
    title = raw.get("title") or raw.get("name") or "Unknown"
    artists = raw.get("artists") or []
    artist_name = artists[0].get("name") if artists else raw.get("artist") or "Unknown"
    album = Album(
        item_id=item_id,
        provider=domain,
        name=title,
        provider_mappings={
            ProviderMapping(item_id=item_id, provider_domain=domain, provider_instance=instance_id)
        },
    )
    album.artists = UniqueList([_artist_mapping(artist_name, domain)])
    img = _image(_thumb_url(raw), instance_id)
    if img:
        album.metadata.images = UniqueList([img])
    return album


def _to_artist(raw: dict, browse_id: str, domain: str, instance_id: str) -> Artist:
    item_id = f"ytm:artist:{browse_id}"
    # "artist" key in search results; "name" key in get_artist() response
    name = raw.get("artist") or raw.get("name") or "Unknown"
    artist = Artist(
        item_id=item_id,
        provider=domain,
        name=name,
        provider_mappings={
            ProviderMapping(item_id=item_id, provider_domain=domain, provider_instance=instance_id)
        },
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
        provider_mappings={
            ProviderMapping(item_id=item_id, provider_domain=domain, provider_instance=instance_id)
        },
    )
    img = _image(_thumb_url(raw), instance_id)
    if img:
        pl.metadata.images = UniqueList([img])
    return pl


def _extract_stream_url(watch_url: str) -> str:
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as err:
        raise ProviderUnavailableError("yt-dlp not installed") from err
    with yt_dlp.YoutubeDL(_YTDLP_OPTS) as ydl:
        info = ydl.extract_info(watch_url, download=False)
        return info.get("url", "")


class YouTubeMusicProvider(MusicProvider):
    """Music Assistant provider for YouTube Music via tutubo."""

    @property
    def is_streaming_provider(self) -> bool:
        return True

    async def handle_async_init(self) -> None:
        try:
            from tutubo import YoutubeSearch  # noqa: PLC0415
            from tutubo.ytmus import search_yt_music, _get_ytmus  # noqa: PLC0415
            self._search_cls = YoutubeSearch
            self._search_yt_music = search_yt_music
            self._get_ytmus = _get_ytmus
        except ImportError as err:
            raise ProviderUnavailableError("tutubo not installed") from err
        # Cache track metadata keyed by watch URL — populated during search/browse
        self._track_cache: dict[str, Track] = {}
        self._album_playlist_map: dict[str, str] = {}  # browse_id → playlist_id

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 10
    ) -> SearchResults:
        result = SearchResults()

        def _do():
            from tutubo.ytmus import MusicTrack, MusicAlbum, MusicArtist, MusicPlaylist  # noqa: PLC0415
            tracks, albums, artists, playlists = [], [], [], []
            # search_yt_music fetches full album/artist/playlist data per result — too slow for
            # search. Use the ytmusicapi search results directly without the extra get_* calls.
            ytm = self._get_ytmus()
            for r in ytm.search(search_query):
                rtype = r.get("resultType")
                if rtype == "song" and MediaType.TRACK in media_types and len(tracks) < limit:
                    tracks.append(MusicTrack(r))
                elif rtype == "video" and MediaType.TRACK in media_types and len(tracks) < limit:
                    tracks.append(MusicTrack(r))
                elif rtype == "album" and MediaType.ALBUM in media_types and len(albums) < limit:
                    albums.append((r, r.get("browseId", "")))
                elif rtype == "artist" and MediaType.ARTIST in media_types and len(artists) < limit:
                    artists.append((r, r.get("browseId", "")))
                elif rtype == "playlist" and MediaType.PLAYLIST in media_types and len(playlists) < limit:
                    playlists.append((r, r.get("browseId", "")))
                if len(tracks) + len(albums) + len(artists) + len(playlists) >= limit * len(media_types):
                    break
            return tracks, albums, artists, playlists

        tracks, albums, artists, playlists = await asyncio.to_thread(_do)
        result.tracks = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        result.albums = [_to_album(d, bid, self.domain, self.instance_id) for d, bid in albums]
        result.artists = [_to_artist(d, bid, self.domain, self.instance_id) for d, bid in artists]
        result.playlists = [_to_playlist(d, bid, self.domain, self.instance_id) for d, bid in playlists]
        for t in result.tracks:
            self._track_cache[t.item_id] = t
        # Cache browse_id → playlist_id so get_album() can use the stable playlist path
        for d, bid in albums:
            pl_id = d.get("playlistId", "")
            if bid and pl_id:
                self._album_playlist_map[bid] = pl_id
        return result

    async def browse(self, path: str) -> Sequence[MediaItemType | BrowseFolder]:
        parts = [p for p in path.split("://")[1].split("/") if p] if "://" in path else []
        if not parts:
            return [
                BrowseFolder(item_id="trending", provider=self.domain, path=f"{path}/trending", name="Trending"),
            ]

        def _do():
            s = self._search_cls("trending music")
            return list(s.iterate_music_tracks(max_res=20))

        tracks = await asyncio.to_thread(_do)
        return [_to_track(t, self.domain, self.instance_id) for t in tracks]

    async def get_album(self, prov_album_id: str) -> Album:
        browse_id = prov_album_id.split("ytm:album:")[-1]
        playlist_id = self._album_playlist_map.get(browse_id, "")

        def _do():
            from tutubo.ytmus import get_album  # noqa: PLC0415
            return get_album(browse_id, playlist_id)

        data = await asyncio.to_thread(_do)
        return _to_album(data, browse_id, self.domain, self.instance_id)

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        browse_id = prov_album_id.split("ytm:album:")[-1]
        playlist_id = self._album_playlist_map.get(browse_id, "")

        def _do():
            from tutubo.ytmus import get_album, MusicAlbum  # noqa: PLC0415
            data = get_album(browse_id, playlist_id)
            return MusicAlbum(data).tracks

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
            from tutubo.ytmus import MusicArtist  # noqa: PLC0415
            ytm = self._get_ytmus()
            data = ytm.get_artist(browse_id)
            data["browseId"] = browse_id
            return MusicArtist(data).tracks

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
                if not alb_id:
                    continue
                result.append((alb, alb_id))
            return result

        albums = await asyncio.to_thread(_do)
        # Populate playlist map so get_album() can use the stable playlist path
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
            from tutubo.ytmus import MusicPlaylist  # noqa: PLC0415
            ytm = self._get_ytmus()
            data = ytm.get_playlist(browse_id)
            data["browseId"] = browse_id
            return MusicPlaylist(data).tracks

        tracks = await asyncio.to_thread(_do)
        result = [_to_track(t, self.domain, self.instance_id) for t in tracks]
        for t in result:
            self._track_cache[t.item_id] = t
        return result

    async def get_track(self, prov_track_id: str) -> Track:
        # Return cached track (populated during search/browse) to preserve full metadata
        if prov_track_id in self._track_cache:
            return self._track_cache[prov_track_id]
        # Fallback: fetch metadata via yt-dlp when not in cache
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
            provider_mappings={
                ProviderMapping(
                    item_id=prov_track_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
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
            media_type=MediaType.TRACK,
            stream_type=StreamType.HTTP,
            path=stream_url,
            can_seek=True,
            allow_seek=True,
        )
