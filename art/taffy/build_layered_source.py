from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer


ROOT = Path(__file__).resolve().parent
MASTER_PATH = ROOT / "taffy_master.png"
CANVAS_SIZE = (1024, 1536)


def polygon_mask(points: list[tuple[int, int]]) -> Image.Image:
    mask = Image.new("L", CANVAS_SIZE, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def ellipse_mask(box: tuple[int, int, int, int], blur: int = 0) -> Image.Image:
    mask = Image.new("L", CANVAS_SIZE, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur)) if blur else mask


def rgba_with_mask(source: Image.Image, mask: Image.Image) -> Image.Image:
    result = source.copy()
    alpha = np.minimum(np.asarray(source.getchannel("A")), np.asarray(mask)).astype(np.uint8)
    alpha[alpha < 32] = 0
    result.putalpha(Image.fromarray(alpha, "L"))
    return result


def pink_hair_mask(source: Image.Image) -> Image.Image:
    rgb = np.asarray(source.convert("RGB"))
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    pink = (r > 150) & (r > g + 15) & (r > b - 25) & (g > 55) & (g < 210) & (b > 75)
    region = np.zeros_like(pink)
    region[250:900, 140:880] = True
    mask = Image.fromarray(((pink & region).astype(np.uint8) * 255), "L")
    mask = mask.filter(ImageFilter.MaxFilter(13)).filter(ImageFilter.MaxFilter(9))
    return mask


def clean_face(source: Image.Image) -> Image.Image:
    rgba = np.asarray(source)
    sample = rgba[430:485, 480:545, :3]
    valid = sample[(sample.mean(axis=2) > 180) & (sample.max(axis=2) - sample.min(axis=2) < 90)]
    skin = tuple(np.median(valid, axis=0).astype(np.uint8)) if len(valid) else (255, 221, 211)
    fill = Image.new("RGBA", CANVAS_SIZE, (*skin, 255))
    feature_mask = Image.new("L", CANVAS_SIZE, 0)
    draw = ImageDraw.Draw(feature_mask)
    draw.ellipse((350, 342, 500, 500), fill=255)
    draw.ellipse((520, 342, 670, 500), fill=255)
    draw.ellipse((438, 452, 585, 565), fill=255)
    feature_mask = feature_mask.filter(ImageFilter.GaussianBlur(10))
    return Image.composite(fill, source, feature_mask)


def build_layers() -> dict[str, Image.Image]:
    master = Image.open(MASTER_PATH).convert("RGBA")
    alpha = np.asarray(master.getchannel("A"))

    head_core = polygon_mask(
        [(255, 28), (760, 28), (835, 260), (805, 560), (680, 610), (340, 610), (205, 560), (170, 270)]
    )
    head_core_arr = np.asarray(head_core)
    hair_extension = np.where(head_core_arr == 0, np.asarray(pink_hair_mask(master)), 0).astype(np.uint8)
    hair_left = hair_extension.copy()
    hair_left[:, 512:] = 0
    hair_right = hair_extension.copy()
    hair_right[:, :512] = 0

    left_arm = polygon_mask([(180, 560), (390, 560), (410, 780), (305, 930), (170, 900)])
    right_arm = polygon_mask([(635, 560), (845, 560), (855, 900), (720, 930), (615, 780)])
    left_leg = polygon_mask([(330, 930), (510, 930), (510, 1470), (330, 1470)])
    right_leg = polygon_mask([(510, 930), (690, 930), (690, 1470), (510, 1470)])

    assigned = np.maximum.reduce((head_core_arr, hair_left, hair_right))
    part_masks: dict[str, Image.Image] = {
        "Head_Core": head_core,
        "Hair_L": Image.fromarray(hair_left, "L"),
        "Hair_R": Image.fromarray(hair_right, "L"),
    }
    for name, raw_mask in (
        ("Arm_L", left_arm),
        ("Arm_R", right_arm),
        ("Leg_L", left_leg),
        ("Leg_R", right_leg),
    ):
        mask = np.asarray(raw_mask)
        unique = np.where(assigned == 0, mask, 0).astype(np.uint8)
        assigned = np.maximum(assigned, unique)
        part_masks[name] = Image.fromarray(unique, "L")

    body_mask = np.where(assigned == 0, alpha, 0).astype(np.uint8)
    part_masks["Body_Core"] = Image.fromarray(body_mask, "L")

    cleaned = clean_face(master)
    layers = {name: rgba_with_mask(cleaned if name == "Head_Core" else master, mask) for name, mask in part_masks.items()}

    feature_specs = {
        "Eye_L": (350, 340, 500, 500),
        "Eye_R": (520, 340, 670, 500),
        "Mouth_Default": (440, 455, 585, 560),
    }
    for name, box in feature_specs.items():
        layers[name] = rgba_with_mask(master, ellipse_mask(box, blur=3))
    return layers


def build_psd(layers: dict[str, Image.Image]) -> Path:
    psd = PSDImage.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    order = (
        "Body_Core",
        "Leg_L",
        "Leg_R",
        "Arm_L",
        "Arm_R",
        "Hair_L",
        "Hair_R",
        "Head_Core",
        "Eye_L",
        "Eye_R",
        "Mouth_Default",
    )
    for name in order:
        layer_image = layers[name]
        bbox = layer_image.getchannel("A").getbbox()
        if bbox is None:
            continue
        cropped = layer_image.crop(bbox)
        psd.append(PixelLayer.frompil(cropped, psd, name=name, left=bbox[0], top=bbox[1]))
    output = ROOT / "taffy_live2d_layered_v4.psd"
    psd.save(output)
    return output


def build_preview(layers: dict[str, Image.Image]) -> Path:
    composed = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    for name in (
        "Body_Core",
        "Leg_L",
        "Leg_R",
        "Arm_L",
        "Arm_R",
        "Hair_L",
        "Hair_R",
        "Head_Core",
        "Eye_L",
        "Eye_R",
        "Mouth_Default",
    ):
        composed.alpha_composite(layers[name])
    output = ROOT / "layered_source_preview.png"
    composed.save(output)
    return output


if __name__ == "__main__":
    generated_layers = build_layers()
    layers_dir = ROOT / "live2d_layers"
    layers_dir.mkdir(exist_ok=True)
    for layer_name, layer_image in generated_layers.items():
        layer_image.save(layers_dir / f"{layer_name}.png")
    psd_path = build_psd(generated_layers)
    preview_path = build_preview(generated_layers)
    print(f"PSD: {psd_path}")
    print(f"Preview: {preview_path}")
