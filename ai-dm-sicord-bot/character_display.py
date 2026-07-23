"""
Character Display Module - Renders character sheets, inventory, and portraits
as Discord embeds. All data is rendered from the backend's stored character
(the source of truth); nothing here is AI-generated.
"""
import base64
import binascii
import io
from typing import Optional, Tuple

import discord

import backend_integration


_ABILITY_ORDER = [
    ("strength", "STR"),
    ("dexterity", "DEX"),
    ("constitution", "CON"),
    ("intelligence", "INT"),
    ("wisdom", "WIS"),
    ("charisma", "CHA"),
]

_SHEET_COLOR = 0x7B2D26   # deep parchment red
_INVENTORY_COLOR = 0x8A6D3B
_PORTRAIT_COLOR = 0x4A6FA5


def _portrait_file(payload: Optional[dict], name: str) -> Tuple[Optional[discord.File], Optional[str]]:
    """Decode a backend portrait payload into a (discord.File, attachment_url)."""
    if not payload:
        return None, None
    b64 = payload.get("b64")
    if not b64:
        return None, None
    try:
        data = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None, None
    safe = "".join(c for c in name if c.isalnum() or c in ("_", "-")).strip() or "portrait"
    filename = f"{safe}_portrait.webp"
    return discord.File(io.BytesIO(data), filename=filename), f"attachment://{filename}"


def build_sheet_embed(sheet: dict) -> Tuple[discord.Embed, Optional[discord.File]]:
    """Build a D&D-Beyond-style character sheet embed from backend data."""
    name = sheet.get("name", "Unknown")
    subclass = sheet.get("subclass")
    cls = sheet.get("char_class") or "Adventurer"
    class_line = f"{subclass} {cls}" if subclass else cls
    header = f"Level {sheet.get('level', 1)} {sheet.get('race', '')} {class_line}".strip()
    if sheet.get("deity"):
        header += f"\n*Devoted to {sheet['deity']}*"
    if sheet.get("dnr"):
        header += "\n🕯️ *Do Not Resuscitate*"

    embed = discord.Embed(title=f"📜 {name}", description=header, color=_SHEET_COLOR)

    # Portrait as thumbnail, if present.
    portrait_file, thumb_url = _portrait_file(sheet.get("portrait"), name)
    if thumb_url:
        embed.set_thumbnail(url=thumb_url)

    # Ability scores.
    abilities = sheet.get("abilities", {})
    abil_lines = []
    for key, label in _ABILITY_ORDER:
        a = abilities.get(key) or {}
        score = a.get("score", 10)
        mod = a.get("modifier_text", "+0")
        abil_lines.append(f"**{label}** {score} ({mod})")
    if abil_lines:
        embed.add_field(name="Abilities", value="\n".join(abil_lines), inline=True)

    # Combat block.
    combat = sheet.get("combat", {})
    combat_lines = [
        f"**HP** {combat.get('current_hp', '?')}/{combat.get('max_hp', '?')}",
        f"**Hit Dice** {combat.get('hit_dice_remaining', '?')}/"
        f"{combat.get('hit_dice_total', '?')}{combat.get('hit_die', '')}",
        f"**Prof. Bonus** +{sheet.get('proficiency_bonus', 2)}",
        f"**Passive Perc.** {combat.get('passive_perception', 10)}",
    ]
    if combat.get("exhaustion"):
        combat_lines.append(f"**Exhaustion** {combat['exhaustion']}")
    if combat.get("inspiration"):
        combat_lines.append("✨ **Inspiration**")
    embed.add_field(name="Combat", value="\n".join(combat_lines), inline=True)

    # Physical capabilities (movement / jump / carry).
    phys = sheet.get("physical") or {}
    if phys:
        phys_lines = [
            f"**Speed** {phys.get('walk_speed_ft', 30)} ft",
            f"**Long Jump** {phys.get('long_jump_running_ft', 0)} ft",
            f"**High Jump** {phys.get('high_jump_running_ft', 0)} ft",
            f"**Carry** {phys.get('carrying_capacity_lb', 0)} lb",
        ]
        embed.add_field(name="Movement", value="\n".join(phys_lines), inline=True)

    # Progress / wealth.
    prog_lines = [
        f"**XP** {sheet.get('xp', 0)}",
        f"**Purse** {sheet.get('purse_text', '0 cp')}",
    ]
    if sheet.get("carried_weight") is not None:
        prog_lines.append(f"**Carried** {sheet['carried_weight']} lb")
    if sheet.get("home_region"):
        prog_lines.append(f"**Region** {sheet['home_region']}")
    embed.add_field(name="Progress", value="\n".join(prog_lines), inline=True)

    # Active conditions/status effects (persist between encounters).
    conditions = sheet.get("conditions") or []
    if conditions:
        cond_lines = []
        for c in conditions:
            if isinstance(c, dict):
                label = str(c.get("name", "")).title()
                extra = []
                if c.get("source"):
                    extra.append(f"from {c['source']}")
                if c.get("duration"):
                    extra.append(str(c["duration"]))
                if extra:
                    label += f" ({', '.join(extra)})"
            else:
                label = str(c).title()
            cond_lines.append(f"• {label}")
        embed.add_field(name="Conditions", value="\n".join(cond_lines), inline=False)

    # Spells (names only; the sheet is a summary).
    spells = sheet.get("spells") or []
    if spells:
        spell_names = []
        for sp in spells[:12]:
            if isinstance(sp, dict):
                spell_names.append(str(sp.get("name", "spell")))
            else:
                spell_names.append(str(sp))
        more = f" (+{len(spells) - 12} more)" if len(spells) > 12 else ""
        embed.add_field(name="Spells", value=", ".join(spell_names) + more, inline=False)

    # Inventory summary.
    inv_lines = sheet.get("inventory_lines") or []
    if inv_lines:
        shown = inv_lines[:15]
        more = f"\n…and {len(inv_lines) - 15} more (use `!inventory`)" if len(inv_lines) > 15 else ""
        embed.add_field(name="Inventory", value="\n".join(shown) + more, inline=False)
    else:
        embed.add_field(name="Inventory", value="*(empty)*", inline=False)

    embed.set_footer(text="Rendered from your live character record.")
    return embed, portrait_file


