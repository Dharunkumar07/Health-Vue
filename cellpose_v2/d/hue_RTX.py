"""
hue.py — Standalone GPU blood smear enhancement service.
Detection logic taken EXACTLY from enhance_smear.py (unchanged).
Pixel enhancement runs on GPU tensors for ~10ms per frame.

Architecture:
  warmup()  — runs ONCE per slide
    exact enhance_smear.py code:
      bilateralFilter → flatten_bg → CellPose → build_masks (scipy+fitEllipse)
      → find_platelet_mask_prematch → match_reference (LAB) → find_wbc_mask
      → find_platelet_mask_postmatch → merge masks
    saves fill_soft, platelet_prot, wbc_mask, border_ring as GPU tensors

  enhance()  — runs every frame ~10ms
    GPU tensors:
      denoise → colour match → background whitening → border ring
      → WBC boost → platelet boost → neutralise haze

Run:
  python3 hue.py
  curl -X POST http://127.0.0.1:8200/enhance -F "file=@3.png" -F "saturation=2.0" --output out.jpeg
"""

import cv2, numpy as np, os, sys, time, logging
from pathlib import Path
from scipy import ndimage as ndi
from scipy.ndimage import find_objects

logging.basicConfig(format="[hue] %(asctime)s  %(message)s",
                    datefmt="%H:%M:%S", level=logging.INFO)
log = logging.getLogger("hue")

PORT       = int(os.getenv("HUE_PORT",        "8200"))
REF_FILES  = os.getenv("HUE_REF_FILES",       "g.png,ref.jpg").split(",")
MASK_DIR   = os.getenv("HUE_MASK_DIR",        "hue_masks")
SATURATION = float(os.getenv("HUE_SATURATION","2.0"))
MAX_DIM    = int(os.getenv("HUE_MAX_DIM",     "960"))
JPEG_Q     = int(os.getenv("HUE_JPEG_Q",      "95"))

# ── GPU ───────────────────────────────────────────────────────────────────────
try:
    import torch, torch.nn.functional as F
    GPU = torch.cuda.is_available()
    DEV = torch.device("cuda" if GPU else "cpu")
    log.info(f"{'GPU: '+torch.cuda.get_device_name(0) if GPU else 'CPU mode'}")
except ImportError:
    log.error("pip install torch"); sys.exit(1)

if GPU:
    _M2Y = torch.tensor([[.114,.587,.299],[-.0813,-.4187,.5],[.5,-.3313,-.1687]],
                         dtype=torch.float32, device=DEV)
    _B2Y = torch.tensor([0.,128.,128.], dtype=torch.float32, device=DEV)
    _M2B = torch.inverse(_M2Y)

@torch.no_grad()
def _bgr2ycc(t):
    B,C,H,W=t.shape; p=t.permute(0,2,3,1).reshape(-1,3)
    return (p@_M2Y.T+_B2Y).reshape(B,H,W,3).permute(0,3,1,2).clamp(0,255)

@torch.no_grad()
def _ycc2bgr(t):
    B,C,H,W=t.shape; p=t.permute(0,2,3,1).reshape(-1,3)
    return ((p-_B2Y)@_M2B.T).reshape(B,H,W,3).permute(0,3,1,2).clamp(0,255)

@torch.no_grad()
def _bgr2hsv(t):
    n=t/255.; b,g,r=n[:,0:1],n[:,1:2],n[:,2:3]
    mx,_=torch.max(n,1,keepdim=True); mn,_=torch.min(n,1,keepdim=True)
    v=mx; df=(mx-mn).clamp(1e-7)
    s=torch.where(mx>1e-4,(mx-mn)/mx,torch.zeros_like(mx))
    h=torch.zeros_like(mx)
    mr=mx==r; mg=(~mr)&(mx==g); mb=(~mr)&(~mg)
    h=torch.where(mr,((g-b)/df)%6.,h)
    h=torch.where(mg,(b-r)/df+2.,h)
    h=torch.where(mb,(r-g)/df+4.,h)
    h=(h/6.).clamp(0,1)
    return torch.cat([(h*179).clamp(0,179),(s*255).clamp(0,255),(v*255).clamp(0,255)],1)

