# Music System Documentation

## Overview
The Oracle plays contextual background music in voice channels during character
creation and gameplay. Playback runs through the **Node voice sidecar**
(`voice-service/`) — Discord's DAVE (E2EE) voice protocol made discord.py and
Lavalink unusable for voice, so the sidecar (built on `@discordjs/voice` +
`@snazzah/davey`) owns the voice connection and the bot drives it over a small
localhost HTTP API (`music_player.py`).

## Track sources (three tiers, checked in order)

`music_player.load_playlist(mood)` resolves a mood's tracks in priority order:

1. **`ai-dm-sicord-bot/playlists/<mood>.txt`** — explicit overrides.
   One entry per line (`#` = comment). YouTube URLs, `ytsearch:` queries, or any
   direct HTTPS audio URL. If the file has any active line, it wins outright.
   *Currently `cc_menu.txt` and `character_complete.txt` contain YouTube
   entries, so those two moods use YouTube instead of local files — comment the
   lines out to fall through to the local library.*
2. **`voice-service/audio/<mood>/*.mp3|*.ogg`** — the local ambient library.
   Pre-downloaded from Freesound; streamed by the sidecar via
   `localfile:<path>` → ffmpeg directly (no network, no yt-dlp, instant start).
   Seed/refresh it with:
   ```bash
   uv run python scripts/download_tracks.py
   ```
   (~5 tracks per mood; needs `FREESOUND_API_KEY`.)
3. **Freesound live search** — if neither of the above yields tracks, the bot
   searches freesound.org by mood keywords (`freesound_client.MOOD_QUERIES`,
   with broader `MOOD_FALLBACK_QUERIES` tried when results are thin) and
   streams the HQ preview MP3s directly.

## AI-chosen scene music

The DM brain can set the mood itself: when a scene's location/tone changes the
LLM emits a hook in its narration:

```
[[MUSIC: dark dungeon tension]]
```

The backend strips the hook and returns the query in the `/chat` response
(`music` field); the bot then calls `music_player.play_query_in_channel()`,
which **searches Freesound first** and passes the resulting direct MP3 URLs to
the sidecar (looped). If Freesound has no match (or no API key is set), the raw
query falls back to the sidecar's yt-dlp path as a YouTube search.

## Moods / playlists

| Mood | Context |
|------|---------|
| `cc_menu` | Character creation menu |
| `character_complete` | Character successfully registered |
| `town` | Settlements |
| `tavern` | Taverns, inns, social scenes |
| `desert` | Desert / hot climates |
| `dungeon` | Underground, dark places |
| `combat` | Battle encounters |

Add a new mood by creating `voice-service/audio/<mood>/` with MP3s (or a
`playlists/<mood>.txt`), and optionally an entry in
`freesound_client.MOOD_QUERIES` so the live fallback and the seeding script
know what to search for.

## Configuration

- **`FREESOUND_API_KEY`** — free key from https://freesound.org/apiv2/apply.
  Lives in `ai-dm-sicord-bot/cred.env` (bot runtime) and
  `oracle-dm-backend/backend-cred.env` (used by `scripts/download_tracks.py`).
- **Volume** — passed per play call (`play_music_in_channel(..., volume=50)`,
  scene music defaults to 30); adjust live via
  `music_player.set_volume_in_channel(channel_id, volume)`.
- **Sidecar address** — `VOICE_SERVICE_HOST` / `VOICE_SERVICE_PORT` /
  `VOICE_SERVICE_URL`, optional `VOICE_SERVICE_SECRET` shared token.
- **Looping** — the sidecar loops the track list it was given (`loop: true`).

## Player & flow behavior

- `/enterworld` → voice channel created → `cc_menu` music starts when the
  player joins.
- Character registered → switches to `character_complete`.
- Players toggle music with the 🔊 / 🔇 reactions on the welcome message
  (`music_control.toggle_music`).
- Scene changes during play → `[[MUSIC: ...]]` hooks retarget the ambience.

## Troubleshooting

- **No music at all** — is the sidecar up? The bot spawns it automatically
  (`music_player.start_voice_service`); check `voice-service/voice-service.log`
  and `GET http://127.0.0.1:8790/health`. Node ≥ 22.12 required.
- **A mood is silent** — check the three tiers in order: does
  `playlists/<mood>.txt` have only comments? does `voice-service/audio/<mood>/`
  have files? is `FREESOUND_API_KEY` set?
- **Scene music sounds wrong** — the Freesound search is keyword-based; the
  LLM's `[[MUSIC: ...]]` phrasing drives it. Tighten the prompt guidance in
  `fastapi-dm.py` if it picks poorly.
- **YouTube entries fail** — the sidecar shells out to `yt-dlp`; make sure it's
  installed and current (`yt-dlp -U`).

## Architecture

```
oracle-dm-backend/fastapi-dm.py
└── [[MUSIC: ...]] hook extraction → "music" field in /chat response

ai-dm-sicord-bot/
├── music_player.py        # sidecar HTTP client + 3-tier playlist loading
├── music_control.py       # per-channel enable/disable + playlist switching
├── freesound_client.py    # Freesound search + download (moods & free queries)
├── event_handlers.py / game_session.py   # play scene music from /chat
└── playlists/*.txt        # tier-1 overrides

voice-service/             # Node sidecar (DAVE voice, ffmpeg playback)
├── src/resolver.js        # localfile:/direct-URL fast path, yt-dlp slow path
└── audio/<mood>/*.mp3     # tier-2 local ambient library (seeded from Freesound)

scripts/download_tracks.py # seed/refresh the local library
```
