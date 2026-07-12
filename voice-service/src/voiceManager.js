// Per-guild voice playback manager. Owns the DAVE-encrypted voice connection and
// an audio player, plus a looping queue of resolved tracks.
import {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  StreamType,
  AudioPlayerStatus,
  VoiceConnectionStatus,
  NoSubscriberBehavior,
  entersState,
} from '@discordjs/voice';
import { createAudioPipeline, resolveTitle } from './resolver.js';

// One GuildVoice per guild currently in use.
const guilds = new Map();

class GuildVoice {
  constructor(client, guildId) {
    this.client = client;
    this.guildId = guildId;
    this.channelId = null;
    this.connection = null;
    this.player = createAudioPlayer({
      behaviors: { noSubscriber: NoSubscriberBehavior.Play },
    });
    this.queue = [];
    this.index = 0;
    this.loop = true;
    this.volume = 50;
    this.currentTitle = null;
    this.currentPipeline = null;
    this.currentResource = null;
    this.stopping = false;

    this.player.on(AudioPlayerStatus.Idle, () => this._onIdle());
    this.player.on('error', (err) => {
      console.error(`[voice:${this.guildId}] player error: ${err.message}`);
      this._advance();
    });
    this.player.on(AudioPlayerStatus.Playing, () => {
      console.log(`[voice:${this.guildId}] player status -> playing`);
    });
    this.player.on(AudioPlayerStatus.Buffering, () => {
      console.log(`[voice:${this.guildId}] player status -> buffering`);
    });
  }

  async _ensureConnection(channelId) {
    const guild = await this.client.guilds.fetch(this.guildId);
    // Reuse an existing connection only if it's actually Ready on this channel.
    if (this.connection && this.channelId === channelId &&
        this.connection.state.status === VoiceConnectionStatus.Ready) {
      return;
    }
    // Otherwise, (re)join with retries until the connection reaches Ready.
    const maxAttempts = 5;
    let lastErr = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      // Tear down any prior/failed connection first.
      if (this.connection) {
        try { this.connection.destroy(); } catch { /* ignore */ }
        this.connection = null;
      }
      const connection = joinVoiceChannel({
        guildId: this.guildId,
        channelId,
        adapterCreator: guild.voiceAdapterCreator,
        selfDeaf: false,
        selfMute: false,
      });
      this.connection = connection;
      this.channelId = channelId;
      this._wireConnection(connection, channelId);
      connection.subscribe(this.player);
      try {
        // Shorter per-attempt timeout so a stalled join retries quickly instead
        // of leaving the user staring at ~20s of apparent silence.
        await entersState(connection, VoiceConnectionStatus.Ready, 7_000);
        console.log(`[voice:${this.guildId}] connection Ready on attempt ${attempt}`);
        return;
      } catch (err) {
        lastErr = err;
        console.error(
          `[voice:${this.guildId}] join attempt ${attempt}/${maxAttempts} failed to reach Ready ` +
            `(status=${connection.state.status}): ${err.message}`
        );
        try { connection.destroy(); } catch { /* ignore */ }
        this.connection = null;
        // Brief backoff before retrying.
        await new Promise((r) => setTimeout(r, 400));
      }
    }
    throw new Error(
      `Voice connection failed to reach Ready after ${maxAttempts} attempts: ${lastErr?.message || 'unknown'}`
    );
  }

  _wireConnection(connection, channelId) {
    connection.on('error', (err) => {
      console.error(`[voice:${this.guildId}] connection error: ${err.message}`);
    });
    connection.on('stateChange', (oldState, newState) => {
      console.log(
        `[voice:${this.guildId}] connection ${oldState.status} -> ${newState.status}`
      );
    });
    connection.on(VoiceConnectionStatus.Ready, () => {
      console.log(`[voice:${this.guildId}] voice connection ready (channel ${channelId})`);
      try {
        const netState = connection.state?.networking?.state;
        const enc = netState?.connectionData?.encryptionMode;
        console.log(`[voice:${this.guildId}] encryptionMode=${enc || 'unknown'}`);
      } catch (e) {
        console.log(`[voice:${this.guildId}] could not read encryption state: ${e.message}`);
      }
    });
    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      // Try a quick reconnect; if it fails, tear down.
      try {
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
        ]);
      } catch {
        this.destroy();
      }
    });
  }

  _startCurrent() {
    if (this.index < 0 || this.index >= this.queue.length) return false;
    const query = this.queue[this.index];
    // Kill any lingering process before starting a new one.
    this._killProcess();
    const pipeline = createAudioPipeline(query, this.volume);
    this.currentPipeline = pipeline;
    const resource = createAudioResource(pipeline.stream, {
      // ffmpeg emits Ogg-wrapped Opus; the player demuxes without re-encoding.
      inputType: StreamType.OggOpus,
      // Inline volume adds a per-frame transform and can increase jitter/chop.
      inlineVolume: false,
    });
    this.currentResource = resource;
    // Prebuffer: wait for an actual byte cushion (not just elapsed time) before
    // starting playback so short upstream jitter doesn't immediately starve audio.
    const startToken = Symbol('start');
    this._startToken = startToken;
    if (this._prebufferTimer) clearTimeout(this._prebufferTimer);
    const minPrebufferBytes = 192 * 1024;
    const maxPrebufferMs = 4_000;
    const pollMs = 100;
    const startedAt = Date.now();
    const startWhenBuffered = () => {
      // Guard against a newer track having started during the prebuffer wait.
      if (this._startToken !== startToken || this.stopping) return;
      const bufferedBytes = Number(pipeline.stream?.readableLength || 0);
      const waitedMs = Date.now() - startedAt;
      if (bufferedBytes >= minPrebufferBytes || waitedMs >= maxPrebufferMs) {
        console.log(
          `[voice:${this.guildId}] prebuffer ${bufferedBytes} bytes after ${waitedMs}ms`
        );
        this.player.play(resource);
        return;
      }
      this._prebufferTimer = setTimeout(startWhenBuffered, pollMs);
    };
    this._prebufferTimer = setTimeout(startWhenBuffered, pollMs);
    // Resolve a human-friendly title in the background for logging/status.
    resolveTitle(query).then((title) => {
      this.currentTitle = title;
      console.log(`[voice:${this.guildId}] now playing: ${title}`);
    }).catch(() => {});
    return true;
  }

  _onIdle() {
    if (this.stopping) return;
    this._advance();
  }

  _advance() {
    if (this.stopping) return;
    if (this.queue.length === 0) return;
    this.index += 1;
    if (this.index >= this.queue.length) {
      if (this.loop) {
        this.index = 0;
      } else {
        this.stop();
        return;
      }
    }
    this._startCurrent();
  }

  _killProcess() {
    if (this._prebufferTimer) {
      clearTimeout(this._prebufferTimer);
      this._prebufferTimer = null;
    }
    this._startToken = null;
    if (this.currentPipeline) {
      try { this.currentPipeline.kill(); } catch { /* ignore */ }
      this.currentPipeline = null;
    }
  }

  async play({ channelId, tracks, loop = true, volume = 50 }) {
    this.stopping = false;
    await this._ensureConnection(channelId);
    this.queue = Array.isArray(tracks) ? tracks.filter(Boolean) : [];
    this.index = 0;
    this.loop = Boolean(loop);
    this.volume = clampVolume(volume);
    if (this.queue.length === 0) {
      throw new Error('No tracks provided');
    }
    const started = this._startCurrent();
    return { playing: started, track: this.queue[this.index] };
  }

  setVolume(volume) {
    this.volume = clampVolume(volume);
    // With inlineVolume disabled for playback stability, keep this for status only.
  }

  stop() {
    this.stopping = true;
    this._killProcess();
    try { this.player.stop(true); } catch { /* ignore */ }
    if (this.connection) {
      try { this.connection.destroy(); } catch { /* ignore */ }
      this.connection = null;
    }
    this.channelId = null;
    this.queue = [];
    this.index = 0;
    this.currentResource = null;
    this.currentTitle = null;
  }

  destroy() {
    this.stop();
    guilds.delete(this.guildId);
  }

  status() {
    return {
      connected: Boolean(this.connection) &&
        this.connection.state.status === VoiceConnectionStatus.Ready,
      channelId: this.channelId,
      playing: this.player.state.status === AudioPlayerStatus.Playing,
      track: this.currentTitle,
      volume: this.volume,
      queueLength: this.queue.length,
      loop: this.loop,
    };
  }
}

