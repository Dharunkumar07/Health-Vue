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
    tiny_mask   = np.zeros((h, w), np.uint8)   # platelet-sized CellPose objects
    slices      = find_objects(labels)

    # Adaptive platelet-size cutoff: objects much smaller than the median cell
    # are platelets, not RBCs.  Median RBC ≈ 1600 px on a 1600px-wide frame;
    # platelets segment as ~150-500 px blobs, well under 0.30 × median.
    all_areas = np.bincount(labels.ravel())
    all_areas = all_areas[1:][all_areas[1:] > 0]
    med_area  = float(np.median(all_areas)) if all_areas.size else 1600.0
    tiny_thr  = max(80.0, 0.30 * med_area)
    print(f"[masks]    median cell area {med_area:.0f} px, platelet cutoff < {tiny_thr:.0f} px")

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

        if area < tiny_thr:
            cv2.drawContours(fill_mask, [cnt_full], -1, 255, -1)
            # Objects touching the frame edge are cut-off RBCs, not platelets —
            # their visible area is small only because the rest is off-frame.
            touches_border = (sl[0].start <= 1 or sl[1].start <= 1 or
                              sl[0].stop >= h - 1 or sl[1].stop >= w - 1)
            if not touches_border:
                cv2.drawContours(tiny_mask, [cnt_full], -1, 255, -1)
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

    return fill_mask, border_mask, tiny_mask


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



