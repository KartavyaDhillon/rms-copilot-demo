import os

# The older MCX tests exercise the PREVIOUS download method (queue an export); the grid-view method has its own tests
# (tests/test_mcx_grid.py). Without this the console's default ("grid") would send those tests to the real portal.
os.environ.setdefault("MCX_DOWNLOAD_MODE", "queue")
