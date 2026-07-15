/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Discord application (client) id — public; used to init the Embedded App
      SDK when the UI runs inside Discord. Set in activity-ui/.env. */
  readonly VITE_DISCORD_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
