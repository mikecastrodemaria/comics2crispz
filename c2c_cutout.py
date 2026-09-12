"""comics2crispz - subject cutout (matte) for panels that break the frame.

A "breakout" panel draws its subject (a character, a hand, a web) OVER the
neighbouring panels while its background stays clipped inside the frame -
the classic Spider-Man page. That needs the subject separated from its
background: an alpha matte, computed here with rembg (optional dependency,
CPU is enough, a few seconds per panel). Everything else in the app works
without it; a missing library is reported with the install command, never
a crash.

The matte is stored next to the panel drawing (<pnid>.cutout.png, RGBA at
the drawing's size) and refreshed automatically when the drawing changes.

Two matte modes:
- "ai": rembg finds the main subject. Right for a character over a scene.
- "key": everything that is NOT the plain background colour is kept (the
  colour is sampled on the drawing's edges; `tolerance` 0-100 says how far
  a pixel may drift from it before it counts as subject). Right for line
  art or several small subjects on a plain white/cream ground - butterflies,
  sound effects, props - where the AI keeps only one of them. Needs nothing
  but Pillow.
"""

import os
import time

_SESSION = {}


def available():
    """(True, model) or (False, reason)."""
    try:
        import rembg  # noqa: F401
    except Exception as e:                       # ImportError, DLL errors...
        return False, (f"rembg is not installed ({type(e).__name__}): "
                       f"run  pip install rembg  in the app's venv")
    return True, _model_name()


def _model_name():
    return os.environ.get("C2C_CUTOUT_MODEL") or "isnet-general-use"


def _session():
    from rembg import new_session
    name = _model_name()
    if name not in _SESSION:
        try:
            _SESSION[name] = new_session(name)
        except Exception:
            # a model name unknown to this rembg version -> the default one
            _SESSION[name] = new_session("u2net")
    return _SESSION[name]


MATTE_MODES = ("ai", "key")
DEFAULT_TOLERANCE = 25


