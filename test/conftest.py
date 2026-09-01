"""Test fixtures.

``music_assistant`` (the server package, as opposed to ``music_assistant_models``)
is not published to PyPI and cannot be installed by pip/uv. Since the two
providers here only need ``music_assistant.models.music_provider.MusicProvider``
at import time, a stub module matching the real API is injected into
``sys.modules`` before either provider module is imported.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest


def _install_music_assistant_stubs() -> None:
    if "music_assistant" in sys.modules:
        return

    ma = types.ModuleType("music_assistant")
    ma_models = types.ModuleType("music_assistant.models")
    ma_models_music_provider = types.ModuleType("music_assistant.models.music_provider")

    class Provider:
        """Minimal stand-in for music_assistant.models.provider.Provider."""

        def __init__(self, mass, manifest, config, supported_features=None):
            self.mass = mass
            self.manifest = manifest
            self.config = config
            self.supported_features = supported_features or set()

        @property
        def domain(self) -> str:
            return self.manifest.domain

        @property
        def instance_id(self) -> str:
            return self.config.instance_id

    class MusicProvider(Provider):
        """Minimal stand-in for music_assistant.models.music_provider.MusicProvider."""

    ma_models_music_provider.MusicProvider = MusicProvider

    sys.modules["music_assistant"] = ma
    sys.modules["music_assistant.models"] = ma_models
    sys.modules["music_assistant.models.music_provider"] = ma_models_music_provider


_install_music_assistant_stubs()


@pytest.fixture
def youtube_manifest():
    return SimpleNamespace(domain="tutubo_youtube", type="music")


@pytest.fixture
def ytmusic_manifest():
    return SimpleNamespace(domain="tutubo_music", type="music")


@pytest.fixture
def provider_config():
    return SimpleNamespace(instance_id="tutubo_1")


@pytest.fixture
def youtube_provider(youtube_manifest, provider_config):
    """A YouTubeProvider instance with handle_async_init skipped."""
    from youtube_ma_provider.youtube import YouTubeProvider, SUPPORTED_FEATURES

    prov = YouTubeProvider(
        mass=None, manifest=youtube_manifest, config=provider_config,
        supported_features=SUPPORTED_FEATURES,
    )
    return prov


@pytest.fixture
def ytmusic_provider(ytmusic_manifest, provider_config):
    """A YouTubeMusicProvider instance with handle_async_init skipped."""
    from youtube_ma_provider.ytmusic import YouTubeMusicProvider, SUPPORTED_FEATURES

    prov = YouTubeMusicProvider(
        mass=None, manifest=ytmusic_manifest, config=provider_config,
        supported_features=SUPPORTED_FEATURES,
    )
    prov._track_cache = {}
    prov._album_playlist_map = {}
    return prov
