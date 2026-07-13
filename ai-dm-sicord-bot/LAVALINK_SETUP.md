# Lavalink Music Setup

## What is Lavalink?

Lavalink is a standalone audio server that handles music streaming for Discord bots. It processes audio from YouTube, SoundCloud, and other sources, then streams it to Discord.

## Quick Start

### 1. Download Lavalink

Download the latest Lavalink.jar from:
https://github.com/lavalink-devs/Lavalink/releases

Save it to: `d:/The Oracle/ai-dm-sicord-bot/Lavalink.jar`

### 2. Install Java

Lavalink requires Java 17 or higher.

**Windows:**
```bash
# Check if Java is installed
java -version

# If not installed, download from:
# https://adoptium.net/temurin/releases/?version=17
```

### 3. Start the Bot (Automatic Method - Recommended)

**The bot now automatically starts and stops Lavalink!**

Just run the bot normally:
```bash
cd "d:/The Oracle/ai-dm-sicord-bot"
uv run python oracle-dm-discord-bot.py
```

The bot will:
- ✅ Automatically start Lavalink server in the background
- ✅ Wait for it to initialize
- ✅ Connect to Lavalink
- ✅ Automatically stop Lavalink when bot exits

You should see:
```
[Lavalink] Starting Lavalink server...
[Lavalink] Server started with PID 12345
[Lavalink] Waiting 10 seconds for server to initialize...
Bot is online as YourBot (ID: ...)
[Lavalink] Connected successfully
[Lavalink] Node Lavalink is ready!
```

### 3b. Manual Method (Optional)

If you prefer to run Lavalink separately:

**Terminal 1 - Start Lavalink:**
```bash
cd "d:/The Oracle/ai-dm-sicord-bot"
java -jar Lavalink.jar
```

**Terminal 2 - Start Bot:**
```bash
cd "d:/The Oracle/ai-dm-sicord-bot"
uv run python oracle-dm-discord-bot.py
```

## Playlist Configuration

Edit `playlists/cc_menu.txt` to customize the character creation music:

```
# CC Menu Playlist - Character Creation Background Music
https://www.youtube.com/watch?v=YOUR_VIDEO_ID
https://soundcloud.com/artist/track
https://example.com/direct-audio.mp3
```

- One URL per line
- Lines starting with `#` are comments
- Supports: YouTube, SoundCloud, Bandcamp, Twitch, direct URLs

## How It Works

1. Player joins voice channel → Bot auto-starts music
2. Music loops continuously
3. When channel is deleted → Music stops automatically
4. Volume is set to 30% by default

## Troubleshooting

**"Lavalink connection failed"**
- Make sure Lavalink is running (`java -jar Lavalink.jar`)
- Check that port 2333 is not blocked by firewall

**"No tracks in playlist"**
- Check `playlists/cc_menu.txt` exists
- Make sure URLs are valid and not commented out

**Music doesn't play**
- Check bot logs for `[music]` messages
- Verify bot has `Connect` and `Speak` permissions in voice channel
- Try restarting both Lavalink and the bot

**"Failed to load track"**
- Some YouTube videos are region-locked or age-restricted
- Try different URLs or use direct audio links

## Advanced Configuration

Edit `application.yml` to customize Lavalink:
- Change password (update in bot code too)
- Adjust buffer settings
- Enable/disable music sources
- Configure audio quality

## Production Tips

1. **Auto-restart Lavalink** with a process manager (PM2, systemd)
2. **Use a VPS** for 24/7 uptime
3. **Monitor logs** for performance issues
4. **Keep Lavalink updated** for bug fixes and new features
