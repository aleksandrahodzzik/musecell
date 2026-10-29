"""Fetch the BBBC021 subset MuseCell needs (spec §2).

Stage `plan` (default): download the 3 metadata CSVs into DATA_DIR/metadata,
print their real columns + sample rows, select every field of the 38
MoA-annotated compounds (all concentrations) plus the DMSO wells on the same
plates, read each needed plate zip's central directory (a few KB, via HTTP
Range) and print the plan with exact byte counts. Writes DATA_DIR/plan.json and
DATA_DIR/plan_fields.csv. Downloads no images.

Stage `fetch`: run the plan, then read only the planned TIFFs from the remote
zips with HTTP Range (remotezip) into DATA_DIR/raw/<plate>/. If the server
refuses Range, falls back to full zip -> extract -> delete. Resumable and
idempotent: files already present with the right size are skipped; members are
CRC32-checked by zipfile and size-checked, then written atomically.

Usage:
  python scripts/fetch_bbbc021.py plan
  python scripts/fetch_bbbc021.py fetch [--workers 8]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
from remotezip import RangeNotSupported, RemoteZip
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_paths import data_dir  # noqa: E402

BASE_URL = "https://data.broadinstitute.org/bbbc/BBBC021/"
CSV_NAMES = {
    "image": "BBBC021_v1_image.csv",
    "compound": "BBBC021_v1_compound.csv",
    "moa": "BBBC021_v1_moa.csv",
}
CHANNELS = ("DAPI", "Tubulin", "Actin")
CONTROL = "DMSO"
SIZE_CAP_GB = 12.0
UNLABELLED_SITES = 2
CONTROL_SITES = 2
TIMEOUT = 60
RETRIES = 4
SITE_RE = re.compile(r"_s(\d+)_")


def zip_name(plate: str) -> str:
    return f"BBBC021_v1_images_{plate}.zip"


def zip_url(plate: str) -> str:
    return BASE_URL + zip_name(plate)


# ---------------------------------------------------------------- metadata


def download_metadata(meta_dir: Path) -> dict[str, Path]:
    """Download the CSVs; skip any whose local size already matches the server's."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, name in CSV_NAMES.items():
        dest = meta_dir / name
        remote_size = int(requests.head(BASE_URL + name, timeout=TIMEOUT).headers.get("content-length", -1))
        if dest.exists() and dest.stat().st_size == remote_size:
            print(f"  {name}: up to date ({remote_size:,} bytes)")
        else:
            resp = requests.get(BASE_URL + name, timeout=TIMEOUT)
            resp.raise_for_status()
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(resp.content)
            if remote_size >= 0 and tmp.stat().st_size != remote_size:
                raise SystemExit(f"{name}: size mismatch ({tmp.stat().st_size} != {remote_size})")
            tmp.replace(dest)
            print(f"  {name}: downloaded ({dest.stat().st_size:,} bytes)")
        paths[key] = dest
    return paths


def load_tables(paths: dict[str, Path]) -> dict[str, pd.DataFrame]:
    return {key: pd.read_csv(path) for key, path in paths.items()}


def show_tables(tables: dict[str, pd.DataFrame]) -> None:
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 40):
        for key, df in tables.items():
            print(f"\n=== {CSV_NAMES[key]}: {len(df):,} rows")
            print("columns:", list(df.columns))
            print(df.head(3).to_string(index=False))


# ---------------------------------------------------------------- plan


