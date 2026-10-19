"""
离线生成程序图标 icon.ico（不依赖网络，纯 PIL 绘制）
效果：深色圆角方块背景 + 青绿色二维码定位角 + 放大镜，呼应主程序的深色扁平风格
"""
from PIL import Image, ImageDraw

BG = (28, 30, 34, 255)        # DARK_BG
ACCENT = (0, 200, 150, 255)   # ACCENT_OK
ACCENT_DIM = (0, 160, 120, 255)
WHITE = (232, 232, 234, 255)


def draw_base(size: int) -> Image.Image:
    # 用 4x 超采样再缩小，边缘更平滑
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = S * 0.06
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=S * 0.22, fill=BG)

    # 三个二维码定位角（左上、右上、左下），营造"扫码"意象
    corner = S * 0.24
    thick = max(2, int(S * 0.045))
    margin = S * 0.16

    def corner_mark(x, y, flip_x=False, flip_y=False):
        dx = -1 if flip_x else 1
        dy = -1 if flip_y else 1
        d.line([(x, y), (x + dx * corner, y)], fill=ACCENT, width=thick)
        d.line([(x, y), (x, y + dy * corner)], fill=ACCENT, width=thick)

    corner_mark(margin, margin)
    corner_mark(S - margin, margin, flip_x=True)
    corner_mark(margin, S - margin, flip_y=True)

    # 右下角放大镜，代表"识别/截图"
    cx, cy = S * 0.68, S * 0.68
    r = S * 0.16
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=int(S * 0.045))
    handle_len = S * 0.14
    hx1 = cx + r * 0.72
    hy1 = cy + r * 0.72
    hx2 = hx1 + handle_len * 0.72
    hy2 = hy1 + handle_len * 0.72
    d.line([(hx1, hy1), (hx2, hy2)], fill=WHITE, width=int(S * 0.06))

    img = img.resize((size, size), Image.LANCZOS)
    return img


sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [draw_base(s) for s in sizes]
imgs[-1].save(
    "icon.ico",
    format="ICO",
    sizes=[(s, s) for s in sizes],
)
print("icon.ico 已生成")
