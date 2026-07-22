"""
enhance_smear.py — Blood smear enhancement using CellPose.

Pipeline:
  1.  Denoise + flatten illumination
  2.  CellPose cell segmentation (GPU, cached model)
  3.  Build per-cell ellipse masks  ← EARLY: needed for gap-based platelet detection
  4.  Pre-match platelet detection  (saturation spike in cell-free gaps, before colour
                                     matching can destroy the saturation signal)
  5.  Colour / LAB matching to reference(s)   — multiple -r images are averaged
  6.  WBC detection                ← POST colour matching
  7.  Background whitening          (protected by ellipses + platelets + WBCs)
  8.  Draw smooth ellipse border rings
  9.  WBC purple enhancement
 10.  Platelet visibility boost     (strong pull toward dark purple dots)
 11.  Neutralise near-white haze

WHY PRE-MATCH-ONLY PLATELET DETECTION:
  Post-match detection (looking for non-white pixels in gaps) was tried but
  created a destructive feedback loop on lavender slides:
    1. After colour matching the background is ~white but not perfectly so;
       loose thresholds (val<232 OR sat>12) flagged background noise as platelets.
    2. platelet_prot became huge → background whitening was blocked → lavender
       background stayed → detected as even more "platelets" → hundreds of
       false-positive blue dots in the output.

  Pre-match detection is reliable:
    • Platelets have sat >> background before matching (sat≈90 vs bg≈25–30).
    • Threshold = max(25, median_gap_sat × 1.8) is adaptive to the slide.
    • fill_mask is expanded by 7 px before gap computation so cell-border
      pixels — which are also saturated — are excluded from consideration.
    • max_area=150 prevents small clusters of border artifacts from surviving.

Usage:
  python3 enhance_smear.py input.png -r ref1.png [-r ref2.png] -o result.jpeg
"""

import cv2
import numpy as np
from scipy import ndimage as ndi
from scipy.ndimage import find_objects

# ── CellPose model cache ──────────────────────────────────────────────────────
_MODEL = None


def _load_model():
    global _MODEL
    if _MODEL is None:
        from cellpose import models
        _MODEL = models.CellposeModel(gpu=True, model_type='cyto3')
        print("[cellpose] model loaded and cached")
    return _MODEL


def segment(img_bgr, max_dim=960):
    """CellPose at reduced resolution.  Returns per-cell integer label array."""
    h, w   = img_bgr.shape[:2]
    scale  = max_dim / max(h, w)
    iw, ih = max(160, int(w * scale)), max(120, int(h * scale))
    small  = cv2.resize(img_bgr, (iw, ih), interpolation=cv2.INTER_AREA)
    model  = _load_model()
    masks, _, _ = model.eval(
        cv2.cvtColor(small, cv2.COLOR_BGR2RGB),
        diameter=None, channels=[0, 0],
        flow_threshold=0.4, cellprob_threshold=0,
    )
    labels = cv2.resize(masks.astype(np.uint16), (w, h),
                        interpolation=cv2.INTER_NEAREST)
    print(f"[cellpose] {iw}x{ih} → {int(labels.max())} cells")
    return labels.astype(np.int32)


# ── Ellipse masks from CellPose labels ───────────────────────────────────────

