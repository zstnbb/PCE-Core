# SPDX-License-Identifier: Apache-2.0
import json

from pce_core.rich_content import (
    RICH_CONTENT_SCHEMA,
    build_content_json,
    build_rich_content_envelope,
    load_attachments_from_content_json,
)


def test_build_content_json_keeps_legacy_attachments_and_adds_envelope():
    payload = build_content_json(
        [
            {
                "type": "image_url",
                "url": "https://example.com/cat.png",
                "alt": "cat",
            },
            {
                "type": "code_block",
                "language": "python",
                "code": "print('hello')",
            },
        ],
        plain_text="Describe this image\n```python\nprint('hello')\n```",
    )

    assert payload is not None
    data = json.loads(payload)
    assert data["attachments"][0]["type"] == "image_url"

    envelope = data["rich_content"]
    assert envelope["schema"] == RICH_CONTENT_SCHEMA
    assert envelope["plain_text"].startswith("Describe this image")
    assert [block["type"] for block in envelope["blocks"]] == ["image", "code"]
    assert envelope["blocks"][0]["data"]["url"] == "https://example.com/cat.png"
    assert envelope["assets"][0]["kind"] == "image"


def test_build_content_json_returns_none_without_attachments():
    assert build_content_json([], plain_text="plain") is None
    assert build_content_json(None, plain_text="plain") is None


def test_load_attachments_from_envelope_only_payload():
    envelope = build_rich_content_envelope(
        "Look at this",
        [{"type": "file", "name": "report.pdf"}],
    )
    payload = json.dumps({"rich_content": envelope}, ensure_ascii=False)

    attachments = load_attachments_from_content_json(payload)

    assert attachments == [{"type": "file", "name": "report.pdf"}]
