
BLOOD SMEAR ENHANCEMENT - CELLPOSE PIPELINE
============================================

Synthetic Wright-Giemsa rendering of peripheral blood smear images.

INPUT : raw / poorly stained / lavender smear frame
OUTPUT: white background, pale-red RBCs, purple WBC nucleus with pale
        lavender rim, dark-purple platelet dots

Command : python3 cellpose.py input.png -r ref1.png [-r ref2.png] -s 1.25 -o result.jpeg



PIPELINE FLOW
===============

              INPUT  (raw BGR smear image)
                       |
                       v
STEP 1   DENOISE + FLATTEN
         bilateral filter -> LAB L-channel illumination flatten
                       |
                       v
STEP 2   CELLPOSE SEGMENTATION
         cyto3 GPU @960px -> nearest-upscale to full res -> labels
                       |
                       v
STEP 3   BUILD CELL MASKS
         fit ellipse per label 
         -> fill_mask / border_mask / tiny_mask
         -> fill_safe = fill_mask dilated 3px
                       |
                       v
STEP 4   PLATELET DETECT  (PRE-match)
         saturation spike in cell-free gap -> platelet_pre
                       |
                       v
STEP 5   WBC DETECT  (PRE-match)
         adaptive threshold vs this slide's own RBCs -> wbc_pre
                       |
                       v
STEP 6   COLOUR MATCHING
         LAB mean/std transfer to reference(s)
         (no -r  ->  2-98 percentile levels stretch instead)
                       |
                       v
STEP 7   WBC DETECT  (POST-match)
         refines wbc_pre only, cannot add new nuclei -> wbc_mask
                       |
                       v
STEP 8   PLATELET DETECT  (POST-match)
         dark AND saturated in gap -> platelet_post
                       |
                       v
STEP 9   MERGE PLATELET MASKS
         platelet_pre + platelet_post + tiny_mask -> platelet_prot
                       |
                       v
STEP 10  BACKGROUND WHITENING
         88% pull to white, blocked by cells / platelets / WBC
                       |
                       v
STEP 11  DRAW ELLIPSE BORDERS
         soft darkened ring around each cell
                       |
                       v
STEP 12  RBC TINT
         pull cell interiors toward pale red, hue 177
                       |
                       v
STEP 13  RBC CLEAN
         kill grey/blue cast, saturation floor, value floor
                       |
                       v
STEP 14  PLATELET BOOST
         60% pull to dark purple, hue 130
                       |
                       v
STEP 15  HAZE KILL
         near-white low-sat pixels forced to pure white
                       |
                       v
STEP 16  GLOBAL SATURATION BOOST
         optional, -s flag
                       |
                       v
STEP 17  RBC REFERENCE CLAMP
         hue 172, sat clamped 40-82, value floor 178
                       |
                       v
STEP 18  SUPPRESS STRAY VIOLET
         leftover lavender outside WBC/platelet zones -> white or red
                       |
                       v
STEP 19  WBC PASSTHROUGH
         nucleus composited back from the ORIGINAL image, untouched
                       |
                       v
STEP 20  PERINUCLEAR CYTOPLASM RIM
         25px pale lavender ring around the nucleus
                       |
                       v
              OUTPUT  (enhanced smear image)



CLI OPTIONS
================

  input               source smear image
  -o, --output        output path            (default <input>_cp.jpeg)
  -r, --reference     colour reference, repeatable, LAB stats averaged
  -d, --max-dim       CellPose inference size, lower = faster  (default 960)
  -q, --quality       JPEG quality           (default 95)
  -s, --saturation    global saturation multiplier  (default 1.0)



WHY THE ORDERING MATTERS
============================

PLATELETS ARE DETECTED BEFORE COLOUR MATCHING (Step 4)
  Post-match-only detection caused a feedback loop on lavender slides: loose
  thresholds flagged background noise as platelets -> whitening was blocked ->
  lavender background survived -> detected as even more platelets -> hundreds
  of false blue dots. Pre-match, platelets sit at sat ~90 vs background ~25-30,
  so the adaptive threshold max(20, median_gap_sat * 1.6) separates cleanly.

FILL_MASK IS DILATED ~3PX BEFORE GAP COMPUTATION (Step 3)
  Pixels just outside a fitted ellipse are cell-border pixels - also saturated
  - and would otherwise register as platelets.

WBC THRESHOLDS ARE SLIDE-RELATIVE, NOT FIXED (Step 5)
  satThr = max(70, rbc_sat * 1.5). Value is deliberately not a discriminator:
  on a lavender slide the nucleus is BRIGHTER than the RBCs (V 224 vs 200), on
  a pink slide it is darker. Saturation separates reliably in both cases.

POST-MATCH WBC DETECTION CAN ONLY REFINE, NEVER ADD (Step 7)
  It is multiplied by a 21x21 dilation of wbc_pre. Without this gate, RBC
  clusters the adaptive filter correctly rejected come back as false WBCs.

STRAY VIOLET IS SUPPRESSED BEFORE THE WBC PASSTHROUGH (Step 18)
  Red-forcing steps only run INSIDE the cell ellipses, and whitening only pulls
  the gap 88% toward white. Cells CellPose missed or merged in dense clumps
  fall in neither set and keep their lavender. This step catches them globally
  - bright violet desaturated to white, dark violet forced into RBC red.

THE WBC IS RESTORED FROM THE ORIGINAL IMAGE (Step 19)
  No stage - denoise, flatten, matching, whitening, tint, borders, saturation -
  survives on those pixels, so the leucocyte keeps its raw chromatin texture.
  Only the perinuclear rim is synthesised afterwards.



TUNING KNOBS
===============

  platelet size cutoff       build_masks
                             tiny_thr = max(80, 0.30 * median_area)

  border ring drawn          build_masks
                             max_ellipse_area = 4000

  pre-match platelet sens.   find_platelet_mask_prematch
                             blob_sat_thr = max(45, bg_sat * 2.0), area 4-300

  WBC strictness             find_wbc_mask_prematch
                             sat multiplier 1.5, solidity 0.72, stdV <= 24

  background whiteness       Step 10   blend = bg_mask * 0.88

  WBC halo protection        Step 10   wbc_guard 41x41 dilate, sigma 9,
                                       weight 0.85

  RBC final colour           match_rbc_to_reference
                             hue 172, sat 40-82, val_floor 178

  platelet visibility        boost_platelets
                             pull 0.60, hue 130, sat 160, V x0.75

  cytoplasm rim              tint_perinuclear_cytoplasm
                             reach 25, hue 133, sat_mul 0.35, lighten 0.30



NOTES ON THE CURRENT FILE
==============================

  * The module docstring lists an 11-step order that predates the post-match
    platelet and stray-violet stages. THE CODE IS THE SOURCE OF TRUTH.

  * close_wbc_cell is defined TWICE, IDENTICALLY - and neither copy is called.

  * Not called by enhance():
        enhance_wbc, build_wbc_cell_mask, enhance_wbc_structured,
        find_cyto_mask, recolor_wbc, close_wbc_cell, segment_wbc_cytoplasm
    Earlier cytoplasm-rendering attempts, superseded by raw passthrough +
    tint_perinuclear_cytoplasm. Safe to delete.

  * wbc_cyto_bin / wbc_cyto_soft / other_cells are allocated as zeros and never
    populated - the WBC region is the nucleus mask only.

  * The CellPose model is cached in module-global _MODEL, so a long-lived
    process pays the load cost once.



DEPENDENCIES
===============

  opencv-python
  numpy
  scipy
  cellpose        # model_type='cyto3', gpu=True
  torch           # CUDA build matching your device



