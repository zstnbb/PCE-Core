"""One-shot helper to generate a minimal valid PDF for C10 manual testing."""
from pathlib import Path
import os


def main() -> None:
    content_stream = b"BT /F1 18 Tf 72 720 Td (PCE-C10-4471 test PDF document) Tj ET"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        f"<</Length {len(content_stream)}>>stream\n".encode() + content_stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n"
    out += f"{xref_offset}\n%%EOF\n".encode()

    target = Path(os.environ["USERPROFILE"]) / "Downloads" / "pce-c10-4471.pdf"
    target.write_bytes(bytes(out))
    print(f"OK wrote {len(out)} bytes -> {target}")


if __name__ == "__main__":
    main()
