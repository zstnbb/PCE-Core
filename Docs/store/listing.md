# Chrome Web Store — Listing Copy (v1.0.0)

Copy-paste targets for the Chrome Web Store Developer Dashboard. Keep
this file in sync with whatever actually lives in the store — it is the
single source of truth when we need to re-submit after a policy change.

---

## EN — primary language

### Title (max 45 chars)

```
PCE — Local-First AI Conversation Capture
```

(41 chars; leaves headroom. Keywords "local-first", "AI", "conversation",
"capture" all match common Chrome Web Store searches.)

### Summary (max 132 chars)

```
Keep a searchable history of every ChatGPT, Claude, Copilot and Gemini conversation — stored 100% on your own machine.
```

(120 chars. Leads with the concrete benefit, names the big-four AI
products so the listing matches direct searches, ends with the trust
signal that separates PCE from cloud-based competitors.)

### Category

```
Productivity
```

### Language

Primary: `English (United States)`. Add `Chinese (Simplified)` as a
secondary language once the Chinese description below is ready.

### Detailed description (max 16 000 chars)

```
PCE is a local-first capture layer for your AI conversations.

Every message you exchange with ChatGPT, Claude, Copilot, Gemini, and a dozen other AI tools flows into a SQLite database on your own computer — never to a cloud we control, never into a third-party analytics pipeline. When you want to find that brilliant prompt you used three weeks ago, or compare how Claude and ChatGPT answered the same question, the history is right there, on your machine, searchable in full-text.

▶ WHY PCE

You probably have more valuable thinking trapped inside AI chat tabs than inside your own note-taking app. But:

 • ChatGPT history lives on OpenAI's servers. Claude history lives on Anthropic's. Copilot history lives in Microsoft's.
 • Each vendor gives you their search, their export, their retention policy — never a unified view.
 • Enterprise privacy teams frequently block these exports entirely.

PCE fixes this without asking you to change how you use AI. Install the extension, run the PCE Core desktop app once, and every future conversation on a covered site gets archived locally, automatically.

▶ WHAT THIS EXTENSION DOES

 • Detects when you visit a supported AI site.
 • Reads the conversation DOM in real time as messages arrive.
 • Forwards captured messages to a PCE Core server running on your own computer (http://127.0.0.1:9800).
 • Queues captures locally in IndexedDB if PCE Core is offline, and forwards them later.
 • Adds a right-click "Save selection as snippet" command so you can keep specific quotes for later.

The extension by itself is only half the product. You also need to run the free PCE Core desktop app, which stores and indexes your captures. See the installation guide: https://github.com/zstnbb/PCE-Core#install

▶ SUPPORTED SITES

Dedicated AI tools:

 • ChatGPT (chatgpt.com)
 • Claude (claude.ai)
 • Google Gemini (gemini.google.com)
 • Google AI Studio (aistudio.google.com)
 • Microsoft Copilot (copilot.microsoft.com)
 • DeepSeek (chat.deepseek.com)
 • Perplexity (perplexity.ai)
 • Poe (poe.com)
 • HuggingChat (huggingface.co/chat)
 • Grok (grok.com)
 • Mistral (chat.mistral.ai)
 • Kimi (kimi.com / kimi.moonshot.cn)
 • Zhipu (chat.z.ai)
 • Manus (manus.im)

AI features inside productivity tools:

 • Microsoft 365 Copilot (m365.cloud.microsoft)
 • Notion AI (notion.so)
 • Figma AI (figma.com)
 • Gmail "Help me write" (mail.google.com)

More sites added based on community feedback — file an issue on GitHub.

▶ YOUR DATA STAYS YOURS

 • No cloud backend. PCE is open source (Apache-2.0) — you can read the code that runs inside the extension.
 • No telemetry. The extension has exactly one network destination: your own computer.
 • No third-party SDKs. No ads, no analytics, no crash reports to us.
 • No account required. No signup, no login, no email address.

Read the full privacy policy: https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md

▶ WHO IS THIS FOR

 • Developers who talk to Copilot, Cursor-like assistants, and ChatGPT all day and want a single searchable archive.
 • Researchers who need provenance for AI-generated text they cite or publish.
 • Privacy-conscious knowledge workers whose enterprise policy forbids cloud-hosted AI history exports.
 • Anyone who has ever thought "I wish I could find that prompt I used last month".

▶ WHAT THIS IS NOT

 • Not a chatbot. PCE does not answer your questions — it just records the ones you already asked.
 • Not a browser history logger. PCE only runs on the AI sites listed above.
 • Not a SaaS product. There is no login, no subscription, no free trial.

▶ OPEN SOURCE

Source code, issue tracker, and discussion:
https://github.com/zstnbb/PCE-Core

Licensed under Apache-2.0. Pull requests welcome.
```

