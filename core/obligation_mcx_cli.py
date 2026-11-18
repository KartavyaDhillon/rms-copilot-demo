"""Child process of the OBLIGATION card: MCX eclear RSK335 "Margin Utilization View" (all rows, blank search) -> SearchResults xlsx.

Same login as every other MCX download of the console (core/mcx_grid.login): password from Credentials (SA_MCX_DEPOSIT_*), the RSA
code filled automatically (MCX_TOKEN_MODE=auto - the soft-token seed, never a popup, at most 2 wrong codes). Runs in its own
process so Stop kills it at once.  Tags the parent understands: [MCX_DEPOSIT_AUTH_FAIL], [MCX_TOKEN_FAIL], [OBL_MCX_FAILED].

    python core/obligation_mcx_cli.py --out <folder>   ->  <folder>/SearchResults_<yyyymmddHHMMSS>.xlsx   (prints '[OBL_MCX_RESULT] <path>')
"""
import argparse
import importlib.util
import os
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

COLUMNS_NEEDED = ["TM / CP Margin Limit", "Total MU (Rs)", "Margin Utilization", "Cli / TM Mrgn 90% (Rs)"]


def _load(rel):
    spec = importlib.util.spec_from_file_location(os.path.basename(rel)[:-3] + "_obl", os.path.join(REPO, *rel.split("/")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_search_results(grid, sess, rnd, out_dir, log=print):
    """The Margin Utilization View with nothing filled in (every TM / CP) -> saved xlsx path, rows."""
    url = grid._RSK335_URL
    grid._page_init(sess, rnd, "RSK335", "RSK", "Margin Utilization View", "panel3")

    def post(size):
        d = grid._data_335(rnd, "", size)
        d["sQuery"] = "Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT"
        r = sess.post(url, headers=dict(grid._FETCH_H, Referer=url), timeout=300, data=d)
        if grid._expired(r.text, "RSK335_Table"):
            raise grid._SessionLost()
        return r.text
    heads, rows, amounts, _total = grid._fetch_whole(post, "RSK335_Table", log)
    norm = {" ".join(str(h).split()).lower() for h in heads}
    missing = [c for c in COLUMNS_NEEDED if c.lower() not in norm]
    if missing:
        raise RuntimeError(f"column(s) {missing} are not in the Margin Utilization View - found: {heads}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"SearchResults_{datetime.now():%Y%m%d%H%M%S}.xlsx")
    grid.write_excel(heads, rows, amounts, path)
    return path, len(rows)


def download(out_dir, user, password, token_fn, log=print, tries=4, sleep=time.sleep, grid=None, session_factory=None):
    """One fresh login (= the next RSA code) per attempt. A wrong password / refused code stops at once."""
    grid = grid or _load("core/mcx_grid.py")
    import requests
    last = ""
    for attempt in range(1, tries + 1):
        log(f"=== MCX Margin Utilization View - attempt {attempt}/{tries} ===")
        sess = session_factory() if session_factory else requests.Session()
        try:
            rnd = grid.login(sess, user, password, token_fn, log, 2, "MCX_DEPOSIT")
            path, n = fetch_search_results(grid, sess, rnd, out_dir, log)
            log(f"OK: {n} rows -> {os.path.basename(path)}")
            return path
        except (grid.GridAuthError, grid.GridTokenError):
            raise
        except grid._SessionLost:
            last = "the MCX session ended (someone else logged in with this user)"
            log(last + " - logging in again with the next RSA code.")
        except Exception as e:                                   # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            log(f"Attempt {attempt}/{tries} failed: {last}")
        if attempt < tries:
            sleep(5)
    raise RuntimeError(last or "no file")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault("MCX_TOKEN_MODE", "auto")
    user = os.getenv("SA_MCX_DEPOSIT_USER", "").strip() or "56720CMMKR014139"      # the console's shared eclear user id (not a secret)
    password = os.getenv("SA_MCX_DEPOSIT_PASSWORD", "").strip()
    if not password:
        print("[MCX_DEPOSIT_AUTH_FAIL] SA_MCX_DEPOSIT_PASSWORD is not set - add it in Credentials")
        return 2
    dep = _load("sa_codes/mcx_deposit_downloader.py")
    grid = _load("core/mcx_grid.py")
    try:
        path = download(a.out, user, password, dep._token_popup, print, grid=grid)
    except (grid.GridAuthError, grid.GridTokenError) as e:
        print(f"[OBL_MCX_FAILED] {e}")
        return 3
    except Exception as e:                                       # noqa: BLE001
        print(f"[OBL_MCX_FAILED] {type(e).__name__}: {e}")
        return 1
    print(f"[OBL_MCX_RESULT] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
