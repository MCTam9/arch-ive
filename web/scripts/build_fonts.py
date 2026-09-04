#!/usr/bin/env python3
"""Convert the display face from OTF to WOFF2 for the web build.

    python web/scripts/build_fonts.py

Both formats stay gitignored -- this is a commercial licence and the binaries
never enter the public repo. What ships to Vercel is the WOFF2 alone
(web/.vercelignore excludes the .otf), because a public /fonts/*.otf is an
installable desktop font served to anyone with the URL, which is not what a
webfont licence covers. It is also ~3x the bytes of the WOFF2.

Requires fonttools and brotli:  pip install fonttools brotli
"""
from __future__ import annotations

import sys
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "public" / "fonts"


def main() -> int:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        print("fonttools is not installed: pip install fonttools brotli", file=sys.stderr)
        return 1

    sources = sorted(FONT_DIR.glob("*.otf")) + sorted(FONT_DIR.glob("*.ttf"))
    if not sources:
        print(f"no .otf/.ttf under {FONT_DIR} -- nothing to convert")
        return 0

    for src in sources:
        font = TTFont(src)
        font.flavor = "woff2"
        out = src.with_suffix(".woff2")
        font.save(out)
        print(f"{src.name} {src.stat().st_size:>7} B -> {out.name} {out.stat().st_size:>7} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
