# SPDX-License-Identifier: Apache-2.0
"""Quick verifier for manual E2E sweep — prints latest captures + sessions."""
from __future__ import annotations
import argparse
import json
import sys

import requests

BASE = "http://127.0.0.1:9800"


def head(s: str) -> str:
    return s if len(s) <= 200 else s[:200] + f" ... [+{len(s) - 200}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="Token to grep for in body / messages")
    ap.add_argument("--last", type=int, default=5)
    ap.add_argument("--show-body", action="store_true")
    args = ap.parse_args()

    stats = requests.get(f"{BASE}/api/v1/stats", timeout=5).json()
    print("=== STATS ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    print("\n=== LATEST CAPTURES (anthropic) ===")
    caps = requests.get(
        f"{BASE}/api/v1/captures",
        params={"last": args.last, "provider": "anthropic"},
        timeout=5,
    ).json()
    items = caps if isinstance(caps, list) else caps.get("captures", caps)
    for i, c in enumerate(items):
        body = c.get("body_text_or_json") or ""
        meta = c.get("meta_json") or "{}"
        try:
            meta_obj = json.loads(meta)
        except Exception:
            meta_obj = {}
        cap_method = meta_obj.get("capture_method", "?")
        cap_type = meta_obj.get("capture_type") or meta_obj.get("extraction_strategy") or "-"
        token_hit = (args.token in body) if args.token else None
        print(
            f"\n--- #{i} ts={c.get('created_at'):.0f} dir={c.get('direction')} "
            f"method={c.get('method')} status={c.get('status_code')} "
            f"path={c.get('path')!s:60s} "
            f"cap={cap_method}/{cap_type} "
            f"hint={c.get('session_hint')} "
            f"body_len={len(body)} "
            f"token_in_body={token_hit} ---"
        )
        if args.show_body:
            print("BODY:", head(body))

    print("\n=== LATEST SESSIONS ===")
    sess = requests.get(
        f"{BASE}/api/v1/sessions",
        params={"last": args.last, "provider": "anthropic"},
        timeout=5,
    ).json()
    sitems = sess if isinstance(sess, list) else sess.get("sessions", sess)
    for i, s in enumerate(sitems):
        sid = s.get("session_id") or s.get("id")
        print(
            f"\n--- session #{i} id={sid} model={s.get('model_name')} "
            f"created_at={s.get('created_at')} msgs={s.get('message_count')} ---"
        )
        if sid and args.token:
            msgs = requests.get(
                f"{BASE}/api/v1/sessions/{sid}/messages", timeout=5
            ).json()
            mitems = msgs if isinstance(msgs, list) else msgs.get("messages", msgs)
            for j, m in enumerate(mitems):
                content = m.get("content_text") or m.get("content") or ""
                cj = m.get("content_json")
                if isinstance(content, list):
                    content = json.dumps(content, ensure_ascii=False)
                hit = args.token in content
                cj_hit = cj is not None and args.token in json.dumps(cj, ensure_ascii=False)
                print(
                    f"    msg #{j} role={m.get('role')} model={m.get('model_name')} "
                    f"len={len(content)} token_in_text={hit} token_in_json={cj_hit}"
                )
                if args.show_body:
                    print("    →", head(content))

    return 0


if __name__ == "__main__":
    sys.exit(main())
