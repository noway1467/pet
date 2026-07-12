"""统一完整表情图的角色高度、水平中心和脚底基线。"""
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent / "full_expressions"
NAMES = ("money", "happy", "shy", "hard_cry", "toothy",
         "nervous", "soft_cry", "cheer", "surprised")
CANVAS = (1024, 1536)
TARGET_HEIGHT = 1380
MAX_WIDTH = 720
BASELINE_Y = 1460


for name in NAMES:
    path = ROOT / f"{name}.png"
    image = Image.open(path).convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box is None:
        raise RuntimeError(f"空表情图: {path}")
    crop = image.crop(box)
    scale = min(TARGET_HEIGHT / crop.height, MAX_WIDTH / crop.width)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    crop = crop.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    x = (CANVAS[0] - crop.width) // 2
    y = BASELINE_Y - crop.height
    canvas.alpha_composite(crop, (x, y))
    canvas.save(path, optimize=True)
    print(name, size, (x, y))
