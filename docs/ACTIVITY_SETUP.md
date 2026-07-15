# The Oracle — Discord Activity setup

The web play surface (`activity-ui/`) can run two ways:

1. **Embedded Activity** — runs *inside Discord* (the rocket/Activities tray),
   launched from the entry channel. Needs the setup below.
2. **Browser link** — the same UI opened in a normal browser tab. Works with no
   Discord setup; used as a fallback and for local testing.

Both are served by the backend at the same origin. This doc covers getting the
**embedded** path live.

---

## 1. Credentials (already wired in code)

The Discord **application** is the same one the bot uses. Two values:

| Value | Public? | Where it must live |
|-------|---------|--------------------|
| Client ID | yes (ships in the browser bundle) | `ai-dm-sicord-bot/cred.env` (`ORACLE_DM_CLIENT_ID`), `oracle-dm-backend/backend-cred.env` (`ORACLE_DM_CLIENT_ID`), `activity-ui/.env` (`VITE_DISCORD_CLIENT_ID`) |
| Client Secret | **no — server only** | `oracle-dm-backend/backend-cred.env` (`ORACLE_DM_CLIENT_SECRET`) |

- The **backend** does the OAuth code→token exchange (`POST /api/token`), so it
  needs both id **and** secret.
- The **bot** needs the id to mint the embedded-app invite from the entry channel.
- The **client** bakes the id in at build time (`activity-ui/.env`). Rebuild the
  UI after changing it (`npm run build`).

All three files are gitignored. The client secret must never be committed.

---

## 2. Public HTTPS URL

Discord loads the Activity in an iframe **through its own proxy** — it cannot
reach `localhost`. You need a public HTTPS URL that forwards to the backend on
`127.0.0.1:8000`.

`The Oracle.exe` starts cloudflared automatically (`launcher/run_cloudflared.bat`)
and **prints the Activity URL** in the launcher window. It runs in one of two
modes and is **torn down with the rest of the game** on shutdown — including when
you close the launcher window with the **X** button (a Windows console-close
handler kills the tunnel/backend/bot, not just Ctrl+C).

cloudflared was installed with `winget install --id Cloudflare.cloudflared`. Set
`ORACLE_START_CLOUDFLARE=0` to skip the built-in tunnel if you run your own.

### 2a. Named tunnel — stable URL (recommended)

A named tunnel gives a hostname that **never changes**, so you set the Discord
URL mapping **once**. One-time setup (needs a domain on a Cloudflare account):

```bash
cloudflared tunnel login                       # browser auth; pick your domain
cloudflared tunnel create oracle               # creates ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns oracle oracle.<yourdomain>
```

Create `%USERPROFILE%\.cloudflared\config.yml`:
```yaml
tunnel: oracle
credentials-file: C:\Users\holyp\.cloudflared\<UUID>.json
ingress:
  - hostname: oracle.<yourdomain>
    service: http://localhost:8000
  - service: http_status:404
```

Then tell the launcher to use it (persistent env vars):
```bat
setx ORACLE_TUNNEL_NAME oracle
setx ORACLE_TUNNEL_HOSTNAME oracle.<yourdomain>
```

Now every launch runs `cloudflared tunnel run oracle`, the launcher prints the
fixed `https://oracle.<yourdomain>`, and you never touch the Dev Portal mapping
again. (Open a fresh terminal/launcher after `setx` so the vars are picked up.)

### 2b. Quick tunnel — fallback (no setup)

With no `ORACLE_TUNNEL_NAME` set, the launcher opens a quick tunnel and prints a
**new random** `https://<random>.trycloudflare.com` each launch:

```
============================================================
  DISCORD ACTIVITY URL  (set this in the Developer Portal)
============================================================
    https://random-words.trycloudflare.com
    prefix  /   ->   target  random-words.trycloudflare.com
============================================================
```

Because the host changes every run, you must re-paste it into the Dev Portal
mapping (step 3) each time — which is exactly why 2a is recommended.

### Deploy (alternative)
Host the backend somewhere with a stable HTTPS domain and use that instead.

---

## 3. Discord Developer Portal

<https://discord.com/developers/applications> → your app:

1. **Activities → Settings**: enable **Activities**.
2. **Activities → URL Mappings**: add a root mapping
   - **Prefix**: `/`
   - **Target**: your public host from step 2 (e.g. `random-words.trycloudflare.com`)

   The backend serves the SPA at `/` (for the iframe), the WebSocket at
   `/ws/activity/{channel}`, and the token exchange at `/api/token` — all
   same-origin, so the single `/` mapping covers everything.
3. **OAuth2**: the embedded flow uses the SDK's `authorize`; no redirect URI
   needs to be added for the Activity itself. Scopes used: `identify`, `guilds`.
4. (Optional) **App → Installation / Default Install Settings**: make sure the
   app is installed to your test guild so it appears in the Activities shelf.

---

## 4. Launching

1. Run `The Oracle.exe` — it starts the backend, bot, and Cloudflare tunnel, and
   prints the **Activity URL**. Put that host in the Dev Portal URL mapping
   (step 3) if it changed since last launch.
2. In `#enter-the-world-of-gatvorhain`, send any message → the bot posts
   **Enter the Oracle**.
3. **Join a voice channel**, then press **Enter the Oracle**. The bot creates an
   embedded-app invite for that voice channel and gives you a **Launch The
   Oracle** button. Click it → the Activity opens inside the call.

Character creation, resume, and play all happen in the Activity. The old
channel-based character wizard and `!enterworld` text flow are retired (see
`MODULE_ARCHITECTURE.md`).

---

## 5. Browser fallback / local testing

Open the UI directly, passing context as query params:
```
http://127.0.0.1:8000/activity/?channel=<channelId>&user_id=<you>&username=<name>
```
This skips the Discord handshake (no `frame_id`), so it uses the URL params and
talks to the same backend WebSocket. Handy for iterating on the UI without a
tunnel.

---

## Troubleshooting

- **Blank iframe / CSP errors**: the URL mapping target is wrong, or the backend
  isn't reachable over HTTPS. Re-check step 2/3.
- **"Activity OAuth not configured"** from `/api/token`: client id/secret missing
  from `backend-cred.env`.
- **Button says "join a voice channel first"**: you must be connected to voice —
  embedded Activities can't launch from a text channel alone.
- **Invite fails (Forbidden)**: the bot needs **Create Invite** on the voice
  channel.
