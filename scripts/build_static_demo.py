"""Static public demo (GitHub Pages): the replay-only app as plain files, no server, never asleep.

Asks the real FastAPI app (in-process, replay-only: no key, no BBBC021 data, backend/.env not read) for every
answer the replay UI needs and writes it under <dist>/data/, next to a frontend built with VITE_STATIC=1:
  health, specimens, recorded runs, gallery, eval results/baseline; per recorded demo run its state snapshot,
  dossier (.json/.md) and recorded events; the shipped thumbnails as images/.
The browser then replays the recorded events itself (frontend/src/lib/staticDemo.ts).

Usage (from the repo root):
  cd frontend; $env:VITE_STATIC="1"; $env:VITE_BASE="/musecell/"; npm run build; cd ..
  python scripts/build_static_demo.py [--dist frontend/dist]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import Settings  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402

STATIC_NOTE = ("This is the static demo: there is no server here, so recorded live runs are replayed in your "
               "browser. Live runs need your own Meta Model API key and the BBBC021 subset (README quickstart).")


def main_() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dist", type=Path, default=ROOT / "frontend" / "dist")
    args = p.parse_args()
    if not (args.dist / "index.html").exists():
        print(f"{args.dist} has no index.html; build the frontend with VITE_STATIC=1 first.")
        return 1
    out = args.dist / "data"
    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)
    (out / "eval").mkdir()

    with tempfile.TemporaryDirectory() as tmp:
        # A manager with no key and no data (never reads backend/.env), so the snapshot is exactly replay-only.
        manager = RunManager(Settings(_env_file=None, data_dir=tmp), runs_dir=Path(tmp) / "runs")
        main.app.state.manager = manager
        with TestClient(main.app) as client:
            def get(path: str):
                r = client.get(path)
                r.raise_for_status()
                return r

            def save(name: str, obj) -> None:
                (out / name).write_text(json.dumps(obj), encoding="utf-8")

            health = get("/api/health").json()
            health.update(data_error=STATIC_NOTE, model_error=None)
            save("health.json", health)
            save("specimens.json", get("/api/specimens").json())
            recorded = [r for r in get("/api/recorded").json() if r.get("source") != "local"]
            save("recorded.json", recorded)
            save("gallery.json", get("/api/gallery").json())
            for name in ("results", "baseline"):
                save(f"eval/{name}.json", get(f"/api/eval/{name}").json())
            for r in recorded:
                rid = r["run_id"]
                save(f"runs/{rid}.json", get(f"/api/runs/{rid}").json())
                save(f"runs/{rid}.dossier.json", get(f"/api/runs/{rid}/dossier.json").json())
                (out / "runs" / f"{rid}.dossier.md").write_text(get(f"/api/runs/{rid}/dossier.md").text,
                                                                 encoding="utf-8")
                events = [json.loads(line) for line in (main.DEMO_RUNS_DIR / f"{rid}.jsonl")
                          .read_text(encoding="utf-8").splitlines() if line.strip()]
                save(f"runs/{rid}.events.json", events)
        main.app.state.manager = None

    shutil.copytree(main.DEMO_THUMBS, out / "images")
    # GitHub Pages: serve files as they are (no Jekyll), and fall back to the app for unknown paths.
    (args.dist / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copyfile(args.dist / "index.html", args.dist / "404.html")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"static demo data: {len(recorded)} recorded runs, {len(list((out / 'images').glob('*.png')))} images, "
          f"{size:.1f} MB -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
