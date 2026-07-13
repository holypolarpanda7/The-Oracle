"""
Deterministic character-creation wizard — Discord components, zero LLM.

Every option comes from the backend's rules DB (``GET /cc/options``), every
roll from the internal dice engine (``POST /cc/roll_abilities``), and every
constraint (score bounds, point-buy budget, skill counts, racial bonuses
applied exactly once) is enforced in code. The Oracle's LLM plays no part
here — narration is its job, not rules adjudication.

Flow (one message per step, owner-locked components):
  name (modal) -> race -> [racial bonus picks] -> class -> background
  -> ability method (array / point buy / roll) -> assign scores -> skills
  -> review -> confirm -> POST /register_character
"""
import re
from typing import Dict, List, Optional

import aiohttp
import discord

from backend_integration import _api_base, register_character_backend

# text_channel_id -> wizard state dict
wizard_state: Dict[int, Dict] = {}

ABILITIES = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
ABIL_SHORT = {"strength": "STR", "dexterity": "DEX", "constitution": "CON",
              "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA"}

ALL_SKILLS = ["Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception",
              "History", "Insight", "Intimidation", "Investigation", "Medicine",
              "Nature", "Perception", "Performance", "Persuasion", "Religion",
              "Sleight of Hand", "Stealth", "Survival"]

# Skills a race grants automatically (excluded from the choice list).
RACE_GRANTED_SKILLS = {"elf": ["Perception"], "half-orc": ["Intimidation"]}
# Extra free-choice skills a race grants on top of the class picks.
RACE_EXTRA_SKILLS = {"half-elf": 2, "custom-lineage": 1}


# ---------------------------------------------------------------------------
# Backend I/O
# ---------------------------------------------------------------------------

async def fetch_cc_options(backend_url: str) -> Optional[dict]:
    url = f"{_api_base(backend_url)}/cc/options"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        print(f"[cc_wizard] options fetch failed: {e}")
    return None


