"""Safe image decoding and normalization for player-provided entity artwork."""
from __future__ import annotations

import base64
import binascii
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SOURCE_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


def sanitize_image_data_url(data_url: str | None) -> tuple[bytes, str] | None:
    if not data_url:
        return None
    if not data_url.startswith((
        "data:image/jpeg;base64,",
        "data:image/png;base64,",
        "data:image/webp;base64,",
    )):
        raise HTTPException(status_code=422, detail="Use a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(status_code=422, detail="Image data is not valid base64.") from error
    if not raw or len(raw) > MAX_SOURCE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image must not exceed 4 MiB.")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(status_code=422, detail="Use a JPEG, PNG or WebP image.")
            if (
                source.width > 12000 or source.height > 12000
                or source.width * source.height > MAX_IMAGE_PIXELS
            ):
                raise HTTPException(status_code=413, detail="Image dimensions exceed the 40-megapixel limit.")
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, format="WEBP", quality=82, method=6)
            return output.getvalue(), "image/webp"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(status_code=422, detail="Image file is damaged or cannot be decoded.") from error


def image_bytes_to_data_url(image_data: bytes, media_type: str) -> str:
    encoded = base64.b64encode(image_data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def image_record_to_data_url(image) -> str | None:
    if image is None:
        return None
    return image_bytes_to_data_url(image.image_data, image.media_type)
