// Track resolution: turn a playlist entry (URL or search phrase) into a live
// audio stream using yt-dlp. yt-dlp is far more resilient to YouTube changes than
// any pure-JS extractor. youtube-dl-exec is used only to vendor/download the
// yt-dlp binary; we spawn it ourselves with an argv array so multi-word search
// phrases aren't split into multiple inputs (which youtube-dl-exec's helper does).
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pipeline, PassThrough } from 'node:stream';

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
  // Local file: use the filename part as the title.
  if (isLocalFile(query)) {
    const p = localFilePath(query);
    return Promise.resolve(p.replace(/.*[\\/]/, '').replace(/\.[^.]+$/, ''));
  }
  // Direct audio URL: use the last path segment.
  if (isDirectAudioUrl(query)) {
    try {
      const seg = new URL(query).pathname.split('/').pop() || query;
      return Promise.resolve(decodeURIComponent(seg).replace(/\.[^.]+$/, ''));
    } catch { return Promise.resolve(query); }
  }
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
  // Fast path: local file or direct HTTPS audio URL — skip yt-dlp entirely.
  if (isLocalFile(query) || isDirectAudioUrl(query)) {
    return createDirectPipeline(query, volume);
  }
  // Slow path: let yt-dlp extract and stream the audio (YouTube etc.).
  return createYtdlpPipeline(query, volume);
}

// ---------------------------------------------------------------------------
// Fast path: local file or a direct HTTPS .mp3/.ogg/etc. URL.
// ffmpeg reads the source directly — no yt-dlp, instant start, no video risk.
// ---------------------------------------------------------------------------
function isLocalFile(s) {
  return typeof s === 'string' && s.startsWith('localfile:');
}

function localFilePath(s) {
  // Normalize Windows backslashes so ffmpeg is happy on all platforms.
  return s.slice('localfile:'.length).replace(/\\/g, '/');
}

function isDirectAudioUrl(s) {
  if (typeof s !== 'string') return false;
  try {
    const url = new URL(s);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return false;
    return /\.(mp3|ogg|opus|wav|flac|m4a)(\?.*)?$/i.test(url.pathname);
  } catch {
    return false;
  }
}

function createDirectPipeline(query, volume) {
  const src = isLocalFile(query) ? localFilePath(query) : query;
  let killed = false;
  const amplFactor = clampVolume(volume) / 100.0;

  const ffmpeg = spawn(
    ffmpegPath,
    [
      '-hide_banner', '-loglevel', 'error',
      '-i', src,
      '-vn', '-map', '0:a:0',
      '-af', `volume=${amplFactor}`,
      '-c:a', 'libopus',
      '-b:a', '224k', '-vbr', 'on', '-compression_level', '10',
      '-ar', '48000', '-ac', '2',
      '-frame_duration', '20', '-application', 'audio',
      '-f', 'ogg', 'pipe:1',
    ],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );

  const buffered = new PassThrough({ highWaterMark: 1 << 22 });
  pipeline(ffmpeg.stdout, buffered, (err) => {
    if (!err || killed || isBenignPipeError(err)) return;
    console.error(`[resolver] direct pipeline error: ${err.message}`);
  });
  ffmpeg.stderr?.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (text) console.error(`[resolver] ffmpeg(direct): ${text}`);
  });
  ffmpeg.on('error', (err) => {
    if (killed || isBenignPipeError(err)) return;
    console.error(`[resolver] ffmpeg(direct) process error: ${err.message}`);
  });

  const kill = () => {
    killed = true;
    try { ffmpeg.kill('SIGKILL'); } catch { /* ignore */ }
    try { buffered.destroy(); } catch { /* ignore */ }
  };
  return { stream: buffered, kill };
}

// ---------------------------------------------------------------------------
// Slow path: yt-dlp + ffmpeg for YouTube and other extractable URLs.
// ---------------------------------------------------------------------------
function createYtdlpPipeline(query, volume) {
  const arg = normalizeQuery(query);
  if (!arg) throw new Error('Empty audio query');
  let killed = false;

  const ytdlp = spawn(
    YT_DLP_BIN,
    [
      arg,
      '-o', '-',
      '-f', 'bestaudio[acodec=opus][vcodec=none]/bestaudio[vcodec=none]/bestaudio/best',
      '--no-playlist', '--no-warnings', '--quiet',
      '--extractor-args', 'youtube:player_client=android,web',
      '--retries', '10', '--fragment-retries', '10',
      '--extractor-retries', '5', '--retry-sleep', '2',
    ],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );

  const amplFactor = clampVolume(volume) / 100.0;
  const ffmpeg = spawn(
    ffmpegPath,
    [
      '-hide_banner', '-loglevel', 'error',
      '-thread_queue_size', '4096',
      '-i', 'pipe:0',
      '-vn', '-map', '0:a:0',
      '-af', `volume=${amplFactor}`,
      '-c:a', 'libopus',
      '-b:a', '224k', '-vbr', 'on', '-compression_level', '10',
      '-ar', '48000', '-ac', '2',
      '-frame_duration', '20', '-application', 'audio',
      '-f', 'ogg', 'pipe:1',
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] }
  );

  const buffered = new PassThrough({ highWaterMark: 1 << 22 });
  pipeline(ytdlp.stdout, ffmpeg.stdin, (err) => {
    if (!err || killed || isBenignPipeError(err)) return;
    console.error(`[resolver] stream pipeline error: ${err.message}`);
  });
  pipeline(ffmpeg.stdout, buffered, (err) => {
    if (!err || killed || isBenignPipeError(err)) return;
    console.error(`[resolver] buffer pipeline error: ${err.message}`);
  });

  ytdlp.stderr?.on('data', (chunk) => {
    const text = chunk.toString();
    if (/unable to write data: \[errno 22\] invalid argument/i.test(text)) return;
    if (/error/i.test(text)) console.error(`[resolver] yt-dlp: ${text.trim()}`);
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
    if (text.trim()) console.error(`[resolver] ffmpeg: ${text.trim()}`);
  });

  const kill = () => {
    killed = true;
    try { ytdlp.kill('SIGKILL'); } catch { /* ignore */ }
    try { ffmpeg.kill('SIGKILL'); } catch { /* ignore */ }
    try { buffered.destroy(); } catch { /* ignore */ }
  };
  return { stream: buffered, kill };
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
