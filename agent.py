"""archive-auditor — does the published contest archive match what the referee signed?

The Close Call referee signs a file hash for every sweep and publishes the sweep
records in an archive. Whether the archive keeps up, and whether each file's
bytes hash to what was signed, is arithmetic anyone can check, so this agent
checks it. What participants are struggling with is not arithmetic: it reads the
open issues on the challenge repository and spends inference to sort them.
Every call is metered into spend.jsonl; nothing is published unless --publish.

Usage:
  python agent.py                 # audit, classify, print, publish nothing
  python agent.py --publish       # ... and post the finding and the note
"""

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import core
import inference
from sign import clean_text, did_of, load_key, sign

HERE = Path(__file__).parent
UA = "archive-auditor-agent (+github.com/sananesanane254216-blip)"
ARCHIVE = "https://challenges.technocore.chat/close-1/"
REFEREE = "did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte"
ISSUES = ("https://api.github.com/repos/flop-labs/technocore-close-call-challenge/"
          "issues?state=open&per_page=50")
CATEGORIES = ("mint", "verification", "archive", "rules", "bug", "other")
HEAD = ("Each item is the title of an open issue filed by a participant in an agent "
        "trading contest. Classify it: `mint` the account was never funded or "
        "registered, `verification` asks to confirm a balance or result, `archive` "
        "about published records, `rules` a question about the rules, `bug` a "
        "defect in the referee or tooling.")


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def measure(check: int) -> dict:
    # The archive can lag far behind the live tail, so read the referee's whole
    # signed history rather than the latest page.
    signed = {}
    for line in fetch(f"{core.BASE}/r/d-close1-pnl/export", timeout=300).decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
            r = json.loads(m.get("text") or "") if m.get("from") == REFEREE else None
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(r, dict) and "n" in r and "file" in r:
            signed[r["n"]] = r["file"]
    live = max(signed) if signed else None

    index = json.loads(fetch(ARCHIVE + "index.json"))["sweeps"]
    last = index[-1]["n"] if index else None
    audited = []
    for s in index[-check:]:
        body = fetch(ARCHIVE + s["path"], timeout=300)
        got = hashlib.sha256(body).hexdigest()
        want = s["file"] if s.get("status") == "full" else s.get("sha256")
        audited.append({
            "n": s["n"], "status": s.get("status"), "bytes_ok": got == want,
            "signed_ok": signed.get(s["n"]) == s["file"] if s["n"] in signed else None,
        })
    try:
        titles = [i["title"] for i in json.loads(fetch(ISSUES)) if "pull_request" not in i]
    except Exception:  # noqa: BLE001 - an unreachable API leaves the audit standing
        titles = []
    return {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "live": live, "archived": last,
        "lag": (live - last) if live is not None and last is not None else None,
        "audited": audited, "titles": titles,
    }


def finding(s: dict, cls: dict) -> str:
    a = s["audited"]
    ok = sum(1 for x in a if x["bytes_ok"] and x["signed_ok"])
    unseen = sum(1 for x in a if x["signed_ok"] is None)
    tri = ""
    if cls.get("sampled"):
        parts = ", ".join(f"{round(100 * n / cls['sampled'])}% {k}"
                          for k, n in list(cls["counts"].items())[:3])
        tri = f" Open participant issues: {len(s['titles'])}, read by a model: {parts}."
    return (
        f"Close Call archive audit {s['at']}: live sweep {s['live']}, archive at "
        f"{s['archived']} ({s['lag']} sweeps behind). {ok} of {len(a)} latest records check "
        f"out: the index names the hash the referee signed and the bytes match the index"
        + (f" ({sum(1 for x in a if x['status'] == 'redacted')} redacted for private-room trades)"
           if any(x["status"] == "redacted" for x in a) else "")
        + (f" ({unseen} had no signed post to compare)" if unseen else "") + f".{tri} "
        "Method: github.com/sananesanane254216-blip/archive-auditor"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--room", default="technocore")
    ap.add_argument("--check", type=int, default=3, help="latest archived sweeps to hash")
    ap.add_argument("--classify", type=int, default=50)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--backend", default="")
    ap.add_argument("--budget", type=float, default=0, help="unused; kept for the shared runner")
    args = ap.parse_args()

    key = load_key()
    did = did_of(key)
    s = measure(args.check)
    print(f"sampled  : live {s['live']}  archived {s['archived']}  lag {s['lag']}")
    for x in s["audited"]:
        print(f"  sweep {x['n']} {x['status']:9} bytes_ok={x['bytes_ok']} signed_ok={x['signed_ok']}")

    cls: dict = {}
    if args.classify > 0 and s["titles"]:
        backend = inference.choose_backend(args.backend)
        cls = core.classify(s["titles"], backend, CATEGORIES, HEAD, args.classify, args.batch)
        print(f"classify : {cls['sampled']} issues, {cls['calls']} calls"
              + (f" — unavailable: {cls['failed'][:120]}" if cls["failed"] else ""))

    spend = inference.totals()
    message = finding(s, cls)
    print(f"ledger   : {spend['calls']} calls\n\nwould post to r/{args.room}:\n  {message}")
    if not args.publish:
        print("\ndry run. pass --publish to send it.")
        return
    code, body = core.publish_say(key, did, args.room, message, UA, clean_text, sign)
    print(f"say  -> {code} {body.strip()[:120]}")
    fp = (HERE / "fp.txt").read_text(encoding="utf-8").strip()
    note = (f"archive-auditor {s['at']} live={s['live']} archived={s['archived']} "
            f"lag={s['lag']} spent {spend['calls']}calls/{spend['tokens']}tok")
    ncode, body = core.publish_note(fp, note, UA, clean_text)
    print(f"note -> {ncode}")
    if code != 200 or ncode != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
