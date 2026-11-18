# Headless Flux.2 Klein + LanPaint

This project can use a private, loopback-only ComfyUI daemon for higher-quality text
removal. The runtime is separate from the backend Python environment and is pinned to
known ComfyUI and LanPaint commits.

The backend side of the integration is `backend/src/manga_backend/comfy_lanpaint/` (client, workflow, crop planning, CLI) and `backend/src/manga_backend/models/flux_lanpaint_inpainter.py`; the install, start, and benchmark helpers are in `scripts/comfy-lanpaint/`.

## Install

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\install.ps1
```

The installer creates `backend\.runtime\ComfyUI` and `backend\.venv-comfy`, installs
CUDA PyTorch plus the matching Windows Triton runtime for Comfy-Kitchen fused kernels,
and downloads the minimal model set:

- FLUX.2 Klein Distilled 4B FP8 (the four-step default)
- FLUX.2 Klein Base 4B FP8 (kept for manual comparison or fallback)
- Qwen 3 4B FP4 Flux.2 text encoder
- Flux.2 VAE, hard-linked from the existing local copy when possible

The installed runtime is intentionally ignored by Git. Distilled is downloaded by
default because it gives the best quality/latency balance on this 6 GB GPU. To omit
it for a Base-only reinstall, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\install.ps1 -IncludeDistilled:$false
```

Both checkpoints can coexist; the backend selects Distilled unless
`COMFY_LANPAINT_MODEL` overrides it.

The extension exposes this runtime under **Settings > Local Backend > Inpainter** as
`FLUX.2 Klein Distilled + LanPaint (Quality)`, alongside `LaMa (Fast)` and `None`.
Fresh settings select Distilled LanPaint; an existing saved LaMa choice is preserved.
The LanPaint prompt is deliberately general so the same option can reconstruct
photographs, illustrations, furniture, appliances, flat surfaces, and textured
backgrounds without material-specific configuration.

## Run

Start the hidden daemon:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\start.ps1
```

`balanced` is the default profile for a 6 GB RTX 4050. `fast` keeps less VRAM in
reserve, while `compatibility` restores CPU VAE and disables experimental kernels.
Startup prewarms the model by default so the first real translation does not pay the
model-load penalty; pass `-SkipWarmup` only when startup time matters more:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\start.ps1 -Profile fast
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\start.ps1 -Profile compatibility
```

On a 16 GB system running Chrome and the translation backend together, disable
pinned host memory to reduce RAM pressure without changing the model, sampling
steps, crop settings, or image quality:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\start.ps1 `
  -Profile balanced -DisablePinnedMemory
```

ComfyUI listens only on `http://127.0.0.1:8188`. Stop it with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\stop.ps1
```

Run a standalone binary-mask job:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\comfy-lanpaint\inpaint.ps1 `
  -Image "C:\path\page.png" `
  -Mask "C:\path\mask.png" `
  -Output "C:\path\clean.png"
```

Measure the real daemon path, crop sizes, and exact outside-mask preservation. The
benchmark imports the backend package, so run it with the environment that
`backend\start.ps1` creates (`backend\.venv-gpu`), and sync that environment first
(for example by running `powershell -File backend\start.ps1 -Runtime gpu` once):

```powershell
backend\.venv-gpu\Scripts\python.exe scripts\comfy-lanpaint\benchmark.py `
  --image "C:\path\page.png" --mask "C:\path\mask.png" `
  --output-dir "backend\.runtime\bench" --profile balanced --runs 2
