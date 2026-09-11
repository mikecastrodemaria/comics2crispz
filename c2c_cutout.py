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


def cutout(image):
    """PIL image -> RGBA matte of its main subject (same size). Raises
    RuntimeError with a clear message when rembg is unavailable."""
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


def refresh(image_path, force=False):
    """Compute (or recompute) the matte of a drawing; returns its path."""
    from PIL import Image
    cp = cutout_path(image_path)
    if not force and not is_stale(image_path):
        return cp
    with Image.open(image_path) as im:
        m = cutout(im)
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
