"""Tests for youtube_ma_provider.ytmusic (the YouTube Music provider)."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest
from music_assistant_models.enums import MediaType
from music_assistant_models.errors import MediaNotFoundError


class _FakeTrack:
    def __init__(self, title="A Song", watch_url="https://youtu.be/xyz", length=200, artist="An Artist"):
        self.title = title
        self.watch_url = watch_url
        self.length = length
        self.artist = artist
        self.thumbnail_url = "http://img/t.jpg"
        self._raw_data = {"artists": [{"id": "UC_artist", "name": artist}]}


class _FakeRawItem:
    """Stand-in for a tutubo search-result item exposing ._raw_data."""

    def __init__(self, raw):
        self._raw_data = raw


@pytest.fixture(autouse=True)
def _wire_search(ytmusic_provider):
    ytmusic_provider._YoutubeMusicSearch = MagicMock()
    ytmusic_provider._MusicAlbum = MagicMock()
    ytmusic_provider._MusicArtist = MagicMock()
    ytmusic_provider._MusicPlaylist = MagicMock()
    ytmusic_provider._get_album = MagicMock()
    ytmusic_provider._get_ytmus = MagicMock()
    return ytmusic_provider


def test_search_maps_tracks_with_artist_mapping(ytmusic_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_tracks.return_value = [_FakeTrack()]
        ytmusic_provider._YoutubeMusicSearch.return_value = search_instance

        result = await ytmusic_provider.search("query", [MediaType.TRACK])

        assert len(result.tracks) == 1
        track = result.tracks[0]
        assert track.name == "A Song"
        assert track.artists[0].name == "An Artist"
        assert track.artists[0].item_id == "ytm:artist:UC_artist"
    asyncio.run(_run())

def test_search_maps_albums(ytmusic_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_albums.return_value = [
            _FakeRawItem({"browseId": "MPREb_abc", "title": "An Album", "artists": [{"name": "An Artist"}]})
        ]
        ytmusic_provider._YoutubeMusicSearch.return_value = search_instance

        result = await ytmusic_provider.search("query", [MediaType.ALBUM])

        assert len(result.albums) == 1
        assert result.albums[0].name == "An Album"
        assert result.albums[0].item_id == "ytm:album:MPREb_abc"
    asyncio.run(_run())

def test_search_maps_artists(ytmusic_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_artists.return_value = [
            _FakeRawItem({"browseId": "UC_artist", "artist": "An Artist"})
        ]
        ytmusic_provider._YoutubeMusicSearch.return_value = search_instance

        result = await ytmusic_provider.search("query", [MediaType.ARTIST])

        assert len(result.artists) == 1
        assert result.artists[0].name == "An Artist"
    asyncio.run(_run())

def test_search_maps_playlists(ytmusic_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_playlists.return_value = [
            _FakeRawItem({"browseId": "PL123", "title": "A Playlist", "author": "Some User"})
        ]
        ytmusic_provider._YoutubeMusicSearch.return_value = search_instance

        result = await ytmusic_provider.search("query", [MediaType.PLAYLIST])

        assert len(result.playlists) == 1
        assert result.playlists[0].name == "A Playlist"
        assert result.playlists[0].owner == "Some User"
    asyncio.run(_run())

def test_get_artist_toptracks_uses_get_artist(ytmusic_provider):
    async def _run():
        ytm = MagicMock()
        ytm.get_artist.return_value = {"artist": "An Artist"}
        ytmusic_provider._get_ytmus.return_value = ytm
        ytmusic_provider._MusicArtist.return_value = MagicMock(tracks=[_FakeTrack()])

        tracks = await ytmusic_provider.get_artist_toptracks("ytm:artist:UC_artist")

        assert len(tracks) == 1
        assert tracks[0].name == "A Song"
    asyncio.run(_run())

def _install_fake_yt_dlp(monkeypatch, extract_info):
    fake_module = types.ModuleType("yt_dlp")

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return extract_info(url)

    fake_module.YoutubeDL = FakeYoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)


def test_get_track_uses_cache_when_present(ytmusic_provider):
    async def _run():
        from music_assistant_models.media_items import Track

        cached = Track(
            item_id="ytm:cached", provider="tutubo_music", name="Cached Track",
            provider_mappings=set(),
        )
        ytmusic_provider._track_cache["ytm:cached"] = cached

        track = await ytmusic_provider.get_track("ytm:cached")

        assert track is cached
    asyncio.run(_run())

def test_get_track_happy_path_falls_back_to_ytdlp(ytmusic_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(
            monkeypatch,
            lambda url: {"title": "Resolved Title", "thumbnail": "http://img/thumb.jpg"},
        )

        track = await ytmusic_provider.get_track("https://youtu.be/not-cached")

        assert track.name == "Resolved Title"
    asyncio.run(_run())

def test_get_track_not_found_raises_media_not_found_error(ytmusic_provider, monkeypatch):
    async def _run():
        def _raise(url):
            raise RuntimeError("boom: video unavailable")

        _install_fake_yt_dlp(monkeypatch, _raise)

        with pytest.raises(MediaNotFoundError):
            await ytmusic_provider.get_track("https://youtu.be/does-not-exist")
    asyncio.run(_run())

def test_get_stream_details_uses_ytdlp_extracted_url(ytmusic_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(monkeypatch, lambda url: {"url": "https://cdn.example/stream.m4a"})

        details = await ytmusic_provider.get_stream_details("https://youtu.be/abc123", MediaType.TRACK)

        assert details.path == "https://cdn.example/stream.m4a"
        assert details.can_seek is True
    asyncio.run(_run())

def test_get_stream_details_raises_when_no_url(ytmusic_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(monkeypatch, lambda url: {})

        with pytest.raises(MediaNotFoundError):
            await ytmusic_provider.get_stream_details("https://youtu.be/abc123", MediaType.TRACK)
    asyncio.run(_run())

def test_entry_point_setup_is_importable():
    from youtube_ma_provider.ytmusic import setup, get_config_entries, SUPPORTED_FEATURES
    from music_assistant_models.enums import ProviderFeature

    assert SUPPORTED_FEATURES == {
        ProviderFeature.SEARCH,
        ProviderFeature.ARTIST_ALBUMS,
        ProviderFeature.ARTIST_TOPTRACKS,
    }
    assert callable(setup)
    assert callable(get_config_entries)
