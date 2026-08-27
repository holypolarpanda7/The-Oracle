# Free the GPU for something else (a game). Nothing here touches the database,
# the world, or any generated art -- it only unloads models from VRAM.
#
# Two levels:
#   .\free_gpu.ps1          unload models, LEAVE ComfyUI and Ollama running
#   .\free_gpu.ps1 -Hard    stop ComfyUI and Ollama outright
param([switch]$Hard)

function VRAM {
  try { (nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader) }
  catch { "no nvidia-smi on PATH" }
}

Write-Host "before: $(VRAM)" -ForegroundColor DarkGray

# --- ComfyUI (SDXL, TRELLIS) --------------------------------------------
# Cancel what is queued FIRST. Unloading while a batch is still running just
# means the next job loads the model straight back in.
try {
  irm -Method Post -Uri http://127.0.0.1:8188/queue `
      -ContentType application/json -Body '{"clear":true}' | Out-Null
  irm -Method Post -Uri http://127.0.0.1:8188/interrupt `
      -ContentType application/json -Body '{}' | Out-Null
  irm -Method Post -Uri http://127.0.0.1:8188/free `
      -ContentType application/json `
      -Body '{"unload_models":true,"free_memory":true}' | Out-Null
  Write-Host "ComfyUI: queue cleared, models unloaded" -ForegroundColor Green
} catch {
  Write-Host "ComfyUI: not running (nothing to free)" -ForegroundColor DarkGray
}

# --- Ollama (the local LLM) ---------------------------------------------
try {
  $loaded = @(ollama ps 2>$null | Select-Object -Skip 1 |
              ForEach-Object { ($_ -split '\s+')[0] } |
              Where-Object { $_ })
  if ($loaded.Count) {
    foreach ($m in $loaded) { ollama stop $m 2>$null | Out-Null }
    Write-Host "Ollama: unloaded $($loaded -join ', ')" -ForegroundColor Green
  } else {
    Write-Host "Ollama: no model loaded" -ForegroundColor DarkGray
  }
} catch {
  Write-Host "Ollama: not running" -ForegroundColor DarkGray
}

if ($Hard) {
  # Stop the servers themselves. Safe: ComfyUI keeps nothing in memory that
  # matters, and Ollama's tray app restarts the server on the next request.
  foreach ($port in 8188, 11434) {
    $owner = (Get-NetTCPConnection -LocalPort $port -State Listen `
              -ErrorAction SilentlyContinue).OwningProcess
    foreach ($p in $owner) {
      Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
      Write-Host "stopped the process listening on $port (pid $p)" `
                 -ForegroundColor Yellow
    }
  }
}

Start-Sleep -Seconds 3
Write-Host "after:  $(VRAM)" -ForegroundColor Cyan
