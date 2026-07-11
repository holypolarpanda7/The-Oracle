// Track resolution: turn a playlist entry (URL or search phrase) into a live
// audio stream using yt-dlp. yt-dlp is far more resilient to YouTube changes than
// any pure-JS extractor. youtube-dl-exec is used only to vendor/download the
// yt-dlp binary; we spawn it ourselves with an argv array so multi-word search
// phrases aren't split into multiple inputs (which youtube-dl-exec's helper does).
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const pkgRoot = dirname(require.resolve('youtube-dl-exec/package.json'));
export const YT_DLP_BIN = join(
  pkgRoot,
  'bin',
  process.platform === 'win32' ? 'yt-dlp.exe' : 'yt-dlp'
);

// A playlist line is either a direct URL or a search phrase (optionally carrying
// a legacy `ytsearch:` / `ytmsearch:` prefix from the old Lavalink format).
// Returns the single positional argument yt-dlp should receive.
export function normalizeQuery(raw) {
  const s = (raw || '').trim();
  if (!s) return '';
  if (s.includes('://')) return s; // direct URL -> pass through
  let q = s;
  for (const prefix of ['ytmsearch:', 'ytsearch:']) {
    if (q.toLowerCase().startsWith(prefix)) {
      q = q.slice(prefix.length).trim();
      break;
    }
  }
  // yt-dlp search syntax: grab the single best match for the whole phrase.
  return `ytsearch1:${q}`;
}

// Fetch lightweight metadata (title) for a resolved query. Best-effort; resolves
// to the raw query text on failure so playback logging never blocks on it.
export function resolveTitle(query) {
  const arg = normalizeQuery(query);
  if (!arg) return Promise.resolve(query);
  return new Promise((resolve) => {
    const p = spawn(YT_DLP_BIN, [
      arg, '-J', '--skip-download', '--no-warnings', '--no-playlist',
    ]);
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.on('error', () => resolve(query));
    p.on('close', () => {
      try {
        const info = JSON.parse(out);
        if (typeof info.title === 'string') return resolve(info.title);
        if (Array.isArray(info.entries) && info.entries[0]?.title) {
          return resolve(info.entries[0].title);
        }
      } catch {
        /* ignore */
      }
      resolve(query);
    });
  });
}

// Spawn a yt-dlp process that streams the best audio to stdout. The caller pipes
// stdout into @discordjs/voice, which transcodes to Opus via ffmpeg (ffmpeg-static).
// Returns the child process. Caller owns killing it.
export function createAudioProcess(query) {
  const arg = normalizeQuery(query);
  if (!arg) throw new Error('Empty audio query');
  const subprocess = spawn(
    YT_DLP_BIN,
    [
      arg,
      '-o', '-',                 // stream media to stdout
      '-f', 'bestaudio/best',
      '--no-playlist',
      '--no-warnings',
      '--quiet',
    ],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );
  // Drain stderr so the buffer never fills; only surface real errors.
  subprocess.stderr?.on('data', (chunk) => {
    const text = chunk.toString();
    if (/error/i.test(text)) {
      console.error(`[resolver] yt-dlp: ${text.trim()}`);
    }
  });
  return subprocess;
}
