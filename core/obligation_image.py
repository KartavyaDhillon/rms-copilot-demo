"""core/obligation_image.py - the DAILY OBLIGATION REPORT picture (Sheet4 'detailed' of obligation_new_final.py), drawn by the console.

No Excel, no xlwings, no screen needed: the same sheet layout is built as a small grid (rows / columns / merged cells) and painted
with Pillow, so the picture looks like the Excel screenshot that was e-mailed (blue title bars, teal summary headers, grey
"Top Buyer -" cells, pastel metric blocks, auto-fitted columns, black frame).

    build_data(main_csv, fo_file, search_file) -> (obligation df, custodian df, margins dict)      (same maths as the script)
    render(path, obl, cust, margins)            -> PNG path
"""
from __future__ import annotations

import glob
import os
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

METRICS = ["PayInPayOut", "FuturesMTM", "DayNetPremium"]
MCX_METRICS = ["MCXFuturesMTM", "MCXDayNetPremium"]
COLORS = {"PayInPayOut": ("#BDD7EE", "#DDEBF7"), "FuturesMTM": ("#FFF8DC", "#FFFBF0"), "DayNetPremium": ("#D9EAD3", "#F0F8E8"),
          "MCXFuturesMTM": ("#FFF4CC", "#FFF9E6"), "MCXDayNetPremium": ("#D0E0E3", "#E2EFDA")}
TITLE_BLUE, TEAL, VALUE_GREEN, GREY, WHITE, THIN = "#4F81BD", "#4BACC6", "#E2EFDA", "#4F4F4F", "#FFFFFF", "#7DA7C0"
ROW_H = 20


# ───────────────────────── the numbers (same rules as the script) ─────────────────────────
def _num(series):
    return series.astype(str).str.replace(",", "", regex=False).astype(float)


def read_search_results(path):
    import pandas as pd
    for i in range(5):                                           # the header can be in row 1-5
        try:
            df = pd.read_csv(path, header=i) if str(path).lower().endswith(".csv") else pd.read_excel(path, header=i)
            if "TM / CP Margin Limit" in df.columns:
                return df
        except Exception:                                        # noqa: BLE001
            continue
    raise ValueError("could not find the header row of the SearchResults file")


def build_data(main_csv: str, fo_file: str, search_file: str):
    """-> (obligation df [Product '-'], custodian df, margins dict in Cr). Exactly the script's calculation."""
    import pandas as pd
    df = pd.read_csv(main_csv)
    obl = df[df["Product"] == "-"].copy()
    cust = df[df["Product"] != "-"].copy()
    for d in (obl, cust):
        for c in METRICS:
            d[c + "_Cr"] = d[c] / 1e7
    for c in ("MCXDayNetPremium", "MCXFuturesMTM"):
        obl[c + "_Cr"] = obl[c] / 1e7 if c in obl.columns else 0.0
    for c in ("MCXDayNetPremium", "MCXFuturesMTM", "MCXDayNetPremium_Cr", "MCXFuturesMTM_Cr"):
        if c in cust.columns:
            cust = cust.drop(columns=[c])
    mcx = read_search_results(search_file)
    from core import nmass_level1 as L1
    if L1.is_level1(fo_file):                                   # the NMASS Level-1 row: the portal's own four FO numbers
        fo = L1.read_boxes(str(fo_file))
    else:                                                       # old full FO_ClientLevelCollaterals file (kept for old backups)
        coll = pd.read_csv(fo_file) if str(fo_file).lower().endswith((".csv", ".txt")) else pd.read_excel(fo_file)
        fo = {"fo_deposit": float((coll["Cash allocated (b)"].sum() + coll["Cash-others (c)"].sum() + coll["Non-cash Securities (d)"].sum()) / 1e7),
              "fo_used": float(coll["Minimum Margins (a)"].sum() / 1e7), "fo_pro": None, "fo_client": None}
    margins = {"fo_deposit": fo["fo_deposit"], "fo_used": fo["fo_used"], "fo_pro": fo["fo_pro"], "fo_client": fo["fo_client"],
               "mcx_deposit": float(_num(mcx["TM / CP Margin Limit"]).sum() / 1e7),
               "mcx_used": float(_num(mcx["Total MU (Rs)"]).sum() / 1e7),
               "pro_util": float(_num(mcx["Margin Utilization"]).sum() / 1e7),
               "client_util": float(_num(mcx["Cli / TM Mrgn 90% (Rs)"]).sum() / 1e7)}
    return obl, cust, margins


