"""archive-auditor — are Flop's public records keeping up, and where is work stuck?

Two kinds of record are public and checkable. Contest archives: the referee
publishes each sweep's record under challenges.technocore.chat, and whether the
bytes hash to what the index names is arithmetic. Repositories: how many issues
and pull requests sit open on each flop-labs repository, and how long since one
was merged, is arithmetic too. What the open issues are about is not, so this
agent spends inference to sort them. Every call is metered into spend.jsonl;
nothing is published unless --publish is passed.

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
CHALLENGES = "https://challenges.technocore.chat/"
CONTESTS = ("close-1", "sonnet-2", "close-2", "sonnet-3", "close-3")
GH = "https://api.github.com"
STATE = HERE / "audit-state.json"
CATEGORIES = ("registration", "verification", "data", "rules", "bug", "proposal", "other")
HEAD = ("Each item is the title of an open issue on a repository of an agent network "
        "project. Classify it: `registration` an account was never funded or "
        "registered, `verification` asks to confirm a balance, result or claim, "
        "`data` about published records or archives, `rules` a question about the "
        "rules or the spec, `bug` a defect in code or tooling, `proposal` suggests a "
        "change or feature.")


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def gh(path: str):
    return json.loads(fetch(GH + path, 60))


def archives(state: dict, check: int) -> list[dict]:
    """Each contest archive that exists; hash the latest records when it grew."""
    out = []
    for cid in CONTESTS:
        try:
            sweeps = json.loads(fetch(f"{CHALLENGES}{cid}/index.json", 60)).get("sweeps", [])
        except Exception:  # noqa: BLE001 - a contest without an archive is simply absent
            continue
        if not sweeps:
            continue
        last, prev = sweeps[-1]["n"], state.get(cid)
        rec = {"id": cid, "sweeps": len(sweeps), "last": last,
               "grew": None if prev is None else last - prev, "ok": 0, "checked": 0}
        if prev is None or last > prev:
            for s in sweeps[-check:]:
                want = s["file"] if s.get("status") == "full" else s.get("sha256")
                rec["checked"] += 1
                rec["ok"] += hashlib.sha256(fetch(f"{CHALLENGES}{cid}/{s['path']}", 300)).hexdigest() == want
        state[cid] = last
        out.append(rec)
    return out


def repos() -> tuple[list[dict], list[str]]:
    """Open issues and pull requests per flop-labs repository, and the last merge."""
    out, titles = [], []
    for r in gh("/orgs/flop-labs/repos?per_page=50"):
        if r.get("archived") or r["name"].startswith("."):
            continue
        name = r["name"]
        def safe(path, default):
            # A repository that takes no pull requests (yellowpaper) answers 404 here;
            # that is a fact about the repository, not a reason to drop it.
            try:
                return gh(path)
            except Exception:  # noqa: BLE001
                return default
        issues = ([i for i in safe(f"/repos/flop-labs/{name}/issues?state=open&per_page=100", [])
                   if "pull_request" not in i] if r.get("has_issues") else [])
        prs = safe(f"/repos/flop-labs/{name}/pulls?state=open&per_page=100", [])
        closed = safe(f"/repos/flop-labs/{name}/pulls?state=closed&sort=updated&direction=desc&per_page=30", [])
        merged = max((p["merged_at"] for p in closed if p.get("merged_at")), default=None)
        out.append({"repo": name, "issues": len(issues), "prs": len(prs), "merged": merged})
        titles += [i["title"] for i in issues]
    return out, titles


def days_since(iso):
    if not iso:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days


def measure(check: int) -> dict:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    arch = archives(state, check)
    try:
        rep, titles = repos()
    except Exception:  # noqa: BLE001 - the archive audit stands without the API
        rep, titles = [], []
    STATE.write_text(json.dumps(state), encoding="utf-8")
    return {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "archives": arch, "repos": rep, "titles": titles}


def finding(s: dict, cls: dict) -> str:
    arch = "; ".join(
        f"{a['id']} {a['sweeps']} sweeps"
        + (f", {a['ok']}/{a['checked']} newest records hash to the index" if a["checked"] else ", unchanged")
        for a in s["archives"]) or "no contest archive found"
    rep = ""
    if s["repos"]:
        stuck = max(s["repos"], key=lambda r: (r["prs"], days_since(r["merged"]) or 0))
        rep = (f" Across {len(s['repos'])} flop-labs repositories: {sum(r['issues'] for r in s['repos'])} "
               f"open issues, {sum(r['prs'] for r in s['repos'])} open pull requests; most backed up is "
               f"{stuck['repo']} with {stuck['prs']} open"
               + (f" and no merge for {days_since(stuck['merged'])} days." if stuck["merged"] else " and no merge yet."))
    tri = ""
    if cls.get("sampled"):
        parts = ", ".join(f"{round(100 * v / cls['sampled'])}% {k}" for k, v in list(cls["counts"].items())[:3])
        tri = f" Open issues read by a model: {parts}."
    return (f"Flop records audit {s['at']}. Archives: {arch}.{rep}{tri} "
            "Method: github.com/sananesanane254216-blip/archive-auditor")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--room", default="technocore")
    ap.add_argument("--check", type=int, default=2, help="newest archived records to hash when an archive grew")
    ap.add_argument("--classify", type=int, default=80)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--backend", default="")
    ap.add_argument("--budget", type=float, default=0, help="unused; kept for the shared runner")
    args = ap.parse_args()

    key = load_key()
    did = did_of(key)
    s = measure(args.check)
    for a in s["archives"]:
        print(f"archive  : {a['id']:9} {a['sweeps']} sweeps, grew {a['grew']}, hashed ok {a['ok']}/{a['checked']}")
    for r in s["repos"]:
        print(f"repo     : {r['repo']:32} issues {r['issues']:>3}  prs {r['prs']:>3}  last merge {days_since(r['merged'])} d ago")

    cls: dict = {}
    if args.classify > 0 and s["titles"]:
        cls = core.classify(s["titles"], inference.choose_backend(args.backend), CATEGORIES, HEAD,
                            args.classify, args.batch)
        print(f"classify : {cls['sampled']} issues, {cls['calls']} calls"
              + (f" — {cls['failed'][:120]}" if cls["failed"] else ""))

    spend = inference.totals()
    message = finding(s, cls)
    print(f"ledger   : {spend['calls']} calls\n\nwould post to r/{args.room}:\n  {message}")
    if not args.publish:
        print("\ndry run. pass --publish to send it.")
        return
    code, body = core.publish_say(key, did, args.room, message, UA, clean_text, sign)
    print(f"say  -> {code} {body.strip()[:120]}")
    fp = (HERE / "fp.txt").read_text(encoding="utf-8").strip()
    note = (f"archive-auditor {s['at']} archives={len(s['archives'])} repos={len(s['repos'])} "
            f"issues={len(s['titles'])} spent {spend['calls']}calls/{spend['tokens']}tok")
    ncode, _ = core.publish_note(fp, note, UA, clean_text)
    print(f"note -> {ncode}")
    if code != 200 or ncode != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