def build_inventory_embed(inv: dict) -> discord.Embed:
    """Build an inventory list embed from backend data."""
    name = inv.get("name", "Unknown")
    embed = discord.Embed(title=f"🎒 {name}'s Inventory", color=_INVENTORY_COLOR)
    lines = inv.get("lines") or []
    if lines:
        # Chunk into fields to respect Discord's 1024-char field limit.
        chunk, chunks = [], []
        length = 0
        for line in lines:
            if length + len(line) + 1 > 1000 and chunk:
                chunks.append(chunk)
                chunk, length = [], 0
            chunk.append(line)
            length += len(line) + 1
        if chunk:
            chunks.append(chunk)
        for i, ch in enumerate(chunks):
            title = "Items" if i == 0 else "\u200b"
            embed.add_field(name=title, value="\n".join(ch), inline=False)
    else:
        embed.description = "*You aren't carrying anything listed.*"

    footer = f"Purse: {inv.get('purse_text', '0 cp')}"
    if inv.get("carried_weight") is not None:
        footer += f"  •  Carried: {inv['carried_weight']} lb"
    embed.set_footer(text=footer)
    return embed


def build_portrait_embed(payload: dict, name: str) -> Tuple[discord.Embed, Optional[discord.File]]:
    """Build an embed showing a character's portrait."""
    embed = discord.Embed(title=f"🖼️ {name}", color=_PORTRAIT_COLOR)
    portrait_file, url = _portrait_file(payload, name)
    if url:
        embed.set_image(url=url)
    if payload.get("caption"):
        embed.description = payload["caption"]
    if payload.get("offline"):
        embed.set_footer(text="Image service is offline — showing a placeholder.")
    return embed, portrait_file


class PortraitView(discord.ui.View):
    """Regenerate / Keep controls shown under a freshly generated portrait."""

    def __init__(self, character_id: int, character_name: str, backend_url: str,
                 owner_id: int, description: str = "", *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.character_id = character_id
        self.character_name = character_name
        self.backend_url = backend_url
        self.owner_id = owner_id
        self.description = description

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the character's player can change this portrait.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.primary, emoji="🔄")
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        result = await backend_integration.generate_portrait(
            self.character_id, self.backend_url, description=self.description)
        if result.get("error"):
            await interaction.followup.send(
                f"⚠️ Couldn't regenerate the portrait: {result['error']}", ephemeral=True)
            return
        embed, portrait_file = build_portrait_embed(result, self.character_name)
        kwargs = {"embed": embed, "view": self}
        if portrait_file:
            kwargs["attachments"] = [portrait_file]
        await interaction.edit_original_response(**kwargs)

    @discord.ui.button(label="Keep", style=discord.ButtonStyle.success, emoji="✅")
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PortraitDescribeModal(discord.ui.Modal, title="Describe your character's portrait"):
    """Collects a free-text appearance description and generates a portrait."""

    description = discord.ui.TextInput(
        label="Appearance",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. weathered half-elf ranger, green hooded cloak, scar over one eye",
        required=True,
        max_length=400,
    )

    def __init__(self, character_id: int, character_name: str, backend_url: str, owner_id: int):
        super().__init__()
        self.character_id = character_id
        self.character_name = character_name
        self.backend_url = backend_url
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🎨 Painting your portrait — this can take a moment...", ephemeral=False)
        desc = str(self.description.value)
        result = await backend_integration.generate_portrait(
            self.character_id, self.backend_url, description=desc)
        if result.get("error"):
            await interaction.followup.send(
                f"⚠️ Couldn't generate a portrait: {result['error']}\n"
                f"You can try again anytime with `!portrait <description>`.")
            return
        embed, portrait_file = build_portrait_embed(result, self.character_name)
        view = PortraitView(self.character_id, self.character_name, self.backend_url,
                            self.owner_id, description=desc)
        if portrait_file:
            await interaction.followup.send(embed=embed, file=portrait_file, view=view)
        else:
            await interaction.followup.send(embed=embed, view=view)


class PortraitSetupView(discord.ui.View):
    """Offered right after character creation: generate, upload, or skip a portrait."""

    def __init__(self, character_id: int, character_name: str, backend_url: str,
                 owner_id: int, *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.character_id = character_id
        self.character_name = character_name
        self.backend_url = backend_url
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the character's player can set this portrait.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Generate from description", style=discord.ButtonStyle.primary, emoji="🎨")
    async def generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            PortraitDescribeModal(self.character_id, self.character_name,
                                  self.backend_url, self.owner_id))

    @discord.ui.button(label="I'll upload one", style=discord.ButtonStyle.secondary, emoji="📤")
    async def upload(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "📤 To use your own portrait, send `!portrait` in this channel with an image "
            "attached (PNG, JPG, or WebP).", ephemeral=True)

    @discord.ui.button(label="Skip for now", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="No portrait for now — you can add one anytime with `!portrait`.", view=self)
        self.stop()