def build_masks(labels, max_ellipse_area=4000):
    """
    Per-label: fill ring-cell interior on bbox crop (fast), fit smooth ellipse.
    Size bands:
      < 5px   : skip (noise)
      5–79px  : platelet/tiny  — fill only, no border ring
      ≥ 80px  : normal cell    — ellipse fill + border (if ≤ max_ellipse_area)
    """
    h, w        = labels.shape
    fill_mask   = np.zeros((h, w), np.uint8)
    border_mask = np.zeros((h, w), np.uint8)
    slices      = find_objects(labels)

    for lb_i, sl in enumerate(slices, 1):
        if sl is None:
            continue
        r1 = max(0, sl[0].start - 2); r2 = min(h, sl[0].stop + 2)
        c1 = max(0, sl[1].start - 2); c2 = min(w, sl[1].stop + 2)
        m_crop = (labels[r1:r2, c1:c2] == lb_i).astype(np.uint8)
        area   = int(m_crop.sum())
        if area < 5:
            continue

        filled  = ndi.binary_fill_holes(m_crop > 0).astype(np.uint8)
        cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            continue
        cnt      = max(cnts, key=cv2.contourArea)
        cnt_full = cnt + np.array([[[c1, r1]]])

        if area < 80:
            cv2.drawContours(fill_mask, [cnt_full], -1, 255, -1)
            continue

        if len(cnt) >= 5:
            try:
                ell = cv2.fitEllipse(cnt)
                (_, _), (ma, mi), ang = ell
                ell_full = ((ell[0][0] + c1, ell[0][1] + r1), ell[1], ang)
                cv2.ellipse(fill_mask, ell_full, 255, -1)
                if np.pi * (ma / 2) * (mi / 2) <= max_ellipse_area:
                    cv2.ellipse(border_mask, ell_full, 255, 2)
                continue
            except Exception:
                pass
        cv2.drawContours(fill_mask,   [cnt_full], -1, 255, -1)
        cv2.drawContours(border_mask, [cnt_full], -1, 255,  1)

    return fill_mask, border_mask


# ── WBC detection ─────────────────────────────────────────────────────────────