def select_fields(image: pd.DataFrame, moa: pd.DataFrame) -> pd.DataFrame:
    """One row per field: annotated compounds at all concentrations + same-plate DMSO."""
    annotated = moa[moa["compound"] != CONTROL]
    compound_moa = annotated.groupby("compound")["moa"].agg(lambda s: sorted(set(s)))
    ambiguous = compound_moa[compound_moa.map(len) > 1]
    if len(ambiguous):
        raise SystemExit(f"compounds with >1 MoA in moa.csv: {ambiguous.to_dict()}")
    compound_moa = compound_moa.map(lambda v: v[0])
    annotated_pairs = {(c, round(x, 6)) for c, x in zip(annotated["compound"], annotated["concentration"])}

    df = image.rename(
        columns={
            "Image_Metadata_Plate_DAPI": "plate",
            "Image_Metadata_Well_DAPI": "well",
            "Replicate": "replicate",
            "Image_Metadata_Compound": "compound",
            "Image_Metadata_Concentration": "concentration_uM",
        }
    )
    treated = df["compound"].isin(compound_moa.index)
    plates = set(df.loc[treated, "plate"])
    control = (df["compound"] == CONTROL) & df["plate"].isin(plates)
    df = df[treated | control].copy()

    df["role"] = df["compound"].map(lambda c: "control" if c == CONTROL else "treated")
    df["moa"] = df["compound"].map(compound_moa)
    df["moa_annotated_conc"] = [
        (c, round(x, 6)) in annotated_pairs for c, x in zip(df["compound"], df["concentration_uM"])
    ]
    df["site"] = df["Image_FileName_DAPI"].map(lambda f: int(SITE_RE.search(f).group(1)))
    for ch in CHANNELS:
        if not (df[f"Image_PathName_{ch}"] == df["Image_PathName_DAPI"]).all():
            raise SystemExit(f"{ch} images are not in the same folder as DAPI; plan logic needs updating")
        df[f"file_{ch.lower()}"] = df[f"Image_FileName_{ch}"]
    df["zip_dir"] = df["Image_PathName_DAPI"].str.split("/").str[-1]
    cols = ["plate", "well", "site", "replicate", "compound", "concentration_uM", "moa", "moa_annotated_conc",
            "role", "zip_dir"] + [f"file_{ch.lower()}" for ch in CHANNELS]
    return df[cols].sort_values(["plate", "well", "site"]).reset_index(drop=True)


def apply_subset(fields: pd.DataFrame) -> pd.DataFrame:
    """Subset "G" (approved 2026-10-28, see DECISIONS.md) to stay under the 12 GB cap.

    - Plate positive controls dropped: wells of a compound on plates where that
      compound has no other concentration (taxol 0.3 uM sits on every plate).
    - MoA-labelled compound-concentrations keep all sites; unlabelled ones keep
      sites 1..UNLABELLED_SITES; DMSO wells keep sites 1..CONTROL_SITES.
    """
    treated = fields["role"] == "treated"
    n_conc = fields[treated].groupby(["plate", "compound"])["concentration_uM"].nunique()
    series = {key for key, n in n_conc.items() if n > 1}
    in_series = [(p, c) in series for p, c in zip(fields["plate"], fields["compound"])]
    keep_site = (
        (treated & fields["moa_annotated_conc"])
        | (treated & ~fields["moa_annotated_conc"] & (fields["site"] <= UNLABELLED_SITES))
        | (~treated & (fields["site"] <= CONTROL_SITES))
    )
    keep = keep_site & (~treated | pd.Series(in_series, index=fields.index))
    return fields[keep].reset_index(drop=True)


def zip_listing(plate: str, cache_dir: Path) -> dict:
    """{member basename: [member name, file_size, compress_size]} + zip size; cached on disk."""
    cache = cache_dir / f"{plate}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    zip_size = int(requests.head(zip_url(plate), timeout=TIMEOUT).headers["content-length"])
    with RemoteZip(zip_url(plate), timeout=TIMEOUT) as rz:
        members = {Path(i.filename).name: [i.filename, i.file_size, i.compress_size]
                   for i in rz.infolist() if not i.is_dir()}
    listing = {"plate": plate, "zip_size": zip_size, "members": members}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(listing))
    return listing


def build_plan(fields: pd.DataFrame, root: Path) -> dict:
    plates = sorted(fields["plate"].unique())
    listings = {p: zip_listing(p, root / "zip_listings") for p in tqdm(plates, desc="zip listings", unit="plate")}
    files, missing = [], []
    for row in fields.itertuples():
        for ch in CHANNELS:
            name = getattr(row, f"file_{ch.lower()}")
            entry = listings[row.plate]["members"].get(name)
            if entry is None:
                missing.append(f"{row.plate}/{name}")
                continue
            files.append({"plate": row.plate, "member": entry[0], "file_size": entry[1], "compress_size": entry[2]})
    if missing:
        raise SystemExit(f"{len(missing)} planned TIFFs not found in their zips, e.g. {missing[:3]}")
    per_plate = pd.DataFrame(files).groupby("plate").agg(
        tiffs=("member", "size"), compressed=("compress_size", "sum"), raw=("file_size", "sum"))
    per_plate["zip_size"] = [listings[p]["zip_size"] for p in per_plate.index]
    return {"plates": plates, "files": files, "per_plate": per_plate}


