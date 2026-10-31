"""Sharp screen clips for the submission video, recorded from a running MuseCell server (replays only, no API).

Frames come from Chrome's screencast (CDP `Page.startScreencast`, JPEG q95) at their real timestamps, not from
Playwright's built-in recorder (which is soft at 1080p). The page is 1536x720 CSS px at device scale 1.25, so
each frame is 1920x900. ffmpeg (bundled with imageio-ffmpeg) writes two files per scene:
  raw/<scene>.mp4     1920x900, the screen only
  framed/<scene>.mp4  1920x1080, the screen on top and a dark 180 px band below it for burned-in subtitles

Scenes: 01_landing, 02_slides, 03_run (replay at 3x to the reflection), 04_microscope, 05_board_and_costs,
06_dossier, 07_benchmark, 08_github. Nothing here starts a live run.

Usage (server with the data running on 8787):
  python scripts/record_video_clips.py --out "<folder>" [--run run_9afbd903a2d2] [--only 03_run 06_dossier]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.async_api import Page, async_playwright

CSS = {"width": 1536, "height": 720}
SCALE = 1.25  # -> 1920 x 900 device pixels
W, H, BAND = 1920, 900, 180
BAND_COLOR = "0x0B0A09"  # the app's instrument night
REPO = "https://github.com/aleksandrahodzzik/musecell"


def api(base: str, path: str, method: str = "GET"):
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


async def smooth_scroll(page: Page, total: int, seconds: float) -> None:
    steps = max(1, int(seconds * 30))
    for _ in range(steps):
        await page.mouse.wheel(0, total / steps)
        await page.wait_for_timeout(1000 / 30)


async def blur(page: Page) -> None:
    await page.evaluate("document.activeElement && document.activeElement.blur()")


async def replay(page: Page, base: str, run: str, speed: float) -> str:
    rid = api(base, f"/api/runs/{run}/replay?speed={speed}", "POST")["run_id"]
    await page.goto(f"{base}/#/run/{rid}")
    await page.get_by_text("Replay of a live run").first.wait_for()
    return rid


async def finished_replay(page: Page, base: str, run: str) -> str:
    rid = await replay(page, base, run, 20)
    await page.get_by_role("heading", name="Reflection").wait_for(timeout=240_000)
    await page.wait_for_timeout(1800)
    return rid


# --- scenes: each gets the page already prepared by its `setup`, then `act` runs while recording ---------------

async def landing_setup(page, base, run):
    await page.goto(f"{base}/#/")
    await page.get_by_text("right mechanism from").first.wait_for()
    await page.wait_for_timeout(1500)


async def landing_act(page, base, run):
    await page.mouse.move(W / SCALE * 0.3, 300)
    await page.wait_for_timeout(2500)
    for stain in ("Tubulin", "Actin", "DNA"):
        await page.get_by_role("button", name=stain, exact=True).first.click()
        await page.wait_for_timeout(900)
        await page.get_by_role("button", name=stain, exact=True).first.click()
        await page.wait_for_timeout(500)
    for i in range(40):  # pan the eyepiece with the pointer
        await page.mouse.move(1180 + 60 * (i % 20) / 20, 260 + 40 * (i // 20))
        await page.wait_for_timeout(40)
    await page.mouse.move(700, 500)
    await smooth_scroll(page, 2600, 12)
    await page.wait_for_timeout(2000)


async def slides_setup(page, base, run):
    await page.goto(f"{base}/#/")
    await page.get_by_role("radio").first.wait_for()
    await page.wait_for_timeout(800)


async def slides_act(page, base, run):
    await page.get_by_role("button", name="Choose a specimen").first.click()
    await page.wait_for_timeout(2600)
    radios = page.get_by_role("radio")
    for i in (0, 3, 7):
        if await radios.count() > i:
            await radios.nth(i).hover()
            await page.wait_for_timeout(500)
            await radios.nth(i).click()
            await page.wait_for_timeout(700)
    await blur(page)
    await page.get_by_role("button", name="Watch a recorded run").first.click()
    await page.wait_for_timeout(3500)


async def run_setup(page, base, run):
    await page.goto(f"{base}/#/")
    await page.wait_for_timeout(500)


async def run_act(page, base, run):
    await replay(page, base, run, 3)
    await page.mouse.move(5, 5)
    await page.get_by_role("heading", name="Reflection").wait_for(timeout=300_000)
    await page.wait_for_timeout(4500)


async def microscope_setup(page, base, run):
    await finished_replay(page, base, run)


async def microscope_act(page, base, run):
    await page.get_by_role("tab", name="Microscope").click()
    await blur(page)
    await page.wait_for_timeout(1500)
    for key in ("1", "1", "2", "2", "3", "3"):  # each stain off and on again
        await page.keyboard.press(key)
        await page.wait_for_timeout(650)
    await page.keyboard.press("m")
    await page.wait_for_timeout(900)
    pane = page.get_by_role("img", name="Treated").first
    box = await pane.bounding_box()
    if box:
        for fx, fy in ((0.3, 0.35), (0.45, 0.45), (0.55, 0.5), (0.62, 0.42)):
            await page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy, steps=12)
            await page.wait_for_timeout(1100)
    await page.mouse.move(5, 5)
    await page.keyboard.press("m")
    await page.keyboard.press("d")
    await page.wait_for_timeout(1800)
    await page.keyboard.press("d")
    await page.keyboard.press("ArrowRight")
    await page.wait_for_timeout(1500)


async def board_setup(page, base, run):
    await finished_replay(page, base, run)


async def board_act(page, base, run):
    await page.get_by_role("tab", name="Over time").click()
    await page.wait_for_timeout(1200)
    box = await page.get_by_role("slider", name="Board update").bounding_box()
    if box:  # scrub through the agent's board updates with the pointer
        for i in range(31):
            await page.mouse.move(box["x"] + box["width"] * (0.03 + 0.94 * i / 30), box["y"] + box["height"] * 0.5)
            await page.wait_for_timeout(70)
    await page.wait_for_timeout(800)
    await page.get_by_role("tab", name="Now").click()
    await page.wait_for_timeout(800)
    for node in ("Scientist,", "PI reviewer,"):  # circuit nodes: aria-label "<name>, <model>: <stats>"
        btn = page.get_by_role("button", name=node).first
        if await btn.count():
            await btn.click()
            await page.wait_for_timeout(2800)
            await btn.click()  # the same button closes the per-call ledger
            await page.wait_for_timeout(500)
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(800)


async def dossier_setup(page, base, run):
    page.dossier_id = await finished_replay(page, base, run)  # type: ignore[attr-defined]
    await page.goto(f"{base}/#/dossier/{page.dossier_id}")  # type: ignore[attr-defined]
    await page.get_by_role("heading", name="Experiments run").wait_for()
    await page.wait_for_timeout(1200)


async def dossier_act(page, base, run):
    await page.mouse.move(760, 400)
    await page.wait_for_timeout(1500)
    height = await page.evaluate("document.documentElement.scrollHeight")
    await smooth_scroll(page, height - CSS["height"], 18)
    await page.wait_for_timeout(1500)


async def bench_setup(page, base, run):
    await page.goto(f"{base}/#/benchmark")
    await page.get_by_text("Fixed rule").first.wait_for()
    await page.wait_for_timeout(1200)


async def bench_act(page, base, run):
    await page.mouse.move(760, 400)
    await page.wait_for_timeout(3500)
    await smooth_scroll(page, 1700, 14)
    await page.wait_for_timeout(1500)


async def github_setup(page, base, run):
    await page.goto(REPO)
    await page.wait_for_timeout(2500)


async def github_act(page, base, run):
    await page.mouse.move(760, 400)
    await page.wait_for_timeout(1500)
    await smooth_scroll(page, 900, 8)
    await page.wait_for_timeout(1500)


SCENES = {
    "01_landing": (landing_setup, landing_act),
    "02_slides": (slides_setup, slides_act),
    "03_run": (run_setup, run_act),
    "04_microscope": (microscope_setup, microscope_act),
    "05_board_and_costs": (board_setup, board_act),
    "06_dossier": (dossier_setup, dossier_act),
    "07_benchmark": (bench_setup, bench_act),
    "08_github": (github_setup, github_act),
}


# --- capture and encode ---------------------------------------------------------------------------------------

async def capture(page: Page, act, base: str, run: str, frames_dir: Path) -> list[tuple[Path, float]]:
    cdp = await page.context.new_cdp_session(page)
    frames: list[tuple[Path, float]] = []
    acks: list[asyncio.Task] = []
    stopped = False

    async def ack(session_id: int) -> None:
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:  # noqa: BLE001 - a frame acknowledged after the stop is harmless
            pass

    def on_frame(params: dict) -> None:
        if stopped or not frames_dir.exists():
            return
        path = frames_dir / f"f{len(frames):06d}.jpg"
        try:
            path.write_bytes(base64.b64decode(params["data"]))
        except OSError:
            return
        frames.append((path, params["metadata"]["timestamp"]))
        acks.append(asyncio.ensure_future(ack(params["sessionId"])))

    cdp.on("Page.screencastFrame", on_frame)
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": W, "maxHeight": H,
                                            "everyNthFrame": 1})
    try:
        await act(page, base, run)
    finally:  # stop the screencast even when a scene fails, so the next scene starts clean
        end = time.time()
        stopped = True
        await cdp.send("Page.stopScreencast")
        await asyncio.gather(*acks)
        await cdp.detach()
    # durations from the real frame timestamps; the last frame lasts until the scene ended
    return [(p, max(0.001, (frames[i + 1][1] if i + 1 < len(frames) else end) - t)) for i, (p, t) in enumerate(frames)]


def encode(frames: list[tuple[Path, float]], work: Path, raw: Path, framed: Path) -> float:
    lst = work / "list.txt"
    lines = []
    for p, d in frames:
        lines += [f"file '{p.as_posix()}'", f"duration {d:.4f}"]
    lines.append(f"file '{frames[-1][0].as_posix()}'")  # concat demuxer: the last entry needs repeating
    lst.write_text("\n".join(lines), encoding="utf-8")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base_vf = f"scale={W}:{H}:flags=lanczos:in_range=full:out_range=tv,fps=30,format=yuv420p"
    common = ["-c:v", "libx264", "-crf", "15", "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    for out, vf in ((raw, base_vf), (framed, f"{base_vf},pad={W}:{H + BAND}:0:0:color={BAND_COLOR}")):
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", vf, *common, str(out)], check=True)
    return sum(d for _, d in frames)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--run", default="run_9afbd903a2d2")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=None, choices=list(SCENES))
    args = ap.parse_args()
    (args.out / "raw").mkdir(parents=True, exist_ok=True)
    (args.out / "framed").mkdir(parents=True, exist_ok=True)
    failed = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, (setup, act) in SCENES.items():
            if args.only and name not in args.only:
                continue
            ctx = await browser.new_context(viewport=CSS, device_scale_factor=SCALE)
            page = await ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            work = Path(tempfile.mkdtemp(prefix=f"clip_{name}_"))
            try:
                await setup(page, args.base, args.run)
                frames = await capture(page, act, args.base, args.run, work)
                secs = encode(frames, work, args.out / "raw" / f"{name}.mp4", args.out / "framed" / f"{name}.mp4")
                own = [e for e in errors if args.base in e or "github" not in name]
                print(f"{name}: {secs:5.1f} s, {len(frames)} frames, console errors {len(own)}", flush=True)
            except Exception as exc:  # noqa: BLE001 - report and go on with the next scene
                failed.append(name)
                print(f"{name}: FAILED {type(exc).__name__}: {str(exc).splitlines()[0]}", flush=True)
            finally:
                try:
                    await ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                shutil.rmtree(work, ignore_errors=True)
        await browser.close()
    print("failed:", failed or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
