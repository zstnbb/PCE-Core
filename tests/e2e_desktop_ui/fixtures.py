# SPDX-License-Identifier: Apache-2.0
"""Test-fixture file generators for D17 (image) / D18 (PDF) D-cases.

Each helper is idempotent: returns the path to an existing fixture if
already generated, otherwise creates one in the user's Downloads
directory (the same default that ``scripts/_make_test_image.py`` and
``scripts/_make_test_pdf.py`` use, so the same fixtures can be reused
by manual sanity runs).

The ``_make_test_*.py`` scripts at the repo root are the upstream
sources of truth; this module just wraps them for the desktop-UI
case scripts so the cases don't have to shell out.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads"


def ensure_test_image(token: str = "PCE-D17-5039") -> Path:
    """Return path to a small PNG with a visible token. Generates one
    if not present.

    The token is rendered into the PNG so a vision-capable assistant
    can be asked "what does the image say" and we can verify the
    OCR'd token round-trips into the assistant's reply.
    """
    out = DOWNLOADS / f"pce-d17-{token.lower().replace(' ', '-')}.png"
    if out.exists() and out.stat().st_size > 0:
        return out
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Pillow not installed; install with `pip install pillow` "
            "or run `python scripts/_make_test_image.py` once before "
            "running this case."
        ) from exc

    w, h = 600, 300
    img = Image.new("RGB", (w, h), color=(248, 248, 252))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(8, 8), (w - 8, h - 8)], outline=(40, 40, 80), width=4)
    font = None
    for cand in (
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ):
        try:
            font = ImageFont.truetype(cand, 56)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), token, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2, (h - th) // 2), token, fill=(20, 20, 60), font=font)
    img.save(out, "PNG")
    return out


def ensure_test_pdf(token: str = "PCE-D18-4471") -> Path:
    """Return path to a tiny single-page PDF containing ``token``.

    Generates a minimal valid PDF (no Pillow / reportlab dependency) on
    first call. Reused on subsequent calls.
    """
    out = DOWNLOADS / f"pce-d18-{token.lower().replace(' ', '-')}.pdf"
    if out.exists() and out.stat().st_size > 0:
        return out
    DOWNLOADS.mkdir(parents=True, exist_ok=True)

    body_text = f"{token} test PDF document"
    content_stream = (
        f"BT /F1 18 Tf 72 720 Td ({body_text}) Tj ET".encode("ascii")
    )
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        f"<</Length {len(content_stream)}>>stream\n".encode()
        + content_stream
        + b"\nendstream",
    ]

    out_buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out_buf))
        out_buf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out_buf)
    out_buf += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        out_buf += f"{off:010d} 00000 n \n".encode()
    out_buf += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n"
    out_buf += f"{xref_offset}\n%%EOF\n".encode()

    out.write_bytes(bytes(out_buf))
    return out


# Suppress unused-warning for the struct import (kept for future
# fixtures that may need binary packing)
_ = struct
