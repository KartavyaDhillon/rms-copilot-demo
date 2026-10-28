"""Child process of LIVE ALLOCATION CAPTURE: MCX RSK383 download -> MCX_Web_Allocation_<NN>.xlsx.

Runs in its own process so the capture can be stopped instantly (the parent kills it) and a token wait or a re-login
wait never blocks the console.  Prints tags the parent understands: [MCX_DEPOSIT_AUTH_FAIL], [MCX_TOKEN_FAIL],
[MCX_DEPOSIT_BUSY:...]."""
import argparse
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--slot", type=int, required=True)
    a = ap.parse_args()
    os.environ.setdefault("MCX_TOKEN_MODE", "auto")
    os.environ.setdefault("MCX_DEPOSIT_CALLER", "liveallocation")
    spec = importlib.util.spec_from_file_location("mcx_deposit_downloader", os.path.join(REPO, "sa_codes", "mcx_deposit_downloader.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.makedirs(a.out, exist_ok=True)
    try:
        path = mod.download_mcx_deposit(a.out)
    except mod.MCXDepositBusyError as e:
        print(f"[MCX_DEPOSIT_BUSY:{e.owner}] {e}")
        return 3
    if not path or not os.path.isfile(path):
        print("MCX download produced no file")
        return 2
    final = os.path.join(a.out, f"MCX_Web_Allocation_{a.slot:02d}.xlsx")
    if os.path.exists(final):
        os.remove(final)
    os.replace(path, final)
    print(f"✅ Saved: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
