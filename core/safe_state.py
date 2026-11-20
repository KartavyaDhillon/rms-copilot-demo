"""Windows-safe JSON state writer.

`tmp.replace(target)` raises WinError 5 when another reader (antivirus, backup,
a status poll) has state.json open for a moment.  A failed *bookkeeping* write
must never turn a finished run into FAILED, so: retry, fall back to a direct
write, and as a last resort log and carry on.
"""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


def write_json(path, data, retries: int = 8, delay: float = 0.25) -> bool:
    path = Path(path)
    text = json.dumps(data, indent=1)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
    except OSError as e:
        log.warning("state tmp write failed (%s): %s", path.name, e)
        tmp = None
    if tmp is not None:
        for i in range(retries):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(delay * (i + 1))
            except OSError as e:
                log.warning("state replace failed (%s): %s", path.name, e)
                break
    for i in range(3):  # fallback: write the target directly
        try:
            path.write_text(text, encoding="utf-8")
            try:
                if tmp is not None:
                    Path(tmp).unlink()
            except OSError:
                pass
            return True
        except OSError:
            time.sleep(delay * (i + 1))
    log.warning("state not saved (%s) - will be rewritten on the next update", path.name)
    return False
