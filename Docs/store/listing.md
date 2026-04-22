# Chrome Web Store — Listing Copy (v1.0.1, post-rejection rewrite)

Copy-paste targets for the Chrome Web Store Developer Dashboard. Keep
this file in sync with whatever actually lives in the store — it is the
single source of truth when we need to re-submit after a policy change.

## Rejection history

**2026-04-22 — v1.0.0 rejected (Yellow Argon, keyword spam).**
Chrome Web Store flagged the previous SUPPORTED SITES section because
it enumerated 14 AI-tool brand names with URLs as a bullet list:

> 产品说明中有过多和/或不相关的关键字
> (The product description contains excessive or irrelevant keywords.)

Root cause: bullet list of 14 brand-name/URL pairs triggered Google's
SEO-stuffing heuristic, even though every site is actually supported.

Fix applied in this revision:

- SUPPORTED SITES bullet list removed — the full list now lives in the
  public GitHub README, which is the correct place for it.
- Summary no longer enumerates multiple vendor brands.
- WHY PCE bullets describe the problem category, not specific vendor
  storage locations.
- Brief brand mentions that remain are contextual prose, not lists.

This preserves the product's discoverability while matching Chrome Web
Store's "describe what the extension does, don't stuff a keyword list"
policy. See Docs/stability/P5B-PLAN.md for the v1.0.1 rollup.

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
Keep a searchable local history of your AI chat conversations — stored entirely on your own computer, never in our cloud.
```

(121 chars. Leads with the benefit, describes what the product does
without enumerating vendor names, ends with the trust signal that
separates PCE from cloud-based competitors. Rejection-compliant
rewrite of the previous summary, which listed four AI brands.)

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

Every AI chat you have in your browser flows into a SQLite database on your own computer — never to a cloud we control, never into a third-party analytics pipeline. When you want to find that brilliant prompt you wrote three weeks ago, or compare how two different assistants answered the same question, the history is right there, on your own machine, searchable in full-text.

▶ WHY PCE

You probably have more valuable thinking trapped inside AI chat tabs than inside your own note-taking app. But:

 • Each AI vendor keeps your history on their servers, bound by their own retention and export policy.
 • You never get a unified, searchable view across the tools you actually use.
 • Enterprise privacy teams frequently block cloud-hosted AI history exports entirely.

PCE fixes this without asking you to change how you use AI. Install the extension, run the free PCE Core companion server on your own computer, and every future conversation on a covered site gets archived locally, automatically.

▶ WHAT THIS EXTENSION DOES

 • Detects when you visit a supported AI site.
 • Reads the conversation DOM in real time as messages arrive.
 • Forwards captured messages to a PCE Core server running on your own computer (http://127.0.0.1:9800).
 • Queues captures locally in IndexedDB if PCE Core is offline, and forwards them later.
 • Offers two one-click capture shortcuts: a right-click "Capture This Page" command for the whole conversation, and a floating "Save" button that appears when you highlight a passage — both routed to the same local PCE Core server.

The extension by itself is only half the product. You also need to run the free, open-source PCE Core companion server (a local Python application), which stores and indexes your captures. See the installation guide: https://github.com/zstnbb/PCE-Core#install

▶ WHERE IT WORKS

PCE runs on the major web-based AI assistants people use for chat, coding help, and research, and also on the AI features embedded in several popular productivity tools. The extension detects a supported site automatically — there is nothing to configure per-site.

The complete, always-up-to-date list of supported sites is maintained on the project's public README:
https://github.com/zstnbb/PCE-Core#supported-ai-tools

New sites are added regularly based on community feedback. If your favorite AI assistant is not supported yet, please open an issue on GitHub.

▶ YOUR DATA STAYS YOURS

 • No cloud backend. PCE is open source (Apache-2.0) — you can read the code that runs inside the extension.
 • No telemetry. The extension has exactly one network destination: your own computer.
 • No third-party SDKs. No ads, no analytics, no crash reports to us.
 • No account required. No signup, no login, no email address.

Read the full privacy policy: https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md

▶ WHO IS THIS FOR

 • Developers who lean on AI assistants every day and want a single searchable archive of their prompts and answers.
 • Researchers who need provenance for AI-generated text they cite or publish.
 • Privacy-conscious knowledge workers whose enterprise policy forbids cloud-hosted AI history exports.
 • Anyone who has ever thought "I wish I could find that prompt I used last month".

▶ WHAT THIS IS NOT

 • Not a chatbot. PCE does not answer your questions — it just records the ones you already asked.
 • Not a general browser history logger. PCE only runs on the specific AI sites enumerated in its host_permissions manifest.
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
把每一次 AI 对话都留在你自己的电脑上 — 可全文搜索、本地存储、不上云。完全开源、零遥测、无登录。
```

(符合 2026-04-22 拒稿修复:不再枚举多个厂商品牌名,改为描述性文案。)

### 详细描述

