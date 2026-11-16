"""Tests for core/mcx_grid.py (grid-view download of MCX Deposit RSK383 + MCX 90% RSK335) and its hooks.
Runs with pytest or plain `python tests/test_mcx_grid.py`. No network: sessions / logins are faked."""
import importlib.util
import os
import re
import sys
import tempfile
import types
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from core import mcx_grid as G                                   # noqa: E402


def _grid_html(rows, table="RSK383_Table", total=None, hidden_last=True):
    """A tiny RSK-style grid: 4 visible columns (one amount) + 1 hidden column + a stray trailing cell."""
    ths = ('<th columnLabel="a" data-sort="false">UCC Code&nbsp;</th><th columnLabel="b">Client Name&nbsp;</th>'
           '<th columnLabel="c">Margin Limit&nbsp;</th><th columnLabel="d">Date&nbsp;</th>'
           '<th columnLabel="e">wca_hidden&nbsp;</th>')
    trs = ""
    for i, (code, name, amt) in enumerate(rows):
        trs += (f'<tr class="c_even_Row" tableId="{table}"><td svrVal="{code}" class=" ">{code}</td>'
                f'<td svrVal="{name}" class=" ">{name}</td>'
                f'<td svrVal="{amt}" class="alignRight " isAmountField="true">{amt}</td>'
                f'<td svrVal="20261116" class="alignCenter ">16/11/2026</td>'
                f'<td svrVal="111108" class=" hide">111108</td><td class="hide"></td></tr>')
    t = len(rows) if total is None else total
    return (f'IXHRts#*#1~|~ErrorSeverity#*#0~|~~|~{table}#*#<table tableRecSize="{t}" id="{table}"><thead><tr>{ths}</tr></thead>'
            f"<tbody>{trs}</tbody></table>")


# ------------------------------------------------------------------ the switch
def test_mode_default_set_and_env(tmp_path=None):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "mode.json")
    prev = os.environ.pop("MCX_DOWNLOAD_MODE", None)               # tests/__init__.py pins "queue" for the older tests
    try:
        _mode_checks(f)
    finally:
        if prev is not None:
            os.environ["MCX_DOWNLOAD_MODE"] = prev


def _mode_checks(f):
    assert G.get_mode(f) == "grid"                                # nothing saved -> grid (the new method)
    G.set_mode("queue", "tester", f)
    assert G.get_mode(f) == "queue" and G.describe(f)["by"] == "tester"
    G.set_mode("grid", "tester", f)
    assert G.get_mode(f) == "grid"
    try:
        G.set_mode("bogus", "x", f)
        assert False, "must reject unknown modes"
    except ValueError:
        pass
    os.environ["MCX_DOWNLOAD_MODE"] = "queue"
    try:
        assert G.get_mode(f) == "queue" and G.describe(f)["env_override"] is True
    finally:
        os.environ.pop("MCX_DOWNLOAD_MODE", None)


def test_file_names_match_what_the_console_looks_for():
    assert re.fullmatch(r"MCX_DEPOSIT_\d\d\.\d\d\.xlsx", G.deposit_name())          # Short Allocation / ms15 'deposit'
    n = G.portal_name(datetime(2026, 11, 16, 12, 20, 18))
    assert re.fullmatch(r"[A-Za-z0-9]{10}-20261116122018\.xlsx", n), n               # = VFGrGQDoaM-20261109082512
    assert re.search(r"-\d{14}\.xlsx$", n) and "20261116" in n                      # ms15_auto / pipeline_box patterns
    assert G.portal_name() != G.portal_name()


# ------------------------------------------------------------------ parsing + excel
def test_parse_grid_drops_hidden_columns_and_reads_amounts():
    heads, rows, amounts = G.parse_grid(_grid_html([("03PI94", "KARAN", "2.67861904855E9"), ("070012138", "X", "0.0")]),
                                        "RSK383_Table")
    assert heads == ["UCC Code", "Client Name", "Margin Limit", "Date"]               # wca_hidden + trailing cell gone
    assert rows[1][0] == "070012138"                                                  # leading zero kept
    assert amounts == {"Margin Limit"}


