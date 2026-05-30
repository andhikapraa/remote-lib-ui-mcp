"""Vector fallback icon generator (Pillow).

NOTE: the committed assets/icon.png is the AI-generated mark (via /image-gen).
This script is the no-dependency fallback used when that broker is unavailable;
running it OVERWRITES icon.png with the simpler vector version.

A hand-built vector-style mark to the design brief: an open book whose pages
dissolve upward into a small glowing AI node constellation, with a keyhole cut
into the book's center — library + AI + secure access. Indigo base, cyan->violet
accent glow. Run:  uv run --with pillow assets/make_icon.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024
CX = S // 2
INDIGO_TOP = (30, 42, 120)     # #1E2A78
INDIGO_BOT = (40, 53, 147)     # #283593
WHITE = (248, 250, 252)        # #F8FAFC
CYAN = (34, 211, 238)          # #22D3EE
VIOLET = (124, 58, 237)        # #7C3AED


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # rounded-rect indigo background with a vertical gradient
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), lerp(INDIGO_TOP, INDIGO_BOT, y / S))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=190, fill=255)
    img.paste(grad, (0, 0), mask)

    # AI node constellation (top), dissolving down toward the book spine
    nodes = [
        ((CX, 300), 30, CYAN),
        ((CX - 118, 398), 20, lerp(CYAN, VIOLET, 0.35)),
        ((CX + 118, 398), 20, lerp(CYAN, VIOLET, 0.55)),
        ((CX, 470), 16, VIOLET),
    ]
    dissolve = [((CX, 524), 10), ((CX, 568), 7), ((CX, 604), 5)]
    edges = [(0, 1), (0, 2), (1, 3), (2, 3), (1, 2)]
    line_col = lerp(CYAN, VIOLET, 0.5)

    # glow layer (blurred) behind the crisp art
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for a, b in edges:
        gd.line([nodes[a][0], nodes[b][0]], fill=line_col + (255,), width=10)
    for (x, y), r, c in nodes:
        gd.ellipse([x - r, y - r, x + r, y + r], fill=c + (255,))
    for (x, y), r in dissolve:
        gd.ellipse([x - r, y - r, x + r, y + r], fill=line_col + (255,))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(18)))

    draw = ImageDraw.Draw(img)

    # open book (lower center): two white pages meeting at a center spine
    left = [(CX, 624), (205, 656), (235, 792), (CX, 766)]
    right = [(CX, 624), (819, 656), (789, 792), (CX, 766)]
    draw.polygon(left, fill=WHITE)
    draw.polygon(right, fill=WHITE)
    # stacked-page lines parallel to each page's lower edge
    pl = lerp(INDIGO_TOP, WHITE, 0.20)
    draw.line([(256, 768), (CX - 8, 748)], fill=pl, width=5)
    draw.line([(292, 740), (CX - 14, 724)], fill=pl, width=5)
    draw.line([(2 * CX - 256, 768), (CX + 8, 748)], fill=pl, width=5)
    draw.line([(2 * CX - 292, 740), (CX + 14, 724)], fill=pl, width=5)
    # spine valley (kept inside the book)
    draw.line([(CX, 620), (CX, 760)], fill=lerp(INDIGO_TOP, WHITE, 0.30), width=8)

    # crisp constellation on top
    for a, b in edges:
        draw.line([nodes[a][0], nodes[b][0]], fill=line_col, width=6)
    draw.line([nodes[3][0], (CX, 600)], fill=line_col, width=5)
    for (x, y), r, c in nodes:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=c)
    for (x, y), r in dissolve:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=line_col)

    # keyhole cut into the book center (accent)
    kx, ky = CX, 700
    draw.ellipse([kx - 26, ky - 26, kx + 26, ky + 26], fill=VIOLET)
    draw.polygon([(kx - 16, ky + 8), (kx + 16, ky + 8), (kx + 26, ky + 64), (kx - 26, ky + 64)], fill=VIOLET)
    return img


def main() -> None:
    here = os.path.dirname(__file__)
    icon = make()
    icon.save(os.path.join(here, "icon.png"))
    for size in (512, 256, 128):
        icon.resize((size, size), Image.LANCZOS).save(os.path.join(here, f"icon-{size}.png"))
    print("wrote assets/icon.png (1024) + 512/256/128")


if __name__ == "__main__":
    main()
