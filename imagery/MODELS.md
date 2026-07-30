# Diffusion models — setup & context switching

The Oracle runs **two checkpoints** and picks one per render based on the
requested content. Selection is automatic:

- **Default / "safe"** → `ImageryConfig.checkpoint` — a strong **non-NSFW** SDXL
  finetune. Used for all normal scenes, portraits, and UI-adjacent art.
- **Mature** → `ImageryConfig.checkpoint_mature` — a **Pony-family** SDXL model.
  Used only when a render is flagged `mature=True` (see *Wiring* below). Leave
  `checkpoint_mature = None` to disable NSFW rendering entirely — mature-flagged
  renders then silently fall back to the safe checkpoint.

When a render is mature, the client also swaps the prompt tags to
`mature_style_prompt` / `mature_negative_prompt` (Pony keys quality off
`score_*` tags + a rating token and wants its own negatives). All four strings
are operator-editable in `game_config/config.py` → `ImageryConfig`.

## Recommended models (RTX 3080 Ti / 12 GB — both fit comfortably, fast)

| Slot | Model | Why |
|------|-------|-----|
| `checkpoint` (safe) | **Juggernaut XL** (or **RealVisXL** / a dark-fantasy concept finetune) | Huge jump over base SDXL in quality + prompt adherence; painterly dark-fantasy looks great; ~8–10 GB VRAM, ~8 s/image |
| `checkpoint_mature` | **Pony Diffusion V6 XL** (or a Pony-based finetune) | The most capable, best-supported uncensored SDXL ecosystem; excellent stylized character art; same VRAM class |

Both are SDXL, so they share the same workflow, IP-Adapter, and resolution
settings — no separate pipeline. Only the checkpoint file swaps at graph-build
time (`ComfyClient._build_graph(checkpoint=...)`), so switching costs only a
model reload, not a code path.

