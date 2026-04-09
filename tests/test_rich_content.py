"""Tests for rich content extraction in normalizers.

Verifies that image_url, tool_calls, audio, citations, and other
non-text content types are properly extracted into content_json attachments.
"""

import json
import pytest

from pce_core.normalizer.openai import OpenAIChatNormalizer
from pce_core.normalizer.anthropic import AnthropicMessagesNormalizer
from pce_core.normalizer.conversation import ConversationNormalizer


# ---------------------------------------------------------------------------
# OpenAI normalizer
# ---------------------------------------------------------------------------

class TestOpenAIRichContent:
    def setup_method(self):
        self.n = OpenAIChatNormalizer()
        self.kwargs = dict(provider="openai", host="api.openai.com", path="/v1/chat/completions")

    def _normalize(self, req_data, resp_data):
        return self.n.normalize(
            json.dumps(req_data),
            json.dumps(resp_data),
            **self.kwargs,
        )

    def test_image_url_in_user_message(self):
        result = self._normalize(
            {"model": "gpt-4o", "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg", "detail": "high"}},
                ]},
            ]},
            {"model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "A cat"}}]},
        )
        assert result is not None
        user_msg = result.messages[0]
        assert user_msg.role == "user"
        assert "[Image]" in user_msg.content_text
        att = json.loads(user_msg.content_json)["attachments"]
        assert len(att) == 1
        assert att[0]["type"] == "image_url"
        assert att[0]["url"] == "https://example.com/img.jpg"
        assert att[0]["detail"] == "high"

    def test_data_uri_truncated(self):
        long_data = "data:image/png;base64," + "A" * 1000
        result = self._normalize(
            {"model": "gpt-4o", "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": long_data}},
                ]},
            ]},
            {"model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )
        att = json.loads(result.messages[0].content_json)["attachments"]
        assert "truncated" in att[0]["url"]
        assert len(att[0]["url"]) < 300

    def test_tool_calls_in_assistant(self):
        result = self._normalize(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "Search for cats"}]},
            {"model": "gpt-4o", "choices": [{"message": {
                "role": "assistant",
                "content": "Let me search for that.",
                "tool_calls": [
                    {"id": "tc_1", "function": {"name": "web_search", "arguments": '{"q": "cats"}'}},
                ],
            }}]},
        )
        asst = result.messages[1]
        assert "[Tool call: web_search]" in asst.content_text
        att = json.loads(asst.content_json)["attachments"]
        assert att[0]["type"] == "tool_call"
        assert att[0]["name"] == "web_search"
        assert att[0]["id"] == "tc_1"

    def test_tool_result_message(self):
        result = self._normalize(
            {"model": "gpt-4o", "messages": [
                {"role": "user", "content": "What time is it?"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "tc_1", "function": {"name": "get_time", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "tc_1", "name": "get_time", "content": "14:30 UTC"},
            ]},
            {"model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "It's 14:30 UTC"}}]},
        )
        # Find the tool message
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        att = json.loads(tool_msgs[0].content_json)["attachments"]
        assert att[0]["type"] == "tool_result"
        assert att[0]["tool_call_id"] == "tc_1"

    def test_audio_input(self):
        result = self._normalize(
            {"model": "gpt-4o-audio", "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "Listen to this"},
                    {"type": "input_audio", "input_audio": {"format": "mp3"}},
                ]},
            ]},
            {"model": "gpt-4o-audio", "choices": [{"message": {"role": "assistant", "content": "I heard music"}}]},
        )
        att = json.loads(result.messages[0].content_json)["attachments"]
        assert att[0]["type"] == "audio"
        assert att[0]["format"] == "mp3"

    def test_citations_in_annotations(self):
        result = self._normalize(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "Search cats"}]},
            {"model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": [
                {"type": "text", "text": "Cats are great [1].", "annotations": [
                    {"type": "url_citation", "url_citation": {"url": "https://cats.org", "title": "About Cats"}, "text": "[1]"},
                ]},
            ]}}]},
        )
        asst = result.messages[1]
        att = json.loads(asst.content_json)["attachments"]
        citations = [a for a in att if a["type"] == "citation"]
        assert len(citations) == 1
        assert citations[0]["url"] == "https://cats.org"

    def test_simple_text_no_attachments(self):
        result = self._normalize(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]},
            {"model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "Hi!"}}]},
        )
        for m in result.messages:
            assert m.content_json is None  # No attachments for plain text


# ---------------------------------------------------------------------------
# Anthropic normalizer
# ---------------------------------------------------------------------------

