"""Runtime capability flags.

The GitHub Pages build runs the whole agent inside the browser (Pyodide /
WebAssembly). That runtime cannot start threads and has no sockets, so the
two places that need them — the Treatment∥Panel wave / panel fan-out, and
the LLM HTTP transport — consult these flags and take their serial /
browser-native paths. Behavior (ledger order, audit, release) is identical;
only scheduling and transport differ.
"""

from __future__ import annotations

import sys

#: True inside Pyodide (emscripten) or another WebAssembly runtime.
IN_BROWSER = sys.platform in ("emscripten", "wasi")

#: Threads can be started (False in the browser build).
THREADS_AVAILABLE = not IN_BROWSER
