# PCE Rich Content Current-State Report

Generated: 2026-04-10 11:45:50
Repo: `F:\INVENTION\You.Inc\PCE Core`
DB: `C:\Users\ZST\.pce\data\pce.db`

## DB Counts

| raw_captures | sessions | messages |
| --- | --- | --- |
| 253 | 53 | 177 |

## Provider Rich Content Summary

| provider | sessions | messages | rich_messages | attachment_types | block_types |
| --- | --- | --- | --- | --- | --- |
| deepseek | 7 | 60 | 30 | code_block:24, citation:24, file:12 | - |
| google | 33 | 91 | 52 | citation:31, file:19, code_block:16, image_generation:4, tool_call:4, image_url:4 | - |
| manus | 1 | 2 | 2 | citation:2, code_block:1 | - |
| openai | 11 | 23 | 15 | file:10, code_block:6, citation:6, image_url:2 | - |
| unknown | 1 | 1 | 0 | - | - |

## Raw Capture Direction Summary

| provider | conversation | network_intercept | request | response |
| --- | --- | --- | --- | --- |
| deepseek | 23 | 0 | 0 | 0 |
| google | 39 | 0 | 0 | 0 |
| manus | 2 | 0 | 0 | 0 |
| openai | 12 | 136 | 0 | 0 |
| unknown | 0 | 41 | 0 | 0 |

## Live Screenshot Artifact Summary

| site | before_send | after_response | capture_failed | session_failed | send_fail | no_input |
| --- | --- | --- | --- | --- | --- | --- |
| chatgpt | 35 | 34 | 2 | 3 | 0 | 1 |
| claude | 0 | 0 | 0 | 0 | 0 | 1 |
| deepseek | 19 | 18 | 0 | 0 | 0 | 0 |
| gemini | 13 | 13 | 0 | 1 | 0 | 0 |
| googleaistudio | 22 | 19 | 1 | 0 | 0 | 1 |
| grok | 12 | 12 | 0 | 0 | 0 | 0 |
| kimi | 1 | 0 | 0 | 0 | 1 | 0 |
| manus | 13 | 13 | 1 | 0 | 0 | 0 |
| perplexity | 11 | 11 | 0 | 0 | 0 | 1 |
| poe | 13 | 13 | 0 | 1 | 0 | 0 |
| zhipu | 15 | 12 | 0 | 1 | 3 | 0 |

## Recent Rich Message Samples

| provider | role | attachment_types | text_preview |
| --- | --- | --- | --- |
| deepseek | assistant | code_block, citation | Source |
| deepseek | assistant | code_block, citation | I received the image file sample_square.png showing a blue square and outlined circle. |
| deepseek | assistant | code_block, citation | I received the image file sample_square.png showing a blue square and outlined circle.  Source  python 复制 下载 print('PCE-RICH-deepseek-image-1775792701') |
| deepseek | user | code_block, file, citation | sample_square.pngPNG 12.74KBPCE-RICH-deepseek-image-1775792701Confirm which attachment you received in one short sentence. Then include this markdown link exact |
| openai | assistant | code_block, citation | I received the image attachment PCE-RICH-chatgpt-image-1775792675.  Source  Python 运行 print('PCE-RICH-chatgpt-image-1775792675') |
| openai | user | image_url | PCE-RICH-chatgpt-image-1775792675Confirm which attachment you received in one short sentence. Then include this markdown link exactly: [Source](https://example. |
| deepseek | assistant | code_block, citation | Source |
| deepseek | assistant | code_block, citation | I received the text attachment named sample_note.txt as shown in your message. |
| deepseek | assistant | code_block, citation | I received the text attachment named sample_note.txt as shown in your message.  Source  python 复制 下载 print('PCE-RICH-deepseek-file-1775792648') |
| deepseek | assistant | file | If you need me to process, summarize, or extract anything specific from this file, just let me know! |
| deepseek | assistant | file | Thanks for sharing the file. I can confirm that I received the text attachment named sample_note.txt with the content you provided. The file appears to be a tes |
| deepseek | user | code_block, file, citation | sample_note.txtTXT 190BPCE-RICH-deepseek-file-1775792648 |

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
