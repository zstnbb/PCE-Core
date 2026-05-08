# SPDX-License-Identifier: Apache-2.0
"""Generate a tiny PNG with a visible token for C11 vision testing."""
from __future__ import annotations
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="PCE-C11-5039")
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.expanduser("~"), "Downloads", "pce-c11-5039.png"),
    )
    ap.add_argument("--w", type=int, default=600)
    ap.add_argument("--h", type=int, default=300)
    args = ap.parse_args()

    img = Image.new("RGB", (args.w, args.h), color=(248, 248, 252))
    draw = ImageDraw.Draw(img)

    # Outer rectangle so vision has structure to describe.
    draw.rectangle(
        [(8, 8), (args.w - 8, args.h - 8)],
        outline=(40, 40, 80),
        width=4,
    )

    # Pick a font that's likely to exist on Windows; fall back to default.
    font = None
    for candidate in [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\consola.ttf",
    ]:
        try:
            font = ImageFont.truetype(candidate, 56)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Caption.
    sub_font = font
    try:
        sub_font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 24)
    except OSError:
        pass

    title = args.token
    subtitle = "PCE C11 vision test image"

    # Center the text.
    title_bbox = draw.textbbox((0, 0), title, font=font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]

    draw.text(
        ((args.w - title_w) // 2, (args.h - title_h) // 2 - 20),
        title,
        fill=(20, 20, 60),
        font=font,
    )
    draw.text(
        ((args.w - sub_w) // 2, (args.h + title_h) // 2 + 8),
        subtitle,
        fill=(80, 80, 120),
        font=sub_font,
    )

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    img.save(args.out, "PNG")
    print(f"Wrote: {args.out}")
    print(f"Size: {os.path.getsize(args.out)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
