"""Render the ghostpkg demo GIF frame by frame with Pillow.

No browser, no ffmpeg -- the terminal is drawn directly, so the output is
deterministic and the file stays small enough for a README.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 940, 300
BG = "#0D1117"
CARD = "#161B22"
BORDER = "#30363D"
TEXT = "#E6EDF3"
MUTED = "#8B949E"
DIM = "#6E7681"
RED = "#F85149"
GREEN = "#3FB950"
VIOLET = "#A78BFA"

FONT_PATHS = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
FONT_BOLD_PATHS = [
    r"C:\Windows\Fonts\consolab.ttf",
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
]


def load(paths, size):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


F = load(FONT_PATHS, 19)
FB = load(FONT_BOLD_PATHS, 19)
FS = load(FONT_PATHS, 16)

CMD = "ghostpkg check requests-async-helper-sdk numpy"

PAD_X, CARD_X = 26, 22
CARD_Y, CARD_W = 20, W - 44
BAR_H = 36
LINE0 = CARD_Y + BAR_H + 26
LH = 30


def base_frame():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [CARD_X, CARD_Y, CARD_X + CARD_W, H - 20],
        radius=12, fill=CARD, outline=BORDER, width=1,
    )
    d.line([CARD_X + 1, CARD_Y + BAR_H, CARD_X + CARD_W - 1, CARD_Y + BAR_H], fill=BORDER)
    for i, c in enumerate((RED, "#D29922", GREEN)):
        cx = CARD_X + 20 + i * 20
        cy = CARD_Y + BAR_H // 2
        d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=c)
    d.text((CARD_X + CARD_W // 2 - 42, CARD_Y + 10), "ghostpkg", font=FS, fill=DIM)
    return img, d


def draw_run(d, x, y, parts, font=F):
    """Draw coloured segments on one line, returning the end x."""
    for text, colour in parts:
        d.text((x, y), text, font=font, fill=colour)
        x += d.textlength(text, font=font)
    return x


def frame(typed, out_lines, cursor):
    img, d = base_frame()
    x = CARD_X + CARD_X
    end = draw_run(d, x, LINE0, [("$ ", GREEN), (CMD[:typed], TEXT)])
    if cursor:
        d.rectangle([end + 2, LINE0 + 2, end + 11, LINE0 + 21], fill=VIOLET)

    y = LINE0 + LH + 8
    if out_lines >= 1:
        draw_run(d, x, y, [("BLOCKED", RED)], font=FB)
        draw_run(d, x + 108, y, [("requests-async-helper-sdk", TEXT)])
    if out_lines >= 2:
        d.text((x + 108, y + LH - 4), "- does not exist on pypi", font=FS, fill=MUTED)
    if out_lines >= 3:
        yy = y + LH * 2 - 2
        draw_run(d, x, yy, [("ok", GREEN)], font=FB)
        endn = draw_run(d, x + 108, yy, [("numpy", TEXT)])
        d.text((endn + 10, yy + 2), "(136 releases, 19.8y old)", font=FS, fill=DIM)
    if out_lines >= 4:
        yy = y + LH * 3 + 10
        draw_run(d, x, yy, [("1 blocked: ", RED), ("requests-async-helper-sdk", RED)], font=FB)
    return img


frames, delays = [], []


def add(img, ms):
    frames.append(img)
    delays.append(ms)


add(frame(0, 0, True), 500)
step = 3
for n in range(step, len(CMD) + 1, step):
    add(frame(n, 0, True), 55)
add(frame(len(CMD), 0, True), 450)
add(frame(len(CMD), 0, False), 180)
add(frame(len(CMD), 1, False), 260)
add(frame(len(CMD), 2, False), 260)
add(frame(len(CMD), 3, False), 380)
add(frame(len(CMD), 4, False), 2600)

out = Path(__file__).with_name("demo.gif")
frames[0].save(
    out, save_all=True, append_images=frames[1:], duration=delays,
    loop=0, optimize=True, disposal=2,
)
total = sum(delays) / 1000
print(f"frames: {len(frames)}   duration: {total:.1f}s   size: {out.stat().st_size/1024:.0f} KB")
print(f"path: {out}")
