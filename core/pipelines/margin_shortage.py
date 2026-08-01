"""
core/pipelines/margin_shortage.py — RETIRED.

This was the OLD standalone NSCCL + NCDEX downloader (its own independent
EasyOCR/pytesseract captcha solver, separate from NMASS_NCFE_API_MIX.PY) that
backed the small "Margin Shortage" download-only tile/button on the dashboard
("-> 15 min" / "-> CTCL"). Retired per explicit instruction, replaced by the
full Margin Shortage (15-min) pipeline:
  • core/pipelines/margin_shortage_15min.py — processing (ported from
    15minutesfinal_whatsapp.PY, same calculations/thresholds/WhatsApp logic)
  • core/pipelines/nmass_download.py / mcx90_download.py — downloads, called
    with pipe="ms15" from the new dashboard box's own Download buttons
  • PIPELINES["ms15"] in core/pipeline_box.py — dashboard wiring

Nothing imports this module anymore (core/agent.py's "run_margin_shortage"
chat tool and core/pipelines/margin_shortage_run.py's SSE wrapper were removed
in the same change). Kept as a stub — rather than deleted outright — so a
stale import anywhere fails loudly with this explanation instead of a bare
ImportError.
"""


def download_margin_shortage_files(*args, **kwargs):
    raise RuntimeError(
        "The standalone Margin Shortage downloader has been retired. Use the "
        "Margin Shortage (15-min) box's own Download buttons instead "
        "(core/pipelines/nmass_download.py / mcx90_download.py with pipe='ms15')."
    )


def download_nsccl_files(*args, **kwargs):
    raise RuntimeError("Retired — see core/pipelines/nmass_download.py (pipe='ms15').")


def download_ncdex_files(*args, **kwargs):
    raise RuntimeError("Retired — see core/pipelines/nmass_download.py (pipe='ms15').")