def find_wbc_mask(img, min_area=200, sat_thresh=60, val_thresh=155, max_area=15000):
    """
    Detect WBC nuclei on the POST-match image.

    After colour matching to a pink reference:
      background → white  (sat≈0, val≈255)
      RBCs       → pink   (hue 155–175°)  outside purple window
      WBC nuclei → dark saturated purple  (sat>60, val<155, hue 100–175°)
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    pm = ((h > 100) & (h < 175) & (s > sat_thresh) & (v < val_thresh)).astype(np.uint8) * 255
    pm = cv2.morphologyEx(pm, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(pm, connectivity=8)
    keep = np.zeros_like(pm)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if min_area <= a <= max_area:
            keep[labels == i] = 255
    out = cv2.GaussianBlur(keep.astype(np.float32) / 255.0, (0, 0), sigmaX=3)
    return np.clip(out, 0, 1)


# ── Platelet detection ────────────────────────────────────────────────────────

def find_platelet_mask_prematch(img_flat, fill_mask, min_area=4, max_area=300):
    """
    Pre-colour-match platelet detection, restricted to cell-free gaps.

    Before colour matching, platelets are clearly more saturated than the
    background.  Threshold = max(20, median_gap_sat × 1.6) adapts automatically
    to any staining protocol (lavender, gray, light-pink backgrounds).

    Only the gap region (fill_mask==0) is examined so large RBC blobs — which
    can also be saturated — are never considered.
    """
    gap = (fill_mask == 0)
    hsv = cv2.cvtColor(img_flat, cv2.COLOR_BGR2HSV)
    s   = hsv[:, :, 1].astype(np.float32)
    v   = hsv[:, :, 2].astype(np.float32)

    gap_sat = s[gap]
    bg_sat  = float(np.median(gap_sat)) if gap_sat.size > 100 else 30.0
    sat_thr = max(20.0, bg_sat * 1.6)

    # Saturated pixel in gap, not pure white
    candidate = (gap & (s > sat_thr) & (v < 248)).astype(np.uint8) * 255
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    keep = np.zeros_like(candidate)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if min_area <= a <= max_area:
            keep[lbl == i] = 255

    print(f"[platelet] pre-match  : {int(keep.sum()):6d} px  "
          f"(bg_sat={bg_sat:.1f}, thr={sat_thr:.1f})")
    keep = cv2.dilate(keep, np.ones((3, 3), np.uint8))
    return np.clip(
        cv2.GaussianBlur(keep.astype(np.float32) / 255.0, (0, 0), sigmaX=1.5), 0, 1)


def find_platelet_mask_postmatch(img_matched, fill_mask, min_area=5, max_area=300):
    """
    Post-colour-match platelet detection, restricted to cell-free gaps.

    After matching to a pink/white reference the background in the gap region
    becomes near-white (val≈240-255, sat≈0-5).  Real platelets are BOTH darker
    AND more saturated than pure white.

    Threshold: val < 215  AND  sat > 20   (clearly stained, not background haze)

    AND-based (not OR) to avoid catching:
      - Background haze (slightly off-white, low sat)
      - JPEG/compression artefacts (tiny sat spikes in flat regions)
      - Cell-border remnants just outside the ellipse (handled by fill_mask
        dilation in the caller, but AND gives a second safety layer)

    Note: fill_mask passed here should already be dilated by the caller so the
    gap region excludes a 5px border around each cell ellipse.
    """
    gap = (fill_mask == 0)
    hsv = cv2.cvtColor(img_matched, cv2.COLOR_BGR2HSV)
    s   = hsv[:, :, 1].astype(np.float32)
    v   = hsv[:, :, 2].astype(np.float32)

    # Require BOTH: dark enough to be stained  AND  saturated enough to be coloured
    stained   = (v < 215) & (s > 20)
    candidate = (gap & stained).astype(np.uint8) * 255

    # Open with 3×3 removes thin arc artefacts (cell-border slivers) but keeps
    # compact blobs; Close bridges fragmented platelet pixels
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN,  np.ones((3, 3), np.uint8))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    keep = np.zeros_like(candidate)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if min_area <= a <= max_area:
            keep[lbl == i] = 255

    print(f"[platelet] post-match : {int(keep.sum()):6d} px")
    keep = cv2.dilate(keep, np.ones((3, 3), np.uint8))
    return np.clip(
        cv2.GaussianBlur(keep.astype(np.float32) / 255.0, (0, 0), sigmaX=1.5), 0, 1)


def boost_platelets(img, platelet_mask_float):
    """
    Pull platelet regions to clearly-visible dark purple dots.

    75% pull toward:  hue=130 (purple-blue), sat=180, val×0.70
    Previous gentle boost (×1.4/×0.92) left platelets indistinguishable
    from the pink cell colour.  This version makes them pop as small dark
    purple dots against the white background.
    """
    if platelet_mask_float is None or platelet_mask_float.sum() < 1:
        return img
    hsv    = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    mask_f = platelet_mask_float[:, :, None]
    bh     = hsv.copy()
    pull   = 0.60
    bh[:, :, 0] = hsv[:, :, 0] * (1 - pull) + 130 * pull          # pull hue → purple
    bh[:, :, 1] = np.clip(hsv[:, :, 1] * (1 - pull) + 160 * pull, 0, 255)  # boost sat
    bh[:, :, 2] = np.clip(hsv[:, :, 2] * 0.75, 0, 255)             # darken 25 %
    boosted = cv2.cvtColor(bh.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    return np.clip(
        img.astype(np.float32) * (1 - mask_f) + boosted * mask_f, 0, 255
    ).astype(np.uint8)


# ── WBC enhancement ───────────────────────────────────────────────────────────

def enhance_wbc(img, wbc_mask_float,
                boost_sat=1.5, deepen=1.1, target_hue=132, hue_pull=0.85):
    """
    Pull WBC nucleus hue toward violet and boost saturation.
    Applied AFTER whitening so the purple lands on a clean white background.
    deepen=1.1 lifts value so nucleus reads as dark purple, not black.
    """
    if wbc_mask_float is None or wbc_mask_float.sum() < 1:
        return img
    hsv    = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    mask_f = wbc_mask_float[:, :, None]
    bh     = hsv.copy()
    bh[:, :, 0] = hsv[:, :, 0] * (1 - hue_pull) + target_hue * hue_pull
    bh[:, :, 1] = np.clip(hsv[:, :, 1] * boost_sat, 0, 255)
    bh[:, :, 2] = np.clip(hsv[:, :, 2] * deepen,    0, 255)
    boosted = cv2.cvtColor(bh.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    return np.clip(
        img.astype(np.float32) * (1 - mask_f) + boosted * mask_f, 0, 255
    ).astype(np.uint8)


# ── Colour helpers ────────────────────────────────────────────────────────────

def flatten_bg(img, target=235.0):
    """Remove uneven illumination at half resolution (fast)."""
    h, w    = img.shape[:2]
    small   = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    lab     = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    k = max(15, int(min(h, w) * 0.03))
    if k % 2 == 0:
        k += 1
    bg    = cv2.morphologyEx(l, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg    = cv2.GaussianBlur(bg, (0, 0), sigmaX=k / 3)
    flat_l = np.clip(
        (l.astype(np.float32) + 1) / (bg.astype(np.float32) + 1) * target,
        0, 255).astype(np.uint8)
    small2 = cv2.cvtColor(cv2.merge([flat_l, a, b]), cv2.COLOR_LAB2BGR)
    return cv2.resize(small2, (w, h), interpolation=cv2.INTER_LINEAR)


def match_reference(img, refs):
    """
    Per-channel LAB mean/std shift toward average statistics across all refs.

    refs: single ndarray  OR  list of ndarrays
    Multiple references are averaged so you can pass a normal-RBC reference
    and a WBC reference simultaneously:
      python3 enhance_smear.py 3.png -r g.png -r ref.jpg -o result.jpeg
    """
    if not isinstance(refs, (list, tuple)):
        refs = [refs]

    lab_img   = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    ref_means = []
    ref_stds  = []
    for ref in refs:
        lab_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
        ref_means.append([lab_ref[:, :, ch].mean() for ch in range(3)])
        ref_stds .append([lab_ref[:, :, ch].std()  for ch in range(3)])

    rm = np.mean(ref_means, axis=0)  # (3,)
    rs = np.mean(ref_stds,  axis=0)

    for ch in range(3):
        sm, ss = lab_img[:, :, ch].mean(), lab_img[:, :, ch].std()
        if ss < 1e-3:
            continue
        lab_img[:, :, ch] = (lab_img[:, :, ch] - sm) * (rs[ch] / ss) + rm[ch]

    return cv2.cvtColor(np.clip(lab_img, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def brighten(img, gamma=0.80, white=255, black=30):
    """Simple levels stretch when no reference is provided."""
    gray    = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lo, hi  = np.percentile(gray, [2, 98])
    if hi - lo < 1:
        hi = lo + 1
    s = np.clip((img.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return np.clip(np.power(s, gamma) * (white - black) + black, 0, 255).astype(np.uint8)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def enhance(img_bgr, reference_bgrs=None, max_dim=960, saturation_boost=1.0):

    # ── Step 1: Denoise + illumination flatten ────────────────────────────
    x = cv2.bilateralFilter(img_bgr, d=5, sigmaColor=25, sigmaSpace=5)
    x = flatten_bg(x)

    # ── Step 2: CellPose cell segmentation ───────────────────────────────
    labels = segment(img_bgr, max_dim=max_dim)

    # ── Step 3: Build per-cell ellipse masks — EARLY ──────────────────────
    # Must come before platelet detection so we know which pixels are cell
    # interiors and which are the cell-free gap where platelets live.
    fill_mask, border_mask = build_masks(labels)

    # Dilate fill_mask by ~5px to create a conservative "safe gap" for platelet
    # detection.  Without this, pixels at the very edge of each cell ellipse
    # (which fall just outside the fitted shape) get classified as gap and are
    # then falsely detected as platelets.  The 5px buffer excludes those border
    # artefacts.  fill_mask itself (undilated) is still used for whitening and
    # border drawing so actual cell coverage is not affected.
    _dil_k   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))  # ~5px radius
    fill_safe = cv2.dilate(fill_mask, _dil_k)

    # ── Step 4: Pre-match platelet detection ──────────────────────────────
    # Saturation spike in safe gap — most reliable BEFORE colour matching
    # destroys the saturation contrast between platelets and background.
    platelet_pre = find_platelet_mask_prematch(x, fill_safe)

    # ── Step 5: Colour / LAB matching ────────────────────────────────────
    if reference_bgrs is not None:
        x = match_reference(x, reference_bgrs)
    else:
        x = brighten(x)

    # ── Step 6: WBC detection — POST colour matching ─────────────────────
    wbc_mask = find_wbc_mask(x, sat_thresh=60, val_thresh=155)
    n_wbc    = int((wbc_mask > 0.3).sum())
    print(f"[WBC]      post-match : {n_wbc:6d} px")

    # ── Step 7: Post-match platelet detection ─────────────────────────────
    # After matching, background in safe gap→white. Objects that are BOTH
    # dark (v<215) AND saturated (s>20) in the safe gap are platelets.
    platelet_post = find_platelet_mask_postmatch(x, fill_safe)

    # ── Step 8: Merge platelet masks (union) ──────────────────────────────
    platelet_prot = np.clip(np.maximum(platelet_pre, platelet_post), 0, 1)
    n_plt         = int((platelet_prot > 0.3).sum())
    print(f"[platelet] total prot : {n_plt:6d} px")

    # ── Step 9: Background whitening ─────────────────────────────────────
    ellipse_soft = np.clip(
        cv2.GaussianBlur(fill_mask.astype(np.float32) / 255.0, (0, 0), sigmaX=1), 0, 1)
    bg_mask = np.clip(
        (1.0 - ellipse_soft) * (1.0 - platelet_prot) * (1.0 - wbc_mask), 0, 1)
    blend = np.clip(bg_mask * 0.88, 0, 1)[:, :, None]
    white = np.full_like(x, 255, dtype=np.float32)
    x = np.clip(
        x.astype(np.float32) * (1 - blend) + white * blend, 0, 255
    ).astype(np.uint8)

    # ── Step 10: Draw smooth ellipse borders ─────────────────────────────
    k        = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    bm_thick = cv2.dilate(border_mask, k)
    ring     = (bm_thick.astype(np.float32) / 255.0) * ellipse_soft
    ring     = cv2.GaussianBlur(ring, (0, 0), sigmaX=0.6)
    ring     = np.clip(ring, 0, 1)[:, :, None]
    dark     = x.astype(np.float32) * 0.80
    x        = np.clip(
        x.astype(np.float32) * (1 - ring) + dark * ring, 0, 255
    ).astype(np.uint8)

    # ── Step 11: WBC purple enhancement ──────────────────────────────────
    x = enhance_wbc(x, wbc_mask)

    # ── Step 12: Platelet visibility boost ───────────────────────────────
    # Strong 75% pull toward dark purple so platelets pop as distinct dots
    # even when surrounded by pink RBCs.
    x = boost_platelets(x, platelet_prot)

    # ── Step 13: Neutralise near-white haze ──────────────────────────────
    hsv        = cv2.cvtColor(x, cv2.COLOR_BGR2HSV).astype(np.float32)
    hs, s, v   = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    s[(s < 15) & (v > 240)] = 0
    x = cv2.cvtColor(cv2.merge([hs, s, v]).astype(np.uint8), cv2.COLOR_HSV2BGR)

    if saturation_boost != 1.0:
        hsv              = cv2.cvtColor(x, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1]     = np.clip(hsv[:, :, 1] * saturation_boost, 0, 255)
        x = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    return x


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys, time

    p = argparse.ArgumentParser(description="CellPose blood smear enhancement.")
    p.add_argument("input")
    p.add_argument("-o", "--output",     default=None)
    p.add_argument("-r", "--reference",  action="append", default=None,
                   metavar="REF",
                   help="Colour reference image (can be repeated for averaged matching)")
    p.add_argument("-d", "--max-dim",    type=int,   default=960,
                   help="CellPose inference max dimension (lower=faster, default 960)")
    p.add_argument("-q", "--quality",    type=int,   default=95)
    p.add_argument("-s", "--saturation", type=float, default=1.0)
    args = p.parse_args()

    img = cv2.imread(args.input, cv2.IMREAD_COLOR)
    if img is None:
        print(f"Error: cannot read '{args.input}'", file=sys.stderr)
        sys.exit(1)

    refs = None
    if args.reference:
        refs = []
        for rpath in args.reference:
            r = cv2.imread(rpath, cv2.IMREAD_COLOR)
            if r is None:
                print(f"Error: cannot read reference '{rpath}'", file=sys.stderr)
                sys.exit(1)
            refs.append(r)
        print(f"[ref] {len(refs)} reference image(s) loaded")

    out = args.output or (args.input.rsplit(".", 1)[0] + "_cp.jpeg")

    t0      = time.perf_counter()
    result  = enhance(img, reference_bgrs=refs, max_dim=args.max_dim,
                      saturation_boost=args.saturation)
    elapsed = time.perf_counter() - t0

    cv2.imwrite(out, result, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
    print(f"Saved: {out}  ({elapsed*1000:.0f} ms)")
