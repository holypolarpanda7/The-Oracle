# Freeing the GPU (so you can game)

Two things on this machine hold VRAM: **ComfyUI** (SDXL and TRELLIS, port 8188)
and **Ollama** (the local LLM, port 11434). Nothing else does — the backend, the
Discord bot and the Cloudflare tunnel are all CPU.

**None of this destroys anything.** No database, no world state, no generated
art. It only unloads models out of VRAM. Everything reloads by itself the next
time it is asked for.

---

## The one you want

**Double-click `launcher\free_gpu.bat`.**

It cancels whatever ComfyUI has queued, unloads its models, unloads any Ollama
model, and prints the VRAM before and after. ComfyUI and Ollama stay running,
so nothing needs restarting afterwards — the next render just loads its model
back in and takes an extra ~20 s.

Measured on this rig: **8650 MiB → 2026 MiB.**

## If that is not enough

**`launcher\free_gpu.bat /hard`** — same thing, then stops ComfyUI and Ollama
outright. Use it if a batch keeps re-loading models, or you want the last ~2 GB.

To bring them back: `launcher\run_comfyui.bat` and `launcher\run_ollama.bat`
(ComfyUI takes ~40 s to be ready). Ollama also restarts on its own the next time
anything asks it for something.

---

## The raw commands

If you would rather paste than click. **PowerShell:**

```powershell
# cancel the queue, unload ComfyUI's models
irm -Method Post -Uri http://127.0.0.1:8188/queue     -ContentType application/json -Body '{"clear":true}'
irm -Method Post -Uri http://127.0.0.1:8188/interrupt -ContentType application/json -Body '{}'
irm -Method Post -Uri http://127.0.0.1:8188/free      -ContentType application/json -Body '{"unload_models":true,"free_memory":true}'

# unload whatever Ollama has loaded
ollama ps
ollama stop <the model name that printed>

# how much is actually free
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

**Hard stop, PowerShell:**

```powershell
Get-NetTCPConnection -LocalPort 8188  -State Listen | %{ Stop-Process -Id $_.OwningProcess -Force }
Get-NetTCPConnection -LocalPort 11434 -State Listen | %{ Stop-Process -Id $_.OwningProcess -Force }
```

**From the WSL terminal** (the same scripts, no PowerShell window needed):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'D:\Projects\The Oracle\launcher\free_gpu.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'D:\Projects\The Oracle\launcher\free_gpu.ps1' -Hard
```

---

## Two things worth knowing

**Clear the queue before unloading.** Freeing VRAM while a batch is still
running buys you a few seconds — the next job in the queue loads the model
straight back in. That is why the script clears the queue first, and it is the
main reason a manual `/free` sometimes seems not to work.

**A killed render costs nothing but its own time.** Every batch in this project
is resumable: swatches, item art, species portraits and debris sprites are all
keyed by slug and skip what already exists, so re-running picks up where it
stopped. Stop a batch whenever you like.
