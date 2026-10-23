"""Send ONE test failure-alert picture to WhatsApp, to check the alert end to end.

Run from the console folder (the same Python the console uses):

    .venv\\Scripts\\python tools\\send_test_failure_alert.py otm_3_9_blocking_files
    .venv\\Scripts\\python tools\\send_test_failure_alert.py otm_3_9_blocking_files 9812345678
    .venv\\Scripts\\python tools\\send_test_failure_alert.py otm_3_9_blocking_files 9812345678 MISSED

Arguments: 1) process key (the key is shown by `--list`), 2) optional 10-digit number to send to
(otherwise the numbers saved for that process, then the common list in Credentials > WhatsApp),
3) optional kind: FAILED (default) | MISSED | INTERRUPTED.
Nothing is run or changed - only the picture is built and sent, and it says TEST in the reason.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv):
    from core.scheduler import catalog, failure_alert as fa
    catalog.load_builtin()
    if not argv or argv[0] == "--list":
        print("Process keys:")
        for p in sorted(catalog.all_defs() if hasattr(catalog, "all_defs") else [], key=lambda d: d.key):
            print(f"  {p.key:<40} {p.name}")
        if not hasattr(catalog, "all_defs"):
            print("  (see the Processes tab; the key is the lower-case name with underscores, e.g. otm_3_9_blocking_files)")
        return 0
    pdef = catalog.get(argv[0])
    if pdef is None:
        print(f"Unknown process key '{argv[0]}'. Use --list.")
        return 1
    kind = (argv[2] if len(argv) > 2 else "FAILED").upper()
    if kind not in fa.KINDS:
        print(f"Kind must be one of: {', '.join(fa.KINDS)}")
        return 1
    if len(argv) > 1 and argv[1].strip():
        nums = fa.parse_numbers(argv[1])
        if not nums:
            print(f"'{argv[1]}' is not a valid number (type the 10-digit mobile number).")
            return 1
        fa.numbers_for = lambda _p, _s, _n=nums: (_n, "test number")      # send ONLY to this number
    sched = {"id": "test-" + argv[0], "name": f"TEST - {pdef.name}", "process_key": argv[0]}
    res = fa.notify(sched, pdef, "testrun00000", "RuntimeError", "TEST ALERT - please ignore. Nothing really failed.",
                    None, "MANUAL_RUN_NOW", 1, kind=kind, now=10 ** 10)
    print(res)
    print("SENT - check WhatsApp." if res.get("sent") else f"NOT SENT - {res.get('reason')}")
    if res.get("image"):
        print("Picture saved at:", res["image"])
    return 0 if res.get("sent") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
