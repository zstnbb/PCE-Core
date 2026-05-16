# Sprint Acceptance Gate (Closing Checklist) — STRICT MODE

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §5
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
>
> **本文件**: 冲刺关闭时, 把这份完整 checklist 复制到
> `HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md`.
>
> **STRICT MODE 不变式**: 13/13 严格 ≥3 V-GREEN 才能发 v1.1.6.
> 12/13 + carry-forward 不接受. 任一场景滑点 → release 顺移.

---

## GLOBAL SPRINT ACCEPTANCE

```
=== G1 — Coverage (13/13 P0 scenarios ≥ 3 V-GREEN per
                   REDUNDANCY-AUDIT §3) ===

# Web 5 (Tier-α)
[ ] f1_chatgpt_web                  L1 ✅  L3a ✅  L3d ✅
[ ] f1_claude_web                   L1 ✅  L3a ✅  L3d ✅
[ ] f1_gemini_web                   L1 ✅  L3a ✅  L4a ✅
[ ] f1_gas                          L1 ✅  L3a ✅  L4a ✅
[ ] f1_grok_web                     L1 ✅  L3a ✅  L4a ✅

# Desktop 8 / Tier-α
[ ] f4_p1_claude_desktop            L1 ✅  L3g ✅  L3f ✅
[ ] f5_p4_windsurf                  L1 ✅  L3f ✅  L4a ✅
[ ] f6_p6_claude_code_cli           L1 ✅  L3g ✅  L3h ✅
[ ] f6_p7_codex_cli                 L1 ✅  L3g ✅  L3h ✅
[ ] f6_p8_gemini_cli                L1 ✅  L3g ✅  L3h ✅

# Desktop 8 / Tier-β (Phase B)
[ ] f4_p2_chatgpt_desktop           L1 ✅  A2 ✅(corr.)  L4b ✅
[ ] f5_p3_cursor                    L1 ✅(protobuf)  L3f ✅  L3b/L4a ✅
[ ] f5_p5_github_copilot            L1 ✅  L3c ✅  L3f ✅


=== G2 — Runtime redundancy alive ===
[ ] /api/v1/supervisor/status returns 13 scenarios, all "redundant"
[ ] dedup proven: ≥ 3 live tests across different scenarios
    (W4-T4 P1 Claude Desktop, W7-T2 P2 ChatGPT Desktop UIA dedup,
     W8 live Cursor 三 leg dedup)
[ ] kill 1 leg → status flips to "minimal" within 30s
[ ] dashboard /redundancy renders 13 cards correctly
[ ] scenarios.yaml independent_basis uniqueness enforced


=== G3 — Visibility ===
[ ] REDUNDANCY-MATRIX.svg renders 13 cards (all green)
[ ] CONTRIBUTING.md links to REDUNDANCY-AUDIT §7 update protocol
[ ] nightly probe schedules redundancy check daily
[ ] tools/render_redundancy_matrix.py runs deterministically
    (same input → same SVG)
[ ] redundancy-degraded auto-issue channel verified (dry-run)
[ ] 7-day post-tag window: 0 redundancy-degraded auto-issue regression


=== Governance ===
[ ] ADR-021 (capture_supervisor OSS boundary) Adopted
    + check_import_direction.py allowlist updated
    + CI green
[ ] ADR-018 Phase 5 (SSLKEYLOGFILE) Closed
    + ADR status field updated
[ ] ADR-022 (UIA accessibility OSS boundary) Adopted
    + Wave 7 落地证据
[ ] ADR-023 (gRPC-web protobuf decoder) Adopted
    + Wave 8 落地证据
[ ] PR template asks "did you update REDUNDANCY-AUDIT-MATRIX §3?"
[ ] UNIVERSAL-CAPTURE-STACK-DESIGN.md 附录 B 加口径声明


=== Hygiene ===
[ ] 70+ new tests GREEN, 0 flake in CI
    Phase A 47+ (test_capture_supervisor 31 + test_keylog_mode 16)
    Wave 6   20  (test_normalizer_github_copilot 12 +
                  vscode_ext/copilot tests 8)
    Wave 7   28  (test_uia_driver_unit 18 + test_uia_redact 6 +
                  e2e_uia 4)
    Wave 8   60+ (test_normalizer_protobuf_framing 22 +
                  test_normalizer_protobuf_decoder 18 +
                  test_normalizer_cursor 14 + L3b/L4a 6-8)
[ ] CHANGELOG entry for P5.D.1 Redundancy Sprint (Phase A + B + C)
[ ] git tag v1.1.6 issued (only after 13/13 verified by W9-T1)
[ ] All 30+ handoff files committed under Docs/handoff/
[ ] _evidence_W*_2026-*/ snapshot dirs committed
    (gitignored payload, signed manifest)


=== Per-scenario evidence trail ===

# Web 5
[ ] f1_chatgpt_web                  W4-T1 handoff (L3d CDP live)
[ ] f1_claude_web                   W4-T2 handoff (L3d CDP live)
[ ] f1_gemini_web                   W1-T4 + W4-T3 handoffs
[ ] f1_gas                          W1-T5 + W4-T3 handoffs
[ ] f1_grok_web                     W1-T6 + W4-T3 handoffs

# Desktop 8 / Tier-α
[ ] f4_p1_claude_desktop            W1-T1/T2 + W4-T4 handoffs
[ ] f5_p4_windsurf                  W1-T7 + W4-T5 handoffs
[ ] f6_p6_claude_code_cli           W1-T3 + W4-T6 handoffs
[ ] f6_p7_codex_cli                 W1-T8 + W1-T9 + W4-T7 handoffs
[ ] f6_p8_gemini_cli                W1-T10 + W1-T11 + W4-T8 handoffs

# Desktop 8 / Tier-β
[ ] f4_p2_chatgpt_desktop           W2 + W7-T1 + W7-T2 handoffs
[ ] f5_p3_cursor                    W8-T1 + W8-T2 + W8-T3 handoffs
[ ] f5_p5_github_copilot            W6-T1 + W6-T2 + W6-T3 handoffs


=== Carry-forward sanity ===
[ ] 全部 carry-forwards 列在 SCOPE-LOCK §4, 不变
[ ] 不在 v1.1.6 内的场景 (Perplexity, F2, F3, F9, F10, etc.)
    REDUNDANCY-AUDIT §3 仍按各自当前等级标记, 不标 V-GREEN
[ ] 任何 V-DEAD path 仍是 V-DEAD; 没有静悄悄翻案; ADRs supersede
    explicitly when applicable
[ ] STRICT MODE 不接受 12/13 + carry-forward — 任一未达 release 顺移
```

