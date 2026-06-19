"""把纯白背景的立绘抠成透明 PNG（仅适合干净白底，如 1.jpg）。

做法：从图像四边对"接近白色"的像素做洪水填充 -> 只去掉与边缘连通的背景白，
角色内部的白色（白裙子/白袜子）因被轮廓包住而保留；再按行分带裁掉底部水印。

用法：python make_cutout.py 输入.jpg 输出.png [阈值=240]
"""
import sys
from collections import deque

import numpy as np
from PySide6.QtGui import QImage


def load_argb(path):
    img = QImage(path)
    if img.isNull():
        raise SystemExit("无法读取图片: " + path)
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.bits(), dtype=np.uint8).reshape(h, w, 4).copy()  # B,G,R,A
    return arr, w, h


def flood_background(arr, thresh):
    """返回与四边连通的白色背景掩码（bool）。"""
    h, w, _ = arr.shape
    is_white = (arr[..., 2] >= thresh) & (arr[..., 1] >= thresh) & (arr[..., 0] >= thresh)
    white = bytearray(is_white.reshape(-1).astype(np.uint8).tobytes())
    bg = bytearray(h * w)
    dq = deque()

    def seed(i):
        if white[i] and not bg[i]:
            bg[i] = 1
            dq.append(i)

    for x in range(w):
        seed(x)
        seed((h - 1) * w + x)
    for y in range(h):
        seed(y * w)
        seed(y * w + w - 1)
    while dq:
        i = dq.popleft()
        y, x = divmod(i, w)
        for n, ok in ((i - w, y > 0), (i + w, y < h - 1), (i - 1, x > 0), (i + 1, x < w - 1)):
            if ok and white[n] and not bg[n]:
                bg[n] = 1
                dq.append(n)
    bg_arr = np.frombuffer(bytes(bg), dtype=np.uint8).reshape(h, w).astype(bool)

    # 去白边：把紧挨背景的"很亮"像素也并入背景，减少白色描边残留
    light = ((arr[..., 2] >= thresh - 10) & (arr[..., 1] >= thresh - 10) & (arr[..., 0] >= thresh - 10))
    nb = np.zeros_like(bg_arr)
    nb[1:, :] |= bg_arr[:-1, :]
    nb[:-1, :] |= bg_arr[1:, :]
    nb[:, 1:] |= bg_arr[:, :-1]
    nb[:, :-1] |= bg_arr[:, 1:]
    bg_arr |= (light & nb & ~bg_arr)
    return bg_arr


def keep_largest(solid):
    """只保留最大的 8-邻接连通块（角色），其余（水印碎块等）丢弃。"""
    h, w = solid.shape
    s = bytearray(solid.reshape(-1).astype(np.uint8).tobytes())
    seen = bytearray(h * w)
    best = []
    dq = deque()
    for start in range(h * w):
        if s[start] and not seen[start]:
            seen[start] = 1
            dq.append(start)
            comp = []
            while dq:
                i = dq.popleft()
                comp.append(i)
                y, x = divmod(i, w)
                for ny in (y - 1, y, y + 1):
                    if 0 <= ny < h:
                        base = ny * w
                        for nx in (x - 1, x, x + 1):
                            if 0 <= nx < w:
                                n = base + nx
                                if s[n] and not seen[n]:
                                    seen[n] = 1
                                    dq.append(n)
            if len(comp) > len(best):
                best = comp
    mask = np.zeros(h * w, dtype=bool)
    if best:
        mask[np.array(best, dtype=np.int64)] = True
    return mask.reshape(h, w)


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "1.jpg"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "pet_1.png"
    thresh = int(sys.argv[3]) if len(sys.argv) > 3 else 240

    arr, w, h = load_argb(in_path)
    alpha = arr[..., 3]
    if (alpha < 40).mean() > 0.03:           # 已经是透明背景：按 alpha 清杂块
        print(f"输入 {in_path} {w}x{h}（已含透明背景），清理残留杂块…")
        solid = alpha > 128                  # 用不透明核心，断开半透明羽化桥接
    else:                                    # 纯白背景：洪水填充抠图
        print(f"输入 {in_path} {w}x{h}，白底抠图，阈值 {thresh}")
        solid = ~flood_background(arr, thresh)
    kept = keep_largest(solid)               # 只保留角色这一大块，丢弃水印/碎块
    arr[~kept, 3] = 0

    ys, xs = np.where(kept)
    if len(ys) == 0:
        raise SystemExit("没有检测到前景，可能阈值不合适")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    print(f"保留区域 x[{x0}:{x1}] y[{y0}:{y1}]，丢弃碎块像素 {int(solid.sum() - kept.sum())}(水印等)")

    crop = np.ascontiguousarray(arr[y0:y1 + 1, x0:x1 + 1])
    ch, cw = crop.shape[:2]
    out = QImage(crop.tobytes(), cw, ch, QImage.Format_ARGB32).copy()
    out.save(out_path)
    print(f"已保存 {out_path} {cw}x{ch}，前景像素 {int(kept.sum())}")


if __name__ == "__main__":
    main()