---

## ZH — secondary language (optional)

### 标题

```
PCE — 本地优先的 AI 对话捕获
```

### 摘要(max 132 chars)

```
把 ChatGPT、Claude、Copilot、Gemini 的每一次对话留下来 — 全部存在你自己电脑上,可全文搜索,不上云。
```

### 详细描述

```
PCE 是一个本地优先的 AI 对话捕获层。

你和 ChatGPT、Claude、Copilot、Gemini 等十几个 AI 工具的每一次对话,都会进到你自己电脑上的 SQLite 数据库 — 不会上传到任何云端,不会进入第三方分析管道。三周前那个绝妙的 prompt、Claude 和 ChatGPT 对同一个问题的不同答复,都能在本地全文搜索出来。

▶ 为什么需要 PCE

你可能在 AI 聊天窗口里积累的思考,比自己笔记本上还多。但:

 • ChatGPT 历史在 OpenAI 服务器;Claude 在 Anthropic;Copilot 在微软。
 • 每家只给你他们自己的搜索、导出和保留策略,从没有一个统一视图。
 • 企业合规团队经常直接禁掉这些云端导出。

PCE 让这件事自动发生 — 装好扩展、启动一次 PCE Core 桌面应用,之后所有支持站点上的新对话都会被自动归档到本地。

▶ 扩展做什么

 • 识别你正在访问的是不是受支持的 AI 站点
 • 实时读取对话 DOM
 • 把捕获内容发送到你自己电脑上的 PCE Core(http://127.0.0.1:9800)
 • PCE Core 离线时,捕获缓存在浏览器 IndexedDB 里,稍后自动补发
 • 提供"右键保存选中文字为 Snippet"的快捷入口

扩展本身只是产品的一半。你还需要运行免费的 PCE Core 桌面应用来存储和索引捕获。安装说明:https://github.com/zstnbb/PCE-Core#install

▶ 支持的站点

专用 AI 工具:ChatGPT、Claude、Gemini、Google AI Studio、Microsoft Copilot、DeepSeek、Perplexity、Poe、HuggingChat、Grok、Mistral、Kimi、智谱清言、Manus

办公软件内嵌 AI:Microsoft 365 Copilot、Notion AI、Figma AI、Gmail "Help me write"

缺哪个站?到 GitHub 提 issue。

▶ 你的数据仍然属于你

 • 没有云端。开源(Apache-2.0),代码可审计。
 • 没有 telemetry。扩展只往一个地址发数据:你自己的电脑。
 • 没有第三方 SDK。没有广告、没有分析、没有崩溃上报。
 • 不需要账号。不用注册、不用登录、不要邮箱。

完整隐私政策:https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md

▶ 适合谁

 • 整天和 Copilot、Cursor 类助手、ChatGPT 打交道的开发者
 • 需要追溯 AI 产出来源的研究者
 • 合规策略禁止云端 AI 历史导出的企业知识工作者
 • 任何曾经想过"上个月那个 prompt 到哪去了?"的人

▶ 这不是什么

 • 不是聊天机器人。PCE 只记录,不回答。
 • 不是浏览历史记录器。只在上面列出的 AI 站运行。
 • 不是 SaaS。没有登录、没有订阅、没有免费试用。

▶ 开源

源代码、Issue、讨论:https://github.com/zstnbb/PCE-Core

Apache-2.0 许可。欢迎 PR。
```

---

## Proofreading checklist before submitting

- [ ] GitHub URL in all four places still resolves (replace `zstnbb/PCE-Core` if the org/repo name changed)
- [ ] PRIVACY.md URL resolves after the repo goes public
- [ ] Supported-sites list matches `wxt.config.ts` COVERED_SITES verbatim
- [ ] Version in title/summary/description stays generic ("v1.0", not "v1.0.0-rc2")
- [ ] No forward-looking promises ("we will add X") — store policy prefers present-tense factual claims
- [ ] No mention of features gated behind Pro / paid — this is the OSS extension only