def summary_text(df, metric: str) -> str:
    total = float(df[metric + "_Cr"].sum())
    if metric in ("PayInPayOut", "DayNetPremium", "MCXDayNetPremium"):
        tag = "NA" if abs(total) < 1e-9 else ("PayIn" if total >= 0 else "PayOut")
        shown = -abs(total) if total >= 0 else abs(total)
    else:
        tag = "NA" if abs(total) < 1e-9 else ("Profit" if total >= 0 else "Loss")
        shown = total
    return f"{shown:.2f}Cr ({tag})"


def top_lists(df, metric: str) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """[(row title, [(client, value text), ...]), ...] - the two rows of one metric block."""
    col = metric + "_Cr"

    def fmt(rows, sign=None):
        out = []
        for cid, v in rows:
            v = -abs(v) if sign == "-" else abs(v) if sign == "+" else v
            s = f"{v:.2f}"
            if s not in ("0.00", "-0.00"):
                out.append((str(cid), s))
        return out
    if metric in ("FuturesMTM", "MCXFuturesMTM"):
        g = df[df[col].round(2) > 0][["ClientID", col]].nlargest(10, col)
        l = df[df[col].round(2) < 0][["ClientID", col]].nsmallest(10, col)
        return [("Top Gainers -", fmt(g.itertuples(index=False))), ("Top Losers -", fmt(l.itertuples(index=False)))]
    if metric in ("DayNetPremium", "MCXDayNetPremium"):
        b = df[df[col] > 0][["ClientID", col]].nlargest(10, col)
        s = df[["ClientID", col]].nsmallest(10, col)
    else:
        b = df[["ClientID", col]].nlargest(10, col)
        s = df[["ClientID", col]].nsmallest(10, col)
    return [("Top Buyer -", fmt(b.itertuples(index=False), "-")), ("Top Seller -", fmt(s.itertuples(index=False), "+"))]


