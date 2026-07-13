# Oracle Voice Service (DAVE-capable sidecar)

Discord enforces the **DAVE** end-to-end-encryption voice protocol. Clients that
don't implement it are disconnected from voice with close code **4017**. Neither
`discord.py` nor Lavalink implements DAVE, so The Oracle's voice/music moved into
this small Node.js sidecar built on **`@discordjs/voice`**, which supports DAVE via
the pre-installed **`@snazzah/davey`** native binding.

The Python bot keeps doing everything else; it just calls this service's HTTP API
to join channels and play/stop music.

## Requirements
- Node.js **>= 22.12** (this repo tested on v23).
- FFmpeg and yt-dlp are bundled via `ffmpeg-static` and `youtube-dl-exec` — no
  system install needed.

## Install
```bash
cd voice-service
npm install
```
`npm install` also downloads the yt-dlp binary (network required, one-time).

## Run
Normally the Python bot spawns this automatically. To run it standalone:
```bash
npm start
```
The bot token is read from `../ai-dm-sicord-bot/cred.env` (`ORACLE_DM_TOKEN`) or
`DISCORD_TOKEN`. See `.env.example` for options.

## HTTP API (localhost)
| Method | Path       | Body / Query                                                     |
| ------ | ---------- | --------------------------------------------------------------- |
| GET    | `/health`  | → `{ ok, ready, dave, guilds }`                                 |
| POST   | `/play`    | `{ guildId, channelId, tracks: string[], loop?, volume? }`      |
| POST   | `/stop`    | `{ guildId?, channelId? }`                                       |
| POST   | `/volume`  | `{ guildId, volume }`                                            |
| GET    | `/status`  | `?guildId=` or `?channelId=`                                     |

`tracks` entries are either direct URLs or search phrases (a legacy
`ytsearch:` / `ytmsearch:` prefix is stripped and treated as a YouTube search).
