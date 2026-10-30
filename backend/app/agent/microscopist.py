"""Microscopist: Muse Spark vision describes treated fields against a same-plate DMSO control (spec §4).

It gets pixels (composite + one grayscale image per channel, for each treated field and its DMSO
control) *and* our measured top-deviating features, and must say which visual findings agree with the
numbers. Output is strict structured JSON (schema below). The prompt names fields by opaque id only.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image

from app.agent.guardrails import UNKNOWN_MOA

COMPOSITE_MAX_SIDE = 1024  # coordinates of notable cells refer to this image
CHANNEL_MAX_SIDE = 512
CHANNELS = ("dna", "tubulin", "actin")

SYSTEM = (
    "You are a microscopist reading fluorescence images of MCF-7 breast-cancer cells, 24 h after treatment "
    "with an unidentified compound. Stains: DNA (DAPI) shown blue in the composite, beta-tubulin green, F-actin "
    "red; each channel is also given as a grayscale image. Every treated field is shown next to a DMSO "
    "(vehicle) control field from the same plate, with identical contrast settings. Describe only what is "
    "visible. You also receive measured features (robust z-scores vs. the plate's DMSO wells); for each "
    "observation, state whether it agrees with those measurements. If an observation is not covered by a "
    "measurement, set agrees_with_measurements to false and say so in the finding. Candidate mechanisms "
    "must come from the given list; use 'unknown' when the images do not support any. Do not guess the "
    "compound. Be concise and concrete."
)


def response_schema(moa_classes: list[str], field_ids: list[str]) -> dict:
    """Strict schema (spec §4). Enums keep MoA names and field ids to what exists."""
    def obj(props: dict) -> dict:
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    return obj({
        "cell_density": {"type": "string", "enum": ["lower", "similar", "higher"],
                         "description": "Cell density of the treated fields vs the DMSO control."},
        "observations": {"type": "array", "items": obj({
            "channel": {"type": "string", "enum": ["dna", "tubulin", "actin", "composite"]},
            "finding": {"type": "string"},
            "agrees_with_measurements": {"type": "boolean"},
        })},
        "notable_cells": {"type": "array", "items": obj({
            "field_id": {"type": "string", "enum": field_ids},
            "x": {"type": "integer", "description": "pixel x in that field's composite image"},
            "y": {"type": "integer", "description": "pixel y in that field's composite image"},
            "why": {"type": "string"},
        })},
        "candidate_mechanisms": {"type": "array", "items": obj({
            "moa": {"type": "string", "enum": [*moa_classes, UNKNOWN_MOA]},
            "why": {"type": "string"},
        })},
        "uncertainty": {"type": "string"},
    })


def png_data_url(path: Path, max_side: int) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as im:
        im = im.convert("RGB") if im.mode not in ("L", "RGB") else im.copy()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        size = im.size
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), size


def field_images(label: str, field_id: str, paths: dict[str, Path], channels: bool = True) -> list[dict]:
    """Content parts for one field: a caption, the composite, then each channel (unless channels=False)."""
    url, (w, h) = png_data_url(paths["composite"], COMPOSITE_MAX_SIDE)
    parts = [{"type": "text", "text": f"{label} field {field_id}: composite ({w}x{h} px)."},
             {"type": "image_url", "image_url": {"url": url}}]
    for ch in CHANNELS if channels else ():
        url, _ = png_data_url(paths[ch], CHANNEL_MAX_SIDE)
        parts += [{"type": "text", "text": f"{label} field {field_id}: {ch} channel (grayscale)."},
                  {"type": "image_url", "image_url": {"url": url}}]
    return parts


def build_messages(treated: list[tuple[str, dict[str, Path]]], controls: list[tuple[str, dict[str, Path]]],
                   measurements: dict, moa_classes: list[str], channels: bool = True) -> list[dict]:
    """treated / controls: (field_id, {composite, dna, tubulin, actin: png path}). channels=False sends only the
    composites (a provider with a small per-request image cap) and tells the model so."""
    content: list[dict] = [{"type": "text", "text": (
        "Measured features of the treated wells (robust z vs same-plate DMSO; |z| > 3 is a strong change):\n"
        + json.dumps(measurements, indent=1)
        + f"\n\nMoA classes you may name: {moa_classes} (or '{UNKNOWN_MOA}').")}]
    for fid, paths in treated:
        content += field_images("Treated", fid, paths, channels)
    for fid, paths in controls:
        content += field_images("DMSO control", fid, paths, channels)
    note = "" if channels else (" Only the composites are given this time (no separate grayscale channels): read "
                                 "each stain from its colour.")
    content.append({"type": "text", "text": "Compare the treated fields to the DMSO control and fill in the schema."
                    + note})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