# ───────────────────────── the sheet (grid) ─────────────────────────
def _font(bold=False, size=15):
    names = (["calibrib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["calibri.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Sheet:
    def __init__(self, ncols: int):
        self.ncols = ncols
        self.cells = {}                                           # (row, col) -> dict(text, fill, bold, color, align, border)
        self.merges = []                                          # (r1, c1, r2, c2)
        self.nrows = 0

    def put(self, r, c, text="", fill=None, bold=False, color="#000000", align="left", border=True, size=15):
        self.cells[(r, c)] = dict(text=str(text), fill=fill, bold=bold, color=color, align=align, border=border, size=size)
        self.nrows = max(self.nrows, r)

    def merge(self, r1, c1, r2, c2):
        self.merges.append((r1, c1, r2, c2))
        self.nrows = max(self.nrows, r2)


def _summary(sh: Sheet, df, row: int, metrics: List[str]):
    for i, m in enumerate(metrics, 1):
        sh.put(row, i, m if m != "DayNetPremium" else "DAY NET PREMIUM", TEAL, True, WHITE, "center")
        sh.put(row + 1, i, summary_text(df, m), VALUE_GREEN, True, "#000000", "center")


def _blocks(sh: Sheet, df, row: int, metrics: List[str]) -> int:
    """The metric blocks (5 rows + 1 gap each). Returns the row after the last block's gap, like the script's current_row."""
    for m in metrics:
        lab, val = COLORS[m]
        sh.put(row, 1, "DAY NET PREMIUM" if m == "DayNetPremium" else m, lab, True, "#000000", "center")
        sh.merge(row, 1, row + 4, 1)
        for k in range(1, 5):                                     # the merged label shows its fill all over
            sh.put(row + k, 1, "", lab, border=True)
        (t1, r1), (t2, r2) = top_lists(df, m)
        for base, title, rows in ((row, t1, r1), (row + 3, t2, r2)):
            sh.put(base, 2, title, GREY, True, WHITE)
            sh.put(base + 1, 2, "Values(In Crores)", None, True)
            for j, (cid, v) in enumerate(rows):
                sh.put(base, 3 + j, cid, val)
                sh.put(base + 1, 3 + j, v, val)
        row += 6
    return row


def build_sheet(obl, cust, margins: dict) -> Sheet:
    ncols = 12
    sh = Sheet(ncols)
    # 1. Obligation
    sh.put(1, 1, "Obligation", TITLE_BLUE, True, WHITE, "center", size=19)
    sh.merge(1, 1, 1, ncols)
    _summary(sh, obl, 3, METRICS + MCX_METRICS)
    cur = _blocks(sh, obl, 7, METRICS + MCX_METRICS)
    # 2. FO / MCX deposit + margin block (header row, value row)
    info = cur + 2
    # line 1 = FO, line 2 = MCX (four boxes each)
    lines = [(info, "#E7F3FF", [("FO DEPOSIT", "fo_deposit"), ("TOTAL MARGIN USED (FO)", "fo_used"),
                                ("PRO MARGIN UTILISATION (FO)", "fo_pro"), ("CLIENT MARGIN UTILISATION (FO)", "fo_client")]),
             (info + 3, "#FDE9D9", [("MCX DEPOSIT", "mcx_deposit"), ("TOTAL MARGIN USED (MCX)", "mcx_used"),
                                    ("PRO MARGIN UTILIZATION (MCX)", "pro_util"), ("CLIENT MARGIN UTILIZATION (MCX)", "client_util")])]
    for r0, fill, items in lines:
        for i, (h, k) in enumerate(items, 1):
            v = margins.get(k)
            sh.put(r0, i, h, fill, True, "#000000", "center")
            sh.put(r0 + 1, i, "-" if v is None else f"{v:.2f} Cr", "#F8FBFF", True, "#000000", "center")
    # 3. Custodian Obligation
    start = info + 6
    sh.put(start, 1, "Custodian Obligation", TITLE_BLUE, True, WHITE, "center", size=19)
    sh.merge(start, 1, start, ncols)
    _summary(sh, cust, start + 2, METRICS)
    _blocks(sh, cust, start + 6, METRICS)
    sh.nrows = max(r for (r, _c) in sh.cells) + 1
    return sh


# ───────────────────────── painting ─────────────────────────
def render(path: str, obl, cust, margins: dict, footer: str = "") -> str:
    """footer: optional line painted in a dark-blue bar under the sheet (e.g. 'Report time: 19-11-2026  15:30')."""
    sh = build_sheet(obl, cust, margins)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    merged_cells = {(r, c) for (r1, c1, r2, c2) in sh.merges for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
    wide = {(r, c) for (r1, c1, r2, c2) in sh.merges if c2 > c1 for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
    widths = [64] * (sh.ncols + 1)
    for (r, c), cell in sh.cells.items():
        if (r, c) in wide or not cell["text"]:
            continue                                              # the title bars (merged across) do not count; the side labels do
        f = _font(cell["bold"], cell["size"])
        w = probe.textlength(cell["text"], font=f) + 10
        widths[c] = max(widths[c], int(w))
    xs = [6]
    for c in range(1, sh.ncols + 1):
        xs.append(xs[-1] + widths[c])
    W, H = xs[-1] + 6, 6 + sh.nrows * ROW_H + 6
    img = Image.new("RGB", (W, H), "#000000")
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, W - 7, H - 7], fill=WHITE)

    def box(r1, c1, r2, c2):
        return xs[c1 - 1], 6 + (r1 - 1) * ROW_H, xs[c2] - 1, 6 + r2 * ROW_H - 1

    def paint(r1, c1, r2, c2, cell):
        x0, y0, x1, y1 = box(r1, c1, r2, c2)
        if cell["fill"]:
            d.rectangle([x0, y0, x1, y1], fill=cell["fill"])
        if cell["border"] and (cell["fill"] or cell["text"]):
            d.rectangle([x0, y0, x1, y1], outline=THIN)
        if cell["text"]:
            f = _font(cell["bold"], cell["size"])
            bb = d.textbbox((0, 0), cell["text"], font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            ty = y0 + ((y1 - y0 + 1) - th) / 2 - bb[1]
            tx = x0 + ((x1 - x0 + 1) - tw) / 2 if cell["align"] == "center" else x0 + 4
            d.text((tx, ty), cell["text"], font=f, fill=cell["color"])
    for (r, c), cell in sorted(sh.cells.items()):
        if (r, c) not in merged_cells:
            paint(r, c, r, c, cell)
    for (r1, c1, r2, c2) in sh.merges:
        paint(r1, c1, r2, c2, sh.cells[(r1, c1)])
    if footer:
        FH = 34
        out = Image.new("RGB", (W, H + FH), "#000000")
        out.paste(img, (0, 0))
        od = ImageDraw.Draw(out)
        od.rectangle([6, H - 6, W - 7, H + FH - 7], fill="#1F3864")
        f = _font(True, 16)
        od.text(((W - od.textlength(footer, font=f)) / 2, H - 6 + (FH - 6 - 16) / 2 - 2), footer, font=f, fill=WHITE)
        img = out
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path)
    return path


def find_inputs(folder: str):
    """(main csv, FO file, SearchResults file) of a folder - the newest of each. FO file = the Level-1 row, else the old collateral file."""
    def newest(*pats):
        files = [f for p in pats for f in glob.glob(os.path.join(folder, p))]
        return max(files, key=os.path.getmtime) if files else None
    return (newest("MAIN*.csv"), newest("FO_Level1_Margins_*.csv", "FO_ClientLevelCollaterals_*.csv", "FO_ClientLevelCollaterals_*.txt", "FO_ClientLevelCollaterals_*.xlsx"),
            newest("SearchResults*.xlsx", "SearchResults*.csv"))
