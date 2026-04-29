# Probe site adapter inventory

Tracks which AI-native sites have ported probe adapters for the
matrix runner (`tests/e2e_probe/test_matrix.py`) and which legacy
Selenium adapters they were derived from.

## Definition of "ported"

A site is **ported** when:

1. A `tests/e2e_probe/sites/<name>.py` exists that subclasses
   `BaseProbeSiteAdapter` and sets `name`, `provider`, `url`, plus
   at minimum `input_selectors` and `login_wall_selectors`.
2. The adapter is registered in `tests/e2e_probe/sites/__init__.py:ALL_SITES`.
3. T00 smoke passes against a logged-in profile (validates
   open + input reachability).

T01-T20 case porting is tracked separately per case file.

## Site inventory

| name              | provider     | URL                                         | adapter file                      | source (selenium)                | T00 verified |
|-------------------|--------------|---------------------------------------------|-----------------------------------|----------------------------------|--------------|
| chatgpt           | openai       | https://chatgpt.com/                        | `chatgpt.py`                      | `tests/e2e/sites/chatgpt.py`     | -            |
| claude            | anthropic    | https://claude.ai/new                       | `claude.py`                       | `tests/e2e/sites/claude.py`      | -            |
| gemini            | google       | https://gemini.google.com/app               | `gemini.py`                       | `tests/e2e/sites/gemini.py`      | -            |
| perplexity        | perplexity   | https://www.perplexity.ai/                  | `perplexity.py`                   | `tests/e2e/sites/perplexity.py`  | -            |
| googleaistudio    | google       | https://aistudio.google.com/prompts/new_chat| `google_ai_studio.py`             | `tests/e2e/sites/google_ai_studio.py` | -       |
| copilot           | microsoft    | https://copilot.microsoft.com/              | `copilot.py`                      | `tests/e2e/sites/copilot.py`     | -            |
| deepseek          | deepseek     | https://chat.deepseek.com/                  | `deepseek.py`                     | `tests/e2e/sites/deepseek.py`    | -            |
| kimi              | moonshot     | https://kimi.com/                           | `kimi.py`                         | `tests/e2e/sites/kimi.py`        | -            |
| grok              | xai          | https://grok.com/                           | `grok.py`                         | `tests/e2e/sites/grok.py`        | -            |
| manus             | manus        | https://manus.im/app                        | `manus.py`                        | `tests/e2e/sites/manus.py`       | -            |
| mistral           | mistral      | https://chat.mistral.ai/chat                | `mistral.py`                      | `tests/e2e/sites/mistral.py`     | -            |
| huggingface       | huggingface  | https://huggingface.co/chat/                | `huggingface.py`                  | `tests/e2e/sites/huggingface.py` | -            |
| poe               | poe          | https://poe.com/                            | `poe.py`                          | `tests/e2e/sites/poe.py`         | -            |
| zhipu             | zhipu        | https://chat.z.ai/                          | `zhipu.py`                        | `tests/e2e/sites/zhipu.py`       | -            |

14 sites registered. The "T00 verified" column is filled in by hand
after a successful matrix run on a logged-in profile.

## Sites NOT yet covered

The browser-extension content scripts cover a few more hosts that
don't have selenium adapters and therefore haven't been ported. Add
them as needed:

| host                        | extension content script                | adapter status |
|-----------------------------|------------------------------------------|----------------|
| copilot.cloud.microsoft     | `entrypoints/m365-copilot.content.ts`    | TODO           |
| notion.so                   | `entrypoints/notion.content.ts`          | TODO           |
| figma.com                   | `entrypoints/figma.content.ts`           | TODO           |
| mail.google.com             | `entrypoints/gmail.content.ts`           | TODO           |
| github.com (Copilot)        | (universal extractor)                     | TODO           |

These are lower priority because they're not chat-style sites — they
fall under "captures while the user works" rather than "agent drives
the site". Worth adding once the chat matrix is stable.

## How to add a new site

```python
# tests/e2e_probe/sites/newsite.py
from .base import BaseProbeSiteAdapter

class NewSiteAdapter(BaseProbeSiteAdapter):
    name = "newsite"
    provider = "newsite-vendor"        # match what the content script emits
    url = "https://newsite.example/"
    input_selectors = ('textarea', '[contenteditable="true"]')
    send_button_selectors = ('button[type="submit"]',)
    stop_button_selectors = ('button[aria-label*="Stop" i]',)
    response_container_selectors = ('[class*="message"]',)
    login_wall_selectors = ('a[href*="/login"]',)
```

Then append `NewSiteAdapter` to `ALL_SITES` in `__init__.py` and add
a row in this table.
