"""
generate_ssl_cert.py — one-time setup to make the dashboard serve HTTPS instead
of plain HTTP, so browsers stop showing "Not Secure" / warning about sending
an "unauthorised token" over an unencrypted connection.

WHY THIS IS NEEDED
------------------
The dashboard is reached over the LAN (http://<this-PC's-IP>:8000) by more than
one person's browser. Plain HTTP sends everything — including the RSA
token/captcha answers you type in — as readable text over the network. Anyone
else on the same office network could, in principle, read it. HTTPS encrypts
that traffic. This script creates a SELF-SIGNED certificate (good enough for an
internal LAN tool — no need to buy one or involve a public certificate
authority) covering:
    - localhost / 127.0.0.1          (this machine, browsing itself)
    - this machine's actual LAN IP    (auto-detected below)
    - this machine's hostname

WHAT TO EXPECT AFTERWARDS
--------------------------
The FIRST time each person's browser visits https://<ip>:8000 it will show a
"Your connection is not private" / "not trusted" warning — that's expected and
normal for ANY self-signed certificate (it wasn't issued by a public authority
the browser already trusts). Click "Advanced" → "Proceed" once; the browser
will remember that choice for this site afterward. This is a one-time step per
browser/machine, not per visit.

HOW TO RUN
----------
    pip install cryptography --break-system-packages   (one-time, if not already installed)
    python generate_ssl_cert.py

Then just start the server as usual (START_SERVER.bat) — it automatically
switches to HTTPS once certs/cert.pem and certs/key.pem exist.

To go back to plain HTTP, just delete the `certs` folder and restart the server.
"""
import datetime
import ipaddress
import os
import socket

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print("The 'cryptography' package is required but not installed.")
    print("Run this once, then re-run this script:")
    print("    pip install cryptography --break-system-packages")
    raise SystemExit(1)

REPO = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(REPO, "certs")


def _detect_lan_ip():
    """Best-effort local LAN IP (doesn't actually send any traffic)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def main():
    os.makedirs(CERT_DIR, exist_ok=True)
    cert_path = os.path.join(CERT_DIR, "cert.pem")
    key_path = os.path.join(CERT_DIR, "key.pem")

    lan_ip = _detect_lan_ip()
    hostname = socket.gethostname()

    print("Detected hostname:", hostname)
    print("Detected LAN IP:  ", lan_ip or "(could not auto-detect)")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RMS Copilot (internal, self-signed)"),
    ])

    san_entries = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    if lan_ip:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(lan_ip)))
        except ValueError:
            pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=5 * 365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    print()
    print(f"✅ Certificate written to: {cert_path}")
    print(f"✅ Key written to:         {key_path}")
    print()
    print("Next: start the server as usual (START_SERVER.bat). It will now serve")
    print(f"HTTPS at https://{lan_ip or '<this-PC-IP>'}:8000 (and https://localhost:8000).")
    print("Each browser will show a one-time trust warning on first visit — see the")
    print("note at the top of this file for what to click.")


if __name__ == "__main__":
    main()
