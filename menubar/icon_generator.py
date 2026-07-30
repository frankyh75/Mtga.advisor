"""Generate a simple menubar icon PNG using Pillow.

Creates a small circular badge with an "M" letter — a minimal MTGA-adjacent
symbol suitable for macOS status bar display.

Usage::

    from menubar.icon_generator import generate_icon
    icon_path = generate_icon()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_ICON_SIZE = 22  # px — menubar icon sweet spot
DEFAULT_ICON_PATH = Path(__file__).parent / "icon.png"


def generate_icon(
    output_path: Path | None = None,
    size: int = DEFAULT_ICON_SIZE,
) -> Path:
    """Generate a menubar icon PNG (dark "M" on transparent background).

    Args:
        output_path: Destination file path. Defaults to ``menubar/icon.png``.
        size: Square edge length in pixels.

    Returns:
        Path to the generated PNG file.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for icon generation. "
            "Install with: pip install pillow"
        ) from exc

    target = output_path or DEFAULT_ICON_PATH

    # Transparent background, RGBA.
    img: Any = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw a filled circle (dark blue, semi-opaque).
    margin = 1
    bbox = [margin, margin, size - margin - 1, size - margin - 1]
    draw.ellipse(bbox, fill=(40, 60, 120, 220), outline=(255, 255, 255, 200), width=1)

    # Draw "M" centered.
    font = _load_font(size)
    text = "M"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    text_x = (size - text_w) / 2 - text_bbox[0]
    text_y = (size - text_h) / 2 - text_bbox[1]
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)

    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(target), "PNG")
    return target


def _load_font(size: int) -> Any:
    """Load a system font, falling back to the default bitmap font."""
    try:
        from PIL import ImageFont

        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except Exception:
        from PIL import ImageFont

        return ImageFont.load_default()


if __name__ == "__main__":
    path = generate_icon()
    print(f"Icon generated: {path}")