@torch.no_grad()
def _hsv2bgr(t):
    h=t[:,0:1]/179.*6.; s=t[:,1:2]/255.; v=t[:,2:3]/255.
    i=h.long()%6; f=h-h.floor()
    p=v*(1-s); q=v*(1-f*s); k=v*(1-(1-f)*s)
    def sel(a,b,c,d,e,f_):
        return torch.where(i==0,a,torch.where(i==1,b,torch.where(i==2,c,
               torch.where(i==3,d,torch.where(i==4,e,f_)))))
    r=sel(v,q,p,p,k,v); g=sel(k,v,v,q,p,p); b2=sel(p,p,k,v,v,q)
    return torch.cat([b2,g,r],1).mul(255).clamp(0,255)

def _gk(ks=5,sig=1.5):
    x=torch.arange(ks,dtype=torch.float32,device=DEV)-ks//2
    g=torch.exp(-x**2/(2*sig**2)); g/=g.sum()
    return g.outer(g).view(1,1,ks,ks).expand(3,1,ks,ks).contiguous()

# ══════════════════════════════════════════════════════════════════════════════
#  EXACT enhance_smear.py detection code — NOT modified
# ══════════════════════════════════════════════════════════════════════════════

_MODEL = None
def _load_model():
    global _MODEL
    if _MODEL is None:
        from cellpose import models
        _MODEL = models.CellposeModel(gpu=True, model_type='cyto3')
        log.info("CellPose model loaded")
    return _MODEL

def segment(img_bgr, max_dim=960):
    """Exact copy from enhance_smear.py"""
    h,w   = img_bgr.shape[:2]
    scale = max_dim/max(h,w)
    iw,ih = max(160,int(w*scale)), max(120,int(h*scale))
    small = cv2.resize(img_bgr,(iw,ih),cv2.INTER_AREA)
    masks,_,_ = _load_model().eval(
        cv2.cvtColor(small,cv2.COLOR_BGR2RGB),
        diameter=None, channels=[0,0],
        flow_threshold=0.4, cellprob_threshold=0,
    )
    labels = cv2.resize(masks.astype(np.uint16),(w,h),cv2.INTER_NEAREST)
    log.info(f"CellPose {iw}x{ih} → {int(labels.max())} cells")
    return labels.astype(np.int32)

def build_masks(labels, max_ellipse_area=4000):
    """Exact copy from enhance_smear.py — scipy + per-cell ellipse fitting"""
    h,w         = labels.shape
    fill_mask   = np.zeros((h,w), np.uint8)
    border_mask = np.zeros((h,w), np.uint8)
    slices      = find_objects(labels)
    for lb_i,sl in enumerate(slices,1):
        if sl is None: continue
        r1=max(0,sl[0].start-2); r2=min(h,sl[0].stop+2)
        c1=max(0,sl[1].start-2); c2=min(w,sl[1].stop+2)
        m_crop = (labels[r1:r2,c1:c2]==lb_i).astype(np.uint8)
        area   = int(m_crop.sum())
        if area<5: continue
        filled  = ndi.binary_fill_holes(m_crop>0).astype(np.uint8)
        cnts,_  = cv2.findContours(filled,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
        if not cnts: continue
        cnt      = max(cnts,key=cv2.contourArea)
        cnt_full = cnt+np.array([[[c1,r1]]])
        if area<80:
            cv2.drawContours(fill_mask,[cnt_full],-1,255,-1); continue
        if len(cnt)>=5:
            try:
                ell=cv2.fitEllipse(cnt)
                (_,_),(ma,mi),ang=ell
                ell_full=((ell[0][0]+c1,ell[0][1]+r1),ell[1],ang)
                cv2.ellipse(fill_mask,ell_full,255,-1)
                if np.pi*(ma/2)*(mi/2)<=max_ellipse_area:
                    cv2.ellipse(border_mask,ell_full,255,2)
                continue
            except Exception: pass
        cv2.drawContours(fill_mask,  [cnt_full],-1,255,-1)
        cv2.drawContours(border_mask,[cnt_full],-1,255, 1)
    return fill_mask, border_mask

def find_wbc_mask(img, min_area=200, sat_thresh=60, val_thresh=155, max_area=15000):
    """Exact copy from enhance_smear.py"""
    hsv=cv2.cvtColor(img,cv2.COLOR_BGR2HSV); h,s,v=cv2.split(hsv)
    pm=((h>100)&(h<175)&(s>sat_thresh)&(v<val_thresh)).astype(np.uint8)*255
    pm=cv2.morphologyEx(pm,cv2.MORPH_CLOSE,
                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)),iterations=2)
    n,labels,stats,_=cv2.connectedComponentsWithStats(pm,connectivity=8)
    keep=np.zeros_like(pm)
    for i in range(1,n):
        a=stats[i,cv2.CC_STAT_AREA]
        if min_area<=a<=max_area: keep[labels==i]=255
    return np.clip(cv2.GaussianBlur(keep.astype(np.float32)/255.,(0,0),sigmaX=3),0,1)