class TestAnthropicRichContent:
    def setup_method(self):
        self.n = AnthropicMessagesNormalizer()
        self.kwargs = dict(provider="anthropic", host="api.anthropic.com", path="/v1/messages")

    def _normalize(self, req_data, resp_data):
        return self.n.normalize(
            json.dumps(req_data),
            json.dumps(resp_data),
            **self.kwargs,
        )

    def test_tool_use_in_response(self):
        result = self._normalize(
            {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "Search for cats"}]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me search."},
                {"type": "tool_use", "id": "tu_1", "name": "web_search", "input": {"query": "cats"}},
            ], "model": "claude-sonnet-4-20250514", "usage": {"output_tokens": 50}},
        )
        asst = result.messages[1]
        assert "[Tool call: web_search]" in asst.content_text
        att = json.loads(asst.content_json)["attachments"]
        tool_calls = [a for a in att if a["type"] == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "web_search"

    def test_tool_result_in_request(self):
        result = self._normalize(
            {"model": "claude-sonnet-4-20250514", "messages": [
                {"role": "user", "content": "Search cats"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "cats"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "Found 10 results about cats"},
                ]},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "Here are the results."}],
             "model": "claude-sonnet-4-20250514", "usage": {"output_tokens": 20}},
        )
        # Find messages with tool_result attachments
        tool_result_msgs = [m for m in result.messages if m.content_json and "tool_result" in m.content_json]
        assert len(tool_result_msgs) >= 1

    def test_image_block(self):
        result = self._normalize(
            {"model": "claude-sonnet-4-20250514", "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image", "source": {"type": "url", "url": "https://example.com/img.jpg", "media_type": "image/jpeg"}},
                ]},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "A cat"}],
             "model": "claude-sonnet-4-20250514", "usage": {"output_tokens": 5}},
        )
        user_msg = result.messages[0]
        assert "[Image]" in user_msg.content_text
        att = json.loads(user_msg.content_json)["attachments"]
        imgs = [a for a in att if a["type"] == "image_url"]
        assert len(imgs) == 1
        assert imgs[0]["url"] == "https://example.com/img.jpg"

    def test_thinking_block(self):
        result = self._normalize(
            {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "Think hard"}]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Let me consider..."},
                {"type": "text", "text": "Here is my answer."},
            ], "model": "claude-sonnet-4-20250514", "usage": {"output_tokens": 30}},
        )
        asst = result.messages[1]
        assert "<thinking>" in asst.content_text
        assert "Let me consider" in asst.content_text
        assert "Here is my answer" in asst.content_text

    def test_document_block(self):
        result = self._normalize(
            {"model": "claude-sonnet-4-20250514", "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "Summarize this PDF"},
                    {"type": "document", "source": {"type": "url", "media_type": "application/pdf"}, "title": "report.pdf"},
                ]},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "The document discusses..."}],
             "model": "claude-sonnet-4-20250514", "usage": {"output_tokens": 10}},
        )
        user_msg = result.messages[0]
        assert "[Document: report.pdf]" in user_msg.content_text
        att = json.loads(user_msg.content_json)["attachments"]
        files = [a for a in att if a["type"] == "file"]
        assert len(files) == 1
        assert files[0]["title"] == "report.pdf"


# ---------------------------------------------------------------------------
# Conversation normalizer (DOM extraction pass-through)
# ---------------------------------------------------------------------------

