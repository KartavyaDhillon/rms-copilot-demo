"""
Render the peak-margin report as a CRISP, EVENLY-SPACED IMAGE (no Excel, headless).

Improvements over the last version:
  - EQUAL column widths for all four Peak columns (Excel-like uniformity).
  - Brighter, cleaner styling (crisper green header, softer gridlines, more padding).
  - Supersampled 3x + PNG for sharp text.
  - Calibri on Windows, DejaVu fallback elsewhere.

Layout: stacked CM Segment / FO Segment / TOTAL, each with its own green title
+ header row, Indian-comma numbers.
"""
import os
from PIL import Image, ImageDraw, ImageFont

# Brighter, cleaner palette
GREEN = (180, 205, 130)        # crisp header green
GREEN_DK = (120, 150, 70)      # title band (slightly deeper for contrast)
WHITE = (255, 255, 255)
ROWALT = (248, 250, 244)       # subtle zebra striping
INK = (33, 37, 41)
HEADINK = (25, 35, 10)
GRID = (200, 206, 196)         # soft gridlines
TOTAL_BG = (224, 233, 200)

SS = 3                          # supersample factor (3x -> very crisp)
PAD = 16 * SS
ROW_H = 32 * SS
TITLE_H = 32 * SS
GAP = 18 * SS
CODE_W = 200 * SS               # client-code column
PEAK_W = 175 * SS               # EQUAL width for every Peak column
COL_W = [CODE_W, PEAK_W, PEAK_W, PEAK_W, PEAK_W]


def _font(size, bold=False):
    size *= SS
    for c in ([("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
               ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
               ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]):
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _inr(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    neg = v < 0; v = abs(v)
    whole = int(v); dec = int(round((v - whole) * 100))
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]; rest = s[:-3]; parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:]); rest = rest[:-2]
        if rest: parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    out = f"{s}.{dec:02d}"
    return ("-" + out) if neg else out


def _cell(d, x, y, w, h, text, font, align="r", fill=WHITE, ink=INK):
    d.rectangle([x, y, x + w, y + h], fill=fill, outline=GRID, width=SS)
    pad = 12 * SS
    if align == "c":
        d.text((x + w // 2, y + h // 2), text, fill=ink, font=font, anchor="mm")
    elif align == "l":
        d.text((x + pad, y + h // 2), text, fill=ink, font=font, anchor="lm")
    else:
        d.text((x + w - pad, y + h // 2), text, fill=ink, font=font, anchor="rm")


STRONG_INK = (0, 0, 0)          # used when strong=True: pure black, bold values


def _section(d, x0, y, title, header, rows, fb, fr, total=False, strong=False):
    table_w = sum(COL_W)
    if title:
        d.rectangle([x0, y, x0 + table_w, y + TITLE_H], fill=GREEN_DK)
        d.text((x0 + table_w // 2, y + TITLE_H // 2), title, fill=WHITE, font=fb, anchor="mm")
        y += TITLE_H
    if header:
        x = x0
        for h, w in zip(["Client Code", "Peak1", "Peak2", "Peak3", "Peak4"], COL_W):
            _cell(d, x, y, w, ROW_H, h, fb, align="c", fill=GREEN, ink=HEADINK)
            x += w
        y += ROW_H
    for ri, row in enumerate(rows):
        x = x0
        bg = TOTAL_BG if total else (ROWALT if ri % 2 else WHITE)
        for i, (val, w) in enumerate(zip(row, COL_W)):
            txt = str(val) if i == 0 else _inr(val)
            _cell(d, x, y, w, ROW_H, txt, (fb if (total or strong) else fr),
                  align="l" if i == 0 else "r", fill=bg, ink=(STRONG_INK if strong else INK))
            x += w
        y += ROW_H
    return y


def _finish(img, out_path):
    img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    png = os.path.splitext(out_path)[0] + ".png"
    img.save(png, "PNG")
    return png


def render_cm_image(report, out_path, strong=False):
    fb, fr = _font(15 if strong else 14, bold=True), _font(13)
    cm, fo, t = report["cm_segment"], report["fo_segment"], report["combined_totals"]
    width = sum(COL_W) + PAD * 2
    height = PAD * 2 + (TITLE_H + ROW_H + ROW_H) + GAP + (TITLE_H + ROW_H + ROW_H) + GAP + ROW_H
    img = Image.new("RGB", (width, height), WHITE); d = ImageDraw.Draw(img)
    y = PAD
    y = _section(d, PAD, y, "CM Segment", True,
                 [[cm.get("Code", "06436"), cm["Peak1"], cm["Peak2"], cm["Peak3"], cm["Peak4"]]], fb, fr, strong=strong)
    y += GAP
    y = _section(d, PAD, y, "FO Segment", True,
                 [[fo.get("Code", "PRO_06436"), fo["Peak1"], fo["Peak2"], fo["Peak3"], fo["Peak4"]]], fb, fr, strong=strong)
    y += GAP
    _section(d, PAD, y, None, False,
             [["TOTAL (CM + FO)", t["Peak1"], t["Peak2"], t["Peak3"], t["Peak4"]]], fb, fr, total=True, strong=strong)
    return _finish(img, out_path)


def render_fo_image(report, out_path, strong=False):
    fb, fr = _font(15 if strong else 14, bold=True), _font(13)
    rows = [[code, p["Peak1"], p["Peak2"], p["Peak3"], p["Peak4"]]
            for code, p in report["fo_clients"].items()]
    width = sum(COL_W) + PAD * 2
    height = PAD * 2 + TITLE_H + ROW_H + ROW_H * len(rows)
    img = Image.new("RGB", (width, height), WHITE); d = ImageDraw.Draw(img)
    _section(d, PAD, PAD, "FO Segment", True, rows, fb, fr, strong=strong)
    return _finish(img, out_path)


def build_report_images(report, folder, strong=False):
    """strong=True → bold, pure-black values (used by the Scheduler's PEAK MARGIN REPORT); default keeps the console look."""
    os.makedirs(folder, exist_ok=True)
    fo = render_fo_image(report, os.path.join(folder, "F_filtered.png"), strong)
    cm = render_cm_image(report, os.path.join(folder, "C_filtered.png"), strong)
    return {"fo_image": fo, "cm_image": cm}
