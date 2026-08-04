"""
gate_engine/tests/fixtures/sample_slip.py

Minimal valid image fixtures for /analyze-and-score endpoint tests.

The 1×1 white PNG is the smallest syntactically valid image that passes
base64 decoding and media-type detection without requiring a real photograph.
Use it whenever a test needs to drive the image-decoding path without calling
the live Anthropic Vision API (mock _anthropic or _ensure_anthropic instead).
"""
from __future__ import annotations

# 1×1 white PNG — 69 bytes, base64-encoded (92 chars)
# Generated with: struct + zlib, no external deps.
SAMPLE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
    "AAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)

# Same image as a data URL (the format most clients send)
SAMPLE_PNG_DATA_URL = f"data:image/png;base64,{SAMPLE_PNG_B64}"

# A minimal 1×1 black JPEG (for media-type auto-detection tests)
SAMPLE_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAACAgJ/8QAFBAB"
    "AAAAAAAAAAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAA"
    "AAAAAAAA/9oADAMBAAIRAxEAPwCwABmX/9k="
)

SAMPLE_JPEG_DATA_URL = f"data:image/jpeg;base64,{SAMPLE_JPEG_B64}"
