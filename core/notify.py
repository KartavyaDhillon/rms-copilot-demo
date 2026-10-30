"""
Notification layer (side-effects: WhatsApp + email). Secrets/recipients from .env.

Matches your original routing:
  - F_filtered (FO clients) -> caption "BJDA_Peak Margin" -> BOTH recipients
  - C_filtered (CM/FO/Total) -> caption "06436_Peak_Margin" -> FIRST recipient only

Every call returns a structured result with a clear reason on failure (for debugging).
Called by the backend only after approval OR when AUTO_SEND=true.
"""
import os
import smtplib
import traceback
import requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import formataddr
from dotenv import load_dotenv

load_dotenv()

WA_AUTH = os.getenv("WA_AUTH_TOKEN")
WA_RECIPIENTS = [r.strip() for r in os.getenv("WA_RECIPIENTS", "").split(",") if r.strip()]
WA_TEMPLATE = os.getenv("WA_TEMPLATE", "daily_report")
WA_UPLOAD = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media"
WA_SEND = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages"

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
SENDER_NAME = os.getenv("SENDER_NAME", "RMS")
MAIL_TO = [m.strip() for m in os.getenv("MAIL_TO", "").split(",") if m.strip()]
MAIL_CC = [m.strip() for m in os.getenv("MAIL_CC", "").split(",") if m.strip()]


def _upload(image_path):
    """Upload one image, return (media_id, error)."""
    try:
        with open(image_path, "rb") as f:
            up = requests.post(WA_UPLOAD,
                headers={"Authorization": f"Bearer {WA_AUTH}", "accept": "application/json"},
                files={"file": (os.path.basename(image_path), f, "image/png")}, timeout=30)
        if up.status_code not in (200, 201):
            return None, f"upload {up.status_code}: {up.text[:200]}"
        return (up.json().get("id") or up.json().get("mediaHandle")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def send_whatsapp(image_path, caption, recipients):
    """Send one image as the template to specific recipients. Structured result."""
    if not WA_AUTH:
        return {"ok": False, "error": "WA_AUTH_TOKEN missing in .env"}
    if not os.path.exists(image_path):
        return {"ok": False, "error": f"image not found: {image_path}"}
    media_id, err = _upload(image_path)
    if err:
        return {"ok": False, "error": err}
    today = datetime.now().strftime("%d-%m-%Y")
    results = []
    for to in recipients:
        try:
            payload = {"to": to, "type": "template", "source": "external",
                "template": {"name": WA_TEMPLATE, "language": {"code": "en"},
                    "components": [
                        {"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]},
                        {"type": "body", "parameters": [
                            {"type": "text", "text": caption},
                            {"type": "text", "text": today}]}]}}
            r = requests.post(WA_SEND,
                headers={"Authorization": f"Bearer {WA_AUTH}", "Content-Type": "application/json"},
                json=payload, timeout=30)
            ok = r.status_code in (200, 201)
            results.append({"to": to, "ok": ok, "detail": None if ok else r.text[:200]})
        except Exception as e:
            results.append({"to": to, "ok": False, "detail": f"{type(e).__name__}: {e}"})
    return {"ok": any(x["ok"] for x in results), "results": results}


def send_email_with_image(subject, body_text, image_path):
    if not (SMTP_SERVER and SENDER_EMAIL and SENDER_PASSWORD):
        return {"ok": False, "error": "SMTP settings missing in .env"}
    if not MAIL_TO:
        return {"ok": False, "error": "MAIL_TO empty in .env"}
    if not os.path.exists(image_path):
        return {"ok": False, "error": f"image not found: {image_path}"}
    try:
        msg = MIMEMultipart("related")
        msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
        msg["To"] = ",".join(MAIL_TO)
        if MAIL_CC:
            msg["Cc"] = ",".join(MAIL_CC)
        msg["Subject"] = subject
        cid = os.path.basename(image_path)
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(f"{body_text}<br><img src='cid:{cid}'><br><br>Best regards,<br>{SENDER_NAME}", "html"))
        msg.attach(alt)
        with open(image_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=cid)
            msg.attach(img)
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, MAIL_TO + MAIL_CC, msg.as_string())
        server.quit()
        return {"ok": True, "to": MAIL_TO, "cc": MAIL_CC}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-400:]}


def send_peak_margin_report(folder):
    """Send both images with your original routing + captions. Structured result."""
    fo_img = os.path.join(folder, "F_filtered.png")
    cm_img = os.path.join(folder, "C_filtered.png")
    r1 = WA_RECIPIENTS[:1]            # first recipient only
    both = WA_RECIPIENTS             # both

    out = {}
    # FO image -> "BJDA_Peak Margin" -> both recipients
    out["wa_fo"] = send_whatsapp(fo_img, "BJDA_Peak Margin", both)
    # CM image -> "06436_Peak_Margin" -> first recipient only
    out["wa_cm"] = send_whatsapp(cm_img, "06436_Peak_Margin", r1)
    # Email the CM/Total image
    out["email"] = send_email_with_image(
        "PEAK MARGIN REPORT",
        f"Dear Sir,<br><br>Please find the Peak Margin Report dated "
        f"{datetime.now().strftime('%d-%m-%Y')}<br><br>", cm_img)

    out["ok"] = bool(out["wa_fo"].get("ok") or out["wa_cm"].get("ok") or out["email"].get("ok"))
    return out
