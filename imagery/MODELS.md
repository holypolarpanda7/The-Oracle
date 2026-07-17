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
