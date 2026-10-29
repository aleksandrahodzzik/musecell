"""Hero GIF for the README: a replay of a recorded real run, filmed with Playwright and converted with ffmpeg.

Needs the backend running with the built frontend (see scripts/screenshots.py). No API credits are spent: the run
is a replay of a recorded live run. ffmpeg comes from the `imageio-ffmpeg` package (requirements-dev.txt).

    python scripts/record_hero_gif.py [--base http://127.0.0.1:8787] [--run run_19b4ee810120] [--speed 6]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "hero.gif"
VIEWPORT = {"width": 1280, "height": 720}
MAX_BYTES = 8 * 1024 * 1024


def api(base: str, path: str, method: str = "GET") -> dict | list:
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def film(base: str, run: str, speed: float, video_dir: Path) -> Path:
    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel)
                break
            except Exception:  # noqa: BLE001 - try the next installed browser
                continue
        if browser is None:
            raise SystemExit("Neither Edge nor Chrome could be launched.")
        context = browser.new_context(viewport=VIEWPORT, record_video_dir=str(video_dir), record_video_size=VIEWPORT)
        page = context.new_page()
        page.goto(f"{base}/#/")
        page.get_by_role("radio").first.wait_for()
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="Watch a recorded run").click()
        page.get_by_role("button", name="Replay").first.wait_for()
        page.wait_for_timeout(1200)

        replay = api(base, f"/api/runs/{run}/replay?speed={speed}", "POST")
        page.goto(f"{base}/#/run/{replay['run_id']}")
        page.get_by_role("heading", name="Reflection").wait_for(timeout=300_000)
        page.mouse.move(5, 5)
        page.wait_for_timeout(4000)  # hold on the unblinding verdict
        video = page.video
        context.close()
        browser.close()
        return Path(video.path())


def to_gif(src: Path, dst: Path, width: int, fps: int, trim_start: float) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{trim_start}", "-i", str(src), "-vf", vf,
                    "-loop", "0", str(dst)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--run", default="run_19b4ee810120", help="recorded run to replay")
    ap.add_argument("--speed", type=float, default=6.0)
    ap.add_argument("--trim-start", type=float, default=0.5, help="seconds cut from the start (blank first frames)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        video = film(args.base, args.run, args.speed, Path(tmp))
        kept = ROOT / "docs" / "hero.webm"  # gitignored; kept for the demo video edit
        shutil.copyfile(video, kept)
        # shrink until it fits the 8 MB README budget
        for width, fps in ((960, 10), (880, 8), (800, 8), (720, 6)):
            to_gif(kept, OUT, width, fps, args.trim_start)
            size = OUT.stat().st_size
            print(f"{OUT.relative_to(ROOT)}: {width}px, {fps} fps, {size / 1e6:.2f} MB")
            if size <= MAX_BYTES:
                return 0
    print("GIF is still over 8 MB.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