class TestConversationRichContent:
    def setup_method(self):
        self.n = ConversationNormalizer()
        self.kwargs = dict(provider="openai", host="chatgpt.com", path="/c/abc123")

    def test_attachments_pass_through(self):
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "Here is an image", "attachments": [
                    {"type": "image_url", "url": "https://example.com/img.jpg", "alt": "a cat"},
                ]},
                {"role": "assistant", "content": "I see a cat", "attachments": [
                    {"type": "code_block", "language": "python", "code": "print('hello')"},
                    {"type": "citation", "url": "https://cats.org", "title": "Cats"},
                ]},
            ],
            "conversation_id": "conv-123",
        })
        result = self.n.normalize(body, body, **self.kwargs)
        assert result is not None

        user_msg = result.messages[0]
        assert user_msg.content_json is not None
        att = json.loads(user_msg.content_json)["attachments"]
        assert len(att) == 1
        assert att[0]["type"] == "image_url"

        asst_msg = result.messages[1]
        att2 = json.loads(asst_msg.content_json)["attachments"]
        assert len(att2) == 2
        types = {a["type"] for a in att2}
        assert "code_block" in types
        assert "citation" in types

    def test_no_attachments_no_content_json(self):
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        })
        result = self.n.normalize(body, body, **self.kwargs)
        for m in result.messages:
            assert m.content_json is None


    def test_file_upload_detected_from_content(self):
        """User message starting with filename.ext should produce a file attachment."""
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "PROJECT(1).md\n\u6587\u4ef6\n\u8bf7\u4f60\u5e2e\u6211\u5206\u6790\u4e00\u4e0b\u8fd9\u4e2a\u9879\u76ee\u7684\u53ef\u4fe1\u6027"},
                {"role": "assistant", "content": "This is a project analysis."},
            ],
        })
        result = self.n.normalize(body, body, **self.kwargs)
        user_msg = result.messages[0]
        assert user_msg.content_json is not None
        att = json.loads(user_msg.content_json)["attachments"]
        assert len(att) == 1
        assert att[0]["type"] == "file"
        assert att[0]["name"] == "PROJECT(1).md"
        # Content should be cleaned: no filename/type label lines
        assert "PROJECT(1).md" not in user_msg.content_text
        assert "\u5206\u6790" in user_msg.content_text

    def test_file_upload_pdf(self):
        """PDF upload pattern: dd2813.pdf\nPDF\nPlease analyze."""
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "dd2813.pdf\nPDF\n\u6e05\u4f60\u5206\u6790\u4e00\u4e0b\u8fd9\u4e2a\u6587\u4ef6"},
            ],
        })
        result = self.n.normalize(body, body, **self.kwargs)
        user_msg = result.messages[0]
        att = json.loads(user_msg.content_json)["attachments"]
        assert att[0]["type"] == "file"
        assert att[0]["name"] == "dd2813.pdf"
        assert "dd2813.pdf" not in user_msg.content_text

    def test_no_false_positive_file_detection(self):
        """Normal user message should NOT trigger file detection."""
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "Hello, how are you?"},
                {"role": "user", "content": "Tell me about python.js frameworks"},
            ],
        })
        result = self.n.normalize(body, body, **self.kwargs)
        for m in result.messages:
            assert m.content_json is None

    def test_file_upload_plus_dom_attachments(self):
        """File upload in content + DOM attachment should merge correctly."""
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "report.pdf\nPDF\nAnalyze this",
                 "attachments": [{"type": "image_url", "url": "https://example.com/screenshot.png"}]},
            ],
        })
        result = self.n.normalize(body, body, **self.kwargs)
        user_msg = result.messages[0]
        att = json.loads(user_msg.content_json)["attachments"]
        assert len(att) == 2
        types = {a["type"] for a in att}
        assert "image_url" in types
        assert "file" in types


# ---------------------------------------------------------------------------
# _detect_file_upload (content-based file detection)
# ---------------------------------------------------------------------------

class TestDetectFileUpload:
    """Tests for conversation.py _detect_file_upload function."""

    def test_standard_pattern(self):
        from pce_core.normalizer.conversation import _detect_file_upload
        content, atts = _detect_file_upload("report.pdf\nPDF\nPlease analyze")
        assert len(atts) == 1
        assert atts[0]["name"] == "report.pdf"
        assert "report.pdf" not in content
        assert "PDF" not in content
        assert "Please analyze" in content

    def test_md_file(self):
        from pce_core.normalizer.conversation import _detect_file_upload
        content, atts = _detect_file_upload("PROJECT(1).md\n\u6587\u4ef6\n\u8bf7\u5206\u6790")
        assert atts[0]["name"] == "PROJECT(1).md"
        assert "\u8bf7\u5206\u6790" in content

    def test_no_match_plain_text(self):
        from pce_core.normalizer.conversation import _detect_file_upload
        content, atts = _detect_file_upload("Hello, how are you?")
        assert atts == []
        assert content == "Hello, how are you?"

    def test_no_match_single_line(self):
        from pce_core.normalizer.conversation import _detect_file_upload
        content, atts = _detect_file_upload("test.py")
        assert atts == []  # single line, no actual message

    def test_assistant_not_detected(self):
        """File detection only runs on user messages; function itself is content-agnostic."""
        from pce_core.normalizer.conversation import _detect_file_upload
        # Even if content matches, assistant messages should not trigger this
        # (controlled by the caller, but function still works)
        content, atts = _detect_file_upload("output.json\nJSON\nHere is the result")
        assert len(atts) == 1  # function detects it; caller controls when to call


# ---------------------------------------------------------------------------
# _clean_content (raw JSON cleaning)
# ---------------------------------------------------------------------------

