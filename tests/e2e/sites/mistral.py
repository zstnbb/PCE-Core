# SPDX-License-Identifier: Apache-2.0
"""Mistral Le Chat (chat.mistral.ai) site adapter.

Covers the Phase 3b batch 5 generic TypeScript extractor
(`entrypoints/generic.content.ts`), which handles Mistral via the
9-selector heuristic ladder. The provider label from the extension's
`HOST_PROVIDER_MAP` is ``"mistral"``.
"""

from .base import BaseSiteAdapter


class MistralAdapter(BaseSiteAdapter):
    name = "mistral"
    provider = "mistral"
    url = "https://chat.mistral.ai/chat"

    input_selector = (
        'textarea[placeholder*="message" i], '
        'textarea[placeholder*="ask" i], '
        'div[role="textbox"][contenteditable="true"], '
        'textarea'
    )
    send_button_selector = (
        'button[type="submit"], '
        'button[aria-label*="send" i]'
    )
    response_container_selector = (
        '[class*="assistant"] .prose, '
        '[class*="message"] .prose, '
        '[class*="markdown"], '
        '.prose'
    )

    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"][accept*="image"], input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 5
    response_timeout_s = 60
