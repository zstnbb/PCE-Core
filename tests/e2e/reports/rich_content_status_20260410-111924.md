# PCE Rich Content Current-State Report

Generated: 2026-04-10 11:19:24
Repo: `F:\INVENTION\You.Inc\PCE Core`
DB: `C:\Users\ZST\.pce\data\pce.db`

## DB Counts

| raw_captures | sessions | messages |
| --- | --- | --- |
| 168 | 42 | 106 |

## Provider Rich Content Summary

| provider | sessions | messages | rich_messages | attachment_types | block_types |
| --- | --- | --- | --- | --- | --- |
| google | 33 | 88 | 49 | citation:29, file:17, code_block:16, image_generation:4, tool_call:4, image_url:4 | - |
| manus | 1 | 2 | 2 | citation:2, code_block:1 | - |
| openai | 7 | 15 | 7 | file:6, code_block:2, citation:2 | - |
| unknown | 1 | 1 | 0 | - | - |

## Raw Capture Direction Summary

| provider | conversation | network_intercept | request | response |
| --- | --- | --- | --- | --- |
| google | 37 | 0 | 0 | 0 |
| manus | 2 | 0 | 0 | 0 |
| openai | 8 | 84 | 0 | 0 |
| unknown | 0 | 37 | 0 | 0 |

## Live Screenshot Artifact Summary

| site | before_send | after_response | capture_failed | session_failed | send_fail | no_input |
| --- | --- | --- | --- | --- | --- | --- |
| chatgpt | 31 | 30 | 2 | 3 | 0 | 1 |
| claude | 0 | 0 | 0 | 0 | 0 | 1 |
| deepseek | 9 | 9 | 0 | 0 | 0 | 0 |
| gemini | 13 | 13 | 0 | 1 | 0 | 0 |
| googleaistudio | 20 | 17 | 1 | 0 | 0 | 1 |
| grok | 12 | 12 | 0 | 0 | 0 | 0 |
| kimi | 1 | 0 | 0 | 0 | 1 | 0 |
| manus | 13 | 13 | 1 | 0 | 0 | 0 |
| perplexity | 11 | 11 | 0 | 0 | 0 | 1 |
| poe | 13 | 13 | 0 | 1 | 0 | 0 |
| zhipu | 15 | 12 | 0 | 1 | 3 | 0 |

## Recent Rich Message Samples

| provider | role | attachment_types | text_preview |
| --- | --- | --- | --- |
| google | assistant | code_block, image_generation | I'll analyze the data. Here's a summary and code:  import pandas as pd df = pd.read_csv('data.csv') print(df.describe())  The data shows strong correlation betw |
| google | user | file, file | Analyze this data and create a chart |
| google | assistant | tool_call, citation | Here are the latest AI news stories I found... |
| google | user | file | Please summarize this document |
| google | user | image_url | Describe this image |
| google | assistant | citation, citation | Quantum computing uses quantum mechanics principles... |
| google | assistant | code_block | Here's a simple Python program:  print("Hello, World!")  This will output "Hello, World!" to the console. |
| google | assistant | code_block, image_generation | I'll analyze the data. Here's a summary and code:  import pandas as pd df = pd.read_csv('data.csv') print(df.describe())  The data shows strong correlation betw |
| google | user | file, file | Analyze this data and create a chart |
| google | assistant | tool_call, citation | Here are the latest AI news stories I found... |
| google | user | file | Please summarize this document |
| google | user | image_url | Describe this image |

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