def print_plan(fields: pd.DataFrame, plan: dict) -> None:
    gb = 1e9
    pp = plan["per_plate"]
    treated = fields[fields["role"] == "treated"]
    control = fields[fields["role"] == "control"]
    wells = fields.groupby(["plate", "well"]).ngroups
    n_conc = treated.groupby("compound")["concentration_uM"].nunique()
    print("\n=== Subset plan")
    print(f"compounds: {treated['compound'].nunique()} annotated, MoA classes: {treated['moa'].nunique()}")
    print(f"concentrations per compound: min {n_conc.min()}, median {int(n_conc.median())}, max {n_conc.max()}")
    print(f"compound-concentrations: {treated.groupby(['compound', 'concentration_uM']).ngroups} total, "
          f"{treated[treated['moa_annotated_conc']].groupby(['compound', 'concentration_uM']).ngroups} with MoA label in moa.csv")
    print(f"wells: {wells} ({treated.groupby(['plate', 'well']).ngroups} treated, "
          f"{control.groupby(['plate', 'well']).ngroups} DMSO)")
    print(f"fields: {len(fields)} ({len(treated)} treated, {len(control)} DMSO); TIFFs: {len(plan['files'])}")
    print(f"plates: {len(plan['plates'])}")
    with pd.option_context("display.width", 200):
        view = pp.assign(compressed_MB=(pp["compressed"] / 1e6).round(1), zip_MB=(pp["zip_size"] / 1e6).round(0))
        print(view[["tiffs", "compressed_MB", "zip_MB"]].to_string())
    ranged = pp["compressed"].sum() / gb
    full = pp["zip_size"].sum() / gb
    raw = pp["raw"].sum() / gb
    print(f"\nDownload, ranged reads (remotezip): {ranged:.2f} GB")
    print(f"Download, full-zip fallback (peak disk = 1 zip at a time): {full:.2f} GB")
    print(f"Raw TIFFs on disk after extraction: {raw:.2f} GB")
    print(f"Preprocessed float32 .npy (3 channels, 2x TIFF size): ~{2 * raw:.2f} GB")
    if ranged > SIZE_CAP_GB:
        print(f"!! ranged plan exceeds the {SIZE_CAP_GB:.0f} GB cap — ask before fetching")


def cmd_plan(full: bool) -> dict:
    root = data_dir()
    print(f"DATA_DIR = {root}\n\n=== Metadata")
    tables = load_tables(download_metadata(root / "metadata"))
    show_tables(tables)
    fields = select_fields(tables["image"], tables["moa"])
    if not full:
        fields = apply_subset(fields)
    plan = build_plan(fields, root)
    print_plan(fields, plan)
    fields.to_csv(root / "plan_fields.csv", index=False)
    (root / "plan.json").write_text(json.dumps({"plates": plan["plates"], "files": plan["files"]}, indent=1))
    print(f"\nWrote {root / 'plan_fields.csv'} and {root / 'plan.json'}")
    return plan


# ---------------------------------------------------------------- fetch


def raw_path(root: Path, plate: str, member: str) -> Path:
    return root / "raw" / plate / Path(member).name


def is_done(path: Path, size: int) -> bool:
    return path.exists() and path.stat().st_size == size


