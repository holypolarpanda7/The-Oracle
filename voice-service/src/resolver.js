// Track resolution: turn a playlist entry (URL or search phrase) into a live
// audio stream using yt-dlp. yt-dlp is far more resilient to YouTube changes than
// any pure-JS extractor. youtube-dl-exec is used only to vendor/download the
// yt-dlp binary; we spawn it ourselves with an argv array so multi-word search
// phrases aren't split into multiple inputs (which youtube-dl-exec's helper does).
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pipeline } from 'node:stream';

const require = createRequire(import.meta.url);
const ffmpegPath = require('ffmpeg-static');
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

// Build a live audio pipeline:
// yt-dlp stdout (container/audio stream) -> ffmpeg (48k stereo PCM s16le) -> Discord resource.
// Returns { stream, kill } where stream is ffmpeg stdout.
export function createAudioPipeline(query, volume = 50) {
  const arg = normalizeQuery(query);
  if (!arg) throw new Error('Empty audio query');
  let killed = false;

  const ytdlp = spawn(
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

  // Normalize volume to 0.0-1.0 amplitude before ffmpeg (avoids per-frame jitter).
  const amplFactor = clampVolume(volume) / 100.0;
  const ffmpeg = spawn(
    ffmpegPath,
    [
      '-hide_banner',
      '-loglevel', 'error',
      '-thread_queue_size', '4096',
      '-i', 'pipe:0',
      '-af', `volume=${amplFactor}`,
      '-f', 's16le',
      '-ar', '48000',
      '-ac', '2',
      'pipe:1',
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] }
  );

  // Media flow: yt-dlp -> ffmpeg stdin, with backpressure + handled pipe errors.
  pipeline(ytdlp.stdout, ffmpeg.stdin, (err) => {
    if (!err || killed || isBenignPipeError(err)) {
      return;
    }
    console.error(`[resolver] stream pipeline error: ${err.message}`);
  });

  // Drain stderr so the buffer never fills; only surface real errors.
  ytdlp.stderr?.on('data', (chunk) => {
    const text = chunk.toString();
    // Benign when we intentionally kill the process while switching tracks.
    if (/unable to write data: \[errno 22\] invalid argument/i.test(text)) {
      return;
    }
    if (/error/i.test(text)) {
      console.error(`[resolver] yt-dlp: ${text.trim()}`);
    }
  });

  ytdlp.on('error', (err) => {
    if (killed || isBenignPipeError(err)) return;
    console.error(`[resolver] yt-dlp process error: ${err.message}`);
  });

  ffmpeg.on('error', (err) => {
    if (killed || isBenignPipeError(err)) return;
    console.error(`[resolver] ffmpeg process error: ${err.message}`);
  });

  ffmpeg.stderr?.on('data', (chunk) => {
    const text = chunk.toString();
    if (text.trim()) {
      console.error(`[resolver] ffmpeg: ${text.trim()}`);
    }
  });

  const kill = () => {
    killed = true;
    try { ytdlp.kill('SIGKILL'); } catch { /* ignore */ }
    try { ffmpeg.kill('SIGKILL'); } catch { /* ignore */ }
  };

  return {
    stream: ffmpeg.stdout,
    kill,
  };
}

function clampVolume(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return 50;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function isBenignPipeError(err) {
  const code = String(err?.code || '').toUpperCase();
  const msg = String(err?.message || '').toLowerCase();
  return (
    code === 'EPIPE' ||
    code === 'ERR_STREAM_PREMATURE_CLOSE' ||
    msg.includes('write epipe') ||
    msg.includes('premature close')
  );
}
