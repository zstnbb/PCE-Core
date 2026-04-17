# ADR-006: 浏览器扩展构建链迁移到 WXT

- Status: Accepted
- Date: 2026-04-17
- Related: 2026-04-17-industrialization-roadmap.md

## Context

`pce_browser_extension/` 当前是手写的 Chrome MV3 扩展：

- 13+ 个站点专用提取器
- 通用 detector / universal extractor / network interceptor
- 手写 `manifest.json`（以及一份 `manifest.store.json` 作为上架备用）
- 原生 JavaScript，没有 TypeScript
- 没有热重载，改一行要手动重载扩展
- 没有跨浏览器（Firefox / Edge）构建能力

这套结构作为 PoC 验证够用，但扩展逻辑本身已经不轻量（13 个站点提取器加起来数千行代码），继续手写管理成本越来越高。

市面上 MV3 扩展框架的主流三选项：Plasmo / WXT / CRXJS。根据 2025 年社区生态现状：

- Plasmo 已进入维护模式，活跃度下降
- CRXJS 依赖 Vite 插件，生态较薄
- WXT 成为 2025 年事实标准，官方模板覆盖 Vue / React / Svelte / Solid，亦支持纯 JS / TS

## Decision

将 `pce_browser_extension/` 的**构建链**迁移到 **WXT**。

关键边界：

- 只替换构建工具、manifest 生成、热重载、跨浏览器打包等**骨架**
- 业务逻辑（detector / universal extractor / 各站点提取器 / network interceptor / text_collector / reconciler bridge）**保持不变**，只是从原生 JS 迁移到 TypeScript
- manifest 生成由 WXT 的定义式配置替代，不再手工维护 `manifest.json` 和 `manifest.store.json` 两份
- 两套发行目标（sideload 全权限 vs webstore 受限权限）通过 WXT 的环境变量机制切换

## Rationale

1. WXT 把我们手工维护的"manifest 双版本、权限切换、跨浏览器"等工程苦力工作标准化了。
2. 迁移后的热重载可以显著提升扩展开发速度。
3. TypeScript 带来编译期检查，对 13+ 站点提取器的长期维护至关重要。
4. WXT 与我们已有的逻辑模块结构（content_scripts / background / interceptor）天然兼容，迁移成本可控。
5. WXT 的产物结构与官方商店格式对齐，未来上架时只需调整权限声明。

## Consequences

### Positive

- 扩展开发循环从"改代码-重载-测试"缩短到"改代码-自动热重载"
- 一次源码，一键产出 Chrome / Firefox / Edge 构建物
- 通过 TypeScript 消除大量目前靠约定俗成维持的接口
- 为将来商店上架铺路
- 把"权限双模式（sideload / webstore）"的切换从手工变成自动

### Negative

- 现有扩展代码需要一次批量迁移（可分模块、分版本进行）
- 需要学习 WXT 的目录约定
- 构建引入 Vite，比原生加载多一步

## Guardrails

- 迁移分两步走：先搭 WXT 骨架并移植 manifest 与 background，再逐个迁移 content scripts
- 每个 content script 迁移完毕必须保留行为兼容测试（现有的 e2e 抓取测试集）
- 迁移过程中不引入任何新功能，保持"结构替换、行为不变"
- 任何业务逻辑修改都单独成 PR，不与框架迁移混合

## Review Trigger

以下情况重新评估：

- WXT v2 出现不兼容的破坏性演进
- 我们决定放弃非 Chromium 浏览器
- 扩展整体被替换为 CDP 内嵌浏览器方案
