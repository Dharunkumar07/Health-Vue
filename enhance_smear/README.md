# enhance_smear.py

Blood smear microscopy image enhancement pipeline using **CellPose** for cell
segmentation. Cleans up illumination, matches colour to a reference stain,
and enhances WBCs and platelets so they stand out clearly for diagnostic
viewing.

---

## What it does (pipeline order)

1. **Denoise + flatten illumination** — bilateral filter followed by
   background flattening in LAB space to remove uneven lighting.
2. **CellPose segmentation** — `cyto3` model (GPU) produces per-cell label
   masks at reduced resolution, then upsampled back to full size.
3. **Build per-cell ellipse masks** — each cell is fit to a smooth ellipse
   (`cv2.fitEllipse`); tiny objects (5–79 px) are treated as
   platelet-sized fills only (no border ring).
4. **Pre-match platelet detection** — platelets are found by a saturation
   spike in the cell-free gaps *before* colour matching, since matching
   would destroy that saturation signal.
5. **Colour / LAB matching** — shifts the image's LAB mean/std toward one
   or more reference images (averaged if multiple `-r` refs are given), or
   falls back to a simple brightness/contrast stretch if no reference is
   given.
6. **WBC detection** — run *after* colour matching, looking for dark,
   saturated, purple-hued regions.
7. **Post-match platelet detection** — a second pass after colour matching
   using a stricter dark+saturated AND condition, merged with the pre-match
   result.
8. **Background whitening** — background pixels (not cells, not platelets,
   not WBCs) are pulled toward white.
9. **Smooth ellipse border rings** — cell borders are drawn/darkened for
   visual clarity.
10. **WBC purple enhancement** — nucleus hue pulled toward violet, boosted
    saturation and value.
11. **Platelet visibility boost** — platelets pulled toward dark purple dots
    so they're clearly distinguishable from RBCs.
12. **Neutralise near-white haze** — removes residual low-saturation haze
    in bright regions.
13. *(optional)* **Global saturation boost** if `-s/--saturation` is passed.

## Why pre-match-only platelet detection exists

Detecting platelets *only* after colour matching created a destructive
feedback loop on lavender-tinted slides: loose thresholds flagged leftover
background tint as platelets, which blocked background whitening, which
kept the background tinted, which caused even more false positives. Doing
an additional **pre-match** detection pass (where platelets are reliably
more saturated than background, before that signal gets washed out) fixes
this, and the two passes are merged (`union`) in the final mask.

---

## Requirements

- Python 3.9+
- A CUDA-capable GPU is strongly recommended (`CellposeModel(gpu=True)` is
  hardcoded in the script)
- See `requirements.txt` for exact packages

## Installation

```bash
pip install -r requirements.txt
```

See `command.txt` for the exact recommended install sequence, including the
GPU-enabled PyTorch install step.

---

## Usage

```bash
python3 enhance_smear.py input.png -r ref1.png [-r ref2.png ...] -o result.jpeg
```

### Arguments

| Flag | Long form | Default | Description |
|---|---|---|---|
| (positional) | `input` | — | Path to the input smear image |
| `-o` | `--output` | `<input>_cp.jpeg` | Output file path |
| `-r` | `--reference` | `None` | Colour reference image. Repeatable — pass multiple times to average several references (e.g. a normal-RBC reference and a WBC-heavy reference together) |
| `-d` | `--max-dim` | `960` | Max image dimension used for CellPose inference (lower = faster, less precise) |
| `-q` | `--quality` | `95` | Output JPEG quality (1–100) |
| `-s` | `--saturation` | `1.0` | Global saturation multiplier applied at the very end |

### Examples

Basic enhancement with one colour reference:
```bash
python3 enhance_smear.py smear1.png -r reference_pink.png -o smear1_enhanced.jpeg
```

Average two references (normal RBC stain + a WBC-rich reference):
```bash
python3 enhance_smear.py smear1.png -r ref_normal.png -r ref_wbc.png -o out.jpeg
```

No reference (falls back to automatic brighten/levels stretch):
```bash
python3 enhance_smear.py smear1.png -o out.jpeg
```

Faster, lower-resolution CellPose pass + extra saturation boost:
```bash
python3 enhance_smear.py smear1.png -r ref.png -d 640 -s 1.2 -o out.jpeg
```

---

## Notes / gotchas

- The CellPose model is loaded once and cached in the module (`_MODEL`) —
  cheap to call `enhance()` repeatedly in the same process (e.g. in a batch
  script or web service).
- `gpu=True` is hardcoded in `_load_model()`. If you don't have a working
  CUDA + PyTorch GPU setup, this will fail or silently fall back depending
  on your `torch`/`cellpose` install — install the correct CUDA build of
  `torch` first (see `requirements.txt` notes and `command.txt`).
- Use `opencv-contrib-python`, not plain `opencv-python` — mixing the two
  in the same environment causes import errors.