def background_colour(image):
    """Dominant colour of the drawing's edges (median per channel of a
    thin border strip): the plain ground of a 'key' matte."""
    img = image.convert("RGB")
    w, h = img.size
    t = max(2, min(w, h) // 40)
    strips = [img.crop((0, 0, w, t)), img.crop((0, h - t, w, h)),
              img.crop((0, 0, t, h)), img.crop((w - t, 0, w, h))]
    chans = ([], [], [])
    for st in strips:
        for px in st.getdata():
            for i in range(3):
                chans[i].append(px[i])
    return tuple(sorted(c)[len(c) // 2] for c in chans)


def cutout_key(image, tolerance=DEFAULT_TOLERANCE, colour=None):
    """Colour-key matte: alpha grows with the distance from the background
    colour. `tolerance` 0-100 (percent of the colour range): below 60% of
    it a pixel is ground, above it the pixel is fully kept, soft ramp
    between the two. Pure Pillow."""
    from PIL import Image, ImageChops, ImageFilter
    img = image.convert("RGB")
    bg = tuple(colour) if colour else background_colour(img)
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg))
    r, g, b = diff.split()
    dist = ImageChops.lighter(ImageChops.lighter(r, g), b)
    try:
        tol = max(0, min(100, int(tolerance)))
    except (TypeError, ValueError):
        tol = DEFAULT_TOLERANCE
    hi = max(4, int(round(tol * 2.55)))
    lo = int(hi * 0.6)
    lut = [0 if v <= lo else 255 if v >= hi else int((v - lo) * 255 / (hi - lo))
           for v in range(256)]
    alpha = dist.point(lut).filter(ImageFilter.MedianFilter(3))   # kills speckles
    # only the ground CONNECTED TO THE EDGES goes transparent: a white area
    # enclosed in a drawing (the inside of a butterfly's wing, a face) is
    # part of the subject and stays opaque
    enclosed = _enclosed_ground(alpha)
    if enclosed is not None:
        alpha = ImageChops.lighter(alpha, enclosed)
    out = img.convert("RGBA")
    out.putalpha(alpha)
    out.info["background"] = bg
    return out


def _enclosed_ground(alpha):
    """L mask (255) of the transparent areas NOT reachable from the image
    border through transparent pixels. scipy when present (fast), else a
    Pillow flood fill from the border; None when nothing is enclosed."""
    from PIL import Image, ImageChops
    ground = alpha.point(lambda v: 255 if v < 128 else 0)      # 255 = ground
    try:
        import numpy as np
        from scipy import ndimage
        g = np.asarray(ground) > 0
        lab, n = ndimage.label(g)
        if n == 0:
            return None
        border = set(np.unique(lab[0, :])) | set(np.unique(lab[-1, :])) \
            | set(np.unique(lab[:, 0])) | set(np.unique(lab[:, -1]))
        keep = np.isin(lab, [i for i in range(1, n + 1) if i not in border]) & g
        if not keep.any():
            return None
        return Image.fromarray((keep * 255).astype("uint8"), "L")
    except Exception:
        pass
    from PIL import ImageDraw
    w, h = ground.size
    reach = ground.copy()                                   # 255 ground, 0 subject
    px = reach.load()
    for x in range(w):
        for y in (0, h - 1):
            if px[x, y] == 255:
                ImageDraw.floodfill(reach, (x, y), 128)
    for y in range(h):
        for x in (0, w - 1):
            if px[x, y] == 255:
                ImageDraw.floodfill(reach, (x, y), 128)
    enclosed = reach.point(lambda v: 255 if v == 255 else 0)   # ground never reached
    return enclosed if enclosed.getbbox() else None


def cutout(image, mode="ai", tolerance=DEFAULT_TOLERANCE):
    """PIL image -> RGBA matte (same size). mode 'ai' = rembg subject
    (RuntimeError with a clear message when rembg is unavailable),
    'key' = everything but the plain background colour."""
    if mode == "key":
        t0 = time.time()
        out = cutout_key(image, tolerance)
        out.info["seconds"] = round(time.time() - t0, 1)
        return out
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    from rembg import remove
    img = image.convert("RGB")
    t0 = time.time()
    out = remove(img, session=_session(), post_process_mask=True)
    out.info["seconds"] = round(time.time() - t0, 1)
    return out.convert("RGBA")


def cutout_path(image_path):
    return os.path.splitext(image_path)[0] + ".cutout.png"


def is_stale(image_path):
    """True when the matte is missing or older than the drawing."""
    cp = cutout_path(image_path)
    if not os.path.isfile(cp):
        return True
    try:
        return os.path.getmtime(cp) < os.path.getmtime(image_path) - 1
    except OSError:
        return True


def refresh(image_path, force=False, mode="ai", tolerance=DEFAULT_TOLERANCE):
    """Compute (or recompute) the matte of a drawing; returns its path."""
    from PIL import Image
    cp = cutout_path(image_path)
    if not force and not is_stale(image_path):
        return cp
    with Image.open(image_path) as im:
        m = cutout(im, mode=mode, tolerance=tolerance)
    tmp = cp + f".{os.getpid()}.tmp"
    m.save(tmp, "PNG")
    os.replace(tmp, cp)
    return cp


def edit_alpha(image_path, mask, mode="remove"):
    """Correct the matte with a painted mask (L, same size or resized):
    'remove' clears the painted area, 'add' brings the drawing's pixels back
    there. Returns the matte path."""
    from PIL import Image, ImageChops
    cp = refresh(image_path)
    with Image.open(cp) as m, Image.open(image_path) as src:
        m = m.convert("RGBA")
        src = src.convert("RGB")
        if mask.size != m.size:
            mask = mask.resize(m.size, Image.NEAREST)
        mask = mask.convert("L")
        r, g, b, a = m.split()
        if mode == "add":
            a = ImageChops.lighter(a, mask)
            sr, sg, sb = src.split()
            r = Image.composite(sr, r, mask)
            g = Image.composite(sg, g, mask)
            b = Image.composite(sb, b, mask)
        else:
            a = ImageChops.subtract(a, mask)
        out = Image.merge("RGBA", (r, g, b, a))
    tmp = cp + f".{os.getpid()}.tmp"
    out.save(tmp, "PNG")
    os.replace(tmp, cp)
    return cp
