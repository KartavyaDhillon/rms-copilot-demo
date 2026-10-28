"""DARWINBOX EXIT CLEARANCE - e-mails new Darwinbox separation tasks, collects the replies and submits the Act form.

All the logic is in core/scheduler/darwin_lib.py (moved unchanged from D:\\DARWIN\\darwinbox_exit_mailer.py). Schedule it
twice a day (10:00 and 15:00). It is safe to repeat: employees already e-mailed are remembered in
data/darwinbox_exit/darwinbox_sent_records.json and are never sent again; a run with nothing to do is recorded as SKIPPED.
Credentials (Darwinbox login, mailbox, WhatsApp token) are set with the Credentials button - never in code or git."""
from core.scheduler import connections, darwin_lib as L, helpers as H
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "DARWINBOX EXIT CLEARANCE"
_TRUE = (True, "true", "1", "yes", "on", "y")


def settings_from_connections(params: dict) -> dict:
    """Credentials from the console's connections + the process settings -> what darwin_lib.configure() needs."""
    d, sm, po, wa = (connections.get(g) for g in ("darwinbox", "smtpb", "pop3b", "whatsapp"))
    s = {"darwin_user": d.get("username"), "darwin_password": d.get("password"),
         "smtp_host": sm.get("server"), "smtp_port": sm.get("port"), "smtp_user": sm.get("email"), "smtp_password": sm.get("password"),
         "pop_host": po.get("server"), "pop_port": po.get("port"), "pop_user": po.get("username") or sm.get("email"),
         "pop_password": po.get("password") or sm.get("password"),
         "wa_token": wa.get("auth_token"), "wa_upload_url": wa.get("upload_url"), "wa_send_url": wa.get("send_url"),
         "wa_template": wa.get("template_name"), "wa_lang": wa.get("template_lang")}
    params = params or {}
    to = H.parse_emails(params.get("to"))
    if to:
        s["to"] = to
    if "cc" in params:
        s["cc"] = H.parse_emails(params.get("cc"))
    if "overdue_phones" in params:
        s["overdue_phones"] = H.parse_phones(params.get("overdue_phones"))
    if params.get("signature"):
        s["signature"] = params["signature"]
    if "act_submit" in params:
        s["act_submit"] = params["act_submit"] in _TRUE
    return s


@H.logged(FOLDER)
def _run(params, ctx):
    connections.require("darwinbox", "username", "password")
    connections.require("smtpb", "email", "password")
    connections.require("pop3b", "username", "password")
    L.configure(settings_from_connections(params))
    L.LOG_HOOK = ctx.log
    L.CANCEL = lambda: bool(getattr(ctx, "cancelled", False))
    try:
        res = L.run_once()
    except L.AppError as e:
        raise RuntimeError(str(e))
    finally:
        L.LOG_HOOK = None
        L.CANCEL = None
    st, rc = res["stats"], res["rc"]
    if st.get("blocked"):
        raise RuntimeError("The Act step was STOPPED by a mismatch - nothing was submitted. "
                           "See Darwin_Act_BLOCKED.txt in data/darwinbox_exit (an e-mail was sent to the sender).")
    if rc:
        raise RuntimeError(f"The Darwinbox run finished with problems (code {rc}) - see the log lines above. "
                           "Nothing is lost: the next run tries again.")
    parts = [f"{st[k]} {label}" for k, label in (("emailed", "employee(s) e-mailed"), ("replies", "reply(ies) read"),
                                                 ("submitted", "Act form(s) submitted"), ("reminders", "reminder(s) sent"),
                                                 ("alerts", "overdue alert(s) sent")) if st.get(k)]
    if not parts:
        # a normal, healthy run with nothing to do is a SUCCESS (shown green), not "skipped"
        return "Checked - nothing new (no new task, no new reply, nothing to submit)"
    return ", ".join(parts)


register(
    key="darwinbox_exit_clearance",
    name="DARWINBOX EXIT CLEARANCE",
    description="Reads new resignations from Darwinbox, e-mails Manohar / Jagesh / Anuj, collects their replies, checks the "
                "shortfall against the risk report and submits the Darwinbox Act form. Never sends the same employee twice.",
    run=_run,
    params=[
        Param("to", "E-mail To (the 3 people asked for details)", "text", required=True, fmt="emails", scope="process",
              default=",".join(L.TO)),
        Param("cc", "E-mail Cc", "text", fmt="emails", scope="process", default=",".join(L.CC)),
        Param("overdue_phones", "WhatsApp numbers for the 'reply overdue' picture", "text", fmt="phones", scope="process",
              default=",".join(L.WA_TO), help="Gets a picture when Manohar / Jagesh / Anuj have not replied for 24 hours."),
        Param("signature", "E-mail signature", "text", scope="process", default="Best regards,\nSandeep Kumar\n011-42111079"),
        Param("act_submit", "Submit the Act form on Darwinbox (ON = really submits, OFF = test only)", "bool",
              scope="process", default=True,
              help="OFF builds the form and saves it to Darwin_Act_Preview.xlsx but submits nothing."),
    ],
    retry_safe=False, parallel_safe=False, category="HR", folder=FOLDER,
    uses=("darwinbox", "smtpb", "pop3b", "whatsapp"),
)
