import importlib.util, os, sys, json, tempfile, re, types
from datetime import datetime
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["SA_MCX_DEPOSIT_PASSWORD"] = "pw"
os.environ.pop("MCX_TOKEN_MODE", None)

def load():
    spec = importlib.util.spec_from_file_location("mdd", os.path.join(REPO, "sa_codes", "mcx_deposit_downloader.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

class Resp:
    def __init__(self, text="", status=200, content=b""):
        self.text, self.status_code, self._c = text, status, content
    def iter_content(self, n): yield self._c

class Server:
    """A pretend MCX: sessions (rndaak), an export list with pages, kick-outs."""
    def __init__(self, ours_on_page=2, ready_after_polls=0, kick_at=None, filler=60):
        self.valid = set(); self.n = 0; self.exports = []; self.polls = 0
        self.ours_on_page = ours_on_page; self.ready_after = ready_after_polls
        self.kick_at = kick_at or []          # poll numbers at which the session gets kicked
        self.filler = filler; self.export_count = 0; self.downloads = []
        self.pre_export_kick = 0
    def login(self):
        self.n += 1; r = f"R{self.n}"; self.valid = {r}; return r
    def rows_html(self, rows):
        trs = "".join(f"<tr><td>1</td><td>x</td><td>{q}</td><td>y</td><td>{st}</td><td>{ts}</td><td>{nm}</td></tr>" for q, st, ts, nm in rows)
        return f"<table><tbody>{trs}</tbody></table>"
    def all_rows(self):
        rows = [("Equals 2026", "Report Generated", "29/10/2026 08:00:00", f"old{i}-20261029080000.xlsx") for i in range(self.filler)]
        for stamp in self.exports:
            polls_done = self.polls
            st = "Report Generated" if polls_done > self.ready_after else "In Queue"
            ts = datetime.strptime(stamp, "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M:%S")
            rows.append(("Transaction From Date Equals 29/10/2026", st, ts, f"AbCd-{stamp}.xlsx"))
        return rows

def make_session_cls(srv, log):
    class Sess:
        def __init__(self): self.cookies = {}; self.headers = {}
        def get(self, url, **k): return Resp("ok")
        def post(self, url, headers=None, data=None, stream=False, **k):
            data = data or {}
            if "authMode" in url: return Resp("ok")
            if "authenticatePassward" in url: return Resp("ok")
            if "authenticateToken" in url:
                return Resp("<EntityList><EntityDetail><entityCode>PRD</entityCode><shortDescription>x</shortDescription></EntityDetail></EntityList>")
            if "method=Login" in url:
                return Resp(f"<input name='rndaak' value='{srv.login()}'>")
            rn = data.get("rndaak")
            if "CommonImpl" in url or "welcome" in url or "layout.save" in url: return Resp("ok")
            if rn not in srv.valid: return Resp("<html>YOUR SESSION HAS EXPIRED</html>")
            if "RSK383.do" in url:
                if data.get("Export"):
                    if srv.pre_export_kick > 0:
                        srv.pre_export_kick -= 1; srv.valid = set(); return Resp("<html>YOUR SESSION HAS EXPIRED</html>")
                    srv.export_count += 1
                    srv.exports.append(data["dateStamp"] + data["timeStamp"].replace(":", "")); log.append("EXPORT")
                    return Resp("ExportBatchSuccess")
                if data.get("GRID_RESPONSE") == "RSK383_Table": return Resp("RSK383_Table")
                return Resp("page")
            if "ar_w_DataExport.do" in url:
                if "GRID_RESPONSE" in data and data["GRID_RESPONSE"] == "ar_w_DataExport_Table" and "xmlReq" not in data:
                    srv.polls += 1
                    if srv.polls in srv.kick_at:
                        srv.valid = set(); return Resp("<html>YOUR SESSION HAS EXPIRED</html>")
                    rows = srv.all_rows(); return Resp(srv.rows_html(rows[:50]))
                if data.get("xmlReq") == "YES":
                    rows = srv.all_rows(); page = int(data["RowNumKeyForPrevNxt"])
                    return Resp(srv.rows_html(rows[50 * page: 50 * (page + 1)]))
                return Resp('<input id="tabId" value="panel4">')
            if "DownloadFileAction1" in url:
                if "checkForFile" in url: return Resp("FileExists")
                fn = re.search(r"fileName=([^&]+)", url).group(1); srv.downloads.append(fn)
                return Resp("", 200, b"XLSX:" + fn.encode())
            return Resp("ok")
    return Sess

def run(srv, state=None, pending=None, mod=None):
    m = mod or load(); log = []
    m.requests.Session = make_session_cls(srv, log)
    m.time.sleep = lambda s: None
    m._token_popup = lambda a, b, c=None: "123456"
    m._auto_mode = lambda: False
    out = tempfile.mkdtemp()
    if state: os.environ["MCX_EXPORT_STATE_FILE"] = state
    else: os.environ.pop("MCX_EXPORT_STATE_FILE", None)
    if pending: os.environ["MCX_PENDING_JSON"] = json.dumps(pending)
    else: os.environ.pop("MCX_PENDING_JSON", None)
    m._acquire_mcx_lock = lambda o: None; m._release_mcx_lock = lambda o=None: None
    p = m._download_mcx_deposit_once(out)
    return p, log

def test_file_on_page_2_is_found():
    srv = Server(); p, log = run(srv)
    assert p and open(p, "rb").read().startswith(b"XLSX:AbCd-"), p
    assert srv.export_count == 1

def test_kicked_after_export_same_file_no_new_export():
    srv = Server(kick_at=[1], ready_after_polls=0); p, log = run(srv)
    assert p, "no file"
    assert srv.export_count == 1, f"exported {srv.export_count} times"
    assert srv.n == 2        # logged in twice

def test_kicked_before_export_logs_in_and_exports_once():
    srv = Server(); srv.pre_export_kick = 1; p, log = run(srv)
    assert p and srv.export_count == 1 and srv.n == 2

def test_retry_resumes_the_same_export():
    srv = Server(ready_after_polls=10**6)          # never ready in the first run
    d = tempfile.mkdtemp(); st = os.path.join(d, ".mcx_export_state.json")
    m = load(); m.POLL = None
    os.environ["MCX_EXPORT_POLL_SEC"] = "30"
    p, log = run(srv, state=st, mod=m)
    assert p is None and srv.export_count == 1
    srv.ready_after = 0
    p2, log2 = run(srv, state=st)
    assert p2, "second run did not fetch the file"
    assert srv.export_count == 1, "a retry pressed Export again"
    assert json.load(open(st))["done"] is True
    os.environ.pop("MCX_EXPORT_POLL_SEC")

def test_catch_up_earlier_folder():
    srv = Server()
    srv.exports.append("20261029101500")              # earlier folder's export, generated now
    d = tempfile.mkdtemp(); out_old = os.path.join(d, "MCX_Web_Allocation_01.xlsx")
    json.dump({"stamp": "20261029101500", "done": False, "date": "20261029"}, open(os.path.join(d, ".mcx_export_state.json"), "w"))
    p, log = run(srv, pending=[{"slot": 1, "stamp": "20261029101500", "out": out_old}])
    assert os.path.isfile(out_old) and open(out_old, "rb").read().startswith(b"XLSX:AbCd-20261029101500")
    assert json.load(open(os.path.join(d, ".mcx_export_state.json")))["done"] is True
    assert p                                           # own file downloaded as well

def test_catch_up_still_in_queue_stays_pending():
    srv = Server(ready_after_polls=10**6); srv.exports.append("20261029101500")
    d = tempfile.mkdtemp(); out_old = os.path.join(d, "MCX_Web_Allocation_01.xlsx")
    os.environ["MCX_EXPORT_POLL_SEC"] = "15"
    run(srv, pending=[{"slot": 1, "stamp": "20261029101500", "out": out_old}])
    os.environ.pop("MCX_EXPORT_POLL_SEC")
    assert not os.path.exists(out_old)

if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    sys.exit(1 if fails else 0)
