"""Report images for the WhatsApp processes (ported from X.PY and mx.py — same look, now plain functions)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from core.scheduler.helpers import need

GREEN = "#90EE90"
GREY = "#F2F2F2"
BLACK = "black"


def _font(size, bold=False):
    from PIL import ImageFont
    for name in (("arialbd.ttf" if bold else "arial.ttf"),
                 ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def load_risk_rows(csv_path: Path, accounts: List[str]):
    """Risk-report rows for `accounts` with the report columns (LEDGER, LIMIT … computed as in X.PY)."""
    pd = need("pandas")
    df = pd.read_csv(csv_path, low_memory=False)
    df.columns = df.columns.str.strip()
    needed = ["ACCOUNTID", "FIN_WITHOUT_MGN", "CASH_LEDGER", "TOTAL_STOCK_EX_REPLG", "MARGIN", "FNONOV"]
    miss = [c for c in needed if c not in df.columns]
    if miss:
        raise RuntimeError(f"Risk report is missing column(s): {', '.join(miss)}")
    df = df[df["ACCOUNTID"].astype(str).str.strip().isin(accounts)].copy()
    if df.empty:
        raise RuntimeError(f"No rows for {', '.join(accounts)} in the risk report.")
    for c in needed[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["LEDGER"] = df["FIN_WITHOUT_MGN"] + df["CASH_LEDGER"]
    df["LIMIT"] = df["LEDGER"] + df["TOTAL_STOCK_EX_REPLG"]
    return df


def risk_table_image(df, date_display: str, out_path: Path, include_nov: bool) -> Path:
    """Green/grey table image 'RISK REPORT-DATED-dd-mm-yyyy' (X.PY layout)."""
    need("PIL", "pillow")
    from PIL import Image, ImageDraw
    cols = ["ACCOUNTID", "LEDGER", "TOTAL_STOCK_EX_REPLG", "LIMIT", "MARGIN"] + (["FNONOV"] if include_nov else [])
    heads = ["CLIENT CODE", "LEDGER", "TOTAL_STOCK_EX_REPLG", "LIMIT", "MARGIN"] + (["FNO_NOV"] if include_nov else [])
    widths = [220, 220, 360, 240, 220] + ([240] if include_nov else [])
    rows = []
    for _, r in df.iterrows():
        row = [str(r["ACCOUNTID"]).strip()]
        for c in cols[1:]:
            row.append(str(round(float(r[c]), 2)))
        rows.append(row)

    row_h, head_h, title_h, lw = 80, 80, 90, 3
    width = sum(widths)
    height = title_h + head_h + row_h * len(rows)
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    tf, hf, xf = _font(36, True), _font(26, True), _font(26)

    d.rectangle([0, 0, width, title_h], fill=GREEN)
    d.text((width // 2, title_h // 2), f"RISK REPORT-DATED-{date_display}", fill=BLACK, font=tf, anchor="mm")
    x, y = 0, title_h
    for w, h in zip(widths, heads):
        d.rectangle([x, y, x + w, y + head_h], fill=GREEN)
        d.text((x + w // 2, y + head_h // 2), h, fill=BLACK, font=hf, anchor="mm")
        x += w
    y += head_h
    for row in rows:
        x = 0
        for w, v in zip(widths, row):
            d.rectangle([x, y, x + w, y + row_h], fill=GREY)
            d.text((x + w // 2, y + row_h // 2), v, fill=BLACK, font=xf, anchor="mm")
            x += w
        y += row_h
    yl = title_h
    d.line([0, yl, width, yl], fill=BLACK, width=lw)
    yl += head_h
    d.line([0, yl, width, yl], fill=BLACK, width=lw)
    for _ in rows:
        yl += row_h
        d.line([0, yl, width, yl], fill=BLACK, width=lw)
    x = 0
    for w in widths:
        d.line([x, title_h, x, yl], fill=BLACK, width=lw)
        x += w
    d.line([width, title_h, width, yl], fill=BLACK, width=lw)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
    return out_path


def format_indian(num) -> str:
    n = int(num)
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    return ("-" if neg else "") + s


def margin_card_image(account: str, report_date: str, v: dict, out_path: Path) -> Path:
    """SN327-style margin card (mx.py layout). v = ledger, fd, pledge, nov, margin (ints)."""
    need("PIL", "pillow")
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 900
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    try:
        fb = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 42)
        f = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 30)
        fs = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 26)
        fe = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 34)
    except Exception:
        fb = f = fs = fe = ImageFont.load_default()
    navy, light, border, teal, black = (15, 35, 70), (245, 247, 250), (180, 180, 180), (25, 110, 140), (0, 0, 0)
    total = v["ledger"] + v["fd"] + v["pledge"] + v["nov"]

    y = 40
    d.rectangle([50, y, 1150, y + 100], fill=navy)
    d.text((70, y + 15), "ACCOUNT ID", fill="white", font=fb)
    d.text((1130, y + 15), account, fill="white", font=fb, anchor="ra")
    d.text((70, y + 60), "REPORT DATE", fill="white", font=fs)
    d.text((1130, y + 60), report_date, fill="white", font=fs, anchor="ra")
    y += 120

    def row(label, value):
        nonlocal y
        d.rectangle([50, y, 1150, y + 70], outline=border, fill=light, width=2)
        d.line([800, y, 800, y + 70], fill=border, width=2)
        d.text((70, y + 18), label, fill=black, font=f)
        d.text((1130, y + 18), format_indian(value), fill=black, font=f, anchor="ra")
        y += 70

    row("Ledger + FD + Pledge Stock Value + NOV", total)
    row("Margin Used", v["margin"])
    y += 20
    d.rectangle([50, y, 1150, y + 70], fill=teal)
    d.text((600, y + 35), "BIFURCATION", fill="white", font=fb, anchor="mm")
    y += 80

    def item(color, emoji, label, value):
        nonlocal y
        d.rectangle([50, y, 1150, y + 80], outline=border, fill=(252, 252, 252), width=2)
        d.line([800, y, 800, y + 80], fill=border, width=2)
        d.ellipse([70, y + 15, 120, y + 65], fill=color)
        d.text((95, y + 40), emoji, font=fe, fill="white", anchor="mm")
        d.text((140, y + 25), label, fill=black, font=f)
        d.text((1130, y + 25), format_indian(value), fill=black, font=f, anchor="ra")
        y += 80

    item((40, 120, 180), "🏦", "Ledger", v["ledger"])
    item((40, 160, 90), "💰", "FD", v["fd"])
    item((220, 170, 40), "📈", "Pledge Stock Value", v["pledge"])
    item((120, 50, 160), "📊", "NOV", v["nov"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
