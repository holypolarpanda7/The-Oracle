// Voice-service entry point. Logs in a lightweight discord.js client (same bot
// token as the Python bot, minimal intents) whose sole job is to own DAVE-encrypted
// voice connections, then exposes an HTTP control API for the Python bot.
import { Client, GatewayIntentBits } from 'discord.js';
import { generateDependencyReport } from '@discordjs/voice';
import { config } from './src/config.js';
import { createServer } from './src/server.js';

let ready = false;

const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
});

client.once('clientReady', () => {
  ready = true;
  console.log(`[voice-service] logged in as ${client.user.tag}`);
  const report = generateDependencyReport();
  console.log(report);
  if (/@snazzah\/davey: not found/i.test(report)) {
    console.error(
      '[voice-service] WARNING: @snazzah/davey not found. DAVE/E2EE voice will ' +
        'fail with close code 4017. Run `npm install` in voice-service/.'
    );
  }
});

client.on('error', (err) => console.error(`[voice-service] client error: ${err.message}`));

const app = createServer(client, () => ready);
const server = app.listen(config.port, config.host, () => {
  console.log(`[voice-service] HTTP control API on http://${config.host}:${config.port}`);
});

client.login(config.token).catch((err) => {
  console.error(`[voice-service] login failed: ${err.message}`);
  process.exit(1);
});

function shutdown() {
  console.log('[voice-service] shutting down...');
  try { server.close(); } catch { /* ignore */ }
  try { client.destroy(); } catch { /* ignore */ }
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
