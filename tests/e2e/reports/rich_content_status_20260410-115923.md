# PCE Rich Content Current-State Report

Generated: 2026-04-10 11:59:23
Repo: `F:\INVENTION\You.Inc\PCE Core`
DB: `C:\Users\ZST\.pce\data\pce.db`

## DB Counts

| raw_captures | sessions | messages |
| --- | --- | --- |
| 300 | 71 | 221 |

## Storage Contract Summary

| content_json | rich_content_v1 | legacy_attachments_only |
| --- | --- | --- |
| 125 | 125 | 0 |

## Provider Rich Content Summary

| provider | sessions | messages | rich_messages | rich_v1 | legacy_only | attachment_types | block_types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | 9 | 72 | 38 | 38 | 0 | code_block:32, citation:32, file:14 | code:32, citation:32, file:14 |
| google | 47 | 119 | 66 | 66 | 0 | citation:37, file:25, code_block:20, image_generation:6, tool_call:6, image_url:6 | citation:37, file:25, code:20, image:12, tool_call:6 |
| manus | 1 | 2 | 2 | 2 | 0 | citation:2, code_block:1 | citation:2, code:1 |
| openai | 13 | 27 | 19 | 19 | 0 | file:12, code_block:8, citation:8, image_url:3 | file:12, code:8, citation:8, image:3 |
| unknown | 1 | 1 | 0 | 0 | 0 | - | - |

## Raw Capture Direction Summary

| provider | conversation | network_intercept | request | response |
| --- | --- | --- | --- | --- |
| deepseek | 27 | 0 | 0 | 0 |
| google | 53 | 0 | 0 | 0 |
| manus | 2 | 0 | 0 | 0 |
| openai | 14 | 161 | 0 | 0 |
| unknown | 0 | 43 | 0 | 0 |

## Live Screenshot Artifact Summary

| site | before_send | after_response | capture_failed | session_failed | send_fail | no_input |
| --- | --- | --- | --- | --- | --- | --- |
| chatgpt | 37 | 36 | 2 | 3 | 0 | 1 |
| claude | 0 | 0 | 0 | 0 | 0 | 1 |
| deepseek | 21 | 20 | 0 | 0 | 0 | 0 |
| gemini | 13 | 13 | 0 | 1 | 0 | 0 |
| googleaistudio | 22 | 19 | 1 | 0 | 0 | 1 |
| grok | 12 | 12 | 0 | 0 | 0 | 0 |
| kimi | 1 | 0 | 0 | 0 | 1 | 0 |
| manus | 13 | 13 | 1 | 0 | 0 | 0 |
| perplexity | 11 | 11 | 0 | 0 | 0 | 1 |
| poe | 13 | 13 | 0 | 1 | 0 | 0 |
| zhipu | 15 | 12 | 0 | 1 | 3 | 0 |

## Recent Rich Message Samples

| provider | role | attachment_types | rich_schema | block_types | text_preview |
| --- | --- | --- | --- | --- | --- |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | Source |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received an image file named sample_square.png containing a blue square and an outlined circle. |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received an image file named sample_square.png containing a blue square and an outlined circle.  Source  python 复制 下载 print('PCE-RICH-deepseek-image-177579349 |
| deepseek | user | code_block, file, citation | pce.rich_content.v1 | code, file, citation | sample_square.pngPNG 12.74KBPCE-RICH-deepseek-image-1775793498Confirm which attachment you received in one short sentence. Then include this markdown link exact |
| openai | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received the image attachment PCE-RICH-chatgpt-image-1775793467.  Source  Python 运行 print("PCE-RICH-chatgpt-image-1775793467") |
| openai | user | image_url | pce.rich_content.v1 | image | PCE-RICH-chatgpt-image-1775793467Confirm which attachment you received in one short sentence. Then include this markdown link exactly: [Source](https://example. |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | Source |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received the text attachment named sample_note.txt. |
| deepseek | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received the text attachment named sample_note.txt.  Source  python 复制 下载 print('PCE-RICH-deepseek-file-1775793441') |
| deepseek | user | code_block, file, citation | pce.rich_content.v1 | code, file, citation | sample_note.txtTXT 190BPCE-RICH-deepseek-file-1775793441Confirm which attachment you received in one short sentence. Then include this markdown link exactly: [S |
| openai | assistant | code_block, citation | pce.rich_content.v1 | code, citation | I received a text attachment named sample_note.txt.   sample_note  Source  Python 运行 print('PCE-RICH-chatgpt-file-1775793413') |
| openai | user | file, file | pce.rich_content.v1 | file, file | PCE-RICH-chatgpt-file-1775793413Confirm which attachment you received in one short sentence. Then include this markdown link exactly: [Source](https://example.c |

## Probe Artifacts

- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\chatgpt_attachment_probe.json` (13508 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\chatgpt_dom_diag.json` (14592 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\chatgpt_user_dom_diag.json` (10295 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\googleaistudio_turn_probe.json` (22809 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\login_status_results.json` (14201 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\site_buttons_probe.json` (105000 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\site_dom_probe.json` (52356 bytes)
- `F:\INVENTION\You.Inc\PCE Core\tests\e2e\site_special_probe.json` (6514 bytes)

## Interpretation Guardrail

This report is evidence inventory only. A site is stable only after capture, storage, Dashboard replay, and repeated live-run gates pass.