function clampVolume(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return 50;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function getGuildVoice(client, guildId) {
  let gv = guilds.get(guildId);
  if (!gv) {
    gv = new GuildVoice(client, guildId);
    guilds.set(guildId, gv);
  }
  return gv;
}

// Resolve a guildId from an explicit guildId or by looking up a channelId in the
// client's channel cache. Returns null if neither works.
async function resolveGuildId(client, { guildId, channelId }) {
  if (guildId) return String(guildId);
  if (channelId) {
    try {
      const channel = await client.channels.fetch(String(channelId));
      if (channel?.guildId) return String(channel.guildId);
    } catch { /* ignore */ }
  }
  return null;
}

export async function play(client, { guildId, channelId, tracks, loop, volume }) {
  const gid = await resolveGuildId(client, { guildId, channelId });
  if (!gid) throw new Error('Could not resolve guild from request');
  if (!channelId) throw new Error('channelId is required to join voice');
  const gv = getGuildVoice(client, gid);
  try {
    return await gv.play({ channelId: String(channelId), tracks, loop, volume });
  } catch (err) {
    // First connect/play after startup can race Discord voice state hydration and
    // throw an AbortError. Retry once quickly so callers don't need to.
    if (isTransientAbort(err)) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      return gv.play({ channelId: String(channelId), tracks, loop, volume });
    }
    throw err;
  }
}

function isTransientAbort(err) {
  const msg = String(err?.message || err || '').toLowerCase();
  const name = String(err?.name || '').toLowerCase();
  return name.includes('abort') || msg.includes('operation was aborted');
}

export async function stop(client, { guildId, channelId }) {
  const gid = await resolveGuildId(client, { guildId, channelId });
  if (!gid) return { stopped: false };
  const gv = guilds.get(gid);
  if (!gv) return { stopped: false };
  gv.stop();
  return { stopped: true };
}

export async function setVolume(client, { guildId, channelId, volume }) {
  const gid = await resolveGuildId(client, { guildId, channelId });
  if (!gid) return { ok: false };
  const gv = guilds.get(gid);
  if (!gv) return { ok: false };
  gv.setVolume(volume);
  return { ok: true, volume: gv.volume };
}

export async function status(client, { guildId, channelId }) {
  const gid = await resolveGuildId(client, { guildId, channelId });
  if (!gid) return { connected: false };
  const gv = guilds.get(gid);
  if (!gv) return { connected: false };
  return gv.status();
}

export function activeGuildCount() {
  return guilds.size;
}