def test_excel_has_real_numbers_and_dates():
    import openpyxl
    heads, rows, amounts = G.parse_grid(_grid_html([("03PI94", "KARAN", "2.67861904855E9")]), "RSK383_Table")
    p = os.path.join(tempfile.mkdtemp(), "x.xlsx")
    G.write_excel(heads, rows, amounts, p)
    ws = openpyxl.load_workbook(p).active
    assert [c.value for c in ws[1]] == heads
    assert ws.cell(row=2, column=3).value == 2678619048.55                           # no 'E9' text
    assert isinstance(ws.cell(row=2, column=4).value, datetime)
    assert ws.cell(row=2, column=1).value == "03PI94"
    assert not os.path.exists(os.path.join(os.path.dirname(p), ".tmp_mcx", "x.xlsx"))  # temp file moved, not left behind


# ------------------------------------------------------------------ retry logic (5 tries, only what is missing)
class _Env:
    """Fakes login() and fetch_one() so run_grid can be driven without a server."""

    def __init__(self, plan):
        self.plan = plan                # {(attempt, key): exception | None}
        self.logins = 0
        self.fetched = []
        self.pauses = []

    def install(self):
        env = self
        self._old = (G.login, G.fetch_one)

        def login(sess, *a, **k):
            env.logins += 1
            return f"R{env.logins}"

        def fetch_one(sess, rnd, key, out_dir, log=print, cm="56720", date_api=None, names=None):
            env.fetched.append((env.logins, key))
            exc = env.plan.get((env.logins, key))
            if exc:
                raise exc
            p = os.path.join(out_dir, f"{key}-{env.logins}.xlsx")
            open(p, "w").write("x")
            return p, 3

        G.login, G.fetch_one = login, fetch_one
        return self

    def uninstall(self):
        G.login, G.fetch_one = self._old


def _run(plan, want=("deposit", "margin"), tries=5, **kw):
    env = _Env(plan).install()
    out, lines = tempfile.mkdtemp(), []
    try:
        got = G.run_grid(want, out, "u", "p", lambda a, m, e: "12345678", log=lines.append, tries=tries,
                         sleep=lambda s: None, session_factory=lambda: object(),
                         before_relogin=lambda n: env.pauses.append(n), **kw)
    finally:
        env.uninstall()
    return got, env, lines


def test_both_files_in_one_login():
    got, env, _ = _run({})
    assert set(got) == {"deposit", "margin"} and env.logins == 1


def test_session_lost_on_second_file_relogs_in_and_fetches_only_the_missing_one():
    got, env, lines = _run({(1, "margin"): G._SessionLost()})
    assert set(got) == {"deposit", "margin"}
    assert env.logins == 2
    assert env.fetched == [(1, "deposit"), (1, "margin"), (2, "margin")]               # deposit NOT downloaded again
    assert env.pauses == [2]                                                           # hook before the 2nd login
    assert any("logging in again" in l for l in lines)


def test_five_tries_then_gives_up_with_partial_result():
    plan = {(n, "margin"): RuntimeError("boom") for n in range(1, 6)}
    got, env, lines = _run(plan)
    assert set(got) == {"deposit"} and env.logins == 5
    assert [f for f in env.fetched if f[1] == "deposit"] == [(1, "deposit")]
    assert any("[MCX_GRID_FAILED]" in l and "margin" in l for l in lines)


def test_wrong_password_and_refused_code_stop_at_once():
    for exc in (G.GridAuthError("pw"), G.GridTokenError("code")):
        env = _Env({}).install()
        old_login = G.login
        G.login = lambda *a, **k: (_ for _ in ()).throw(exc)
        try:
            try:
                G.run_grid(["deposit"], tempfile.mkdtemp(), "u", "p", lambda a, m, e: "1", log=lambda s: None,
                           sleep=lambda s: None, session_factory=lambda: object())
                assert False, "must raise"
            except type(exc):
                pass
        finally:
            G.login = old_login
            env.uninstall()


def test_only_the_wanted_file_is_fetched():
    got, env, _ = _run({}, want=("margin",))
    assert set(got) == {"margin"} and env.fetched == [(1, "margin")]


def test_custom_names_reach_fetch_one():
    seen = {}
    old = G.fetch_one
    G.fetch_one = lambda sess, rnd, key, out, log=print, cm="", date_api=None, names=None: (seen.update(names or {}) or ("p", 1))
    oldl = G.login
    G.login = lambda *a, **k: "R"
    try:
        G.run_grid(["margin"], tempfile.mkdtemp(), "u", "p", lambda *a: "1", log=lambda s: None, sleep=lambda s: None,
                   session_factory=lambda: object(), names={"margin": "Clientwise_mcx_margin.xlsx"})
    finally:
        G.fetch_one, G.login = old, oldl
    assert seen == {"margin": "Clientwise_mcx_margin.xlsx"}


