"""The shared visual layer: tokens, surfaces, and motion.

Until now each page carried its own palette, and the palette was deliberately
flat -- DESIGN 2.7 forbade gradients, glow, glass and chart animation on the
grounds that they read as "demo" while the content is meant to read as
"evidence". That constraint was a *style* argument, not a correctness one, and
it has been lifted: the pages now use depth, translucency and motion.

What did not change, and must not:

* **Density.** 13px body, 11.5px meta, 1.5 line height. The visual language can
  be contemporary; the information per screen cannot drop.
* **Colour carries meaning.** Amber is still "blocked", green is still
  "complete", red is still "this is where the chain broke". The gradients are
  background and chrome -- never a data encoding.
* **Mono for identifiers.** Tool names, hashes, seq numbers. This is what makes
  a page read as an artifact rather than a slide, and it survives the restyle.

Two surfaces, one system:

* ``glass`` -- translucency for things that float over the page background
  (chart panels, the header band). Blur is real (``backdrop-filter``) with an
  opaque-enough fallback alpha for browsers that ignore it.
* ``solid`` -- for dense tables, where a translucent background behind 11.5px
  text costs legibility for nothing.

Motion is limited to entrance and state change, 300-500ms, and it never moves
anything a reader is currently reading. A page that re-flows while being read is
worse than a static one.
"""

from __future__ import annotations

__all__ = ["TOKENS_CSS", "SURFACE_CSS", "MOTION_CSS", "BASE_CSS"]

#: Palette and surfaces. Variable names are the ones ``render.py`` already
#: used, so the replay page's rules keep working unchanged -- only the values
#: moved. Adding a second set of names would have meant two palettes that drift.
TOKENS_CSS = """
:root{
  --bg:#e9edf5;
  --bg-top:#f8f9fc;
  --bg-bottom:#e4e9f3;
  --panel:rgba(255,255,255,.74);
  --panel-solid:#fffefb;
  --ink:#16181d;
  --muted:#646b7a;
  --line:rgba(22,24,29,.11);
  --line-strong:rgba(22,24,29,.20);

  --accent:#0b6b5b;
  --accent-2:#12897a;
  --accent-soft:rgba(11,107,91,.10);
  --bad:#b3261e;
  --bad-soft:rgba(179,38,30,.09);
  --warn:#8a5a00;
  --warn-soft:rgba(138,90,0,.10);
  --rule:#3d4e8a;
  --rule-soft:rgba(61,78,138,.10);

  --radius:14px;
  --radius-sm:9px;
  --shadow-sm:0 1px 2px rgba(16,24,40,.05);
  --shadow-md:0 12px 32px -18px rgba(16,24,40,.35), 0 2px 6px -2px rgba(16,24,40,.10);
  --shadow-lg:0 24px 60px -28px rgba(16,24,40,.40);
  --ring:0 0 0 1px rgba(255,255,255,.60) inset;
}
"""

SURFACE_CSS = """
*,*::before,*::after{box-sizing:border-box}
html{background:var(--bg)}
body{
  margin:0;color:var(--ink);
  font:15px/1.55 -apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif;
  background:
    radial-gradient(1100px 520px at 8% -12%, rgba(11,107,91,.13), transparent 62%),
    radial-gradient(900px 500px at 96% -6%, rgba(61,78,138,.16), transparent 58%),
    radial-gradient(700px 420px at 50% 110%, rgba(18,137,122,.08), transparent 60%),
    linear-gradient(180deg,var(--bg-top) 0%,var(--bg-bottom) 100%);
  background-attachment:fixed;
  min-height:100vh;
}
code,pre,.mono{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:12.5px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

.glass{
  background:var(--panel);
  -webkit-backdrop-filter:blur(16px) saturate(150%);
  backdrop-filter:blur(16px) saturate(150%);
  border:1px solid rgba(255,255,255,.62);
  border-radius:var(--radius);
  box-shadow:var(--shadow-sm),var(--shadow-md),var(--ring);
}
.solid{
  background:var(--panel-solid);
  border:1px solid var(--line);
  border-radius:var(--radius);
  box-shadow:var(--shadow-sm);
}
hr.rule{border:0;border-top:1px solid var(--line);margin:1rem 0}
"""

MOTION_CSS = """
@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes fade{from{opacity:0}to{opacity:1}}
.rise{animation:rise .36s cubic-bezier(.2,.7,.3,1) both}
.rise-2{animation:rise .36s cubic-bezier(.2,.7,.3,1) .06s both}
.rise-3{animation:rise .36s cubic-bezier(.2,.7,.3,1) .12s both}
.fade{animation:fade .4s ease both}
.bar-grow{transform-origin:left center;animation:grow .55s cubic-bezier(.2,.7,.3,1) both}
@media (prefers-reduced-motion: reduce){
  .rise,.rise-2,.rise-3,.fade,.bar-grow{animation:none !important}
  *{transition:none !important}
}
"""

BASE_CSS = TOKENS_CSS + SURFACE_CSS + MOTION_CSS