def write_member(zf, entry: dict, dest: Path) -> None:
    """Read one member (zipfile verifies CRC32 at EOF), write atomically, check size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with zf.open(entry["member"]) as src:
        data = src.read()
    if len(data) != entry["file_size"]:
        raise OSError(f"{entry['member']}: got {len(data)} bytes, expected {entry['file_size']}")
    tmp.write_bytes(data)
    tmp.replace(dest)


def fetch_plate_ranged(plate: str, todo: list[dict], root: Path, bar: tqdm) -> None:
    for attempt in range(1, RETRIES + 1):
        try:
            with RemoteZip(zip_url(plate), timeout=TIMEOUT) as rz:
                for entry in todo:
                    dest = raw_path(root, plate, entry["member"])
                    if is_done(dest, entry["file_size"]):
                        continue
                    write_member(rz, entry, dest)
                    bar.update(entry["compress_size"])
            return
        except RangeNotSupported:
            raise
        except Exception as exc:  # network hiccups, CRC errors: reopen and resume
            if attempt == RETRIES:
                raise
            tqdm.write(f"  {plate}: {type(exc).__name__}: {exc} — retry {attempt}/{RETRIES - 1}")
            time.sleep(2**attempt)


def fetch_plate_full_zip(plate: str, todo: list[dict], root: Path, bar: tqdm) -> None:
    """Fallback: download the whole zip, extract the needed members, delete the zip."""
    zip_dir = root / "zips"
    zip_dir.mkdir(parents=True, exist_ok=True)
    part = zip_dir / (zip_name(plate) + ".part")
    with requests.get(zip_url(plate), stream=True, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("content-length", -1))
        with open(part, "wb") as out:
            for chunk in resp.iter_content(1 << 20):
                out.write(chunk)
    if expected >= 0 and part.stat().st_size != expected:
        part.unlink()
        raise OSError(f"{zip_name(plate)}: size mismatch")
    with zipfile.ZipFile(part) as zf:
        for entry in todo:
            dest = raw_path(root, plate, entry["member"])
            if not is_done(dest, entry["file_size"]):
                write_member(zf, entry, dest)
            bar.update(entry["compress_size"])
    part.unlink()


def cmd_fetch(workers: int, full: bool) -> None:
    plan = cmd_plan(full)
    root = data_dir()
    total_gb = sum(f["compress_size"] for f in plan["files"]) / 1e9
    if total_gb > SIZE_CAP_GB:
        raise SystemExit(f"plan is {total_gb:.2f} GB > {SIZE_CAP_GB} GB cap; refusing to fetch")
    todo: dict[str, list[dict]] = {}
    for f in plan["files"]:
        if not is_done(raw_path(root, f["plate"], f["member"]), f["file_size"]):
            todo.setdefault(f["plate"], []).append(f)
    n_todo = sum(map(len, todo.values()))
    print(f"\n=== Fetch: {n_todo} of {len(plan['files'])} TIFFs missing across {len(todo)} plates")
    if not todo:
        return
    fetcher = fetch_plate_ranged
    bar = tqdm(total=sum(f["compress_size"] for v in todo.values() for f in v), unit="B", unit_scale=True,
               desc="TIFFs", smoothing=0.05)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetcher, p, files, root, bar): p for p, files in todo.items()}
        failed = []
        for fut in as_completed(futures):
            plate = futures[fut]
            try:
                fut.result()
            except RangeNotSupported:
                tqdm.write(f"  {plate}: server refused Range — falling back to full zip")
                try:
                    fetch_plate_full_zip(plate, todo[plate], root, bar)
                except Exception as exc:
                    failed.append(plate)
                    tqdm.write(f"  {plate}: FAILED ({exc})")
            except Exception as exc:
                failed.append(plate)
                tqdm.write(f"  {plate}: FAILED ({type(exc).__name__}: {exc})")
    bar.close()
    if failed:
        raise SystemExit(f"{len(failed)} plates failed: {failed}. Re-run to resume.")
    missing = [f for f in plan["files"] if not is_done(raw_path(root, f["plate"], f["member"]), f["file_size"])]
    if missing:
        raise SystemExit(f"{len(missing)} TIFFs still missing after fetch. Re-run to resume.")
    print(f"All {len(plan['files'])} TIFFs present and size-checked in {root / 'raw'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="plan the full subset instead of subset G (over the cap)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("plan", help="download metadata and print the subset plan (default)")
    fetch = sub.add_parser("fetch", help="plan, then download the planned TIFFs (resumable)")
    fetch.add_argument("--workers", type=int, default=8, help="plates fetched in parallel")
    args = parser.parse_args()
    if args.cmd == "fetch":
        cmd_fetch(args.workers, args.full)
    else:
        cmd_plan(args.full)


if __name__ == "__main__":
    main()
