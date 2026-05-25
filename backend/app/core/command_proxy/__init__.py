"""Command-proxy core: how every module gets a PAN-OS client.

`build_client_with_fallback` is the canonical entry point — proxy-first,
direct-fallback, with Panorama-health tracking as a side effect. Lifted
from pan-fw-upgrader's services/precheck.py at phase 4b. Capacity's
poller now uses it instead of its own simpler _build_client.

## Import discipline

This package's `__init__` is intentionally light — it does NOT eagerly
import `builder.py`. There's a latent cycle between `builder.py` and
`app.core.credentials` (credentials imports `keygen` from `pan_client`,
which would in turn trigger this `__init__`; if `__init__` then loads
`builder.py` which itself imports from `credentials`, you get an
`ImportError: partially initialized module`). It only surfaces when
something outside this package imports `credentials` *before*
`command_proxy` has been fully loaded — depends on which routes/services
get touched first in main.py's import chain, so the failure mode is
"works on Monday, breaks Tuesday after an unrelated refactor."

The fix: callers import directly from the submodule that owns the thing
they want.

  from app.core.command_proxy.builder   import build_client_with_fallback
  from app.core.command_proxy.pan_client import PanDeviceClient, keygen

Do NOT re-add `from app.core.command_proxy.builder import …` to this file.
"""
