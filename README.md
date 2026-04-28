# youtube-ma-provider

YouTube Music provider for [Music Assistant](https://music-assistant.io) — search and stream YouTube Music tracks via `tutubo` and `yt-dlp`.

## Install

```bash
pip install youtube-ma-provider
```

## Usage

```bash
mass-pm   # instead of music-assistant
```

Once running, the YouTube Music provider appears automatically in Music Assistant's provider list.

## Provider domain

| Domain | Description |
|---|---|
| `tutubo` | YouTube Music search and streaming |

## Requirements

- `music-assistant-plugin-manager`
- `tutubo` — YouTube Music search client
- `yt-dlp` — stream URL extraction

## Part of plugin-managers

Powered by [plugin-managers](https://github.com/TigreGotico/plugin-managers) — entrypoint-based plugin discovery for Music Assistant and Home Assistant.