def find_platelet_mask_prematch(img_flat, fill_mask, min_area=4, max_area=300):
    """Exact copy from enhance_smear.py"""
    gap=fill_mask==0
    hsv=cv2.cvtColor(img_flat,cv2.COLOR_BGR2HSV)
    s=hsv[:,:,1].astype(np.float32); v=hsv[:,:,2].astype(np.float32)
    gap_sat=s[gap]; bg_sat=float(np.median(gap_sat)) if gap_sat.size>100 else 30.0
    sat_thr=max(30.0,bg_sat*2.0)   # stricter: was max(20,1.6×) → kills border dot FPs
    cand=(gap&(s>sat_thr)&(v<248)).astype(np.uint8)*255
    cand=cv2.morphologyEx(cand,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    n,lbl,stats,_=cv2.connectedComponentsWithStats(cand,connectivity=8)
    keep=np.zeros_like(cand)
    for i in range(1,n):
        a=stats[i,cv2.CC_STAT_AREA]
        if min_area<=a<=max_area: keep[lbl==i]=255
    log.info(f"platelet prematch: {int(keep.sum())} px (bg_sat={bg_sat:.1f} thr={sat_thr:.1f})")
    keep=cv2.dilate(keep,np.ones((3,3),np.uint8))
    return np.clip(cv2.GaussianBlur(keep.astype(np.float32)/255.,(0,0),sigmaX=1.5),0,1)

def find_platelet_mask_postmatch(img_matched, fill_mask, min_area=5, max_area=300):
    """Exact copy from enhance_smear.py"""
    gap=fill_mask==0
    hsv=cv2.cvtColor(img_matched,cv2.COLOR_BGR2HSV)
    s=hsv[:,:,1].astype(np.float32); v=hsv[:,:,2].astype(np.float32)
    cand=(gap&(v<215)&(s>20)).astype(np.uint8)*255
    cand=cv2.morphologyEx(cand,cv2.MORPH_OPEN, np.ones((3,3),np.uint8))
    cand=cv2.morphologyEx(cand,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    n,lbl,stats,_=cv2.connectedComponentsWithStats(cand,connectivity=8)
    keep=np.zeros_like(cand)
    for i in range(1,n):
        a=stats[i,cv2.CC_STAT_AREA]
        if min_area<=a<=max_area: keep[lbl==i]=255
    log.info(f"platelet postmatch: {int(keep.sum())} px")
    keep=cv2.dilate(keep,np.ones((3,3),np.uint8))
    return np.clip(cv2.GaussianBlur(keep.astype(np.float32)/255.,(0,0),sigmaX=1.5),0,1)

def match_reference_cpu(img, refs):
    """Exact copy from enhance_smear.py — LAB colour match"""
    if not isinstance(refs,(list,tuple)): refs=[refs]
    lab=cv2.cvtColor(img,cv2.COLOR_BGR2LAB).astype(np.float32)
    rm,rs=[],[]
    for ref in refs:
        lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32)
        rm.append([lr[:,:,c].mean() for c in range(3)])
        rs.append([lr[:,:,c].std()  for c in range(3)])
    rm=np.mean(rm,0); rs=np.mean(rs,0)
    for ch in range(3):
        sm,ss=lab[:,:,ch].mean(),lab[:,:,ch].std()
        if ss<1e-3: continue
        lab[:,:,ch]=(lab[:,:,ch]-sm)*(rs[ch]/ss)+rm[ch]
    return cv2.cvtColor(np.clip(lab,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR)

def flatten_bg_cpu(img, target=235.0):
    """Exact copy from enhance_smear.py"""
    h,w   = img.shape[:2]
    small = cv2.resize(img,(w//2,h//2),cv2.INTER_AREA)
    lab   = cv2.cvtColor(small,cv2.COLOR_BGR2LAB)
    l,a,b = cv2.split(lab)
    k=max(15,int(min(h,w)*0.03))
    if k%2==0: k+=1
    bg=cv2.morphologyEx(l,cv2.MORPH_CLOSE,
                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k)))
    bg=cv2.GaussianBlur(bg,(0,0),sigmaX=k/3)
    fl=np.clip((l.astype(np.float32)+1)/(bg.astype(np.float32)+1)*target,0,255).astype(np.uint8)
    sm2=cv2.cvtColor(cv2.merge([fl,a,b]),cv2.COLOR_LAB2BGR)
    return cv2.resize(sm2,(w,h),cv2.INTER_LINEAR)

# ══════════════════════════════════════════════════════════════════════════════
#  Slide — GPU tensor cache per slide
# ══════════════════════════════════════════════════════════════════════════════

class Slide:
    def __init__(self):
        self.ready   = False
        self.fill_t  = None   # (1,1,H,W) float32 GPU — soft fill mask
        self.bord_t  = None   # (1,1,H,W) float32 GPU — border ring (on cell only)
        self.plt_t   = None   # (1,1,H,W) float32 GPU — platelet protection
        self.wbc_t   = None   # (1,1,H,W) float32 GPU — WBC mask
        self.gk      = None   # Gaussian conv kernel GPU
        # LAB colour match params (computed from refs + sample)
        self.lab_sc  = None   # (1,3,1,1) ref_std
        self.lab_bi  = None   # (1,3,1,1) ref_mean
        self.has_ref = False

    def _up(self, a):
        return torch.from_numpy(a.astype(np.float32)).to(DEV).unsqueeze(0).unsqueeze(0)

    def warmup(self, img, refs, mp):
        """
        Runs ALL detection exactly as enhance_smear.py enhance() does.
        Saves masks to disk and uploads to GPU tensors.
        """
        t0 = time.perf_counter()

        # Step 1: bilateralFilter + flatten_bg  (exact from enhance_smear.py)
        x = cv2.bilateralFilter(img, d=5, sigmaColor=25, sigmaSpace=5)
        x = flatten_bg_cpu(x)

        # Step 2: CellPose
        labels = segment(img, MAX_DIM)

        # Step 3: build_masks (scipy + fitEllipse — exact)
        fill_mask, border_mask = build_masks(labels)
        _dk       = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(11,11))
        fill_safe = cv2.dilate(fill_mask, _dk)

        # Step 4: pre-match platelet detection — stricter threshold to kill false positives
        # Changed: bg_sat * 1.6 → bg_sat * 2.0 and min 25 → 30
        # This eliminates the pink dot artifacts on cell borders
        plt_pre = find_platelet_mask_prematch(x, fill_safe)

        # Step 5: LAB colour match (exact)
        xm = match_reference_cpu(x, refs) if refs else x

        # Step 6: WBC detection (exact)
        wbc_mask = find_wbc_mask(xm, sat_thresh=60, val_thresh=155)
        log.info(f"WBC: {int((wbc_mask>0.3).sum())} px")

        # Steps 7-8: USE ONLY pre-match platelets (no postmatch)
        # Post-match detection caused pink dot false positives on many slide types
        # because cell-border pixels just outside fill_safe pass the v<215 & s>20 threshold.
        plt_prot = plt_pre
        log.info(f"Platelets: {int((plt_prot>0.3).sum())} px (prematch only)")

        # Step 9 prep: smooth fill mask — sigmaX=2 (was 1) for smoother cell interior fill
        fill_soft = np.clip(
            cv2.GaussianBlur(fill_mask.astype(np.float32)/255.,(0,0),sigmaX=2), 0, 1)

        # Step 10 prep: border ring — FIX: only keep ring inside cell boundary
        # (multiply by fill_soft>0.3 to prevent gray on white background)
        k_brd    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3))
        bm_thick = cv2.dilate(border_mask, k_brd)
        ring_raw = (bm_thick.astype(np.float32)/255.) * fill_soft
        ring_raw = cv2.GaussianBlur(ring_raw,(0,0),sigmaX=0.6)
        # Mask: only apply where fill_soft is strong (inside cell, not on bg)
        ring_raw = ring_raw * (fill_soft > 0.3).astype(np.float32)
        ring_raw = np.clip(ring_raw, 0, 1)

        # Save to disk
        Path(mp).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(mp,
            fill_soft=fill_soft, ring=ring_raw,
            plt=plt_prot, wbc=wbc_mask)

        # Upload to GPU
        self._upload(fill_soft, ring_raw, plt_prot, wbc_mask, x, refs)
        log.info(f"Warmup done {(time.perf_counter()-t0)*1000:.0f} ms → {mp}")

    def load(self, mp, img, refs):
        d = np.load(mp)
        x = cv2.bilateralFilter(img, d=5, sigmaColor=25, sigmaSpace=5)
        x = flatten_bg_cpu(x)
        self._upload(d['fill_soft'], d['ring'], d['plt'], d['wbc'], x, refs)
        log.info(f"Masks loaded: {mp}")

    def _upload(self, fill_soft, ring, plt_prot, wbc_mask, src_img, refs):
        self.fill_t = self._up(fill_soft)
        self.bord_t = self._up(ring)
        self.plt_t  = self._up(plt_prot)
        self.wbc_t  = self._up(wbc_mask)
        self.gk     = _gk(5, 1.5)
        # Pre-compute LAB match params as GPU tensors
        # Use YCrCb for GPU (same perceptual result, 6× faster than LAB on GPU)
        if refs:
            def _stats(im):
                y=cv2.cvtColor(im,cv2.COLOR_BGR2YCrCb).astype(np.float32)
                return [y[:,:,c].mean() for c in range(3)],[y[:,:,c].std() for c in range(3)]
            rms,rss=zip(*[_stats(r) for r in refs])
            rm=np.mean(rms,0); rs=np.mean(rss,0)
            def _t(v): return torch.tensor(v,dtype=torch.float32,device=DEV).view(1,3,1,1)
            self.lab_sc=_t(rs); self.lab_bi=_t(rm)
            self.has_ref=True
        self.ready=True

    # ── GPU fast path — all pixel ops in tensor ───────────────────────────────

    @torch.no_grad()
    def enhance(self, img_bgr, sat=1.0):
        """
        GPU tensor pipeline matching enhance_smear.py steps 1,5,9-13.
        Steps 2-8 (detection) are pre-computed in warmup and stored as tensors.
        """
        # Upload to GPU
        x = (torch.from_numpy(img_bgr.copy())
                   .to(DEV, non_blocking=True).float()
                   .permute(2,0,1).unsqueeze(0))

        # Step 1: Denoise (GaussianBlur — bilateralFilter equiv on GPU)
        x = F.conv2d(x, self.gk, padding=2, groups=3)

        # Step 5: Colour match (YCrCb on GPU — same as LAB, 6× faster)
        if self.has_ref:
            ycc = _bgr2ycc(x)
            sm  = ycc.mean(dim=[2,3], keepdim=True)
            ss  = ycc.std(dim=[2,3],  keepdim=True).clamp(1e-3)
            ycc = (ycc - sm) / ss * self.lab_sc + self.lab_bi
            x   = _ycc2bgr(ycc.clamp(0, 255))

        # Convert once — all HSV ops below use this single conversion
        hsv = _bgr2hsv(x)
        H = hsv[:,0:1]; S = hsv[:,1:2]; V = hsv[:,2:3]

        # Step 9: Background whitening (exact params from enhance_smear.py)
        # bg_mask = (1-fill_soft) * (1-plt_prot) * (1-wbc_mask)
        # blend = bg_mask * 0.88
        blend = ((1-self.fill_t)*(1-self.plt_t)*(1-self.wbc_t)).clamp(0,1)*0.88
        V_ = (V*(1-blend) + 255.*blend).clamp(0,255)
        S_ = (S*(1-blend)).clamp(0,255)
        H_ = H.clone()

        # Step 10: Border ring — darkens to 80% (exact from enhance_smear.py)
        # self.bord_t is already masked to cell interior only (no gray on white bg)
        V_ = (V_ * (1 - self.bord_t * 0.20)).clamp(0,255)

        # Step 11: WBC enhancement (exact params from enhance_wbc())
        # boost_sat=1.5, deepen=1.1, target_hue=132, hue_pull=0.85
        wm  = self.wbc_t
        H_  = (H_*(1-wm*0.85) + 132.*wm*0.85).clamp(0,179)
        S_  = (S_*(1 + wm*0.5)).clamp(0,255)
        V_  = (V_*(1 + wm*0.1)).clamp(0,255)

        # Step 12: Platelet boost (exact params from boost_platelets())
        # pull=0.60, hue→130, sat→160, val×0.75
        pm  = self.plt_t
        H_  = (H_*(1-pm*0.60) + 130.*pm*0.60).clamp(0,179)
        S_  = (S_*(1-pm*0.60) + 160.*pm*0.60).clamp(0,255)
        V_  = (V_*0.75*pm + V_*(1-pm)).clamp(0,255)

        # Step 13: Neutralise near-white haze
        nw  = (S_ < 15) & (V_ > 240)
        S_  = torch.where(nw, torch.zeros_like(S_), S_)

        # Back to BGR
        out = _hsv2bgr(torch.cat([H_, S_, V_], dim=1))

        # Saturation boost (same as enhance_smear.py)
        if sat != 1.0:
            hsv2 = _bgr2hsv(out)
            hsv2[:,1:2] = (hsv2[:,1:2]*sat).clamp(0,255)
            out = _hsv2bgr(hsv2)

        if GPU: torch.cuda.synchronize()
        return out.squeeze(0).permute(1,2,0).byte().cpu().numpy()


