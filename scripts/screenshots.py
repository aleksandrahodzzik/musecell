"""Screenshots of every screen at 1280×720 (the demo-video size) into docs/screens/.

Needs the backend running with the built frontend (`npm run build` in frontend/, then
`python -m uvicorn app.main:app --app-dir backend --port 8787`) and at least one recorded run. The run screens are
taken from a replay of a recorded real run, so no API credits are spent.

    python scripts/screenshots.py [--base http://127.0.0.1:8787] [--run run_87e2787d1f1e] [--speed 4]

Uses the installed Microsoft Edge (or Chrome) through Playwright, so no browser download is needed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screens"
VIEWPORT = {"width": 1280, "height": 720}


def api(base: str, path: str, method: str = "GET") -> dict | list:
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(400)  # let images decode and bars settle
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    print("saved", path.relative_to(ROOT))


def hover_a_cell(page: Page) -> bool:
    pane = page.get_by_role("img", name="Treated").first
    box = pane.bounding_box()
    if box is None:
        return False
    for fy in (0.5, 0.4, 0.6, 0.3, 0.7):
        for fx in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
            page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            page.wait_for_timeout(60)
            if page.get_by_text("Intensities relative to this plate").count():
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--run", default=None, help="recorded run to replay (default: the first listed)")
    ap.add_argument("--speed", type=float, default=4.0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    recorded = api(args.base, "/api/recorded")
    if not recorded:
        print("No recorded runs on the server; the run screens need one.", file=sys.stderr)
        return 1
    source = args.run or recorded[0]["run_id"]

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel)
                break
            except Exception:  # noqa: BLE001 - try the next installed browser
                continue
        if browser is None:
            print("Neither Edge nor Chrome could be launched.", file=sys.stderr)
            return 1
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{args.base}/#/")
        page.get_by_role("radio").first.wait_for()
        shot(page, "01_choose_specimen")
        page.get_by_role("button", name="Watch a recorded run").click()
        page.get_by_role("button", name="Replay").first.wait_for()
        shot(page, "02_choose_recorded")

        replay = api(args.base, f"/api/runs/{source}/replay?speed={args.speed}", "POST")
        page.goto(f"{args.base}/#/run/{replay['run_id']}")
        page.get_by_text("Replay of a live run").wait_for()
        page.get_by_text("2 of", exact=False).first.wait_for(timeout=120_000)
        page.locator("li button[aria-expanded]").first.wait_for(timeout=120_000)  # a hypothesis board row
        shot(page, "03_run_view")

        page.get_by_role("button", name="Masks").click()
        hovered = hover_a_cell(page)
        shot(page, "04_run_masks_hover" if hovered else "04_run_masks")
        page.get_by_role("button", name="Masks").click()

        page.get_by_role("heading", name="Reflection").wait_for(timeout=240_000)
        page.wait_for_timeout(1500)  # after the ~1.2 s unblinding sequence
        page.mouse.move(5, 5)
        shot(page, "05_unblinding")

        page.goto(f"{args.base}/#/dossier/{replay['run_id']}")
        page.get_by_role("heading", name="Experiments run").wait_for()
        shot(page, "06_dossier")
        page.set_viewport_size({"width": 1280, "height": 3400})
        shot(page, "07_dossier_long")
        page.set_viewport_size(VIEWPORT)

        page.goto(f"{args.base}/#/benchmark")
        page.get_by_role("heading", name="Benchmark", exact=True).wait_for()
        page.get_by_text("compound level").wait_for()
        shot(page, "08_benchmark")

        page.goto(f"{args.base}/#/run/run_000000000000")
        page.get_by_text("This run is not on the server.").wait_for(timeout=20_000)
        shot(page, "09_run_not_found")

        browser.close()
    # the not-found screen's 404 is expected; anything else is a bug
    errors = [e for e in errors if "404" not in e]
    print(f"browser console errors: {len(errors)}")
    for e in errors:
        print("  ", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
