"""
core/pipelines/margin_shortage_run.py — RETIRED.

This was the SSE streaming wrapper for the old standalone "Margin Shortage"
download-only button (dashboard.html's small "-> 15 min" / "-> CTCL" tile).
That button has been retired per explicit instruction, replaced by the full
Margin Shortage (15-min) pipeline: core/pipelines/margin_shortage_15min.py
(processing) + core/pipelines/nmass_download.py / mcx90_download.py called
with pipe="ms15" (downloads, in their own MARGIN_SHORTAGE_FOLDER-scoped
buttons on the new dashboard box) + PIPELINES["ms15"] in core/pipeline_box.py.

Nothing imports this module anymore (api.py's old /margin-shortage-stream
route and dashboard.html's old tile/JS mapping were removed in the same
change). Kept as a stub — rather than deleted outright — so a stale import
anywhere fails loudly with this explanation instead of a bare ImportError.
"""


def stream_margin_shortage(*args, **kwargs):
    raise RuntimeError(
        "The standalone Margin Shortage download button has been retired. "
        "Use the Margin Shortage (15-min) box's own Download buttons instead "
        "(core/pipelines/nmass_download.py / mcx90_download.py with pipe='ms15')."
    )
