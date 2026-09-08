"""Make WeasyPrint's native libraries findable on a Homebrew macOS install.

This is *not* dead code, even though the mechanism looks like it should not
work: macOS dyld reads ``DYLD_*`` environment variables once, at process
start, and never again. But WeasyPrint does not open Pango/GObject via dyld
directly -- cffi's ``dlopen`` falls back to ``ctypes.util.find_library``,
which is pure Python and reads ``os.environ`` at call time. So setting
``DYLD_FALLBACK_LIBRARY_PATH`` here, before ``render.py`` imports
``weasyprint``, works even though setting it in a shell *after* the
interpreter has started would not.
"""

import os
import sys
from pathlib import Path

_LIBRARY = "libgobject-2.0.dylib"

# Apple Silicon Homebrew, then Intel Homebrew.
_CANDIDATES = (Path("/opt/homebrew/lib"), Path("/usr/local/lib"))


def prepare_dyld_fallback_library_path() -> Path | None:
    """Set DYLD_FALLBACK_LIBRARY_PATH so ctypes can find Homebrew's GObject.

    Does nothing off macOS, and does nothing if the variable is already set
    -- the user's own setting always wins. Returns the directory it set the
    variable to, or None if it left the environment alone.
    """
    if sys.platform != "darwin":
        return None
    if os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"):
        return None
    for candidate in _CANDIDATES:
        if (candidate / _LIBRARY).exists():
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(candidate)
            return candidate
    return None
