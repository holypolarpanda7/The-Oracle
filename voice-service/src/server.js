// HTTP control surface for the voice sidecar. The Python bot calls these
// endpoints to drive playback. Localhost-only by default; optional shared-secret
// auth via the X-Voice-Token header.
import express from 'express';
import { config } from './config.js';
import * as voice from './voiceManager.js';

export function createServer(client, getReady) {
  const app = express();
  app.use(express.json());

  // Optional shared-secret gate (skips /health so liveness checks stay cheap).
  app.use((req, res, next) => {
    if (req.path === '/health') return next();
    if (config.secret && req.get('X-Voice-Token') !== config.secret) {
      return res.status(401).json({ ok: false, error: 'unauthorized' });
    }
    next();
  });

  app.get('/health', (req, res) => {
    res.json({
      ok: true,
      ready: getReady(),
      dave: true,
      guilds: voice.activeGuildCount(),
    });
  });

  app.post('/play', async (req, res) => {
    try {
      const { guildId, channelId, tracks, loop, volume } = req.body || {};
      const result = await voice.play(client, {
        guildId,
        channelId,
        tracks,
        loop: loop === undefined ? true : loop,
        volume: volume === undefined ? config.defaultVolume : volume,
      });
      res.json({ ok: true, ...result });
    } catch (err) {
      console.error(`[server] /play failed: ${err.message}`);
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  app.post('/stop', async (req, res) => {
    try {
      const { guildId, channelId } = req.body || {};
      const result = await voice.stop(client, { guildId, channelId });
      res.json({ ok: true, ...result });
    } catch (err) {
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  app.post('/volume', async (req, res) => {
    try {
      const { guildId, channelId, volume } = req.body || {};
      const result = await voice.setVolume(client, { guildId, channelId, volume });
      res.json(result);
    } catch (err) {
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  app.get('/status', async (req, res) => {
    try {
      const result = await voice.status(client, {
        guildId: req.query.guildId,
        channelId: req.query.channelId,
      });
      res.json(result);
    } catch (err) {
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  return app;
}
