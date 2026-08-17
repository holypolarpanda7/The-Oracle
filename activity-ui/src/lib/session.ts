// Resolves the play-session context — which table (channel) we're at and who
// the player is — from one of two runtime environments:
//
//   * Inside Discord (embedded Activity): the Discord Embedded App SDK does an
//     OAuth handshake. The channel comes from the SDK; the user from the
//     authenticated token. The client secret never touches the browser — the
//     backend's /api/token endpoint performs the code->token exchange.
//   * In a plain browser (the "Open The Oracle" link path): context comes from
//     the URL query params the bot put on the link.
//
// Discord tags the Activity iframe URL with `frame_id`, which is how we tell
// the two apart. See docs/ACTIVITY_SETUP.md.

export interface Session {
  channel: string;
  userId: string;
  username: string;
  embedded: boolean;
}

const CLIENT_ID = import.meta.env.VITE_DISCORD_CLIENT_ID;

function urlParam(name: string, dflt: string): string {
  return new URLSearchParams(location.search).get(name) ?? dflt;
}

function browserSession(): Session {
  return {
    channel: urlParam("channel", "1447775459533262868"),
    userId: urlParam("user_id", "activity-dev"),
    username: urlParam("username", "Adventurer"),
    embedded: false,
  };
}

// The live SDK, kept so the player has a way OUT: closing an embedded Activity
// is an RPC call on the same instance that opened it, and nothing else can do
// it — a Discord Activity has no window chrome of its own.
let sdk: Awaited<ReturnType<typeof importSdk>> | null = null;
const importSdk = async () => {
  const { DiscordSDK } = await import("@discord/embedded-app-sdk");
  return new DiscordSDK(String(CLIENT_ID));
};

/** Leave the Activity. Returns false when there is nothing we're allowed to
 *  close (a plain browser tab this script didn't open), so the caller can say
 *  so instead of leaving the player staring at an unchanged screen. */
export async function closeActivity(): Promise<boolean> {
  if (sdk) {
    try {
      const { RPCCloseCodes } = await import("@discord/embedded-app-sdk");
      sdk.close(RPCCloseCodes.CLOSE_NORMAL, "Left the table");
      return true;
    } catch (e) {
      console.error("[activity] close failed:", e);
    }
  }
  window.close();   // only works for a tab we opened; silently ignored otherwise
  return false;
}

export async function resolveSession(): Promise<Session> {
  const inDiscord = new URLSearchParams(location.search).has("frame_id");
  if (!inDiscord || !CLIENT_ID) return browserSession();

  try {
    sdk = await importSdk();
    await sdk.ready();

    const { code } = await sdk.commands.authorize({
      client_id: CLIENT_ID,
      response_type: "code",
      state: "",
      prompt: "none",
      scope: ["identify", "guilds"],
    });

    const res = await fetch("/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!res.ok) throw new Error(`token exchange HTTP ${res.status}`);
    const { access_token } = await res.json();

    const auth = await sdk.commands.authenticate({ access_token });
    const user = auth.user;
    return {
      channel: String(sdk.channelId ?? ""),
      userId: String(user.id),
      username: user.global_name || user.username || "Adventurer",
      embedded: true,
    };
  } catch (e) {
    sdk = null;   // a half-open SDK can't close anything either
    // If the handshake fails, don't hard-crash the whole surface — drop to the
    // browser/demo path so the UI is still explorable.
    console.error("[activity] Discord SDK init failed; using browser session:", e);
    return browserSession();
  }
}
