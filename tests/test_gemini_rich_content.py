# SPDX-License-Identifier: Apache-2.0
"""Tests for Gemini rich content capture through the full pipeline.

Verifies that Gemini conversation captures with various rich content types
(code blocks, citations, images, file uploads, tool calls) are properly
normalized and stored with content_json attachments.
"""

import json
import os
import time
import urllib.request
import urllib.error

BASE = os.environ.get("PCE_BASE_URL", "http://127.0.0.1:9800")


def _post(path, data):
    """POST JSON to the PCE API, bypassing any system proxy."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    # Bypass proxy for localhost
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    resp = opener.open(req, timeout=10)
    return json.loads(resp.read().decode())


def _get(path):
    """GET from the PCE API, bypassing any system proxy."""
    req = urllib.request.Request(f"{BASE}{path}")
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    resp = opener.open(req, timeout=10)
    return json.loads(resp.read().decode())


def _make_gemini_capture(messages, session_hint=None, model_name=None):
    """Build a Gemini conversation capture payload."""
    conv = {
        "messages": messages,
        "url": f"https://gemini.google.com/app/{session_hint or 'test123'}",
        "title": "Gemini Test Conversation",
    }
    return {
        "source_type": "browser_extension",
        "source_name": "gemini-web",
        "direction": "conversation",
        "provider": "google",
        "host": "gemini.google.com",
        "path": f"/app/{session_hint or 'test123'}",
        "model_name": model_name or "gemini-2.0-flash",
        "session_hint": session_hint or f"test-gemini-{int(time.time())}",
        "body_json": json.dumps(conv),
        "body_format": "json",
        "meta": {
            "extraction_strategy": "gemini-dom",
            "capture_mode": "message_delta",
        },
    }


def test_gemini_code_block():
    """Gemini assistant message with code block attachment should produce content_json."""
    session = f"gemini-code-{int(time.time()*1000)}"
    messages = [
        {"role": "user", "content": "Write a Python hello world"},
        {
            "role": "assistant",
            "content": 'Here\'s a simple Python program:\n\nprint("Hello, World!")\n\nThis will output "Hello, World!" to the console.',
            "attachments": [
                {
                    "type": "code_block",
                    "language": "python",
                    "code": 'print("Hello, World!")',
                }
            ],
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id"), f"Capture failed: {resp}"

    # Query sessions to find our session
    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = None
    for s in sessions:
        if s.get("session_key") == session:
            our_session = s
            break

    assert our_session, f"Session '{session}' not found in recent sessions"
    session_id = our_session["id"]

    # Query messages
    msgs = _get(f"/api/v1/sessions/{session_id}/messages")
    assert len(msgs) >= 2, f"Expected >=2 messages, got {len(msgs)}"

    # Find assistant message
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1, "No assistant message found"

    asst = assistant_msgs[0]
    assert asst["content_json"], f"Assistant message has no content_json: {asst}"
    cj = json.loads(asst["content_json"])
    atts = cj.get("attachments", [])
    code_blocks = [a for a in atts if a["type"] == "code_block"]
    assert len(code_blocks) >= 1, f"No code_block attachment found. Attachments: {atts}"
    assert code_blocks[0]["language"] == "python"
    print(f"  ✓ code_block: language={code_blocks[0]['language']}, code={code_blocks[0]['code'][:50]}")


def test_gemini_citation():
    """Gemini assistant message with citation links should produce content_json."""
    session = f"gemini-cite-{int(time.time()*1000)}"
    messages = [
        {"role": "user", "content": "What is quantum computing?"},
        {
            "role": "assistant",
            "content": "Quantum computing uses quantum mechanics principles...",
            "attachments": [
                {
                    "type": "citation",
                    "url": "https://en.wikipedia.org/wiki/Quantum_computing",
                    "title": "Quantum computing - Wikipedia",
                },
                {
                    "type": "citation",
                    "url": "https://www.ibm.com/quantum",
                    "title": "IBM Quantum",
                },
            ],
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id"), f"Capture failed: {resp}"

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session, f"Session '{session}' not found"

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1

    asst = assistant_msgs[0]
    assert asst["content_json"], f"No content_json for citations: {asst}"
    cj = json.loads(asst["content_json"])
    citations = [a for a in cj.get("attachments", []) if a["type"] == "citation"]
    assert len(citations) >= 2, f"Expected >=2 citations, got {citations}"
    print(f"  ✓ citations: {len(citations)} found")
    for c in citations:
        print(f"    - {c['title']}: {c['url']}")


def test_gemini_image():
    """Gemini message with image attachment should produce content_json."""
    session = f"gemini-img-{int(time.time()*1000)}"
    messages = [
        {
            "role": "user",
            "content": "Describe this image",
            "attachments": [
                {
                    "type": "image_url",
                    "url": "https://gemini.google.com/app/uploads/sample.jpg",
                    "alt": "uploaded photo",
                }
            ],
        },
        {
            "role": "assistant",
            "content": "The image shows a sunset over the ocean with vivid orange and purple colors.",
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id")

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) >= 1

    user = user_msgs[0]
    assert user["content_json"], f"User message has no content_json for image: {user}"
    cj = json.loads(user["content_json"])
    images = [a for a in cj.get("attachments", []) if a["type"] == "image_url"]
    assert len(images) >= 1, f"No image_url attachment found: {cj}"
    print(f"  ✓ image_url: {images[0].get('url', '')[:60]}")


def test_gemini_file_upload():
    """Gemini user message with file upload chip in content text."""
    session = f"gemini-file-{int(time.time()*1000)}"
    messages = [
        {
            "role": "user",
            "content": "report.pdf\nPDF\nPlease summarize this document",
        },
        {
            "role": "assistant",
            "content": "This report discusses quarterly results...",
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id")

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) >= 1

    user = user_msgs[0]
    assert user["content_json"], f"No content_json for file upload: {user}"
    cj = json.loads(user["content_json"])
    files = [a for a in cj.get("attachments", []) if a["type"] == "file"]
    assert len(files) >= 1, f"No file attachment detected: {cj}"
    assert files[0]["name"] == "report.pdf"
    # Content should be cleaned (no "report.pdf\nPDF" prefix)
    assert "report.pdf" not in user["content_text"]
    print(f"  ✓ file upload: {files[0]['name']}, cleaned content: {user['content_text'][:50]}")


def test_gemini_tool_call():
    """Gemini assistant message with tool call indicators."""
    session = f"gemini-tool-{int(time.time()*1000)}"
    messages = [
        {"role": "user", "content": "Search for the latest news about AI"},
        {
            "role": "assistant",
            "content": "Here are the latest AI news stories I found...",
            "attachments": [
                {"type": "tool_call", "name": "Google Search"},
                {
                    "type": "citation",
                    "url": "https://news.example.com/ai-update",
                    "title": "AI Industry Update 2026",
                },
            ],
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id")

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1

    asst = assistant_msgs[0]
    assert asst["content_json"], f"No content_json for tool call: {asst}"
    cj = json.loads(asst["content_json"])
    atts = cj.get("attachments", [])
    tools = [a for a in atts if a["type"] == "tool_call"]
    cites = [a for a in atts if a["type"] == "citation"]
    assert len(tools) >= 1, f"No tool_call found: {atts}"
    assert len(cites) >= 1, f"No citation found: {atts}"
    print(f"  ✓ tool_call: {tools[0]['name']}, citations: {len(cites)}")


def test_gemini_mixed_rich_content():
    """Gemini conversation with multiple types of rich content in one session."""
    session = f"gemini-mixed-{int(time.time()*1000)}"
    messages = [
        {
            "role": "user",
            "content": "data.csv\nCSV\nAnalyze this data and create a chart",
            "attachments": [
                {
                    "type": "file",
                    "name": "data.csv",
                    "media_type": "text/csv",
                }
            ],
        },
        {
            "role": "assistant",
            "content": "I'll analyze the data. Here's a summary and code:\n\nimport pandas as pd\ndf = pd.read_csv('data.csv')\nprint(df.describe())\n\nThe data shows strong correlation between variables.",
            "attachments": [
                {
                    "type": "code_block",
                    "language": "python",
                    "code": "import pandas as pd\ndf = pd.read_csv('data.csv')\nprint(df.describe())",
                },
                {
                    "type": "image_generation",
                    "url": "https://gemini.google.com/generated/chart.png",
                    "alt": "correlation chart",
                },
            ],
        },
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id")

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")

    # User message should have file attachment
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) >= 1
    user_cj = json.loads(user_msgs[0]["content_json"])
    user_files = [a for a in user_cj["attachments"] if a["type"] == "file"]
    assert len(user_files) >= 1, f"No file attachment in user msg: {user_cj}"

    # Assistant message should have code_block + image_generation
    asst_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(asst_msgs) >= 1
    asst_cj = json.loads(asst_msgs[0]["content_json"])
    asst_atts = asst_cj["attachments"]
    code_blocks = [a for a in asst_atts if a["type"] == "code_block"]
    images = [a for a in asst_atts if a["type"] == "image_generation"]
    assert len(code_blocks) >= 1, f"No code_block in assistant: {asst_atts}"
    assert len(images) >= 1, f"No image_generation in assistant: {asst_atts}"
    print(f"  ✓ mixed: user has {len(user_files)} files, assistant has {len(code_blocks)} code + {len(images)} images")


def test_gemini_no_attachments_no_content_json():
    """Plain text Gemini messages should NOT have content_json."""
    session = f"gemini-plain-{int(time.time()*1000)}"
    messages = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
    ]
    payload = _make_gemini_capture(messages, session_hint=session)
    resp = _post("/api/v1/captures", payload)
    assert resp.get("id")

    time.sleep(0.3)
    sessions = _get("/api/v1/sessions?last=10")
    our_session = next((s for s in sessions if s.get("session_key") == session), None)
    assert our_session

    msgs = _get(f"/api/v1/sessions/{our_session['id']}/messages")
    for m in msgs:
        assert m["content_json"] is None, f"Plain text message should not have content_json: {m}"
    print(f"  ✓ plain text: {len(msgs)} messages, none have content_json")


if __name__ == "__main__":
    tests = [
        test_gemini_code_block,
        test_gemini_citation,
        test_gemini_image,
        test_gemini_file_upload,
        test_gemini_tool_call,
        test_gemini_mixed_rich_content,
        test_gemini_no_attachments_no_content_json,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            print(f"\n▶ {t.__name__}")
            t()
            passed += 1
            print(f"  PASS")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("All Gemini rich content tests PASSED ✓")
    else:
        print(f"WARNING: {failed} test(s) FAILED")
