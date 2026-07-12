"""从动作参考图直接抠取底部四个完整动作角色。"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "references" / "taffy_reference_actions.png"
SPECS = {
    "dance": (485, 630, 755, 975),
    "sway": (720, 640, 985, 975),
    "curious": (980, 640, 1240, 975),
    "pet": (1220, 620, 1475, 975),
}
CANVAS = (1024, 1536)
TARGET_HEIGHT = 1080
MAX_WIDTH = 880
BASELINE_Y = 1450


def remove_background(image):
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    border = np.concatenate((rgb[:12].reshape(-1, 3), rgb[-12:].reshape(-1, 3),
                             rgb[:, :12].reshape(-1, 3), rgb[:, -12:].reshape(-1, 3)))
    key = np.median(border, axis=0)
    distance = np.sqrt(((rgb - key) ** 2).sum(axis=2))
    alpha = np.clip((distance - 8) * 16, 0, 255).astype(np.uint8)
    alpha = Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(0.8))
    keep = keep_components(alpha)
    result = image.convert("RGBA")
    result.putalpha(keep.filter(ImageFilter.GaussianBlur(0.8)))
    return result


def keep_components(alpha):
    pixels = np.asarray(alpha) > 24
    height, width = pixels.shape
    seen = np.zeros_like(pixels, dtype=bool)
    components = []
    for start_y, start_x in zip(*np.where(pixels)):
        if seen[start_y, start_x]:
            continue
        stack = [(start_y, start_x)]
        seen[start_y, start_x] = True
        points = []
        touches_edge = False
        while stack:
            y, x = stack.pop()
            points.append((y, x))
            touches_edge |= x == 0 or y == 0 or x == width - 1 or y == height - 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and pixels[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        components.append((points, touches_edge))
    largest = max((len(points) for points, _edge in components), default=0)
    output = np.zeros((height, width), dtype=np.uint8)
    for points, touches_edge in components:
        if len(points) == largest or (not touches_edge and len(points) >= 18):
            for y, x in points:
                output[y, x] = 255
    return Image.fromarray(output, "L")


source = Image.open(SOURCE).convert("RGBA")
output = ROOT / "full_actions"
output.mkdir(exist_ok=True)
for name, box in SPECS.items():
    action = remove_background(source.crop(box))
    bounds = action.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError(f"动作抠图为空: {name}")
    action = action.crop(bounds)
    scale = min(TARGET_HEIGHT / action.height, MAX_WIDTH / action.width)
    size = (round(action.width * scale), round(action.height * scale))
    action = action.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    position = ((CANVAS[0] - action.width) // 2, BASELINE_Y - action.height)
    canvas.alpha_composite(action, position)
    canvas.save(output / f"{name}.png", optimize=True)
    print(name, size, position)
