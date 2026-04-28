# youtube-ma-provider

Two [Music Assistant](https://music-assistant.io) providers that bring YouTube and YouTube Music into MA without requiring a Google account or API key.

| Provider domain | Source | What you get |
|---|---|---|
| `tutubo_music` | music.youtube.com (YouTube Music API) | Tracks, albums, artists, playlists with structured metadata |
| `tutubo_youtube` | youtube.com (public page scraping) | Videos as tracks, channels as artists (follow!), playlists, audiobooks, podcasts, live radio |

Both providers resolve stream URLs at play-time via `yt-dlp` — no credentials, no OAuth, no API quotas.

---

## Table of contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Provider reference](#provider-reference)
  - [tutubo\_music — YouTube Music](#tutubo_music--youtube-music)
  - [tutubo\_youtube — Generic YouTube](#tutubo_youtube--generic-youtube)
- [Following a YouTube channel](#following-a-youtube-channel)
- [Architecture deep-dive](#architecture-deep-dive)
- [Development guide](#development-guide)
- [Troubleshooting](#troubleshooting)

---

## Quick start

### 1. Install

```bash
pip install youtube-ma-provider
```

This pulls in three dependencies automatically:

| Package | Role |
|---|---|
| `music-assistant-plugin-manager` | Registers the providers with MA at startup without touching MA's source tree |
| `tutubo` | Scrapes YouTube / YouTube Music search results and channel pages |
| `yt-dlp` | Resolves watch URLs to actual audio stream URLs at play-time |

### 2. Launch Music Assistant through the plugin manager

```bash
mass-pm
```

`mass-pm` is a thin wrapper that registers all installed MA provider plugins (via setuptools entrypoints) before delegating to the normal `music-assistant` process. The two YouTube providers appear automatically in MA's **Settings → Providers** list.

> If you run Music Assistant some other way (Docker, systemd, etc.) see [Architecture deep-dive](#architecture-deep-dive) for how to integrate.

### 3. Enable the providers

In Music Assistant:

1. Open **Settings → Providers**.
2. You will see **YouTube Music (no login)** and **YouTube (no login)**.
3. Click **+** on each one — no configuration fields are required.
4. Both providers are now active.

---

## How it works

```
User searches "Black Sabbath"
         │
         ├─ tutubo_music ──► YoutubeMusicSearch("Black Sabbath")
         │                        ├─ iterate_tracks()  → Track objects
         │                        ├─ iterate_albums()  → Album objects
         │                        ├─ iterate_artists() → Artist objects
         │                        └─ iterate_playlists() → Playlist objects
         │
         └─ tutubo_youtube ─► YoutubeSearch("Black Sabbath")
                                  ├─ iterate_videos()   → Track objects
                                  ├─ iterate_channels() → Artist objects
                                  └─ iterate_playlists()→ Playlist objects

User presses Play on any item
         │
         └─ get_stream_details(watch_url)
                  └─ yt-dlp extracts direct audio URL → MA streams it
```

**Why two providers?**

YouTube Music (`music.youtube.com`) and YouTube (`youtube.com`) are separate surfaces with different search APIs and different data shapes. YouTube Music returns structured music metadata (ISRC-equivalent IDs, artist browse IDs, album browse IDs) that maps cleanly onto MA's music model. Generic YouTube returns video metadata — useful for channels, playlists, long-form content — but without music-level structure.

Keeping them separate means:

- Searching for a song → `tutubo_music` gives you the canonical music result with correct artist/album links.
- Searching for a channel or playlist → `tutubo_youtube` gives you the channel as a followable artist and the playlist as a browseable collection.
- No double-results in music search from generic YouTube noise.

---

## Provider reference

### `tutubo_music` — YouTube Music

**Source:** `youtube_ma_provider/ytmusic/__init__.py`

#### Supported MA features

| Feature | Description |
|---|---|
| `SEARCH` | Tracks, albums, artists, playlists |
| `ARTIST_TOPTRACKS` | Fetch an artist's top songs via YTM API |
| `ARTIST_ALBUMS` | Fetch an artist's discography via YTM API |

#### Media type mapping

| MA type | YTM object | item\_id format |
|---|---|---|
| `Track` | `MusicTrack` / `MusicVideo` | `https://music.youtube.com/watch?v=<id>` |
| `Album` | `MusicAlbum` | `ytm:album:<browseId>` |
| `Artist` | `MusicArtist` | `ytm:artist:<browseId>` |
| `Playlist` | `MusicPlaylist` | `ytm:playlist:<browseId>` |

#### Methods

| Method | What it does |
|---|---|
| `search(query, media_types, limit)` | Searches YTM for each requested media type |
| `get_album(id)` | Fetches full album metadata by browseId |
| `get_album_tracks(id)` | Fetches album track listing |
| `get_artist(id)` | Fetches artist metadata by browseId |
| `get_artist_toptracks(id)` | Fetches artist's top tracks |
| `get_artist_albums(id)` | Fetches artist's album list |
| `get_playlist(id)` | Fetches playlist metadata |
| `get_playlist_tracks(id, page)` | Fetches playlist track listing |
| `get_track(id)` | yt-dlp metadata fallback for unknown watch URLs |
| `get_stream_details(id, media_type)` | Resolves watch URL to audio stream via yt-dlp |

#### Internal caches

- `_track_cache: dict[str, Track]` — avoids redundant yt-dlp calls for tracks seen in search results.
- `_album_playlist_map: dict[browseId, playlistId]` — albums on YTM have both a browse ID and a playlist ID; storing the map lets `get_album()` use the faster `get_playlist()` path instead of `get_album()`.

---

### `tutubo_youtube` — Generic YouTube

**Source:** `youtube_ma_provider/youtube/__init__.py`

#### Supported MA features

| Feature | Description |
|---|---|
| `SEARCH` | Videos (tracks), channels (artists), playlists, audiobooks, podcasts, live radio |
| `ARTIST_TOPTRACKS` | Channel's latest uploads as tracks |

#### Media type mapping

| MA type | YouTube concept | item\_id format |
|---|---|---|
| `Track` | Any video | `https://www.youtube.com/watch?v=<id>` |
| `Artist` | Channel | `https://www.youtube.com/channel/<id>` |
| `Playlist` | Playlist | `https://www.youtube.com/playlist?list=<id>` |
| `Audiobook` | Video classified as AUDIOBOOK | `https://www.youtube.com/watch?v=<id>` |
| `Radio` | Live stream (LIVE\_RADIO or LIVE\_NEWS) | `https://www.youtube.com/watch?v=<id>` |
| `PodcastEpisode` | Video classified as PODCAST | `https://www.youtube.com/watch?v=<id>` |

> Audiobook/podcast/radio classification is done by `tutubo`'s content-type classifier, which analyses video titles, descriptions, and channel tags. It is heuristic — expect occasional misses.

#### Methods

| Method | What it does |
|---|---|
| `search(query, media_types, limit)` | Searches YouTube for each requested media type |
| `get_artist(channel_url)` | Fetches channel metadata via `Channel(url)` page scrape |
| `get_artist_toptracks(channel_url)` | Returns `Channel.videos()` as Tracks |
| `get_playlist(playlist_url)` | Fetches playlist metadata via `Playlist(url)` |
| `get_playlist_tracks(playlist_url, page)` | Returns `Playlist.videos()` as Tracks |
| `get_track(watch_url)` | yt-dlp metadata fallback |
| `get_stream_details(id, media_type)` | Resolves to audio stream; live streams have `can_seek=False` |

#### How search uses factory queries

For content-type filtered results, `tutubo` provides factory classmethods that append keywords to the query before searching YouTube, improving classification accuracy:

| Media type | Factory used | Effect |
|---|---|---|
| `AUDIOBOOK` | `YoutubeSearch.for_audiobooks(query)` | Appends `"full audiobook"` |
| `PODCAST` | `YoutubeSearch.for_podcasts(query)` | Appends `"podcast"` |
| `TRACK`, `ARTIST`, `PLAYLIST`, `RADIO` | `YoutubeSearch(query)` (plain) | No modification |

Radio uses a plain search so that `iterate_live_radio()` and `iterate_live_news()` both get unbiased results on the same query.

---

## Following a YouTube channel

This is the main feature of `tutubo_youtube` beyond music. Any YouTube channel can be followed as an MA Artist, and its uploads become a playable track list.

### Step by step

1. In MA, open **Search** and type the channel name (e.g. `Stoned Meadow Of Doom`).
2. Switch the result filter to **Artists**.
3. The channel appears as an artist card with its avatar.
4. Click the heart / follow button.
5. The channel is now in your MA library under **Artists**.
6. Opening the artist page shows its latest uploads (via `get_artist_toptracks`) as a playable list.

### What `get_artist_toptracks` actually does

```python
ch = Channel("https://www.youtube.com/channel/<id>")
videos = list(ch.videos())   # scrapes the channel's /videos tab
# each Video → Track with watch_url as item_id
```

`Channel(url)` scrapes the channel page — it is not cached. Each visit to the artist page in MA makes a fresh HTTP request to YouTube. This is intentional (always fresh uploads) but means it is as slow as a page load. If you see MA taking a few seconds to open a channel's track list, this is why.

### Using a vanity URL directly

You can also navigate directly to a channel's artist page if you know its URL. In MA's search, paste the channel URL:

```
https://www.youtube.com/@StonedMeadowOfDoom
```

MA will call `get_artist()` with that URL, which passes it straight to `Channel(url)` — tutubo handles both `/channel/<id>` and `/@handle` URL forms.

---

## Architecture deep-dive

### How providers are discovered

Music Assistant has a hard-coded set of built-in providers. Adding an external one normally requires a pull request to the MA core repository. `music-assistant-plugin-manager` works around this by patching MA's provider discovery at process start.

The patch works by monkey-patching the private method `_MusicAssistant__load_provider_manifests` (Python name-mangling of `__load_provider_manifests`) to also scan the `music_assistant.provider` setuptools entrypoint group before MA does its own discovery.

The entrypoints declared in `pyproject.toml`:

```toml
[project.entry-points."music_assistant.provider"]
tutubo_music   = "youtube_ma_provider.ytmusic"
tutubo_youtube = "youtube_ma_provider.youtube"
```

Each value is a Python module path. MA expects every provider module to:

1. Contain a `manifest.json` as a package data file in the same directory.
2. Export `async def setup(mass, manifest, config) -> ProviderInstanceType`.
3. Export `async def get_config_entries(...) -> tuple[ConfigEntry, ...]`.

### Manifest files

Each sub-provider has its own `manifest.json`:

**`ytmusic/manifest.json`**
```json
{
  "type": "music",
  "domain": "tutubo_music",
  "name": "YouTube Music (no login)",
  "stage": "beta",
  "requirements": ["tutubo", "yt-dlp"]
}
```

**`youtube/manifest.json`**
```json
{
  "type": "music",
  "domain": "tutubo_youtube",
  "name": "YouTube (no login)",
  "stage": "beta",
  "requirements": ["tutubo", "yt-dlp"]
}
```

The `domain` field is the stable identifier MA uses internally. It appears in all `ProviderMapping` objects and in `self.domain` inside the provider class.

### Stream resolution flow

```
MA calls get_stream_details(item_id="https://www.youtube.com/watch?v=XYZ", media_type=TRACK)
  │
  └─ asyncio.to_thread(_extract_stream_url, "https://...XYZ")
       │
       └─ yt_dlp.YoutubeDL({"format": "bestaudio/best", "skip_download": True})
            └─ extract_info(url, download=False)
                 └─ returns {"url": "https://...googlevideo.com/...", ...}
                      │
                      └─ StreamDetails(stream_type=HTTP, path=direct_url)
                               └─ MA fetches and plays the direct URL
```

yt-dlp is invoked once per play. The resulting `googlevideo.com` URL is ephemeral (expires in minutes), so it is never cached.

### Why item\_id is a URL

In `tutubo_music`, item IDs are opaque strings like `ytm:album:<browseId>`. In `tutubo_youtube`, item IDs are full YouTube URLs. This is deliberate:

- `Channel(url)` and `Playlist(url)` take a URL directly — no ID-to-URL mapping needed.
- `get_stream_details` passes `item_id` straight to yt-dlp, which accepts any YouTube URL — no lookup step.

The trade-off: item IDs in `tutubo_youtube` are longer but the provider methods are simpler.

### Package layout

```
youtube_ma_provider/
├── __init__.py          # empty package root
├── ytmusic/
│   ├── __init__.py      # YouTubeMusicProvider class + setup() + converters
│   └── manifest.json    # domain: tutubo_music
└── youtube/
    ├── __init__.py      # YouTubeProvider class + setup() + converters
    └── manifest.json    # domain: tutubo_youtube
```

Both sub-packages are self-contained — they share no code with each other. This is intentional: they use completely different tutubo APIs and MA media types, and keeping them separate makes each file independently readable.

### Dependency graph

```
youtube-ma-provider
├── music-assistant-plugin-manager   ← patches MA discovery at startup
├── tutubo
│   ├── tutubo.search.YoutubeMusicSearch   ← used by ytmusic provider
│   ├── tutubo.search.YoutubeSearch        ← used by youtube provider
│   ├── tutubo.channel.Channel             ← used by youtube provider
│   ├── tutubo.channel.Playlist            ← used by youtube provider
│   └── tutubo.ytmus.*                     ← MusicTrack, MusicAlbum, etc.
└── yt-dlp                           ← stream URL extraction (both providers)
```

---

## Development guide

### Set up a dev environment

```bash
git clone https://github.com/TigreGotico/youtube-ma-provider
cd youtube-ma-provider
pip install -e .
```

If you also want to hack on tutubo itself:

```bash
git clone https://github.com/TigreGotico/tutubo
pip install -e ../tutubo
```

### Run MA with your local provider

```bash
mass-pm
```

Changes to the Python source take effect on the next restart — no reinstall needed when installed with `-e`.

### Adding a new media type

Say you want to support `MediaType.MOVIE` in `tutubo_youtube`:

1. **Add a converter** in `youtube/__init__.py`:
   ```python
   def _to_movie(v, domain, instance_id) -> Track:
       # MA has no Movie type; map to Track or Audiobook depending on your use case
       ...
   ```

2. **Add a branch in `search()`**:
   ```python
   if MediaType.TRACK in media_types:
       for v in s.iterate_movies(max_res=limit):
           tracks.append(v)  # or a separate list if you add a new type
   ```

3. No manifest changes needed — MA infers supported media types from `SUPPORTED_FEATURES` and which `get_*` methods you implement.

### Adding a new provider domain

To ship a third provider domain in this package:

1. Create `youtube_ma_provider/mysite/` with `__init__.py` and `manifest.json`.
2. Add the entrypoint to `pyproject.toml`:
   ```toml
   [project.entry-points."music_assistant.provider"]
   mysite = "youtube_ma_provider.mysite"
   ```
3. Add package data:
   ```toml
   [tool.setuptools.package-data]
   "youtube_ma_provider.mysite" = ["manifest.json"]
   ```
4. Reinstall: `pip install -e .`

### Key tutubo APIs used

**`YoutubeMusicSearch(query)`** — searches music.youtube.com:
```python
s = YoutubeMusicSearch("Black Sabbath")
for track in s.iterate_tracks(max_res=10):
    print(track.title, track.artist, track.watch_url)
for album in s.iterate_albums(max_res=5):
    print(album.title, album._raw_data.get("browseId"))
```

**`YoutubeSearch(query)`** — searches youtube.com:
```python
s = YoutubeSearch("doom metal")
for v in s.iterate_videos(max_res=10):
    print(v.title, v.watch_url, v.content_type)
for ch in s.iterate_channels(max_res=5):
    print(ch.title, ch.channel_url)
for pl in s.iterate_playlists(max_res=5):
    print(pl.title, pl.playlist_url)
```

**`Channel(url)`** — fetches a channel page:
```python
ch = Channel("https://www.youtube.com/@StonedMeadowOfDoom")
print(ch.channel_name, ch.subscribers)
for v in ch.videos():      # channel's /videos tab
    print(v.title, v.watch_url)
for v in ch.live():        # active live streams
    print(v.title)
```

**`Playlist(url)`** — fetches a playlist:
```python
pl = Playlist("https://www.youtube.com/playlist?list=PLxxx")
for v in pl.videos():
    print(v.title, v.watch_url)
```

### yt-dlp options

Both providers share the same yt-dlp options:
```python
_YTDLP_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "format": "bestaudio/best",   # prefer audio-only formats
    "skip_download": True,        # extract URL, don't download
}
```

To prefer a specific format (e.g. opus at 160kbps), change `format`:
```python
"format": "bestaudio[ext=webm]/bestaudio/best"
```

yt-dlp is imported lazily inside `_extract_stream_url` and `get_track` — if it is not installed, the provider raises `ProviderUnavailableError` on first use rather than at import time.

---

## Troubleshooting

### Provider doesn't appear in MA

The plugin manager must be active. Check:
```bash
python -c "
from music_assistant_plugin_manager.entrypoints import scan_entrypoints
print(scan_entrypoints())
"
```
You should see `{'tutubo_music': 'youtube_ma_provider.ytmusic', 'tutubo_youtube': 'youtube_ma_provider.youtube'}`.

If the dict is empty, the package is not installed or the entrypoints are not registered. Run `pip install -e .` from the repo root.

### Playback fails / "Could not resolve stream"

yt-dlp is rate-limited by YouTube and occasionally breaks when YouTube updates its player JS. Fix:
```bash
pip install -U yt-dlp
```

If it still fails for a specific video, test yt-dlp directly:
```bash
yt-dlp -f bestaudio/best --get-url "https://www.youtube.com/watch?v=<id>"
```

### Search returns no results

tutubo scrapes YouTube's public search page. YouTube A/B tests its page structure — if tutubo's parser hits an unknown layout variant it returns nothing. Open an issue at [tutubo](https://github.com/TigreGotico/tutubo) with the search query that fails.

### Channel artist page is slow to load

`Channel(url)` scrapes the channel page on every call. A channel with many videos may take 2–5 seconds. This is a network round-trip to YouTube, not a code bug. Upstream caching in tutubo or MA would be the fix.

### Audiobooks / podcasts not appearing in search

Content-type classification is heuristic. A video titled "Full Album" on a metal channel may classify as `MUSIC_AUDIO` rather than `AUDIOBOOK`. The classifier looks at title keywords, description, and channel tags — if all three are ambiguous, it defaults to the most common type for that channel.

To debug classification directly:
```python
from tutubo.content_type import classify_video
ct = classify_video(title="Lord of the Rings Audiobook Full", description="", is_live=False, channel_tags=[])
print(ct)  # ContentType.AUDIOBOOK
```

### YouTube Music results differ from the website

YouTube Music's API (via `ytmusicapi`) may return different results than the website depending on region. tutubo's `_get_ytmus()` initialises `ytmusicapi` in unauthenticated mode, which may exclude region-locked content.
