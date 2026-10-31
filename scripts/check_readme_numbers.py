"""Every accuracy-like number in README.md must come from eval/results.json (no API, no data needed).

Checks percentages ("89.5%"), compound counts ("34/38", "17/19"), citation counts ("117/117") and bootstrap
points ("+8.3 points", "-0.2 to +16.9"). Prints each README number that results.json does not contain and exits 1
if there is one. Numbers from other sources (costs, IoU, latency) are not checked.

Usage: python scripts/check_readme_numbers.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def allowed(res: dict) -> tuple[set[str], set[str]]:
    pcts, fracs = set(), set()
    for r in res["rows"]:
        for k in ("accuracy", "accuracy_before_review", "scientist_accuracy", "nsc_on_chosen_wells", "sd"):
            if r.get(k) is not None:
                pcts.add(f"{100 * r[k]:.1f}")
        for k, v in (r.get("split_accuracy") or {}).items():
            fracs.add(f"{round(v * r['n'] / 2)}/{r['n'] // 2}")
        if r.get("correct") is not None:
            fracs.add(f"{r['correct']}/{r['n']}")
        if r.get("citations"):
            fracs.add(r["citations"])
    wc = res.get("well_choice") or {}
    for row in wc.get("rows", []):
        for part in ("all", "dev", "test"):
            if part in row and float(row[part]["correct"]).is_integer():
                fracs.add(f"{int(row[part]['correct'])}/{row[part]['n']}")
    for d in wc.get("differences", []):
        for part in ("all", "dev", "test"):
            if part in d:
                for x in (d[part]["mean"], *d[part]["ci95"]):
                    pcts.add(f"{abs(100 * x):.1f}")
    t = wc.get("agent_wells_in_top_doses")
    if t:
        fracs.add(f"{t['wells']}/{t['of']}")
    return pcts, fracs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = json.loads((ROOT / "eval" / "results.json").read_text(encoding="utf-8"))
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    pcts, fracs = allowed(res)
    t = (res.get("well_choice") or {}).get("agent_wells_in_top_doses")
    bad = []
    for m in re.finditer(r"(\d+\.\d)\s*%", text):
        if m.group(1) not in pcts:
            bad.append(m.group(0))
    for m in re.finditer(r"(?<![\d/.$])(\d+)/(\d+)(?![\d/])", text):
        n = int(m.group(2))
        if n in (19, 38) or (m.group(1) == m.group(2) and n >= 50) or (t and n == t["of"]):  # x/x < 50: demo runs
            if m.group(0) not in fracs:
                bad.append(m.group(0))
    for m in re.finditer(r"([+−-]\d+\.\d) (?:points|to)|to ([+−-]\d+\.\d)", text):
        v = (m.group(1) or m.group(2)).lstrip("+−-")
        if v not in pcts:
            bad.append(m.group(0))
    checked = len(re.findall(r"\d+\.\d\s*%", text))
    if bad:
        print(f"README numbers not in eval/results.json ({len(bad)}): " + ", ".join(dict.fromkeys(bad)))
        return 1
    print(f"OK: {checked} percentages and every x/19, x/38 and citation count in README.md match eval/results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
