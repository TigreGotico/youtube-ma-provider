"""Tests for youtube_ma_provider.youtube (the generic YouTube provider)."""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from music_assistant_models.enums import MediaType
from music_assistant_models.errors import MediaNotFoundError


def _video(title="Some Title", length=180, thumb="http://img/t.jpg", author="Some Channel"):
    return SimpleNamespace(
        watch_url="https://youtu.be/abc123",
        title=title,
        length=length,
        thumbnail_url=thumb,
        author=author,
    )


def _channel():
    return SimpleNamespace(
        channel_url="https://youtube.com/channel/UCxyz",
        channel_name="Some Channel",
        thumbnail_url="http://img/c.jpg",
    )


def _playlist():
    return SimpleNamespace(
        playlist_url="https://youtube.com/playlist?list=abc",
        title="Some Playlist",
        thumbnail_url="http://img/p.jpg",
    )


@pytest.fixture(autouse=True)
def _wire_search(youtube_provider):
    """Attach fake tutubo search/lookup classes as handle_async_init would."""
    youtube_provider._YoutubeSearch = MagicMock()
    youtube_provider._Channel = MagicMock()
    youtube_provider._Playlist = MagicMock()
    return youtube_provider


def test_search_maps_tracks(youtube_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_videos.return_value = [_video(title="Track One")]
        youtube_provider._YoutubeSearch.return_value = search_instance

        result = await youtube_provider.search("query", [MediaType.TRACK])

        assert len(result.tracks) == 1
        track = result.tracks[0]
        assert track.name == "Track One"
        assert track.item_id == "https://youtu.be/abc123"
        assert track.duration == 180
    asyncio.run(_run())

def test_search_maps_artists(youtube_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_channels.return_value = [_channel()]
        youtube_provider._YoutubeSearch.return_value = search_instance

        result = await youtube_provider.search("query", [MediaType.ARTIST])

        assert len(result.artists) == 1
        assert result.artists[0].name == "Some Channel"
        assert result.artists[0].item_id == "https://youtube.com/channel/UCxyz"
    asyncio.run(_run())

def test_search_maps_audiobooks(youtube_provider):
    async def _run():
        audiobook_search = MagicMock()
        audiobook_search.iterate_audiobooks.return_value = [_video(title="Chapter One")]
        youtube_provider._YoutubeSearch.for_audiobooks.return_value = audiobook_search

        result = await youtube_provider.search("query", [MediaType.AUDIOBOOK])

        assert len(result.audiobooks) == 1
        assert result.audiobooks[0].name == "Chapter One"
    asyncio.run(_run())

def test_search_maps_podcasts_to_episode_with_stub_show(youtube_provider):
    async def _run():
        podcast_search = MagicMock()
        podcast_search.iterate_podcasts.return_value = [_video(title="Episode 1", author="My Show")]
        youtube_provider._YoutubeSearch.for_podcasts.return_value = podcast_search

        result = await youtube_provider.search("query", [MediaType.PODCAST])

        assert len(result.podcasts) == 1
        episode = result.podcasts[0]
        assert episode.name == "Episode 1"
        assert episode.podcast.name == "My Show"
    asyncio.run(_run())

def test_search_maps_radio_from_live_radio_and_live_news(youtube_provider):
    async def _run():
        search_instance = MagicMock()
        search_instance.iterate_live_radio.return_value = [_video(title="Radio Stream")]
        search_instance.iterate_live_news.return_value = [_video(title="News Stream")]
        youtube_provider._YoutubeSearch.return_value = search_instance

        result = await youtube_provider.search("query", [MediaType.RADIO])

        names = {r.name for r in result.radio}
        assert names == {"Radio Stream", "News Stream"}
    asyncio.run(_run())

def test_get_artist_rehydrates_channel(youtube_provider):
    async def _run():
        youtube_provider._Channel.return_value = _channel()

        artist = await youtube_provider.get_artist("https://youtube.com/channel/UCxyz")

        assert artist.name == "Some Channel"
        youtube_provider._Channel.assert_called_once_with("https://youtube.com/channel/UCxyz")
    asyncio.run(_run())

def test_get_playlist_rehydrates_playlist(youtube_provider):
    async def _run():
        youtube_provider._Playlist.return_value = _playlist()

        playlist = await youtube_provider.get_playlist("https://youtube.com/playlist?list=abc")

        assert playlist.name == "Some Playlist"
        assert playlist.owner == "YouTube"
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


def test_get_track_happy_path(youtube_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(
            monkeypatch,
            lambda url: {"title": "Resolved Title", "thumbnail": "http://img/thumb.jpg"},
        )

        track = await youtube_provider.get_track("https://youtu.be/abc123")

        assert track.name == "Resolved Title"
        assert track.item_id == "https://youtu.be/abc123"
    asyncio.run(_run())

def test_get_track_not_found_raises_media_not_found_error(youtube_provider, monkeypatch):
    async def _run():
        def _raise(url):
            raise RuntimeError("boom: video unavailable")

        _install_fake_yt_dlp(monkeypatch, _raise)

        with pytest.raises(MediaNotFoundError):
            await youtube_provider.get_track("https://youtu.be/does-not-exist")
    asyncio.run(_run())

def test_get_stream_details_uses_ytdlp_extracted_url(youtube_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(monkeypatch, lambda url: {"url": "https://cdn.example/stream.m4a"})

        details = await youtube_provider.get_stream_details("https://youtu.be/abc123", MediaType.TRACK)

        assert details.path == "https://cdn.example/stream.m4a"
        assert details.can_seek is True
    asyncio.run(_run())

def test_get_stream_details_radio_not_seekable(youtube_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(monkeypatch, lambda url: {"url": "https://cdn.example/live.m4a"})

        details = await youtube_provider.get_stream_details("https://youtu.be/live", MediaType.RADIO)

        assert details.can_seek is False
        assert details.allow_seek is False
    asyncio.run(_run())

def test_get_stream_details_raises_when_no_url(youtube_provider, monkeypatch):
    async def _run():
        _install_fake_yt_dlp(monkeypatch, lambda url: {})

        with pytest.raises(MediaNotFoundError):
            await youtube_provider.get_stream_details("https://youtu.be/abc123", MediaType.TRACK)
    asyncio.run(_run())

def test_entry_point_setup_is_importable():
    from youtube_ma_provider.youtube import setup, get_config_entries, SUPPORTED_FEATURES
    from music_assistant_models.enums import ProviderFeature

    assert SUPPORTED_FEATURES == {ProviderFeature.SEARCH, ProviderFeature.ARTIST_TOPTRACKS}
    assert callable(setup)
    assert callable(get_config_entries)