async def fetch_rolls(backend_url: str) -> Optional[list]:
    url = f"{_api_base(backend_url)}/cc/roll_abilities"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("rolls")
    except Exception as e:
        print(f"[cc_wizard] roll fetch failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Shared view plumbing
# ---------------------------------------------------------------------------

class _StepView(discord.ui.View):
    """Base view: locks components to the session owner."""

    def __init__(self, state: Dict):
        super().__init__(timeout=3600)
        self.state = state

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.state["user_id"]:
            await interaction.response.send_message(
                "This isn't your character creation!", ephemeral=True)
            return False
        return True


def _final_scores(state: Dict) -> Dict[str, int]:
    """Base scores + racial bonuses, applied exactly once."""
    race = state["race"]
    scores = dict(state["base_scores"])
    for ab, bonus in (race.get("ability_bonuses") or {}).items():
        key = next((a for a in ABILITIES if a.startswith(ab[:3].lower())), None)
        if key:
            scores[key] = scores.get(key, 10) + int(bonus)
    for ab, bonus in state.get("chosen_bonuses", {}).items():
        scores[ab] = scores.get(ab, 10) + int(bonus)
    return scores


def _score_line(state: Dict) -> str:
    finals = _final_scores(state)
    return " · ".join(f"{ABIL_SHORT[a]} {finals.get(a, '?')}" for a in ABILITIES)


# ---------------------------------------------------------------------------
# Step 0: name modal (entry point — needs a button interaction)
# ---------------------------------------------------------------------------

class NameModal(discord.ui.Modal, title="Name your character"):
    name = discord.ui.TextInput(label="Character name", min_length=2, max_length=40,
                                placeholder="e.g. Stevo Brown")

    def __init__(self, voice_channel_id: int, user_id: str, username: str, backend_url: str):
        super().__init__()
        self.voice_channel_id = voice_channel_id
        self.user_id = user_id
        self.username = username
        self.backend_url = backend_url

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        options = await fetch_cc_options(self.backend_url)
        if not options or not options.get("races"):
            await interaction.followup.send(
                "❌ The rules library isn't available (backend offline or CC data "
                "unseeded). Try again in a moment.")
            return
        state = {
            "user_id": self.user_id,
            "username": self.username,
            "voice_channel_id": self.voice_channel_id,
            "backend_url": self.backend_url,
            "options": options,
            "name": str(self.name.value).strip(),
            "race": None, "char_class": None, "background": None,
            "base_scores": {}, "chosen_bonuses": {}, "skills": [],
            "lineage_perk": None,
        }
        wizard_state[interaction.channel.id] = state
        await interaction.followup.send(
            f"⚒️ Forging **{state['name']}** — every choice below comes straight "
            "from the rules, and all dice are real.")
        await _send_race_step(interaction.channel, state)


# ---------------------------------------------------------------------------
# Step 1: race
# ---------------------------------------------------------------------------

async def _send_race_step(channel, state):
    view = _StepView(state)
    sel = discord.ui.Select(placeholder="Choose your race…", min_values=1, max_values=1)
    for r in state["options"]["races"]:
        bonuses = r.get("ability_bonuses") or {}
        if bonuses:
            btxt = ", ".join(f"+{v} {k.upper()[:3]}" for k, v in bonuses.items())
        else:
            btxt = "+2/+1 to abilities of your choice"
        if r.get("choose_bonus") and bonuses:
            btxt += " and +1 to two others"
        desc = f"{btxt} · speed {r['speed']}" + (" · darkvision" if r.get("darkvision") else "")
        sel.add_option(label=r["name"], value=r["slug"], description=desc[:100])

    async def on_pick(interaction: discord.Interaction):
        slug = sel.values[0]
        race = next(r for r in state["options"]["races"] if r["slug"] == slug)
        state["race"] = race
        traits = "\n".join(f"• {t}" for t in (race.get("traits") or [])[:5])
        await interaction.response.edit_message(
            content=f"**Race: {race['name']}**\n{traits}", view=None)
        if race.get("choose_bonus"):
            await _send_bonus_pick_step(interaction.channel, state,
                                        list(race["choose_bonus"]), {})
        elif slug == "custom-lineage":
            await _send_lineage_perk_step(interaction.channel, state)
        else:
            await _send_class_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    await channel.send(f"**Step 1 · Race** — who are {state['name']}'s people?", view=view)


# Racial +N picks for choose-your-own races (custom lineage +2/+1, half-elf +1/+1).
async def _send_bonus_pick_step(channel, state, remaining: List[int], picked: Dict[str, int]):
    bonus = remaining[0]
    fixed = {next((a for a in ABILITIES if a.startswith(k[:3].lower())), "")
             for k in (state["race"].get("ability_bonuses") or {})}
    taken = fixed | set(picked)
    view = _StepView(state)
    sel = discord.ui.Select(placeholder=f"+{bonus} to which ability?",
                            min_values=1, max_values=1)
    for a in ABILITIES:
        if a not in taken:
            sel.add_option(label=f"{ABIL_SHORT[a]} — {a.title()}", value=a)

    async def on_pick(interaction: discord.Interaction):
        ability = sel.values[0]
        picked[ability] = bonus
        await interaction.response.edit_message(
            content=f"Racial bonus: **+{bonus} {ABIL_SHORT[ability]}**", view=None)
        rest = remaining[1:]
        if rest:
            await _send_bonus_pick_step(interaction.channel, state, rest, picked)
        else:
            state["chosen_bonuses"] = picked
            if state["race"]["slug"] == "custom-lineage":
                await _send_lineage_perk_step(interaction.channel, state)
            else:
                await _send_class_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    await channel.send(f"**Racial bonus** — assign your **+{bonus}**:", view=view)


async def _send_lineage_perk_step(channel, state):
    view = _StepView(state)

    async def pick(interaction: discord.Interaction, perk: str, label: str):
        state["lineage_perk"] = perk
        await interaction.response.edit_message(
            content=f"Lineage gift: **{label}**", view=None)
        await _send_class_step(interaction.channel, state)

    b1 = discord.ui.Button(label="Darkvision (60 ft)", style=discord.ButtonStyle.secondary, emoji="🌑")
    b2 = discord.ui.Button(label="One extra language", style=discord.ButtonStyle.secondary, emoji="🗣️")
    b1.callback = lambda i: pick(i, "darkvision", "Darkvision (60 ft)")
    b2.callback = lambda i: pick(i, "language", "One extra language")
    view.add_item(b1); view.add_item(b2)
    await channel.send("**Lineage gift** — your people see in the dark, or speak widely:", view=view)


# ---------------------------------------------------------------------------
# Step 2: class · Step 3: background
# ---------------------------------------------------------------------------

async def _send_class_step(channel, state):
    view = _StepView(state)
    sel = discord.ui.Select(placeholder="Choose your class…", min_values=1, max_values=1)
    for c in state["options"]["classes"]:
        cast = f" · casts ({c['spellcasting_ability']})" if c.get("spellcasting_ability") else ""
        desc = f"d{c['hit_die']} hit die · {c.get('primary_ability') or '?'}{cast}"
        sel.add_option(label=c["name"], value=c["slug"], description=desc[:100])

    async def on_pick(interaction: discord.Interaction):
        slug = sel.values[0]
        cls = next(c for c in state["options"]["classes"] if c["slug"] == slug)
        state["char_class"] = cls
        saves = "/".join(cls.get("saving_throws") or [])
        await interaction.response.edit_message(
            content=f"**Class: {cls['name']}** — d{cls['hit_die']} hit die, saves {saves}",
            view=None)
        await _send_background_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    await channel.send(f"**Step 2 · Class** — what is {state['name']}'s calling?", view=view)


async def _send_background_step(channel, state):
    view = _StepView(state)
    sel = discord.ui.Select(placeholder="Choose your background…", min_values=1, max_values=1)
    for bg in state["options"]["backgrounds"]:
        desc = f"{', '.join(bg.get('skills') or [])} · {bg.get('feature') or ''}"
        sel.add_option(label=bg["name"], value=bg["slug"], description=desc[:100])

    async def on_pick(interaction: discord.Interaction):
        slug = sel.values[0]
        bg = next(b for b in state["options"]["backgrounds"] if b["slug"] == slug)
        state["background"] = bg
        await interaction.response.edit_message(
            content=(f"**Background: {bg['name']}** — {', '.join(bg.get('skills') or [])}; "
                     f"feature: {bg.get('feature')}"), view=None)
        await _send_ability_method_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    await channel.send(f"**Step 3 · Background** — what life shaped {state['name']}?", view=view)


# ---------------------------------------------------------------------------
# Step 4: ability scores
# ---------------------------------------------------------------------------

async def _send_ability_method_step(channel, state):
    methods = state["options"]["ability_methods"]
    view = _StepView(state)

    async def use_array(interaction: discord.Interaction):
        pool = list(methods["standard_array"])
        await interaction.response.edit_message(
            content=f"**Standard array:** {', '.join(map(str, pool))}", view=None)
        await _send_assign_step(interaction.channel, state, pool, 0, {})

    async def use_roll(interaction: discord.Interaction):
        await interaction.response.defer()
        rolls = await fetch_rolls(state["backend_url"])
        if not rolls:
            await interaction.followup.send("❌ The dice tower is jammed (backend offline). Try again.")
            return
        lines = "\n".join(f"🎲 {r['detail']}" for r in rolls)
        pool = [r["total"] for r in rolls]
        await interaction.edit_original_response(
            content=f"**Rolled 4d6-drop-lowest × 6:**\n{lines}", view=None)
        await _send_assign_step(interaction.channel, state, pool, 0, {})

    async def use_pointbuy(interaction: discord.Interaction):
        pb = methods["point_buy"]
        await interaction.response.edit_message(
            content=(f"**Point buy** — budget {pb['budget']}, scores "
                     f"{pb['min']}–{pb['max']}."), view=None)
        await _send_pointbuy_step(interaction.channel, state, 0, {}, int(pb["budget"]))

    b1 = discord.ui.Button(label="Standard array", style=discord.ButtonStyle.primary, emoji="📋")
    b2 = discord.ui.Button(label="Point buy (27)", style=discord.ButtonStyle.primary, emoji="🧮")
    b3 = discord.ui.Button(label="Roll 4d6 × 6", style=discord.ButtonStyle.success, emoji="🎲")
    b1.callback, b2.callback, b3.callback = use_array, use_pointbuy, use_roll
    view.add_item(b1); view.add_item(b2); view.add_item(b3)
    await channel.send("**Step 4 · Ability scores** — how do we determine them?", view=view)


async def _send_assign_step(channel, state, pool: List[int], idx: int, assigned: Dict[str, int],
                            message: Optional[discord.Message] = None):
    """Assign values from `pool` to abilities, one select at a time (same message)."""
    ability = ABILITIES[idx]
    remaining = list(pool)
    for v in assigned.values():
        remaining.remove(v)

    view = _StepView(state)
    sel = discord.ui.Select(placeholder=f"{ABIL_SHORT[ability]} = ?", min_values=1, max_values=1)
    seen = set()
    for v in sorted(remaining, reverse=True):
        if v not in seen:   # duplicate values need only one option
            sel.add_option(label=str(v), value=str(v))
            seen.add(v)

    done = " · ".join(f"{ABIL_SHORT[a]} {v}" for a, v in assigned.items()) or "—"
    content = (f"**Assign scores** ({', '.join(map(str, sorted(remaining, reverse=True)))} left)\n"
               f"Assigned: {done}\nChoose **{ABIL_SHORT[ability]}**:")

    async def on_pick(interaction: discord.Interaction):
        assigned[ability] = int(sel.values[0])
        if idx + 1 < len(ABILITIES):
            await interaction.response.defer()
            await _send_assign_step(interaction.channel, state, pool, idx + 1, assigned,
                                    message=interaction.message)
        else:
            state["base_scores"] = dict(assigned)
            final = _score_line(state)
            await interaction.response.edit_message(
                content=f"**Base scores set.** With racial bonuses: {final}", view=None)
            await _send_skills_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    if message:
        await message.edit(content=content, view=view)
    else:
        await channel.send(content, view=view)


async def _send_pointbuy_step(channel, state, idx: int, assigned: Dict[str, int], budget: int,
                              message: Optional[discord.Message] = None):
    pb = state["options"]["ability_methods"]["point_buy"]
    costs = {int(k): int(v) for k, v in pb["costs"].items()}
    ability = ABILITIES[idx]

    view = _StepView(state)
    sel = discord.ui.Select(placeholder=f"{ABIL_SHORT[ability]} = ?", min_values=1, max_values=1)
    for score in range(int(pb["max"]), int(pb["min"]) - 1, -1):
        cost = costs[score]
        if cost <= budget:
            sel.add_option(label=f"{score}  (cost {cost})", value=str(score))

    done = " · ".join(f"{ABIL_SHORT[a]} {v}" for a, v in assigned.items()) or "—"
    content = (f"**Point buy** — {budget} points left\nAssigned: {done}\n"
               f"Choose **{ABIL_SHORT[ability]}**:")

    async def on_pick(interaction: discord.Interaction):
        score = int(sel.values[0])
        assigned[ability] = score
        left = budget - costs[score]
        if idx + 1 < len(ABILITIES):
            await interaction.response.defer()
            await _send_pointbuy_step(interaction.channel, state, idx + 1, assigned, left,
                                      message=interaction.message)
        else:
            state["base_scores"] = dict(assigned)
            final = _score_line(state)
            await interaction.response.edit_message(
                content=(f"**Point buy complete** ({left} points unspent). "
                         f"With racial bonuses: {final}"), view=None)
            await _send_skills_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    if message:
        await message.edit(content=content, view=view)
    else:
        await channel.send(content, view=view)


# ---------------------------------------------------------------------------
# Step 5: skills
# ---------------------------------------------------------------------------

async def _send_skills_step(channel, state):
    cls = state["char_class"]
    race_slug = state["race"]["slug"]
    bg_skills = state["background"].get("skills") or []
    granted = RACE_GRANTED_SKILLS.get(race_slug, [])
    extra_n = RACE_EXTRA_SKILLS.get(race_slug, 0)

    n = int(cls.get("skill_choices_n") or 2) + extra_n
    # Class options (plus the whole list when the race grants free-choice picks),
    # minus anything already proficient via background or race.
    options = list(cls.get("skill_options") or [])
    if extra_n:
        options = ALL_SKILLS
    options = [s for s in options if s not in bg_skills and s not in granted]
    n = min(n, len(options))

    view = _StepView(state)
    sel = discord.ui.Select(placeholder=f"Pick exactly {n} skills…",
                            min_values=n, max_values=n)
    for s in options[:25]:
        sel.add_option(label=s, value=s)

    async def on_pick(interaction: discord.Interaction):
        state["skills"] = sorted(set(sel.values) | set(bg_skills) | set(granted))
        await interaction.response.edit_message(
            content=f"**Proficiencies:** {', '.join(state['skills'])}", view=None)
        await _send_review_step(interaction.channel, state)

    sel.callback = on_pick
    view.add_item(sel)
    already = ", ".join(bg_skills + granted) or "none"
    await channel.send(
        f"**Step 5 · Skills** — pick **{n}** (already proficient via background/race: {already}):",
        view=view)


# ---------------------------------------------------------------------------
# Step 6: review + confirm
# ---------------------------------------------------------------------------

async def _send_review_step(channel, state):
    race, cls, bg = state["race"], state["char_class"], state["background"]
    finals = _final_scores(state)

    con_mod = (finals.get("constitution", 10) - 10) // 2
    hp = max(1, int(cls["hit_die"]) + con_mod)

    embed = discord.Embed(title=f"📜 {state['name']} — ready to be forged",
                          color=discord.Color.gold())
    embed.add_field(name="Race", value=race["name"], inline=True)
    embed.add_field(name="Class", value=cls["name"], inline=True)
    embed.add_field(name="Background", value=bg["name"], inline=True)
    embed.add_field(name="Abilities (final, racial bonuses included)",
                    value=" · ".join(f"**{ABIL_SHORT[a]}** {finals[a]}" for a in ABILITIES),
                    inline=False)
    embed.add_field(name="HP", value=f"{hp} (d{cls['hit_die']} + CON)", inline=True)
    embed.add_field(name="Speed", value=f"{race['speed']} ft", inline=True)
    perk = state.get("lineage_perk")
    vision = "darkvision" if (race.get("darkvision") or perk == "darkvision") else "normal vision"
    embed.add_field(name="Senses", value=vision, inline=True)
    embed.add_field(name="Skills", value=", ".join(state["skills"]) or "—", inline=False)
    embed.set_footer(text="Starting kit + background gear are granted on creation.")

    view = _StepView(state)

    async def confirm(interaction: discord.Interaction):
        await interaction.response.defer()
        payload = {
            "discord_user_id": state["user_id"],
            "name": state["name"],
            "race": race["name"],
            "char_class": cls["name"],
            "level": 1,
            "stats": {a: finals[a] for a in ABILITIES},
            "background": bg["name"],
            "skills": state["skills"],
            "approve": True,
            "home_region": "Gatvorhain",
            "source": "guided",
        }
        url = f"{_api_base(state['backend_url'])}/register_character"
        result = await register_character_backend(payload, url)
        if result.get("status") == "ok" or result.get("character_id"):
            await interaction.edit_original_response(view=None)
            await _finish(interaction.channel, interaction.user, state, result)
        else:
            await interaction.followup.send(
                f"❌ The forge rejected it: {result.get('error') or result.get('detail') or 'unknown error'}")

    async def restart(interaction: discord.Interaction):
        await interaction.response.edit_message(content="🔁 Starting over.", embed=None, view=None)
        wizard_state.pop(channel.id, None)
        state2 = dict(state, race=None, char_class=None, background=None,
                      base_scores={}, chosen_bonuses={}, skills=[], lineage_perk=None)
        wizard_state[channel.id] = state2
        await _send_race_step(interaction.channel, state2)

    b_ok = discord.ui.Button(label="Confirm & create", style=discord.ButtonStyle.success, emoji="⚒️")
    b_re = discord.ui.Button(label="Start over", style=discord.ButtonStyle.secondary, emoji="🔁")
    b_ok.callback, b_re.callback = confirm, restart
    view.add_item(b_ok); view.add_item(b_re)
    await channel.send(embed=embed, view=view)


async def _finish(channel, player, state, result):
    import character_creation
    await channel.send(
        f"✅ **{state['name']}** steps into the world — created and approved! "
        f"Use `!enterworld` when you're ready.")
    if result.get("character_id"):
        await character_creation._show_final_sheet(channel, result["character_id"],
                                                   state["backend_url"])
    await character_creation._offer_portrait_setup(channel, player, result,
                                                   state["name"], state["backend_url"])
    wizard_state.pop(channel.id, None)

    # Close down the ephemeral session like the old flow did.
    import asyncio
    voice_channel_id = state["voice_channel_id"]
    if voice_channel_id in character_creation.ephemeral_cc_channels:
        await asyncio.sleep(2)
        await character_creation.cleanup_ephemeral_channel(
            channel.guild, voice_channel_id, state["user_id"],
            reason="Character successfully created")


# ---------------------------------------------------------------------------
# Typed input during the wizard (DDB links still work; chatter gets a nudge)
# ---------------------------------------------------------------------------

async def handle_wizard_message(channel, message, backend_url: str) -> bool:
    """Returns True if the message belonged to an active wizard session."""
    state = wizard_state.get(channel.id)
    if state is None:
        return False
    if str(message.author.id) != state["user_id"]:
        return True  # someone else's chatter in the channel: swallow quietly
    text = message.content.strip()
    if "dndbeyond.com/characters" in text.lower() or "ddb.ac/characters" in text.lower():
        import character_creation
        compat = {"session_id": f"cc_wizard:{state['user_id']}",
                  "username": state["username"]}
        await character_creation._handle_ddb_import(channel, message, compat, text, backend_url)
        wizard_state.pop(channel.id, None)
        return True
    if text and not text.startswith("!"):
        await channel.send("🧭 Use the buttons and menus above — every choice is a click. "
                           "(Paste a D&D Beyond link at any time to import instead.)")
    return True
