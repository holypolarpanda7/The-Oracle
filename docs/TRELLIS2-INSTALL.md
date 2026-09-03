# TRELLIS.2 (image → 3D mesh) — install notes

How the ComfyUI TRELLIS.2 install on this machine survives, and the two things
that cost real time to find. Split out of `CLAUDE.md`.

**TRELLIS.2 (image -> 3D mesh) is installed, and how it survives matters.**
`ComfyUI-TRELLIS2` + `ComfyUI-GeometryPack` live in `D:\ComfyUI\custom_nodes`,
but their heavy dependencies do NOT: `comfy-env` provisions pixi environments
under `C:\Users\holyp\AppData\Local\Programs\comfy-env` (~9.7 GB), so the
host ComfyUI venv gained 27 packages and **zero version changes** — nothing
the SDXL pipeline depends on was touched. The package list from before the
install is kept at `D:\ComfyUI\venv-packages-before-trellis2.txt`.

Two things cost real time to find, and both will come back if the isolated
envs are ever rebuilt:

1. **`comfy_kitchen` registers its custom ops with PEP-585 annotations**
   (`kernel_size: list[int]`), and torch 2.6's `torch.library.infer_schema`
   matches parameter types against a DICT that only ever contained
   `typing.List[X]`. Same type to a reader, two different keys to a dict — so
   every op raised at import and ComfyUI registered **0 nodes** from both
   packs, with no error anywhere near the nodes themselves. The fix is a
   documented `sitecustomize.py` in each pixi env's `site-packages` that adds
   the builtin spellings beside the typing ones. It adds names and changes
   none. **Delete `comfy-env\install.hash` and reinstall and the shim is
   gone** — re-copy it, or the packs silently register nothing again.
   (125 GeometryPack + 24 TRELLIS2 nodes when it is in place.)
2. The env is pinned to **torch 2.6 on purpose** — comfy-env matches the host
   so tensors cross the boundary, and every CUDA wheel is built
   `+cu124torch2.6-cp312`. Do not "fix" the shim by upgrading torch there.
   (Other TRELLIS2 wrappers ship hand-built cp311/torch2.8 wheels and would
   not have worked on this machine at all; this one builds its own.)

The gated `facebook/dinov3-*` encoder needs no HuggingFace login: the wrapper
remaps it to a public reupload. Weights auto-download to
`ComfyUI/models/trellis2` on first use — `microsoft/TRELLIS.2-4B` is 16.2 GB
whole, and a geometry-only 512 run pulls ~6 GB of it plus ~1.2 GB of DINOv3.
At 2.58 GB per 1.3B model on a 12.9 GB card that also holds SDXL, it has to
time-share the way the local LLM already does.