def find_wbc_mask_prematch(img_flat, labels=None, min_area=1200, max_area=25000):
    """
    WBC nucleus detection on the PRE-match flattened image — ADAPTIVE.

    Fixed thresholds calibrated on one slide do not transfer: a pink slide has
    different absolute saturation from a lavender one, so a hard "S > 75" both
    misses real nuclei and lets RBC clusters through.  Everything here is
    therefore measured relative to THIS slide's own red cells:

        rbc_sat = median saturation of cell pixels
        rbc_val = median value      of cell pixels

    A nucleus must be clearly more stained than the red cells
    (meanS >= max(70, rbc_sat * 1.7)) AND clearly darker (meanV <= rbc_val*0.92),
    plus compact (solidity >= 0.72) and smooth (stdV <= 24).  Its area must
    also be plausible for a leucocyte: 0.4x - 6x the median cell area.
    """
    hsv = cv2.cvtColor(img_flat, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    sf, vf = s.astype(np.float32), v.astype(np.float32)

    # --- slide-relative reference from the segmented red cells ---------
    if labels is not None and labels.max() > 0:
        cellpx  = labels > 0
        rbc_sat = float(np.median(sf[cellpx]))
        rbc_val = float(np.median(vf[cellpx]))
        areas   = np.bincount(labels.ravel())[1:]
        areas   = areas[areas > 0]
        med_a   = float(np.median(areas)) if areas.size else 1600.0
    else:
        rbc_sat, rbc_val, med_a = 45.0, 200.0, 1600.0

    # Saturation is the reliable discriminator; VALUE is not — on a lavender
    # slide the nucleus is BRIGHTER than the red cells (V 224 vs 200), on a
    # pink slide it is darker.  Measured separation on saturation is clean:
    #     RBC clusters : meanS 60-65
    #     lymphocyte   : meanS 97
    # so a 1.5x multiple of the slide's own RBC saturation splits them safely.
    sat_thr = max(70.0, rbc_sat * 1.5)
    lo_a    = max(min_area, 0.4 * med_a)
    hi_a    = min(max_area, 6.0 * med_a)
    print(f"[WBC]      adaptive: rbc_sat={rbc_sat:.0f} -> satThr={sat_thr:.0f}, "
          f"area {lo_a:.0f}-{hi_a:.0f}")

    # NOTE: threshold at the full sat_thr, not a fraction of it.  A looser
    # candidate mask merges the nucleus into one giant blob spanning every
    # touching RBC (measured: 196 649 px), which then fails every shape test.
    pm = ((h > 100) & (h < 175) & (sf > sat_thr)).astype(np.uint8) * 255
    pm = cv2.morphologyEx(pm, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
                          iterations=2)

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(pm, connectivity=8)
    keep = np.zeros_like(pm)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if not (lo_a <= a <= hi_a):
            continue
        m = lbl == i
        if sf[m].mean() < sat_thr:          # not stained enough -> RBC cluster
            continue
        if vf[m].std() > 24.0:              # blotchy -> stain precipitate
            continue
        cnts, _ = cv2.findContours((m.astype(np.uint8) * 255),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            continue
        c  = max(cnts, key=cv2.contourArea)
        ha = cv2.contourArea(cv2.convexHull(c))
        if ha <= 0 or cv2.contourArea(c) / ha < 0.72:
            continue
        keep[m] = 255

    out = cv2.GaussianBlur(keep.astype(np.float32) / 255.0, (0, 0), sigmaX=3)
    return np.clip(out, 0, 1)


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
    # Per-blob statistics filter: real platelets are BOTH clearly saturated
    # (meanS >= max(45, bg_sat*2.0)) AND clearly dark (meanV <= 230).
    # Faint haze / debris blobs sit at meanS 33-43, meanV 233-247 and are
    # rejected, so they get whitened instead of protected+boosted.
    blob_sat_thr = max(45.0, bg_sat * 2.0)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if not (min_area <= a <= max_area):
            continue
        m = lbl == i
        bx, by = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if bx <= 1 or by <= 1 or bx + bw >= candidate.shape[1] - 1 \
                or by + bh >= candidate.shape[0] - 1:
            continue   # frame-edge fragment of a cut-off cell, not a platelet
        ms_, mv_ = s[m].mean(), v[m].mean()
        if (ms_ >= blob_sat_thr and mv_ <= 230) or (ms_ >= 35 and mv_ <= 215):
            keep[m] = 255

    print(f"[platelet] pre-match  : {int(keep.sum()):6d} px  "
          f"(bg_sat={bg_sat:.1f}, thr={sat_thr:.1f}, blob_sat_thr={blob_sat_thr:.1f})")
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
    # Per-blob filter: post-match platelets must be clearly dark (meanV<=205)
    # and stained (meanS>=30); rejects off-white haze remnants.
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if not (min_area <= a <= max_area):
            continue
        bx, by = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if bx <= 1 or by <= 1 or bx + bw >= candidate.shape[1] - 1 \
                or by + bh >= candidate.shape[0] - 1:
            continue   # frame-edge fragment
        m = lbl == i
        if s[m].mean() >= 30 and v[m].mean() <= 205:
            keep[m] = 255

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



def tint_rbc(img, ellipse_soft, wbc_mask_float,
             target_hue=177, pull=0.85, sat_mul=1.10):
    """
    Pull RBC interiors from pink/magenta toward pale red.
    Applied inside cell ellipses, excluding WBC regions (kept purple).
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    m = np.clip(ellipse_soft * (1.0 - wbc_mask_float), 0, 1)
    hsv[:, :, 0] = H * (1 - pull * m) + target_hue * (pull * m)
    hsv[:, :, 1] = np.clip(S * (1 + (sat_mul - 1) * m), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)



def build_wbc_cell_mask(labels, wbc_nuc_bin):
    """
    Expand nucleus detections to the full WBC cell (incl. cytoplasm).

    Cytoplasm pixels are taken ONLY from:
      • the CellPose label(s) the nucleus overlaps (the WBC's own cell body)
      • a thin dilation ring restricted to background pixels (labels == 0)
    Pixels belonging to OTHER CellPose labels (neighbouring RBCs) are never
    included, so the lavender cytoplasm cannot spill onto adjacent cells.
    """
    own = np.zeros_like(wbc_nuc_bin)
    # A label counts as the WBC's own body only if >=20% of that label's area
    # is covered by the nucleus mask.  A nucleus merely brushing the edge of a
    # neighbouring RBC label (common for smudge cells) must not claim it.
    for L in np.unique(labels[wbc_nuc_bin > 0]):
        if L <= 0:
            continue
        lab_m = labels == L
        ov    = int((wbc_nuc_bin > 0)[lab_m].sum())
        if ov >= 0.20 * int(lab_m.sum()):
            own[lab_m] = 1
    own = np.maximum(own, wbc_nuc_bin)

    # No artificial dilation ring: the cell is exactly the CellPose body +
    # nucleus.  The WBC keeps its raw structure — nothing is added around it.
    cell = np.clip(own, 0, 1).astype(np.uint8)
    cyto = ((cell > 0) & (wbc_nuc_bin == 0)).astype(np.uint8)
    return cell, cyto


def enhance_wbc_structured(img, nuc_soft, cyto_soft):
    """
    Texture-first WBC rendering: a single violet transform over the whole cell
    with S and V scaled multiplicatively, so the raw light/dark structure
    (dense chromatin vs granular cytoplasm) is what the viewer sees — no
    painted-on shapes.  The nucleus region gets extra darkening.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    cell = np.clip(nuc_soft + cyto_soft, 0, 1)

    # Local contrast enhancement inside the cell: CLAHE on V recovers the
    # granular chromatin/cytoplasm structure flattened by matching/denoise.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    v_eq  = clahe.apply(V.astype(np.uint8)).astype(np.float32)
    V     = V * (1 - 0.6 * cell) + v_eq * (0.6 * cell)

    Hc = H * (1 - 0.85 * cell) + 132 * (0.85 * cell)
    Sc = np.clip(S * (1 + 0.50 * cell) + 22 * cell, 0, 255)
    Vc = np.clip(V * (1 - 0.08 * cell - 0.16 * nuc_soft), 0, 255)

    out = cv2.merge([Hc, Sc, Vc]).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_HSV2BGR)



def find_cyto_mask(img_flat, wbc_nuc_bin, max_reach=31):
    """
    Extract the REAL cytoplasm from the raw (flattened) image.

    Cytoplasm on these slides is stained purple at S >= 55 (RBCs max out
    around S 45-50), so: candidate = purple-hue pixels with S >= 55, then
    keep only candidates connected to a nucleus, within max_reach px of it.
    The resulting mask follows the true irregular cytoplasm boundary —
    nothing synthetic is added.
    """
    hsv = cv2.cvtColor(img_flat, cv2.COLOR_BGR2HSV)
    h, s, _ = cv2.split(hsv)
    cand = ((h > 100) & (h < 175) & (s >= 55)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cand = np.maximum(cand, wbc_nuc_bin)

    reach = cv2.dilate(wbc_nuc_bin, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (max_reach, max_reach)))
    cand &= reach

    n, lbl = cv2.connectedComponents(cand, connectivity=8)
    keep_ids = set(np.unique(lbl[wbc_nuc_bin > 0])) - {0}
    keep = np.isin(lbl, list(keep_ids)).astype(np.uint8)
    cyto = ((keep > 0) & (wbc_nuc_bin == 0)).astype(np.uint8)
    return keep, cyto


def recolor_wbc(img, nuc_soft, cyto_soft):
    """
    Textbook Wright-Giemsa colours applied ON TOP of the raw structure.
    All scalings are multiplicative, so the raw texture is untouched:
      nucleus   → deep violet, slightly darker
      cytoplasm → pale lavender-blue, lightened
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    n = nuc_soft
    c = np.clip(cyto_soft * (1 - nuc_soft), 0, 1)

    H2 = H * (1 - 0.85 * n) + 132 * (0.85 * n)
    H2 = H2 * (1 - 0.75 * c) + 133 * (0.75 * c)
    S2 = np.clip(S * (1 + 0.50 * n) + 25 * n, 0, 255)
    S2 = np.clip(S2 * (1 - 0.25 * c) + 12 * c, 0, 255)
    V2 = np.clip(V * (1 - 0.18 * n), 0, 255)
    V2 = np.clip(V2 * (1 + 0.16 * c) + 6 * c, 0, 255)

    out = cv2.merge([H2, S2, V2]).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_HSV2BGR)



def close_wbc_cell(cell_bin, nuc_bin, pad=5):
    """
    Close the WBC cell region into its true outline.

    The pale cytoplasm halo has nearly the same saturation as background
    (medS ~48 vs bg ~41), so threshold growth alone misses it and the
    whitening step then erases the cytoplasm.  Fix: fill interior holes in the
    detected fragments and dilate modestly.  Convex hulls are deliberately NOT
    used — they bridge adjacent cells and produce boxy halos.
    """
    seed = np.clip(cell_bin + nuc_bin, 0, 1).astype(np.uint8)
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    seed = ndi.binary_fill_holes(seed > 0).astype(np.uint8)
    if pad > 0:
        seed = cv2.dilate(seed, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1)))
        seed = ndi.binary_fill_holes(seed > 0).astype(np.uint8)
    return np.clip(seed + nuc_bin, 0, 1).astype(np.uint8)



def close_wbc_cell(cell_bin, nuc_bin, pad=5):
    """
    Close the WBC cell region into its true outline.

    The pale cytoplasm halo has nearly the same saturation as background
    (medS ~48 vs bg ~41), so threshold growth alone misses it and the
    whitening step then erases the cytoplasm.  Fix: fill interior holes in the
    detected fragments and dilate modestly.  Convex hulls are deliberately NOT
    used — they bridge adjacent cells and produce boxy halos.
    """
    seed = np.clip(cell_bin + nuc_bin, 0, 1).astype(np.uint8)
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    seed = ndi.binary_fill_holes(seed > 0).astype(np.uint8)
    if pad > 0:
        seed = cv2.dilate(seed, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1)))
        seed = ndi.binary_fill_holes(seed > 0).astype(np.uint8)
    return np.clip(seed + nuc_bin, 0, 1).astype(np.uint8)



def segment_wbc_cytoplasm(img_flat, nuc_bin, pad=90, min_overlap=0.25):
    """
    STEP 6b — dedicated cytoplasm segmentation.

    The global CellPose pass (downscaled to 960px) resolves RBCs well but
    hugs WBC nuclei, and the pale cytoplasm halo is too low-contrast for
    threshold growth.  This step re-runs CellPose at FULL resolution on a
    crop around the detected nuclei, with a permissive cellprob_threshold
    so faint cytoplasm boundaries are found.

    A resulting label is accepted as a WBC body only if >= min_overlap of its
    area is covered by the nucleus mask, which rejects neighbouring RBCs.
    Returns a full-frame binary cell mask (nucleus + cytoplasm).
    """
    h, w = nuc_bin.shape
    if nuc_bin.sum() < 50:
        return nuc_bin.copy()

    ys, xs = np.where(nuc_bin > 0)
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad)
    roi     = img_flat[y0:y1, x0:x1]
    nuc_roi = nuc_bin[y0:y1, x0:x1]

    model = _load_model()
    masks, _, _ = model.eval(
        cv2.cvtColor(roi, cv2.COLOR_BGR2RGB),
        diameter=60, channels=[0, 0],
        flow_threshold=0.6, cellprob_threshold=-2,
    )

    keep = np.zeros(masks.shape, np.uint8)
    for L in np.unique(masks[nuc_roi > 0]):
        if L == 0:
            continue
        m  = masks == L
        ov = int(nuc_roi[m].sum())
        if ov >= min_overlap * int(m.sum()):
            keep[m] = 1

    cell = np.zeros((h, w), np.uint8)
    cell[y0:y1, x0:x1] = keep
    cell = np.clip(cell + nuc_bin, 0, 1).astype(np.uint8)
    cell = ndi.binary_fill_holes(cell > 0).astype(np.uint8)
    print(f"[WBC]      cytoplasm segmentation: {int(cell.sum()):6d} px cell")
    return cell



def tint_perinuclear_cytoplasm(img, nuc_bin, labels, reach=25,
                               hue_pull=0.85, target_hue=133,
                               sat_mul=0.35, sat_add=16,
                               lighten=0.30):
    """
    FINAL STEP — render the WBC cytoplasm as a thin, pale blue-lavender rim
    hugging the nucleus (Wright-Giemsa lymphocyte appearance).

    Calibrated against a reference lymphocyte crop:
      nucleus   H 136  S 149  V 140   (deep purple, kept raw)
      cytoplasm H 138-142  S 105-135  V 153-175  → reads near-white lavender
    The cytoplasm is a NARROW ring (reach ~25 px), not a wide zone, and its
    value is pulled toward white (lighten=0.30) with saturation cut to 35 %,
    so it stays pale rather than becoming a vivid purple halo.

    Pixels belonging to other CellPose labels are excluded, so neighbouring
    RBCs keep their red.
    """
    if nuc_bin.sum() < 50:
        return img

    zone = cv2.dilate(nuc_bin, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (reach, reach)))

    own = set()
    for L in np.unique(labels[nuc_bin > 0]):
        if L <= 0:
            continue
        m = labels == L
        if int((nuc_bin > 0)[m].sum()) >= 0.20 * int(m.sum()):
            own.add(int(L))
    other = cv2.dilate(((labels > 0) & (~np.isin(labels, list(own)))
                        ).astype(np.uint8), np.ones((3, 3), np.uint8))

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    cyto = ((zone > 0) & (nuc_bin == 0) & (other == 0)).astype(np.float32)
    cyto = np.clip(cv2.GaussianBlur(cyto, (0, 0), sigmaX=1.5), 0, 1)
    print(f"[WBC]      cytoplasm rim : {int((cyto > 0.3).sum()):6d} px")

    hsv[:, :, 0] = H * (1 - hue_pull * cyto) + target_hue * (hue_pull * cyto)
    hsv[:, :, 1] = np.clip(S * (1 - (1 - sat_mul) * cyto) + sat_add * cyto, 0, 255)
    hsv[:, :, 2] = np.clip(V * (1 - lighten * cyto) + 255 * (lighten * cyto), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)



def clean_rbc(img, ellipse_soft, wbc_cell_soft, target_hue=177,
              sat_floor=45, val_floor=150, ring_sat=95):
    """
    Remove the blackish / bluish cast inside RBCs and their border rings.

    Two problems this fixes:
      1. Residual off-hue pixels inside cells (H far from the pale-red target)
         read as grey or blue smudges.  Measured: ~55 k of 848 k cell pixels
         sat outside H 160-179.
      2. The border ring is DESATURATED by the darkening step (ring medS 30 vs
         cell medS 60), so it renders near-black instead of pale red.

    Inside the cell mask the hue is forced to `target_hue`, saturation is given
    a floor and value is lifted off the black end, so every RBC pixel — ring
    included — is a clean pale red.
    """
    m = np.clip(ellipse_soft * (1.0 - wbc_cell_soft), 0, 1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # 1. force hue to pale red inside cells (kills blue/grey pixels)
    H2 = H * (1 - m) + target_hue * m

    # 2. saturation floor — desaturated (grey) pixels become tinted red;
    #    the ring gets a higher floor so it reads as a red outline
    ring_like = np.clip(m * (S < ring_sat).astype(np.float32), 0, 1)
    S2 = np.maximum(S, sat_floor * m)
    S2 = np.maximum(S2, np.minimum(S + 35, ring_sat) * ring_like)

    # 3. value floor — lift the darkest pixels off black
    V2 = np.maximum(V, val_floor * m)

    out = cv2.merge([H2, np.clip(S2, 0, 255), np.clip(V2, 0, 255)]).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_HSV2BGR)



def suppress_stray_violet(img, wbc_protect, platelet_prot,
                          hue_lo=95, hue_hi=163,
                          target_hue=172, sat_lo=40, sat_hi=82,
                          val_floor=178, bright_v=228):
    """
    Kill residual violet/lavender OUTSIDE the WBC + platelet protection zones.

    WHY: every red-forcing step (tint_rbc / clean_rbc / match_rbc_to_reference)
    operates only INSIDE ellipse_soft, and whitening only pulls the gap 88 %
    toward white.  Cells that CellPose missed or merged in dense clumps are
    neither — they keep the lavender colour the LAB match gave them, and soft
    mask edges leave violet rims around segmented RBCs.  This step closes that
    coverage gap globally:

      violet pixel, BRIGHT (V >= bright_v)  → residual background haze
                                              → desaturate to white
      violet pixel, darker                  → unsegmented cell body / rim
                                              → force into the RBC red
                                                envelope (same numbers as
                                                match_rbc_to_reference)

    Must run BEFORE the WBC passthrough and perinuclear rim so the leucocyte
    keeps its purple; wbc_protect / platelet_prot gate it out here.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    protect = np.clip(np.maximum(wbc_protect, platelet_prot), 0, 1)
    violet  = ((H > hue_lo) & (H < hue_hi) & (S > 10)).astype(np.float32)
    violet  = violet * (1.0 - protect)
    violet  = np.clip(cv2.GaussianBlur(violet, (0, 0), sigmaX=1.0), 0, 1)
    n_v = int((violet > 0.3).sum())
    print(f"[violet]   stray violet px recoloured: {n_v:6d}")

    bright = np.clip((V - bright_v) / 12.0, 0, 1) * violet   # haze → white
    cellv  = violet * (1.0 - bright)                          # body → red

    # haze: desaturate + lift to white
    S2 = S * (1 - bright)
    V2 = V + (255.0 - V) * bright

    # unsegmented cell body / rim: RBC red envelope
    H2 = H * (1 - cellv) + target_hue * cellv
    S2 = S2 * (1 - cellv) + np.clip(S, sat_lo, sat_hi) * cellv
    V2 = V2 * (1 - cellv) + np.maximum(V, val_floor) * cellv

    out = cv2.merge([H2, np.clip(S2, 0, 255), np.clip(V2, 0, 255)]).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_HSV2BGR)


def match_rbc_to_reference(img, ellipse_soft, wbc_cell_soft,
                           target_hue=172, sat_lo=40, sat_hi=82,
                           val_floor=178):
    """
    FINAL RBC clean-up — force every red-cell pixel into the colour envelope
    measured from the reference slide, so no grey or blackish pixels remain.

    Reference RBC statistics (ref2.png):
        H  5-95 pct : 163 - 175   (median 171)
        S  5-95 pct :  36 -  81   (median  56)
        V  1-95 pct : 178 - 234   (median 207)

    Before this step our output sat at S median 101 and V 1st-pct 141 — far
    too saturated and far too dark, which is exactly what reads as grey/black
    blotches inside the cells.  Here the hue is set, saturation is CLAMPED to
    [sat_lo, sat_hi] and value is floored at `val_floor`.

    Runs AFTER the global saturation boost so the boost cannot re-darken or
    re-saturate the cells.  WBC regions are excluded.
    """
    m = np.clip(ellipse_soft * (1.0 - wbc_cell_soft), 0, 1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    H2 = H * (1 - m) + target_hue * m
    S2 = S * (1 - m) + np.clip(S, sat_lo, sat_hi) * m
    V2 = V * (1 - m) + np.maximum(V, val_floor) * m

    out = cv2.merge([H2, np.clip(S2, 0, 255), np.clip(V2, 0, 255)]).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_HSV2BGR)


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
    raw_flat = x.copy()   # untouched (illumination-flattened) pixels for WBC passthrough

    # ── Step 2: CellPose cell segmentation ───────────────────────────────
    labels = segment(img_bgr, max_dim=max_dim)

    # ── Step 3: Build per-cell ellipse masks — EARLY ──────────────────────
    # Must come before platelet detection so we know which pixels are cell
    # interiors and which are the cell-free gap where platelets live.
    fill_mask, border_mask, tiny_mask = build_masks(labels)

    # Dilate fill_mask by ~5px to create a conservative "safe gap" for platelet
    # detection.  Without this, pixels at the very edge of each cell ellipse
    # (which fall just outside the fitted shape) get classified as gap and are
    # then falsely detected as platelets.  The 5px buffer excludes those border
    # artefacts.  fill_mask itself (undilated) is still used for whitening and
    # border drawing so actual cell coverage is not affected.
    _dil_k   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))  # ~3px radius
    fill_safe = cv2.dilate(fill_mask, _dil_k)

    # ── Step 4: Pre-match platelet detection ──────────────────────────────
    # Saturation spike in safe gap — most reliable BEFORE colour matching
    # destroys the saturation contrast between platelets and background.
    platelet_pre = find_platelet_mask_prematch(x, fill_safe)

    # ── Step 4b: WBC detection PRE-match (colour matching to pale pink
    # references can wash out WBC saturation, killing post-match detection) ──
    wbc_pre = find_wbc_mask_prematch(x, labels)
    print(f"[WBC]      pre-match  : {int((wbc_pre > 0.3).sum()):6d} px")

    # ── Step 5: Colour / LAB matching ────────────────────────────────────
    if reference_bgrs is not None:
        x = match_reference(x, reference_bgrs)
    else:
        x = brighten(x)

    # ── Step 6: WBC detection — POST colour matching ─────────────────────
    # Post-match detection only REFINES nuclei already found pre-match; it may
    # not introduce new ones, otherwise RBC clusters that the adaptive filter
    # rejected come back as false WBCs.
    wbc_post = find_wbc_mask(x, sat_thresh=60, val_thresh=155)
    seed     = cv2.dilate((wbc_pre > 0.3).astype(np.uint8),
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    wbc_post = wbc_post * seed.astype(np.float32)
    wbc_mask = np.clip(np.maximum(wbc_pre, wbc_post), 0, 1)
    n_wbc    = int((wbc_mask > 0.3).sum())
    print(f"[WBC]      post-match : {int((wbc_post > 0.3).sum()):6d} px  |  union: {n_wbc:6d} px")

    # Full WBC cell = nucleus + REAL cytoplasm.
    # The cytoplasm comes from TWO sources, unioned:
    #   1. the WBC's own CellPose label (CellPose segments the whole cell,
    #      cytoplasm included — visible behind the nucleus in the label map)
    #   2. stain-connected pixels (S >= 55) around the nucleus
    wbc_nuc_bin = (wbc_mask > 0.3).astype(np.uint8)
    # No cytoplasm detection: the WBC region is the nucleus mask only.
    wbc_cell_bin  = wbc_nuc_bin.copy()
    wbc_cyto_bin  = np.zeros_like(wbc_nuc_bin)
    wbc_cyto_soft = np.zeros(wbc_nuc_bin.shape, np.float32)
    wbc_cell_soft = np.clip(cv2.GaussianBlur(
        wbc_cell_bin.astype(np.float32), (0, 0), sigmaX=1.5), 0, 1)
    other_cells   = np.zeros_like(wbc_nuc_bin)
    print(f"[WBC]      nucleus    : {int(wbc_cell_bin.sum()):6d} px")

    # ── Step 7: Post-match platelet detection ─────────────────────────────
    # After matching, background in safe gap→white. Objects that are BOTH
    # dark (v<215) AND saturated (s>20) in the safe gap are platelets.
    platelet_post = find_platelet_mask_postmatch(x, fill_safe)

    # ── Step 8: Merge platelet masks (union) ──────────────────────────────
    # CellPose-detected tiny objects (<80 px) are platelets that live inside
    # fill_mask and therefore never appear in the gap-based detectors — add
    # them explicitly so they get the purple boost instead of the RBC tint.
    tiny_soft = np.clip(cv2.GaussianBlur(
        cv2.dilate(tiny_mask, np.ones((3, 3), np.uint8)).astype(np.float32) / 255.0,
        (0, 0), sigmaX=1.5), 0, 1)
    platelet_prot = np.clip(
        np.maximum(np.maximum(platelet_pre, platelet_post), tiny_soft), 0, 1)
    n_plt         = int((platelet_prot > 0.3).sum())
    print(f"[platelet] total prot : {n_plt:6d} px")

    # ── Step 9: Background whitening ─────────────────────────────────────
    ellipse_soft = np.clip(
        cv2.GaussianBlur(fill_mask.astype(np.float32) / 255.0, (0, 0), sigmaX=1), 0, 1)
    # Soft guard around the WBC: the pale cytoplasm skirt is too low-contrast
    # to segment reliably (its LAB/HSV stats overlap the background), so
    # instead of hard-classifying it, whitening is ATTENUATED with a smooth
    # falloff around the detected cell.  Faint cytoplasm survives; true
    # background a little further out is still whitened normally.
    wbc_guard = cv2.dilate(wbc_cell_bin, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (41, 41)))
    wbc_guard[(other_cells > 0) & (wbc_cell_bin == 0)] = 0
    wbc_guard = np.clip(cv2.GaussianBlur(
        wbc_guard.astype(np.float32), (0, 0), sigmaX=9), 0, 1)
    wbc_protect = np.clip(wbc_cell_soft + 0.85 * wbc_guard, 0, 1)

    bg_mask = np.clip(
        (1.0 - ellipse_soft) * (1.0 - platelet_prot) * (1.0 - wbc_protect), 0, 1)
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
    dark     = x.astype(np.float32) * 0.88
    x        = np.clip(
        x.astype(np.float32) * (1 - ring) + dark * ring, 0, 255
    ).astype(np.uint8)

    # ── Step 11: RBC pale-red tint (excludes the full WBC cell) ──────────
    x = tint_rbc(x, ellipse_soft, wbc_cell_soft)

    # ── Step 11b: clean blackish / bluish cast from RBCs and their rings ──
    x = clean_rbc(x, ellipse_soft, wbc_cell_soft)

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

    # ── Clamp RBCs into the reference colour envelope (after the boost) ──
    x = match_rbc_to_reference(x, ellipse_soft, wbc_cell_soft)

    # ── Kill stray violet everywhere outside WBC/platelet zones ──────────
    # Handles cells CellPose missed in dense clumps (they never entered
    # ellipse_soft, so no red step touched them) and violet rims at soft
    # mask edges.  Runs BEFORE WBC passthrough so the leucocyte stays purple.
    x = suppress_stray_violet(x, wbc_protect, platelet_prot)

    # ── FINAL step: WBC absolute passthrough ─────────────────────────────
    # The WBC (nucleus + real cytoplasm) is composited from the ORIGINAL
    # input image as the very last operation.  No step of the pipeline —
    # denoise, flatten, matching, whitening, tint, borders, saturation —
    # touches these pixels.
    cm = wbc_cell_soft[:, :, None]
    x = np.clip(
        x.astype(np.float32) * (1 - cm) + img_bgr.astype(np.float32) * cm,
        0, 255).astype(np.uint8)

    # ── FINAL step: perinuclear cytoplasm → light violet ─────────────────
    x = tint_perinuclear_cytoplasm(x, wbc_nuc_bin, labels)

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