```

White mask pixels are regenerated; black pixels remain bit-identical. Nearby glyphs
are grouped before context is added, preventing overlapping context from turning
distant text into a full-page diffusion job. Crops are bounded, padded to multiples
of 64 without resizing, and stitched back locally. The backend exposes the route as
`flux2_klein_lanpaint` and uses it by default. If the daemon fails, the local
Flux.2 Klein Q4 runner is used as a fallback only when you opt in with
`COMFY_LANPAINT_FALLBACK_Q4=1`. Otherwise a daemon that is down, or stops answering,
makes the backend inpaint that page with LaMa (`X-Inpainter-Used: lama_large`) and
probe ComfyUI again after 60 seconds; with `INPAINTER_FALLBACK=off` the request
returns a 503 setup hint instead. The Q4 runner needs
`SD_CPP_CLI` and `SD_CPP_MODELS_DIR` set unless sd-cli and its models live in
`backend/models_cache/flux2_klein_q4`; see the inpainter section of
`backend/README.md`. A masked region too large for the crop budget skips LanPaint
for that page and returns the LaMa prefill result instead (or the untouched page
when prefill is off).

## Tuning

The daemon launcher's `-Profile` (`fast`, `balanced`, `compatibility`) and the backend's
`COMFY_LANPAINT_PROFILE` (`fast`, `balanced`, `quality`) are different knobs. The first
sets ComfyUI's memory behaviour; the second sets the backend's crop and sampling defaults
below. `backend\start.ps1 -LanPaintProfile` sets the backend one, which defaults to `fast`.

Profile defaults (`COMFY_LANPAINT_PROFILE`):

| Profile | Context | Max crop pixels | Steps | LanPaint iterations |
| --- | ---: | ---: | ---: | ---: |
| `fast` (default) | 96 | 262144 | 4 | 1 |
| `balanced` | 128 | 393216 | 4 | 1 |
| `quality` | 160 | 524288 | 4 | 1 |

Variables, with the value used when unset:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | Local daemon URL |
| `COMFY_LANPAINT_PROFILE` | `fast` | `fast`, `balanced`, or `quality` defaults (table above) |
| `COMFY_LANPAINT_MODEL` | `flux-2-klein-4b-fp8.safetensors` | Official Distilled checkpoint |
| `COMFY_LANPAINT_ENCODER` | `qwen_3_4b_fp4_flux2.safetensors` | Text encoder file |
| `COMFY_LANPAINT_VAE` | `flux2-vae.safetensors` | VAE file |
| `COMFY_LANPAINT_CONTEXT` | profile (96/128/160) | Validated context around each mask group |
| `COMFY_LANPAINT_GROUP_X` | `32` | Horizontal distance used to group nearby glyphs |
| `COMFY_LANPAINT_GROUP_Y` | `16` | Vertical distance used to group nearby glyphs |
| `COMFY_LANPAINT_MAX_CROP_PIXELS` | profile | Aligned crop pixel budget before context shrinks |
| `COMFY_LANPAINT_MASK_GROWTH` | `2` | Extra generation pixels around the exact commit mask (also dilates the LaMa prefill mask) |
| `COMFY_LANPAINT_FEATHER` | `1` | Feather width when stitching a generated crop back |
| `COMFY_LANPAINT_STEPS` | profile (4) | Native Distilled sampling budget |
| `COMFY_LANPAINT_INNER_STEPS` | profile (1) | LanPaint iterations per step |
| `COMFY_LANPAINT_CLIP_DEVICE` | `default` | GPU-first text encoding; use `cpu` after OOM |
| `COMFY_LANPAINT_GUIDANCE` | `1.0` | Distilled Flux guidance |
| `COMFY_LANPAINT_CFG` | `1.0` | LanPaint CFG |
| `COMFY_LANPAINT_DENOISE` | `0.25` with LaMa prefill, else `1.0` | Sampler denoise strength |
| `COMFY_LANPAINT_LAMA_PREFILL` | `1` | Run LaMa over the masked text first (see below); `0` disables |
| `COMFY_LANPAINT_PREFILL_MODE` | `page` | `page` runs LaMa once on the whole page; `crops` runs it per padded crop |
| `COMFY_LANPAINT_PREFILL` | `1` | Telea prefill of each crop's masked pixels, used when the LaMa prefill is off or fails; `0` disables |
| `COMFY_LANPAINT_PREFILL_RADIUS` | `5` | Telea inpaint radius in pixels (`0` disables the Telea prefill) |
| `COMFY_LANPAINT_FILL_GENERATION_BOXES` | `0` | `1` hides each grouped text box from the model while keeping the commit mask precise |
| `COMFY_LANPAINT_ADAPTIVE_REFINEMENT` | `0` | `1` raises LanPaint iterations to `HARD_INNER_STEPS` on hard crops |
| `COMFY_LANPAINT_ADAPTIVE_PROMPT_MODE` | `0` | `1` switches hard crops to `Prompt First` |
| `COMFY_LANPAINT_HARD_INNER_STEPS` | `2` | LanPaint iterations used on hard crops |
| `COMFY_LANPAINT_HARD_MASK_RATIO` | `0.25` | Masked fraction of a crop at which it counts as hard |
| `COMFY_LANPAINT_HARD_MASK_PIXELS` | `65536` | Masked pixel count at which a crop counts as hard |
| `COMFY_LANPAINT_NATIVE_SCHEDULE` | `0` | `1` uses the native Flux.2 schedule |
| `COMFY_LANPAINT_PROMPT_MODE` | `Image First` | LanPaint prompt mode |
| `COMFY_LANPAINT_FALLBACK_Q4` | `0` | Opt in (`1`) to use the Q4 runner after daemon failure |

The `0`/`1` switches treat any value other than `0` as on. `COMFY_LANPAINT_PROFILE` and
`COMFY_LANPAINT_PREFILL_MODE` reject unknown values when the inpainter loads.

### Default LaMa structural prefill

By default the backend first runs its LaMa inpainter over the whole page with the
text mask (dilated by `COMFY_LANPAINT_MASK_GROWTH`), then lets LanPaint refine the
cropped regions starting from that result at denoise 0.25. This keeps lines and
patterns continuous through the mask while Flux only has to repair detail. The LaMa
model (`lama_large`, `ogkalu/lama-manga-onnx-dynamic`) is therefore an implicit
dependency of the default setup: the backend downloads it on first use into
`backend/models_cache/`, and it is loaded and released around each page. If the LaMa
prefill fails, the backend logs a warning, falls back to the Telea prefill, and runs
those crops at denoise 1.0. With `COMFY_LANPAINT_LAMA_PREFILL=0` the denoise default is
also 1.0, so only the Telea prefill (if enabled) shapes the starting image.

## RTX 4050 benchmark

Measured on the local 6 GB RTX 4050 with the 716 x 932 cover and its full cleanup
mask (fixed seed, three bounded crops):

| Configuration | First run | Warm run |
| --- | ---: | ---: |
| Previous 20/2, cache disabled, CPU VAE | 219-375 s | not cached |
| Balanced 12/1 + RAM-pressure cache + fused kernels | 75.0 s | 44.2 s |
| Fast 8/1 + RAM-pressure cache + fused kernels | 30.5 s | 25.2 s |
| Fast 8/1 without fused kernels | 52.5 s | 29.9 s |
| Fast 8/1 + WebSocket output + embedded alpha mask | prewarmed | 23-24 s |
| Distilled FP8 4/1 + optimized transport | 36.0 s model switch | 14-16 s |

All benchmark outputs preserved pixels outside the supplied binary mask exactly. The
optimized transport returns the generated image directly over WebSocket and embeds the
binary mask in the uploaded source alpha channel, avoiding output-disk polling and a
second upload. A fixed-seed comparison against the previous two-upload path was
byte-for-byte identical. Sampling steps, crop context, model weights, and VAE settings
are unchanged. The custom-kernel comparison is a short local A/B rather than a
statistically controlled hardware benchmark; use `benchmark.py --runs 5` for a stable
p50 on a fixed power and thermal profile.

The Distilled measurement used the same source, mask, three crop regions, prompt,
FP4 text encoder, VAE, seed, and stitching as Base. Distilled is now the default at
four steps, guidance 1, and one LanPaint iteration; this measurement used 128 pixels
of context (the `balanced` profile; the backend default profile is now `fast`).
Tests with CFG 2, two LanPaint iterations, native Flux.2 scheduling, and larger
generation boxes were rejected because they either reinforced the source lettering
or damaged the reconstructed background, so those stay off by default.