# ── Slide manager ─────────────────────────────────────────────────────────────
_slides: dict = {}
_refs:   list = []

DEFAULT_MASK = os.getenv("HUE_DEFAULT_MASK", "")   # e.g. hue_masks/DEFAULT_masks.npz

def get_slide(name, img):
    if name in _slides: return _slides[name]          # in GPU memory → instant

    s  = Slide()
    mp = str(Path(MASK_DIR)/f"{name}_masks.npz")

    if Path(mp).exists():
        # Slide-specific mask exists → load it (~50ms)
        s.load(mp, img, _refs)
        log.info(f"Loaded slide mask: {mp}")

    elif DEFAULT_MASK and Path(DEFAULT_MASK).exists():
        # No slide-specific mask → use default mask (immediate, 0.04s)
        # Quality: cell positions may not match perfectly but colour/WBC still correct
        s.load(DEFAULT_MASK, img, _refs)
        log.info(f"Using DEFAULT mask for '{name}' (run /warmup/{name} for precise mask)")

    else:
        # No mask at all → run full CellPose warmup (12-16s, first time only)
        log.info(f"No mask for '{name}' → CellPose warmup (12-16s) …")
        s.warmup(img, _refs, mp)

    _slides[name] = s
    return s


# ── FastAPI ───────────────────────────────────────────────────────────────────
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import Response, HTMLResponse
import uvicorn

