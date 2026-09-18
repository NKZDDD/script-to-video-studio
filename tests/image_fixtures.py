"""Deterministic real image bytes for tests that exercise result validation."""
import io
import random

from PIL import Image


def png_bytes(seed=1, size=(64, 64)):
    pixels = random.Random(seed).randbytes(size[0] * size[1] * 3)
    with Image.frombytes("RGB", size, pixels) as im, io.BytesIO() as buf:
        im.save(buf, "PNG")
        return buf.getvalue()
