// Configuration + env loading for the voice-service sidecar.
// The Python bot passes DISCORD_TOKEN / VOICE_SERVICE_* via env when it spawns
// this process, but we also fall back to the bot's cred.env so the service can
// be run standalone during development.
import { config as loadEnv } from 'dotenv';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
// config.js lives in voice-service/src, so the repo root is two levels up.
const repoRoot = join(__dirname, '..', '..');

// Load the sidecar's own .env first (if present), then the bot's cred.env as a
// fallback source for the shared bot token. Existing env vars win.
loadEnv({ path: join(__dirname, '.env') });
const credEnv = join(repoRoot, 'ai-dm-sicord-bot', 'cred.env');
if (existsSync(credEnv)) {
  loadEnv({ path: credEnv });
}

export const config = {
  // Same bot token as the Python bot. cred.env stores it as ORACLE_DM_TOKEN.
  token: process.env.DISCORD_TOKEN || process.env.ORACLE_DM_TOKEN || '',
  port: Number(process.env.VOICE_SERVICE_PORT || 8790),
  host: process.env.VOICE_SERVICE_HOST || '127.0.0.1',
  // Optional shared secret; when set, callers must send it as X-Voice-Token.
  secret: process.env.VOICE_SERVICE_SECRET || '',
  // Default playback volume (0-100) when a request omits it.
  defaultVolume: Number(process.env.VOICE_SERVICE_DEFAULT_VOLUME || 50),
};

if (!config.token) {
  console.error(
    '[voice-service] No bot token found. Set DISCORD_TOKEN (or ORACLE_DM_TOKEN in ' +
      'ai-dm-sicord-bot/cred.env).'
  );
  process.exit(1);
}
