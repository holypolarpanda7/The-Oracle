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

Three installed: `DD_Painterly_Clean` + `DarkFanXLGrain` as the house stack,
`SDXL-Battlemaps` for the `map` kind. Each has a probe and a measured strength
below — none of them was chosen by looking at one render.

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

**Result: `SDXL-Battlemaps.safetensors` at 0.5 passes 21 of 21.** Every
archetype comes back flat overhead in one consistent VTT style — the seven
elevation/oblique failures are gone. Configured in `game_settings.json` under
`loras_by_kind.map`.

Strength mattered more than the choice of LoRA:

| strength | result |
|---|---|
| 0.8 | interiors fixed, but outdoor terrain went muddy and the tavern came back an abstract red-lit plan |
| **0.5** | **interiors fixed AND terrain quality kept — tables, bar and chairs in the tavern** |

Higher was worse, the same lesson PAG-vs-CFG taught: more force sharpens the
model's own preference rather than your instruction. Start low.

`--lora NAME[:STRENGTH]` (also on `species_portraits`) applies a stack for one
run without touching config, so candidates and strengths can be swept quickly.

### A drawn grid cannot be the measurement source

A battlemap LoRA that draws its own 5-ft squares is tempting to measure off.
It cannot be, and the arithmetic is the reason. The engine's pitch is
`canvas_size(w,h) / squares`, which is fractional and board-dependent:

| board | canvas | px per square |
|---|---|---|
| 20x20 | 1024x1024 | 51.20 |
| 20x15 | 1216x896 | 60.80 wide vs **59.73 tall** |
| 30x20 | 1280x832 | 42.67 wide vs **41.60 tall** |
| 40x30 | 1216x896 | 30.40 wide vs **29.87 tall** |

On a non-square board the two axes do not even agree, so no uniform drawn grid
can match both. A diffusion model draws a grid at whatever spacing looks right,
with phase error and cumulative drift over twenty cells — it will not land on
51.20px. If players measure off the drawn squares while movement validation
uses the engine's, a token three squares away by eye is four by the rules.

So the drawn grid stays TEXTURE, exactly as the architecture already says ("the
picture is a TEXTURE, the tile grid is the truth"). Either keep negating it via
`_MAP_NEGATIVE` and let the engine overlay be the only grid, or accept a
decorative one underneath and never measure from it.

The probe prints the strongest pitch near the engine's for each render as a
HINT — not a verdict. Deciding "ruled grid" vs "row of flagstones" from the
spectrum alone did not survive real map art: every threshold either missed
faint grids or called a cave pool one. The number is worth knowing; confirm on
the sheet.

**Measured with the LoRA in place, the drawn grid lands 5-67% off** the
engine's pitch across the 21 archetypes — never reliably aligned, exactly as
the arithmetic predicts. It is a pretty texture. Do not measure from it.

## Trigger words

A LoRA trained against a caption tag only half-fires without it. The tag is a
property of the FILE, so it lives beside the LoRA in config rather than in the
house style string — swap the LoRA and the tag goes with it. `ComfyClient`
appends every active LoRA's `trigger` to the positive prompt (appended, not
prepended, so it can never outrank the weighted subject clause).

```python
loras = [{"name": "DD_Painterly_Clean.safetensors", "model": 0.45,
          "clip": 0.45, "trigger": "d&d painterly"}]
```

Read the tag off the file rather than guessing — `ss_tag_frequency` in the
safetensors metadata records the training captions:

* `DD_Painterly_Clean` -> **"d&d painterly"**, on all 138 training images.
* `SDXL-Battlemaps` -> **"battlemap"**, which opens every one of its captions.
  This one was already firing by luck: `_KIND_FRAMING[MAP]` happens to contain
  the word. Now it is explicit rather than accidental.
* `DarkFanXLGrain` -> two tags, and we deliberately fire only ONE. Its 198
  captions carry **"dark fantasy art style"** (99) *and* **"grainy texture"**
  (86). The first is the look we want; the second is film grain, which fights
  the "Clean" in the house LoRA and degrades to plain noise at the ~6x
  downscale a CC card does. A LoRA's tags are separable — when one names a
  quality you do not want, leave it out of the trigger rather than paying for
  it and then negating it.

**Installed house style: `DD_Painterly_Clean` at 0.45 + `DarkFanXLGrain` at
0.20.** The painterly LoRA is warmer, better-painted armour and skin, more
D&D-book than the bare checkpoint — and gentle enough that it did not overrun
the species descriptors, which is exactly what a style LoRA at 1.0 would have
wrecked. The dark-fantasy LoRA is stacked *on top of* it, not instead: it adds
grit, contrast and menace without owning the look.

### Judging a house-style LoRA: `scripts/style_lora_probe.py`

The house style applies to `pc` `npc` `creature` `place` `item` `scene` at
once, so a candidate that flatters a crypt and turns the starter village into a
charnel house is a net loss. One good portrait proves nothing — the probe
renders a fixed subject set across every kind, at every strength, from the SAME
seed, so the sheet's columns differ only by the LoRA.