## Install
1. Download the `.safetensors` files (HuggingFace / Civitai).
2. Drop them in `D:\ComfyUI\models\checkpoints\`.
3. Set the exact filenames in `game_config/config.py`:
   ```python
   checkpoint = "juggernautXL_v9.safetensors"
   checkpoint_mature = "ponyDiffusionV6XL.safetensors"
   ```
4. Restart the backend. No ComfyUI restart needed — it loads checkpoints on demand.

## ComfyUI checkpoint-switch crash (worked around in-client)
Some 2026 ComfyUI nightlies (seen on `328144c`, 2026-07-11) crash their
`prompt_worker` thread when auto-evicting one large checkpoint to load another —
a `None`-deref in `comfy/model_management.py::free_memory`
(`'NoneType' object has no attribute 'is_dynamic'`). Symptom: the first render of
a checkpoint works, but the next render that needs the *other* checkpoint hangs
(job stuck "running", GPU idle) and the executor is dead until ComfyUI restarts.

`ComfyClient` works around this: before rendering with a checkpoint different from
the one it last loaded, it calls `/free` (`free_memory(unload_models=True)`) to
unload cleanly, so ComfyUI never takes the buggy auto-eviction path. Cost is one
model reload per switch (~a few seconds). If you later update/roll back ComfyUI
past this regression, the workaround stays harmless. Verified: safe→mature→safe
all render crash-free.

## Wiring `mature` (per-table policy)
`ImageStore.ensure_image(..., mature=...)` is the switch. The **caller** owns the
decision — pass the table's maturity setting (a per-table opt-in, age-confirmed
flag) when generating a scene. Default is `False` everywhere, so nothing renders
mature until a table explicitly opts in AND `checkpoint_mature` is configured.
Note: this is a shared Discord surface — gate mature output behind explicit,
adult, opt-in table settings, not a global default.

---

# Layers above the prompt

Everything above tunes *words* and *samplers*. These act on the **model**, and
they grip where wording slips — the species portraits needed four rounds of
prompt surgery because style and anatomy were competing for room in one text
budget, which is a problem no rewording actually solves.

The stack `ComfyClient._build_graph` assembles (each layer skipped when
unconfigured, so the default graph is byte-identical to before):

```
checkpoint → LoRA(s) → RescaleCFG → PAG → FreeU → [IP-Adapter] → KSampler
```

LoRA is first and is the only one that also patches CLIP, so the text encoders
are rewired onto its CLIP output — a style LoRA that never reached the encoders
would be half-applied. IP-Adapter is spliced afterwards by `_inject_references`,
which reads whatever the stack left on the sampler, so the two compose without
either knowing about the other.

## Measured on this box (RTX 3080 Ti, Juggernaut XL, 768px / 25 steps)

Tested on the **real 166-word species prompt**, not a toy one — at short prompt
lengths every option looks fine, because the trait clause dominates by default.
The failure only appears at the length the pipeline actually uses. Probe: a
goliath, whose "slate blue-grey skin" loses to "tanned human with warpaint".

| Layer | Result | Verdict |
|---|---|---|
| baseline | human-brown skin, blue-grey patches — reads as a tattooed human | the failure |
| **PAG 3.0** | **solid blue-grey over the whole body, heavy brow, giant** | **ON by default** |
| RescaleCFG 0.7 + cfg 10 | *worse* — fully human skin with face paint | off |
| PAG + RescaleCFG + cfg 10 | good, but over-contrasted | off |
| FreeU v2 | oversaturated to neon | off |

**PAG costs +15%, not 2×** — 15.6s → 17.9s, mean of 3. The extra pass is
cheaper than the arithmetic suggests. Raising CFG (with or without rescale)
*hurt*: high CFG sharpens whatever reading the model already prefers, and here
that reading is the wrong one.

## IP-Adapter: already installed, long unused

`ComfyUI_IPAdapter_plus`, `ip-adapter_sdxl_vit-h` and `CLIP-ViT-H-14` are all
present, and `use_ipadapter` is enabled in `game_settings.json`. Nothing used it
because identity references meant sourcing reference art per species.

**`--kin` makes the set its own reference.** A lineage takes after its base
species, so the three gnomes read as one people instead of three independent
rolls. Weight defaults to 0.45: a lineage that comes back as a copy of its base
is as wrong as one that looks unrelated. A missing parent is skipped, so a cold
run still works (the base renders first, the lineages take after it).

Linking each FEMALE to her species' male (`--kin-cross-sex`) is available and
**off**, because it was tried and it failed: at 0.45 the halfling, reborn,
shifter, kalashtar, firbolg and tabaxi women came back as their own menfolk. A
reference face beats the prompt's sex cue outright — the useful generalisation
being that IP-Adapter overrides whatever the prompt says about the *subject*,
so only use it for things you want copied. Two portraits of one species must
look like the same PEOPLE, which the prompt already handles, not the same
PERSON.

```bash
uv run python -m imagery.species_portraits --lineages --kin --force --species gnome
```

## LoRA — and which situation gets which

Wired but **nothing installed** (`ComfyUI/models/loras/` is empty).

The tempting idea is a different LoRA per situation. Mostly resist it. There
are two kinds of LoRA and they answer different questions:

* a **STYLE** LoRA changes *how* a thing is drawn. Varying this per mood is how
  a game ends up looking like several games. One house style, everywhere, is
  the whole point — and it moves art direction out of the prompt entirely,
  handing the text budget back to anatomy, which is what four rounds of
  species-prompt surgery were actually fighting.
* a **SUBJECT/FUNCTION** LoRA changes *what* is drawn, or how it must be
  constructed. This one IS legitimately per-situation, because some renders
  have a different job.

In this pipeline exactly one kind has a different job:

| ImageKind | LoRA |
|---|---|
| `pc` `npc` `creature` `place` `item` `scene` | the house style — `loras` |
| `map` | a battlemap LoRA — `loras_by_kind["map"]` |

A battlemap must be dead-flat overhead with no perspective and no figures (see
`_MAP_NEGATIVE`) — the exact opposite of the cinematic rim-lit look everything
else wants, and today that fight is fought entirely in the negative prompt. A
battlemap LoRA moves it into the weights, the same argument that made PAG win.

```python
loras = [{"name": "house_style_xl.safetensors", "model": 0.8, "clip": 0.8}]
loras_by_kind = {"map": [{"name": "dnd_battlemaps_xl.safetensors", "model": 0.9}]}
```

A kind listed in `loras_by_kind` uses its list INSTEAD of `loras`, not on top.

### Judging a map LoRA: `scripts/map_lora_probe.py`

A map LoRA that nails a dungeon corridor and then draws a tavern in elevation
is worse than none — the grid the engine enforces stops matching what players
see. One good sample proves nothing, so the probe renders ALL 21 archetypes
through the real `vtt.art.render_battlemap` path and lays them out as one
sheet. Same `--seed` across runs makes two sheets directly comparable.

```bash
./.venv/Scripts/python.exe scripts/map_lora_probe.py --tag baseline
# ...configure loras_by_kind["map"], then
./.venv/Scripts/python.exe scripts/map_lora_probe.py --tag lora-0.9
```

**Baseline result (no LoRA, 2026-07-30) — 14 of 21 pass, and the 7 failures
are all the same failure.** Natural terrain is fine top-down: arena, bridge,
camp, cave, clearing, forest, mountain-pass, open, reef, ruins, sky-islands,
swamp, ship all hold flat overhead. What breaks is BUILT INTERIORS, which
revert to a side or oblique view:

| archetype | what it drew instead |
|---|---|
| `tavern` | a fireplace in elevation, seen from the side |
| `dungeon-room` | a floor/wall corner in perspective |
| `dungeon-complex` | a shelved wall at an angle |
| `crypt` | an archway seen from the side |
| `sewer` | a chasm from the side |
| `street` | strong oblique — buildings at an angle |
| `skyship` | tilted deck |
| `open-water` | flat, but framed in a wooden border (breaks full-bleed) |

That is the acceptance test for any candidate: **does it fix the interiors?**
The outdoor half needs no help. `_MAP_NEGATIVE` is already fighting this
battle in the prompt and losing on exactly these seven.

### Two cautions
* LoRAs are trained against a checkpoint family. One trained on SDXL/Juggernaut
  will not behave on the Pony `checkpoint_mature`; either curate a second set
  or leave the mature path bare.
* Start at `model: 0.6-0.8`. A style LoRA at 1.0 will happily overpower the
  species descriptors we just spent four rounds getting right.

## Card legibility: it was never a detail problem

I expected a detailer (`FaceDetailer`, Impact Pack) to be the fix for faces
mushing on the ~83px CC cards. Measuring first said otherwise, and saved a
third-party install:

| Change | Effect at 83px |
|---|---|
| unsharp after downscale | **marginal** — visible but small |
| render at the card's own 3:4 ratio | **large** — the obvious fix |

A species card is `aspect-ratio: 3/4` with `object-fit: cover`, and the
portraits were rendered SQUARE — so a quarter of every image was cropped away
unseen, and the composition was framed for a frame the player never gets.
Rendering at `896x1152` (an SDXL-native bucket at 0.78) puts every pixel on
screen and the face lands far bigger for the same file size.

The lesson generalises: at a 6x downscale, **composition survives and detail
does not**. Extra facial detail added at 1024px is gone by 83px — which is
exactly why a detailer would not have helped here. It is still worth
considering for SCENE art, which is displayed largest.

Sharpening is kept anyway (`encode_webp(sharpen=0.6)`, ~+17% bytes): it costs
nothing and does help the larger views — the ~250px species detail panel, the
180px PC portrait, the scene panel.

## Not installed
ControlNet (empty dir), Impact Pack / any detailer — see above for why the
detailer is not the win it looks like.
