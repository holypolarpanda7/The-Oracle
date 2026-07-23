"""
DM Commands Module - Command handlers for DM mode and world entry.
"""
import discord
from discord.ext import commands

import backend_integration
import character_creation
import music_player


def is_admin(user: discord.abc.User, admin_id: str) -> bool:
    """Return True if this user is allowed to control DM mode."""
    return str(user.id) == admin_id


_DUEL_TERMS = ("first-blood", "to-yield", "to-the-death")


class DuelConsentView(discord.ui.View):
    """Accept/Decline prompt that makes a duel AUTHORIZED. Only the challenged
    player may answer; Accept opens a server-recorded pact so a fair fight draws no
    divine wrath, while an unsanctioned kill still brings the gods down."""

    def __init__(self, challenger: discord.abc.User, target: discord.abc.User,
                 terms: str, backend_url: str):
        super().__init__(timeout=120)
        self.challenger = challenger
        self.target = target
        self.terms = terms
        self.backend_url = backend_url

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "Only the challenged duelist can answer this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept the duel", style=discord.ButtonStyle.danger,
                       emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        res = await backend_integration.open_pvp_pact(
            str(self.challenger.id), str(self.target.id), self.terms, self.backend_url)
        for c in self.children:
            c.disabled = True
        if res.get("error"):
            await interaction.response.edit_message(
                content=f"⚠️ Could not sanction the duel: {res['error']}", view=self)
        else:
            await interaction.response.edit_message(
                content=(f"⚔️ **Sanctioned duel accepted!** {self.challenger.mention} vs "
                         f"{self.target.mention} — *{self.terms}*. This fight is honorable; "
                         f"no divine wrath will follow. Fight it out in your table."),
                view=self)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=f"🏳️ {self.target.mention} declined the duel. Draw steel anyway and "
                    f"it is murder, not a duel — with all that follows.", view=self)
        self.stop()


async def duel_command(ctx: commands.Context, target: discord.Member,
                       terms: str, backend_url: str) -> None:
    """`!duel @player [terms]` — challenge another player to a SANCTIONED duel."""
    if target is None or target.bot:
        await ctx.send("Usage: `!duel @player [first-blood|to-yield|to-the-death]`")
        return
    if target.id == ctx.author.id:
        await ctx.send("You cannot duel yourself.")
        return
    t = (terms or "to-yield").strip().lower().replace(" ", "-")
    if t not in _DUEL_TERMS:
        t = "to-yield"
    view = DuelConsentView(ctx.author, target, t, backend_url)
    await ctx.send(
        f"⚔️ {target.mention} — **{ctx.author.display_name}** challenges you to a duel "
        f"(*{t}*). Do you accept? A sanctioned duel is honorable and draws no divine "
        f"punishment; refuse, and any attack on you is murder.",
        view=view)


async def start_dm_command(ctx: commands.Context, active_dm_channels: set):
    """Enable DM mode in this channel (admin only)."""
    channel_id = ctx.channel.id
    active_dm_channels.add(channel_id)
    await ctx.send("🧙‍♂️ DM mode **enabled** in this channel. Speak, adventurers.")


async def stop_dm_command(ctx: commands.Context, active_dm_channels: set):
    """Disable DM mode in this channel (admin only)."""
    channel_id = ctx.channel.id
    if channel_id in active_dm_channels:
        active_dm_channels.remove(channel_id)
        await ctx.send("🧙‍♂️ DM mode **disabled** in this channel.")
    else:
        await ctx.send("DM mode is not active here.")


async def reset_dm_command(ctx: commands.Context, reset_url: str):
    """Reset the DM conversation for this channel (admin only)."""
    session_id = f"dm:{ctx.channel.id}"
    result = await backend_integration.reset_backend_session(session_id, reset_url)
    await ctx.send(f"🔄 {result}")


async def enter_world_command(ctx: commands.Context, character_name: str, check_url: str, enter_url: str):
    """Enter the world with your character."""
    user_id = str(ctx.author.id)
    
    # Check if user has a character
    has_character = await backend_integration.check_character_in_db(user_id, check_url)
    if not has_character:
        await ctx.send(
            "❌ You don't have a character yet! Use `/enterworld` to create one first.\n"
            "Or if you already have a D&D Beyond character, import it with Avrae."
        )
        return
    
    # Call backend /enterworld
    result = await backend_integration.enter_world_backend(user_id, character_name, enter_url)
    
    if result.get("ok"):
        session_id = result.get("session_id")
        welcome = result.get("welcome_message", "Welcome to the world!")
        
        await ctx.send(
            f"🌍 **Welcome to the Oracle's realm!**\n\n"
            f"{welcome}\n\n"
            f"*Session ID: `{session_id}`*"
        )

        # Kick off scene-appropriate opening music if the DM recommended one
        # and the player is sitting in a voice channel.
        music_query = result.get("music")
        if music_query:
            voice_state = getattr(ctx.author, "voice", None)
            voice_channel = voice_state.channel if voice_state else None
            if voice_channel is not None:
                try:
                    await music_player.play_query_in_channel(voice_channel, music_query)
                except Exception as e:
                    print(f"[music] Failed to play opening scene music '{music_query}': {e}")
    else:
        error = result.get("error", "Unknown error")
        await ctx.send(f"❌ Failed to enter world: {error}")