class TestCleanContent:
    """Tests for conversation.py _clean_content function."""

    def test_plain_text_unchanged(self):
        from pce_core.normalizer.conversation import _clean_content
        text, atts = _clean_content("Hello world")
        assert text == "Hello world"
        assert atts == []

    def test_chatgpt_text_parts(self):
        from pce_core.normalizer.conversation import _clean_content
        raw = "{'content_type': 'text', 'parts': ['Hello from ChatGPT']}"
        text, atts = _clean_content(raw)
        assert text == "Hello from ChatGPT"
        assert atts == []

    def test_chatgpt_multimodal_with_image(self):
        from pce_core.normalizer.conversation import _clean_content
        raw = ("{'content_type': 'multimodal_text', 'parts': ["
               "{'content_type': 'image_asset_pointer', 'asset_pointer': 'file-abc123', 'size_bytes': 54321}, "
               "'Describe this image']}")
        text, atts = _clean_content(raw)
        assert "[Image]" in text
        assert "Describe this image" in text
        assert len(atts) == 1
        assert atts[0]["type"] == "image_url"
        assert atts[0]["url"] == "file-abc123"

    def test_json_format_text_parts(self):
        from pce_core.normalizer.conversation import _clean_content
        raw = '{"content_type": "text", "parts": ["JSON format test"]}'
        text, atts = _clean_content(raw)
        assert text == "JSON format test"

    def test_generic_text_key(self):
        from pce_core.normalizer.conversation import _clean_content
        raw = '{"text": "Some extracted text", "other": "ignored"}'
        text, atts = _clean_content(raw)
        assert text == "Some extracted text"

    def test_non_dict_json_unchanged(self):
        from pce_core.normalizer.conversation import _clean_content
        raw = '["just", "a", "list"]'
        text, atts = _clean_content(raw)
        assert text == raw


# ---------------------------------------------------------------------------
# _message_hash dedup normalization
# ---------------------------------------------------------------------------

class TestMessageHashDedup:
    """Tests for pipeline.py _message_hash normalization."""

    def test_same_content_same_hash(self):
        from pce_core.normalizer.pipeline import _message_hash
        h1 = _message_hash("user", "Hello world")
        h2 = _message_hash("user", "Hello world")
        assert h1 == h2

    def test_raw_json_matches_clean_text(self):
        from pce_core.normalizer.pipeline import _message_hash
        clean = _message_hash("user", "Hello from ChatGPT")
        raw = _message_hash("user", "{'content_type': 'text', 'parts': ['Hello from ChatGPT']}")
        assert clean == raw

    def test_different_roles_different_hash(self):
        from pce_core.normalizer.pipeline import _message_hash
        h1 = _message_hash("user", "Hello")
        h2 = _message_hash("assistant", "Hello")
        assert h1 != h2

    def test_file_upload_matches_clean_text(self):
        from pce_core.normalizer.pipeline import _message_hash
        clean = _message_hash("user", "请你帮我分析一下这个项目的可信性")
        raw = _message_hash("user", "PROJECT(1).md\n文件\n请你帮我分析一下这个项目的可信性")
        assert clean == raw

    def test_file_upload_pdf_matches(self):
        from pce_core.normalizer.pipeline import _message_hash
        clean = _message_hash("user", "清你分析一下这个文件")
        raw = _message_hash("user", "dd2813.pdf\nPDF\n清你分析一下这个文件")
        assert clean == raw

    def test_attachment_marker_prefix_matches_clean_text(self):
        from pce_core.normalizer.pipeline import _message_hash
        clean = _message_hash("user", "Describe the uploaded image")
        raw = _message_hash("user", "[Image]\nDescribe the uploaded image")
        assert clean == raw


class TestPipelineAttachmentMerge:
    def test_merge_content_json_prefers_renderable_image_url(self):
        from pce_core.normalizer.pipeline import _merge_content_json

        existing = json.dumps({
            "attachments": [
                {
                    "type": "image_url",
                    "url": "sediment://file_abc123",
                    "file_id": "file_abc123",
                    "size": 246,
                }
            ]
        })
        incoming = json.dumps({
            "attachments": [
                {
                    "type": "image_url",
                    "url": "https://chatgpt.com/backend-api/estuary/content?id=file_abc123&v=0",
                    "alt": "uploaded image",
                },
                {
                    "type": "file",
                    "name": "sample_note.txt",
                },
            ]
        })

        merged = json.loads(_merge_content_json(existing, incoming))
        attachments = merged["attachments"]
        assert len(attachments) == 2
        image = next(att for att in attachments if att["type"] == "image_url")
        assert image["file_id"] == "file_abc123"
        assert image["url"].startswith("https://chatgpt.com/backend-api/estuary/content")
        file_att = next(att for att in attachments if att["type"] == "file")
        assert file_att["name"] == "sample_note.txt"

    def test_choose_better_content_text_prefers_cleaner_dom_text(self):
        from pce_core.normalizer.pipeline import _choose_better_content_text

        better = _choose_better_content_text(
            "[Image]\nDescribe the uploaded image",
            "Describe the uploaded image",
        )
        assert better == "Describe the uploaded image"