---

## Verification one-liners (for next-shift agent)

```powershell
# 1. All handoff files present
Get-ChildItem Docs/handoff/HANDOFF-W*-2026-*.md, Docs/handoff/HANDOFF-P5D1-*.md | Measure-Object | Select-Object Count
# Expected: 30+

# 2. Test count post-sprint (should be ≥ 70 added)
python -m pytest tests/test_capture_supervisor.py tests/test_keylog_mode.py tests/test_uia_driver_unit.py tests/test_uia_redact.py tests/test_normalizer_protobuf_framing.py tests/test_normalizer_protobuf_decoder.py tests/test_normalizer_cursor.py tests/test_normalizer_github_copilot.py -v --tb=short -q

# 3. supervisor status alive (STRICT 13/13)
curl http://127.0.0.1:9800/api/v1/supervisor/status | python -c "import sys,json; d=json.load(sys.stdin); n=len(d['scenarios']); r=sum(1 for s in d['scenarios'] if s['status']=='redundant'); print(f'{n} scenarios; redundant={r}'); sys.exit(0 if r==13 else 1)"
# Expected: 13 scenarios; redundant=13

# 4. dedup proof from multiple waves
sqlite3 Docs/handoff/_evidence_W4_2026-05-14/w4_t4.db "SELECT pair_id, source_id, deduped_by FROM raw_captures WHERE deduped_by IS NOT NULL LIMIT 5;"
sqlite3 Docs/handoff/_evidence_W7_2026-*/w7_t2.db "SELECT pair_id, source_id, deduped_by FROM raw_captures WHERE deduped_by IS NOT NULL LIMIT 5;"
sqlite3 Docs/handoff/_evidence_W8_2026-*/w8_live.db "SELECT pair_id, source_id, deduped_by FROM raw_captures WHERE deduped_by IS NOT NULL LIMIT 5;"
# Expected: rows with non-null deduped_by JSON arrays in each

# 5. SVG generated (13 cards)
Test-Path Docs/stability/REDUNDANCY-MATRIX.svg
# Expected: True

# 6. CI for check_import_direction
python scripts/check_import_direction.py
# Expected: exit 0 (capture_supervisor allowed in OSS)

# 7. Final closing handoff present
Test-Path Docs/handoff/HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md
# Expected: True

# 8. git tag v1.1.6 ready
git tag -l v1.1.6
# Expected: v1.1.6 (after W9-T8)
```

If any item fails, the sprint is INCOMPLETE — report back to owner
before tagging v1.1.6. **STRICT MODE 不允许妥协.**

---

## Post-sprint review (within 7 days of close)

```
[ ] 24h post-tag: nightly workflow ran 1 time GREEN
[ ] 7-day window: zero redundancy-degraded auto-issue regression
[ ] 7-day window: zero dedup假阳 reports from owner
[ ] 7-day window: scenarios.yaml independent_basis uniqueness 仍 enforced
    (没人偷偷加重复 basis)
[ ] Run REDUNDANCY-AUDIT §7.3 quarterly check 1 周内提早做
    确认 13 V-GREEN 仍可 reproduce
[ ] 任一 fail 项 → 开 carry-forward issue + 引用本冲刺 gating evidence
```

---

## 标杆性后续 (P5.D.2 启动门)

P5.D.1 close 后, 下一冲刺 P5.D.2 接入新场景必须满足:

1. 新场景的 `scenarios.yaml` 行格式与 13 P0 一致
2. 三 leg 的 `independent_basis` 唯一
3. 全部三 leg V-GREEN 才标"覆盖"
4. handoff 含 SQLite snapshot
5. ADR (如需新边界) 走 ADR-021/022/023 立的 OSS 边界模板

任何想"凑数"的妥协路径必须新 ADR 显式 supersede SCOPE-LOCK §6 严格
验收门, 不允许悄悄松绑.