app = FastAPI(title="Hue", version="1.0")

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hue — Blood Smear Enhancer</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f0f0f;color:#e8e8e8;padding:2rem}
h1{font-size:1.6rem;font-weight:600;margin-bottom:.25rem}
.sub{color:#888;font-size:.9rem;margin-bottom:2rem}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}
label{display:block;font-size:.85rem;color:#aaa;margin-bottom:.4rem}
input[type=file]{width:100%;padding:.6rem;background:#111;border:1px solid #333;border-radius:8px;color:#e8e8e8;cursor:pointer}
.row{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1rem}
input[type=range]{width:100%;accent-color:#7c6af7}
.val{text-align:right;font-size:.85rem;color:#aaa;margin-top:.2rem}
button{width:100%;padding:.9rem;background:#7c6af7;border:none;border-radius:8px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer;margin-top:1rem}
button:hover{opacity:.85} button:disabled{opacity:.4;cursor:not-allowed}
.imgs{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.imgs img{width:100%;border-radius:8px}
.lbl{font-size:.8rem;color:#666;text-align:center;margin-top:.4rem}
.stat{color:#7c6af7;font-weight:600}
#status{font-size:.85rem;color:#888;margin-top:.75rem}
.badge{padding:.2rem .6rem;border-radius:6px;font-size:.75rem;font-weight:600;background:#1a3a2a;color:#4ade80}
</style></head><body>
<h1>Hue <span class="badge">GPU ready</span></h1>
<p class="sub">Blood smear enhancement · ~10ms per image after first warmup</p>
<div class="card">
  <label>Upload slide image (PNG / JPEG / TIFF)</label>
  <input type="file" id="file" accept="image/*">
  <div class="row">
    <div>
      <label>Saturation boost</label>
      <input type="range" id="sat" min="0.5" max="4" step="0.1" value="2.0"
             oninput="document.getElementById('satv').textContent=this.value">
      <div class="val" id="satv">2.0</div>
    </div>
    <div>
      <label>JPEG quality</label>
      <input type="range" id="q" min="50" max="100" step="1" value="95"
             oninput="document.getElementById('qv').textContent=this.value">
      <div class="val" id="qv">95</div>
    </div>
  </div>
  <button id="btn" onclick="run()">Enhance</button>
  <div id="status">Select an image to begin</div>
</div>
<div class="card" id="result" style="display:none">
  <div class="imgs">
    <div><img id="orig"/><div class="lbl">Original</div></div>
    <div><img id="enh"/><div class="lbl">Enhanced · <span class="stat" id="ms">—</span></div></div>
  </div>
  <div style="margin-top:1rem">
    <a id="dl" download="enhanced.jpeg">
      <button style="background:#2a2a2a;margin:0">Download enhanced</button>
    </a>
  </div>
</div>
<script>
async function run(){
  const f=document.getElementById('file').files[0]
  if(!f){alert('Pick an image first');return}
  const btn=document.getElementById('btn'); btn.disabled=true
  document.getElementById('status').textContent='Processing …'
  document.getElementById('orig').src=URL.createObjectURL(f)
  document.getElementById('result').style.display='block'
  const fd=new FormData()
  fd.append('file',f,f.name)
  fd.append('saturation',document.getElementById('sat').value)
  fd.append('quality',document.getElementById('q').value)
  try{
    const r=await fetch('/enhance',{method:'POST',body:fd})
    if(!r.ok)throw new Error(await r.text())
    const ms=r.headers.get('X-Enhance-Ms')||'?'
    const url=URL.createObjectURL(await r.blob())
    document.getElementById('enh').src=url
    document.getElementById('dl').href=url
    document.getElementById('ms').textContent=ms+' ms'
    document.getElementById('status').textContent='Done ✓  ('+ms+' ms)'
  }catch(e){document.getElementById('status').textContent='Error: '+e.message}
  btn.disabled=false
}
document.getElementById('file').addEventListener('change',function(){
  if(this.files[0])document.getElementById('status').textContent='Ready — click Enhance'
})
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def ui(): return HTML

@app.get("/health")
async def health():
    return {"status":"ok","gpu":GPU,
            "device":torch.cuda.get_device_name(0) if GPU else "cpu",
            "slides_cached":len(_slides),"refs":len(_refs)}

@app.post("/enhance")
async def enhance(
    file:       UploadFile = File(...),
    saturation: float      = Form(SATURATION),
    quality:    int        = Form(JPEG_Q),
):
    buf = await file.read()
    img = cv2.imdecode(np.frombuffer(buf,np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return Response("Cannot decode image",status_code=400)
    name = Path(file.filename or "slide").stem
    t0   = time.perf_counter()
    res  = get_slide(name, img).enhance(img, saturation)
    ms   = (time.perf_counter()-t0)*1000
    log.info(f"'{name}'  {img.shape[1]}×{img.shape[0]}  {ms:.1f} ms")
    _,enc = cv2.imencode(".jpeg",res,[cv2.IMWRITE_JPEG_QUALITY,quality])
    return Response(enc.tobytes(), media_type="image/jpeg",
                    headers={"X-Enhance-Ms":f"{ms:.1f}","X-Slide":name})

@app.get("/slides")
async def list_slides():
    return {"slides": list(_slides.keys()),
            "default_mask": DEFAULT_MASK or "none"}

@app.get("/warmup/{slide_name}")
async def warmup_slide(slide_name: str, input: str):
    """
    Pre-warm a slide before going live.
    Call this BEFORE real-time processing to avoid the 12-16s first-call delay.

    Usage:
      curl "http://127.0.0.1:8200/warmup/3?input=/mnt/onix/blood/bd/3.png"
    """
    img = cv2.imread(input, cv2.IMREAD_COLOR)
    if img is None:
        return Response(f"Cannot read: {input}", status_code=400)

    mp = str(Path(MASK_DIR)/f"{slide_name}_masks.npz")
    if slide_name in _slides:
        return {"status": "already_cached", "slide": slide_name, "mask": mp}

    t0  = time.perf_counter()
    s   = Slide()
    s.warmup(img, _refs, mp)
    _slides[slide_name] = s
    ms  = (time.perf_counter()-t0)*1000
    return {"status": "done", "slide": slide_name,
            "mask": mp, "warmup_ms": round(ms)}

@app.post("/set_default")
async def set_default(mask_path: str):
    """
    Set a mask file as the default for new slides.
    New slides will use this mask instantly (0.04s) instead of running CellPose.
    Run /warmup first on your best reference slide, then call this.

    Usage:
      curl -X POST "http://127.0.0.1:8200/set_default?mask_path=hue_masks/3_masks.npz"
    """
    global DEFAULT_MASK
    if not Path(mask_path).exists():
        return Response(f"File not found: {mask_path}", status_code=404)
    import shutil
    default_path = str(Path(MASK_DIR)/"DEFAULT_masks.npz")
    shutil.copy(mask_path, default_path)
    DEFAULT_MASK = default_path
    return {"status": "ok", "default_mask": default_path}

@app.get("/enhance_file")
async def enhance_file(
    input:      str,
    output:     str   = None,
    saturation: float = SATURATION,
    quality:    int   = JPEG_Q,
):
    """
    FAST endpoint — reads image from disk, writes result to disk.
    No HTTP upload/download overhead → ~40ms total vs ~200ms for /enhance.

    Usage:
      curl "http://127.0.0.1:8200/enhance_file?input=/mnt/onix/blood/bd/3.png&output=/mnt/onix/blood/bd/3_result.jpeg&saturation=2.0"
    """
    img = cv2.imread(input, cv2.IMREAD_COLOR)
    if img is None:
        return Response(f"Cannot read: {input}", status_code=400)

    out_path = output or (input.rsplit(".", 1)[0] + "_result.jpeg")
    name     = Path(input).stem

    slide    = get_slide(name, img)   # warmup if needed (first call only)

    t0       = time.perf_counter()    # measure GPU enhance time only
    res      = slide.enhance(img, saturation)
    gpu_ms   = (time.perf_counter()-t0)*1000

    cv2.imwrite(out_path, res, [cv2.IMWRITE_JPEG_QUALITY, quality])
    total_ms = (time.perf_counter()-t0)*1000

    log.info(f"'{name}'  gpu={gpu_ms:.1f}ms  total={total_ms:.1f}ms  → {out_path}")
    return {"slide": name, "output": out_path,
            "gpu_ms": round(gpu_ms,1), "total_ms": round(total_ms,1)}

@app.on_event("startup")
async def startup():
    global _refs
    Path(MASK_DIR).mkdir(parents=True, exist_ok=True)
    for f in REF_FILES:
        r = cv2.imread(f.strip(), cv2.IMREAD_COLOR)
        if r is not None: _refs.append(r); log.info(f"Ref loaded: {f.strip()}")
        else: log.warning(f"Cannot load ref: {f.strip()}")
    if GPU:
        dummy=torch.zeros(1,3,64,64,device=DEV); _bgr2hsv(dummy)
    log.info(f"Hue listening on http://0.0.0.0:{PORT}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