```
PCE 是一个本地优先的 AI 对话捕获层。

你在浏览器里和 AI 助手的每一次对话,都会进到你自己电脑上的 SQLite 数据库 — 不会上传到任何云端,不会进入第三方分析管道。三周前写过的那个绝妙 prompt、不同助手对同一个问题的不同答复,都能在本地全文搜索出来。

▶ 为什么需要 PCE

你可能在 AI 聊天窗口里积累的思考,比自己笔记本上还多。但:

 • 每家 AI 厂商只把你的历史留在他们自己服务器上,按他们自己的保留策略和导出规则
 • 你从来没有一个跨厂商的、统一的搜索视图
 • 企业合规团队经常直接禁掉这些云端导出

PCE 让这件事自动发生 — 装好扩展、在自己电脑上启动免费的 PCE Core 本地服务,之后所有支持站点上的新对话都会被自动归档到本地。

▶ 扩展做什么

 • 识别你正在访问的是不是受支持的 AI 站点
 • 实时读取对话 DOM
 • 把捕获内容发送到你自己电脑上的 PCE Core(http://127.0.0.1:9800)
 • PCE Core 离线时,捕获缓存在浏览器 IndexedDB 里,稍后自动补发
 • 两个一键捕获入口：右键菜单"Capture This Page"抓整个对话，选中文本时出现的浮动"Save"按钮抓指定片段——均发往本地 PCE Core

扩展本身只是产品的一半。你还需要运行免费开源的 PCE Core 本地服务（一个本地 Python 应用）来存储和索引捕获。安装说明:https://github.com/zstnbb/PCE-Core#install

▶ 在哪些站点工作

PCE 运行在主流的浏览器端 AI 助手上 — 包括你日常用来聊天、编程协助和资料检索的那些;也覆盖了若干主流办公软件内嵌的 AI 功能。扩展会自动识别支持的站点,无需你逐个配置。

完整的、持续更新的支持站点清单在项目公开 README:
https://github.com/zstnbb/PCE-Core#supported-ai-tools

根据社区反馈持续添加新站点。如果你常用的助手还不支持,欢迎到 GitHub 提 issue。

▶ 你的数据仍然属于你

 • 没有云端。开源(Apache-2.0),代码可审计。
 • 没有 telemetry。扩展只往一个地址发数据:你自己的电脑。
 • 没有第三方 SDK。没有广告、没有分析、没有崩溃上报。
 • 不需要账号。不用注册、不用登录、不要邮箱。

完整隐私政策:https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md

▶ 适合谁

 • 每天高强度使用各类 AI 助手、需要一个统一可搜索历史库的开发者
 • 需要追溯 AI 产出来源的研究者
 • 合规策略禁止云端 AI 历史导出的企业知识工作者
 • 任何曾经想过"上个月那个 prompt 到哪去了?"的人

▶ 这不是什么

 • 不是聊天机器人。PCE 只记录,不回答。
 • 不是通用浏览历史记录器。只在 manifest host_permissions 里显式声明的 AI 站点运行。
 • 不是 SaaS。没有登录、没有订阅、没有免费试用。

▶ 开源

源代码、Issue、讨论:https://github.com/zstnbb/PCE-Core

Apache-2.0 许可。欢迎 PR。
```

---

## Proofreading checklist before submitting

- [ ] GitHub URL in all four places still resolves (replace `zstnbb/PCE-Core` if the org/repo name changed)
- [ ] PRIVACY.md URL resolves after the repo goes public
- [ ] **No bullet list of vendor brand names in description** — the 2026-04-22 rejection came from exactly that. Brand names MAY appear in contextual prose, but never as a list with URLs. The authoritative site list lives in the public README.
- [ ] Version in title/summary/description stays generic ("v1.0", not "v1.0.0-rc2")
- [ ] No forward-looking promises ("we will add X") — store policy prefers present-tense factual claims
- [ ] No mention of features gated behind Pro / paid — this is the OSS extension only
- [ ] `host_permissions` justification field in `Docs/store/justification.md` §3 STILL enumerates hosts verbatim — that is a compliance field reviewers use to verify manifest/justification match, and is NOT subject to the public-description keyword-spam policy. Do not trim it.

## When re-submitting after the 2026-04-22 rejection

1. Copy the new Summary and Detailed description above into the Chrome Web Store Developer Dashboard **Store listing** tab (both EN and ZH if ZH is enabled).
2. Bump `wxt.config.ts` version: `1.0.0` → `1.0.1`.
3. Rebuild the webstore zip: `cd pce_browser_extension_wxt && pnpm build --mode webstore && pnpm zip --mode webstore`.
4. Upload the new zip in the **Package** tab (it replaces the rejected v1.0.0 archive).
5. Privacy-practices tab: no changes required — `justification.md` text is still accurate.
6. Submit for review. Typical turnaround: 1–3 business days for a second submission after a metadata-only fix.
