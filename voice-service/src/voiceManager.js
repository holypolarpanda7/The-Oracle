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
import { createAudioProcess, resolveTitle } from './resolver.js';

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
    this.currentProcess = null;
    this.currentResource = null;
    this.stopping = false;

    this.player.on(AudioPlayerStatus.Idle, () => this._onIdle());
    this.player.on('error', (err) => {
      console.error(`[voice:${this.guildId}] player error: ${err.message}`);
      this._advance();
    });
  }

  async _ensureConnection(channelId) {
    const guild = await this.client.guilds.fetch(this.guildId);
    if (this.connection && this.channelId === channelId &&
        this.connection.state.status !== VoiceConnectionStatus.Destroyed) {
      return;
    }
    // Different channel or no connection -> (re)join.
    if (this.connection) {
      try { this.connection.destroy(); } catch { /* ignore */ }
      this.connection = null;
    }
    this.connection = joinVoiceChannel({
      guildId: this.guildId,
      channelId,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false,
      selfMute: false,
    });
    this.connection.on('error', (err) => {
      console.error(`[voice:${this.guildId}] connection error: ${err.message}`);
    });
    this.connection.on(VoiceConnectionStatus.Disconnected, async () => {
      // Try a quick reconnect; if it fails, tear down.
      try {
        await Promise.race([
          entersState(this.connection, VoiceConnectionStatus.Signalling, 5_000),
          entersState(this.connection, VoiceConnectionStatus.Connecting, 5_000),
        ]);
      } catch {
        this.destroy();
      }
    });
    this.channelId = channelId;
    this.connection.subscribe(this.player);
    await entersState(this.connection, VoiceConnectionStatus.Ready, 20_000);
  }

  _startCurrent() {
    if (this.index < 0 || this.index >= this.queue.length) return false;
    const query = this.queue[this.index];
    // Kill any lingering process before starting a new one.
    this._killProcess();
    const proc = createAudioProcess(query);
    this.currentProcess = proc;
    proc.on('error', (err) => {
      console.error(`[voice:${this.guildId}] yt-dlp spawn error: ${err.message}`);
    });
    const resource = createAudioResource(proc.stdout, {
      inputType: StreamType.Arbitrary,
      inlineVolume: true,
    });
    resource.volume?.setVolume(this.volume / 100);
    this.currentResource = resource;
    this.player.play(resource);
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
    if (this.currentProcess) {
      try { this.currentProcess.kill('SIGKILL'); } catch { /* ignore */ }
      this.currentProcess = null;
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
    this.currentResource?.volume?.setVolume(this.volume / 100);
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
  return gv.play({ channelId: String(channelId), tracks, loop, volume });
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
