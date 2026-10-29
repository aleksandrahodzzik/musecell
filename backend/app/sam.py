"""SAM 3.1 segmentation client with a disk cache (spec §3, path A).

Request/parse shape verified in Phase 0 (docs/DECISIONS.md): Responses API,
streamed, `metadata={"include_confidence": "true"}`, parsed by meta-sam-parser.
Masks come box-local and are placed at their `bounds` to build a full-frame
label image. An empty result is a normal outcome (0 objects), not an error.

Cache: DATA_DIR/cache/sam/<field_id>/<image>__<prompt>.npz, so a field is
never paid for twice with the same prompt.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import openai
from meta_sam_parser import (
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)
from PIL import Image

from app.config import SAM_PRICE_PER_IMAGE, Settings, get_settings


@dataclass
class SamResult:
    labels: np.ndarray  # uint16 label image in the input image's pixel grid, 0 = background
    confidences: list[float | None]  # per label, index = label - 1
    outcome: str
    cached: bool
    cost_usd: float


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cache_path(settings: Settings, field_id: str, image_name: str, prompt: str) -> Path:
    return settings.data_path / "cache" / "sam" / field_id / f"{image_name}__{_slug(prompt)}.npz"


def masks_to_labels(masks: list[tuple[np.ndarray, tuple[int, int], float | None]],
                    shape: tuple[int, int]) -> tuple[np.ndarray, list[float | None]]:
    """Paint box-local masks into one label image, most confident first; overlaps go to the earlier object."""
    order = sorted(range(len(masks)), key=lambda i: -(masks[i][2] or 0.0))
    labels = np.zeros(shape, dtype=np.uint16)
    confidences: list[float | None] = []
    for i in order:
        mask, (left, top), conf = masks[i]
        h = min(mask.shape[0], shape[0] - top)
        w = min(mask.shape[1], shape[1] - left)
        if h <= 0 or w <= 0:
            continue
        window = labels[top:top + h, left:left + w]
        free = mask[:h, :w] & (window == 0)
        if not free.any():
            continue
        confidences.append(conf)
        window[free] = len(confidences)
    return labels, confidences


def _decode(record: SegmentationMaskRecord) -> tuple[np.ndarray, tuple[int, int], float | None]:
    raster = np.frombuffer(decode_mask_to_raster(record.mask), dtype=np.uint8)
    mask = raster.reshape(record.mask.height, record.mask.width).astype(bool)
    left, top = int(round(record.bounds.left)), int(round(record.bounds.top))
    bw = int(round(record.bounds.right - record.bounds.left))
    bh = int(round(record.bounds.bottom - record.bounds.top))
    if (bh, bw) != mask.shape and bw > 0 and bh > 0:  # raster at another resolution than its box
        mask = np.asarray(Image.fromarray(mask).resize((bw, bh), Image.NEAREST))
    return mask, (left, top), record.confidence


async def _request(settings: Settings, png: bytes, prompt: str):
    if settings.model_api_key is None:
        raise RuntimeError("MODEL_API_KEY is empty. Put your key in backend/.env to use SAM 3.1.")
    client = openai.AsyncOpenAI(base_url=settings.model_base_url,
                                api_key=settings.model_api_key.get_secret_value(), timeout=180, max_retries=3)
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    async with client.responses.stream(
        model=settings.segmenter_model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                            {"type": "input_image", "image_url": url}]}],
        metadata={"include_confidence": "true"},
    ) as stream:
        return await parse_responses_stream(stream, image_segmentation_format()).final_result()


async def segment_async(png_path: Path, prompt: str, field_id: str, image_name: str,
                        settings: Settings | None = None) -> SamResult:
    settings = settings or get_settings()
    path = cache_path(settings, field_id, image_name, prompt)
    if path.exists():
        data = np.load(path)
        meta = json.loads(str(data["meta"]))
        return SamResult(data["labels"], meta["confidences"], meta["outcome"], cached=True, cost_usd=0.0)

    png = png_path.read_bytes()
    with Image.open(png_path) as im:
        shape = (im.height, im.width)
    result = await _request(settings, png, prompt)
    masks = [_decode(r) for r in result.records if isinstance(r, SegmentationMaskRecord)]
    n_boxes = sum(isinstance(r, SegmentationBoxRecord) for r in result.records)
    labels, confidences = masks_to_labels(masks, shape)
    meta = {"prompt": prompt, "image": image_name, "outcome": result.outcome.status, "boxes": n_boxes,
            "masks": len(masks), "confidences": confidences, "model": settings.segmenter_model}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, labels=labels, meta=json.dumps(meta))
    return SamResult(labels, confidences, result.outcome.status, cached=False, cost_usd=SAM_PRICE_PER_IMAGE)


def segment(png_path: Path, prompt: str, field_id: str, image_name: str,
            settings: Settings | None = None) -> SamResult:
    return asyncio.run(segment_async(png_path, prompt, field_id, image_name, settings))
