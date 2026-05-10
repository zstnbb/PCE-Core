"""Verify D06 normalized messages: file attachment in user content_json + assistant text."""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3

DB = pathlib.Path(os.path.expanduser("~/.pce/data/pce.db"))
PAIR = "4e88d79d75fb4cd2"


def main() -> int:
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    cur.execute(
        "SELECT id, role, content_text, content_json, model_name, token_estimate, ts FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (PAIR,),
    )
    rows = cur.fetchall()
    print(f"== {len(rows)} messages for D06 pair ==\n")
    for mid, role, ctext, cjson, mn, te, ts in rows:
        print(f"-- role={role} model={mn} tokens={te} id={mid} ts={ts}")
        print(f"   content_text ({len(ctext or '')} chars):")
        s = (ctext or "")[:600]
        print(f"   {s!r}")
        if cjson:
            try:
                cd = json.loads(cjson)
                print(f"   content_json keys: {list(cd.keys())}")
                if "attachments" in cd:
                    atts = cd["attachments"]
                    print(f"   attachments: {len(atts)} items")
                    for a in atts:
                        print(f"     {a}")
            except Exception as e:
                print(f"   content_json parse err: {e}; raw[:300]={cjson[:300]!r}")
        else:
            print("   content_json: (none)")
        print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