# ------------------------------------------------------------------ the whole fetch with a fake portal
def test_fetch_one_reads_all_rows_in_one_request_and_checks_columns():
    calls = []

    class Sess:
        def post(self, url, headers=None, data=None, **k):
            calls.append(data if isinstance(data, str) else dict(data))
            body = data if isinstance(data, str) else "&".join(f"{a}={b}" for a, b in data.items())
            m = re.search(r"confViewSize=(\d+)", body)
            size = int(m.group(1)) if m else 50
            rows = [(f"C{i}", f"N{i}", "1.5") for i in range(7)][:size]
            return types.SimpleNamespace(text=_grid_html(rows, "RSK383_Table", total=7))

    out = tempfile.mkdtemp()
    path, n = G.fetch_one(Sess(), "R", "deposit", out, lambda s: None)
    assert n == 7 and re.fullmatch(r"MCX_DEPOSIT_\d\d\.\d\d\.xlsx", os.path.basename(path))
    assert any("confViewSize=6000" in (c if isinstance(c, str) else "") for c in calls)     # ONE big request


def test_fetch_one_rejects_a_file_without_the_columns_the_console_needs():
    class Sess:
        def post(self, url, headers=None, data=None, **k):
            return types.SimpleNamespace(text=_grid_html([("1", "A", "2")], "RSK335_Table"))

    try:
        G.fetch_one(Sess(), "R", "margin", tempfile.mkdtemp(), lambda s: None)       # needs Client Code + Client Margin Limit
        assert False, "must refuse"
    except RuntimeError as e:
        assert "missing" in str(e)


def test_session_expired_answer_is_detected():
    assert G._expired("... YOUR SESSION HAS EXPIRED ...", "RSK383_Table")
    assert G._expired("<html><body><form name=login></form></body></html>", "RSK383_Table")
    assert not G._expired(_grid_html([("1", "A", "2")]), "RSK383_Table")


# ------------------------------------------------------------------ hooks: the previous method is kept + LIVE ALLOCATION fallback
def _load_deposit_downloader():
    os.environ["SA_MCX_DEPOSIT_PASSWORD"] = "pw"
    os.environ["SA_OUTPUT_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("mdd_grid_test", os.path.join(REPO, "sa_codes", "mcx_deposit_downloader.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Boom(Exception):
    pass


def _old_method_reached(caller, grid_result):
    """Run download_mcx_deposit with a fake grid module; True when the PREVIOUS method started (it begins with a new
    requests.Session(), which we make explode)."""
    m = _load_deposit_downloader()
    fake = types.SimpleNamespace(enabled=lambda: True, GridAuthError=G.GridAuthError, GridTokenError=G.GridTokenError,
                                 run_grid=lambda *a, **k: grid_result)
    m._load_mcx_grid = lambda: fake
    hit = []

    class S:
        def __init__(self):
            hit.append(1)
            raise _Boom()

    m.requests.Session = S
    os.environ["MCX_DEPOSIT_CALLER"] = caller
    out = tempfile.mkdtemp()
    res = m.download_mcx_deposit(output_dir=out)
    return bool(hit), res


def test_hook_grid_success_returns_the_file_and_never_touches_the_old_method():
    reached, res = _old_method_reached("sa", {"deposit": "C:/x/MCX_DEPOSIT_01.20.xlsx"})
    assert not reached and res == "C:/x/MCX_DEPOSIT_01.20.xlsx"


def test_hook_other_callers_fail_without_falling_back():
    reached, res = _old_method_reached("ctcl", {})
    assert not reached and res is None


def test_hook_live_allocation_falls_back_to_the_previous_method():
    reached, res = _old_method_reached("liveallocation", {})
    assert reached, "LIVE ALLOCATION must use the queued export when the grid view failed 5 times"


def test_hook_queue_mode_runs_only_the_old_method():
    m = _load_deposit_downloader()
    m._load_mcx_grid = lambda: types.SimpleNamespace(enabled=lambda: False)
    hit = []

    class S:
        def __init__(self):
            hit.append(1)
            raise _Boom()

    m.requests.Session = S
    os.environ["MCX_DEPOSIT_CALLER"] = "sa"
    assert m.download_mcx_deposit(output_dir=tempfile.mkdtemp()) is None and hit


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:                                # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