```bash
./.venv/Scripts/python.exe scripts/style_lora_probe.py --tag darkfan \
    --lora DD_Painterly_Clean.safetensors:0.45 \
    --sweep DarkFanXLGrain.safetensors --weight 0 --weight 0.2 --weight 0.35 --weight 0.5
```

The rows are chosen to put the failure modes on screen, not the flattering
cases. **Judge a dark style on the BRIGHT rows** — a sunlit village square and
a plain pair of boots are where it overreaches; the crypt will always look
better and tells you nothing. The species rows go through the real 166-word
portrait prompt (`build_positive`), because the descriptor-overrun failure only
appears at that length. Renders bypass the image DB (`ImageStore._render`), so
sweeping costs nothing but GPU time.

The printed `diff` column is a gate to clear BEFORE any aesthetic call: mean
absolute pixel difference against that row's weight-0 render. 0.00 means the
LoRA did nothing.

**Result: `DarkFanXLGrain` at 0.20.** Measured over 7 rows x 4 strengths:

| strength | result |
|---|---|
| 0.20 | **grit, contrast and better material texture; the village stays sunlit and every descriptor survives** |
| 0.35 | barely darker than 0.20, and the goliath's markings turn decorative (swirls) — more warpaint, not more goliath |
| 0.50 | darker again for no gain; the same drift, further along |

**Nearly the whole effect lands by 0.20.** The village square moves 46.78/255
at 0.20 and only 49.23 at 0.50 — the last 0.30 of strength buys ~5% more
change, all of it in the wrong direction. That is the third time this lesson
has held here (PAG over high CFG, battlemaps at 0.5 over 0.8): past the point
where a layer is *working*, more force sharpens the model's own preference
rather than your instruction. Start low, and stop as soon as it fires.

Where it clearly helps: the owlbear's ruff finally reads as FEATHERS rather
than more bear fur, and the crypt gains depth. Where it must not hurt, it
didn't: the village square keeps its blue sky and flower boxes, the boots are
near-untouched (15.05, the lowest diff on the sheet — the mundane-item style
override survives), and the tiefling stays purple and horned.

**Unrelated finding, recorded so it is not misread as LoRA damage:** at this
seed the goliath comes back as tanned skin with blue-grey *patches* — the
documented pre-existing failure — at weight 0 as well as at every other
weight. The dark-fantasy LoRA neither causes nor fixes it. PAG is confirmed on
(3.0) during the probe, so the "solid blue-grey" result recorded above does not
reproduce on this prompt/seed and is worth a re-measure on its own.

## Checking a LoRA before you install it

The filename lies about architecture often enough to check. A safetensors
header is readable without loading the file:

```python
import json, struct
with open(path, "rb") as f:
    hdr = json.loads(f.read(struct.unpack("<Q", f.read(8))[0]))
keys, meta = [k for k in hdr if k != "__metadata__"], hdr.get("__metadata__", {})
```

* SDXL — `lora_te1_*` AND `lora_te2_*` keys (two text encoders), `input_blocks`,
  and usually `modelspec.architecture: stable-diffusion-xl-v1-base/lora`.
* Flux / Krea / other DiT — `transformer.*.lora_A/lora_B`, `double_blocks`.
  **Will not load on SDXL**, whatever the model page implies.
* SD1.5 — `lora_te_*` only (one text encoder).

**A mismatched LoRA is a silent no-op, not an error.** `LoraLoader` skips keys
it cannot match, so the render succeeds and even yields a byte-different file —
which makes it very easy to conclude it worked. Neither "it rendered" nor "the
file hash changed" is evidence. The only reliable test is a PIXEL comparison at
a fixed seed:

```python
a = np.asarray(Image.open(with_lora).convert("RGB"), float)
b = np.asarray(Image.open(without).convert("RGB"), float)
print(np.abs(a - b).mean())        # 0.00 => the LoRA did nothing
```

Measured here: `DarkFanKrea2` (Flux/Krea keys) scored **0.00/255 mean diff and
0.0% of pixels changed at BOTH 0.5 and 1.0** — pixel-identical to no LoRA. A
working one, `DD_Painterly` at 1.0, scored 63.65/255 and 94.9%. The file has
since been deleted rather than parked: a dud LoRA on disk is 230 MB of
temptation to re-test something already measured. The numbers are the artifact
worth keeping, not the weights.

The dark-fantasy slot was refilled by a LoRA that PASSES this check —
`DarkFanXLGrain` has `lora_te1_*`, `lora_te2_*` and `lora_unet_*` keys with
`ss_base_model_version: sdxl_base_v0-9`, and moved 15-49/255 across the probe
sheet. Check the header BEFORE the download finishes being interesting:
"dark fantasy SDXL LoRA" described both files equally well, and only one of
them was one.

Pony and Illustrious are SDXL-architecture and WILL load on Juggernaut, but are
tuned for different conditioning; a Pony variant properly belongs on the
`checkpoint_mature` path, which is Pony.

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
