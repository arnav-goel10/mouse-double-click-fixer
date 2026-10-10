"""Where every binary a build collects comes from.

PyInstaller collects the libraries a build needs by following each one's
dependencies, and on Windows it looks for a DLL by name the way Windows
does: PATH included. A build machine's PATH can lead to anything: GitHub's
Windows runners put MySQL's OpenSSL (libssl-3-x64.dll, for Qt's OpenSSL
backend) and a JDK's copy of the Universal C Runtime there, and both were
collected. Leaving those names out (tools/make_notices.py's unused_qt_file)
closes only the names someone thought of. So every build also checks the
source of everything it collects: each library, Python extension and DLL
must come from Python itself (sys.base_prefix) or from the site-packages
of the environment that builds, and the build fails, naming each file and
where it came from, if one doesn't. What the spec leaves out by name is
gone before this check, and the name checks stay as a second line
(make_notices.windows_left_out).

doubleclick-fixer.spec runs `check` on what it is about to bundle.
"""

from __future__ import annotations

import os
import site
import sys
import sysconfig
from typing import Iterable, List, Optional, Sequence, Tuple

#: What a build's binaries are, apart from PyInstaller's own type codes.
BINARY_SUFFIXES = (".dll", ".pyd", ".so", ".dylib", ".exe")
#: Mach-O (thin and universal, either byte order) and PE files start so.
_MAGICS = tuple(bytes.fromhex(magic) for magic in ("cffaedfe", "cefaedfe", "feedfacf", "feedface", "cafebabe"))
_BINARY_TYPES = ("BINARY", "EXTENSION")
#: A link PyInstaller makes inside the build (macOS frameworks, and the top
#: level's links to libraries in packages): its "source" is where it points,
#: relative to it, and what it points to is checked as an entry of its own.
_LINK_TYPE = "SYMLINK"


def _normal(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def allowed_roots() -> List[Tuple[str, str]]:
    """(what, folder) for each place a build may collect binaries from: Python
    itself and the site-packages of the environment that builds."""
    roots = [("Python", sys.base_prefix), ("Python", sys.base_exec_prefix)]
    paths = sysconfig.get_paths()
    roots += [("site-packages", paths["purelib"]), ("site-packages", paths["platlib"])]
    roots += [("site-packages", folder) for folder in site.getsitepackages()]
    seen, found = set(), []
    for what, folder in roots:
        normal = _normal(folder)
        if folder and normal not in seen:
            seen.add(normal)
            found.append((what, normal))
    return found


def is_binary(source: str, typecode: str = "") -> bool:
    """Whether a collected file is code the loader runs: by PyInstaller's
    type code, its name, or its first bytes."""
    if typecode in _BINARY_TYPES or source.lower().endswith(BINARY_SUFFIXES):
        return True
    try:
        with open(source, "rb") as file:
            start = file.read(4)
    except OSError:
        return False
    return start in _MAGICS or start[:2] == b"MZ"


def inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def foreign(entries: Iterable[Sequence], roots: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str]]:
    """(destination, source) of each binary among PyInstaller TOC `entries`
    (destination, source, type code) that comes from none of `roots`."""
    roots = allowed_roots() if roots is None else roots
    found = []
    for entry in entries:
        destination, source = entry[0], entry[1]
        typecode = entry[2] if len(entry) > 2 else ""
        if not source or typecode == _LINK_TYPE or not is_binary(source, typecode):
            continue
        if not any(inside(_normal(source), folder) for _what, folder in roots):
            found.append((destination, source))
    return sorted(set(found))


def check(entries: Iterable[Sequence], roots: Optional[List[Tuple[str, str]]] = None) -> str:
    """Fail (SystemExit) if any binary among `entries` comes from outside
    `roots`, naming each with its source; otherwise say what was checked."""
    entries = list(entries)
    roots = allowed_roots() if roots is None else roots
    offenders = foreign(entries, roots)
    where = "; ".join(f"{what} {folder}" for what, folder in roots)
    if offenders:
        lines = "\n".join(f"  {destination}  (from {source})" for destination, source in offenders)
        raise SystemExit(
            f"This build collects {len(offenders)} binaries from outside Python and its packages ({where}):\n"
            f"{lines}\n"
            "Something on this machine (PATH, most likely) led the build to them. Remove that, or build with a "
            "Python whose libraries are all inside it, in an environment of its own (tools/binary_sources.py)."
        )
    count = sum(1 for entry in entries
                if entry[1] and entry[2:3] != (_LINK_TYPE,) and is_binary(entry[1], entry[2] if len(entry) > 2 else ""))
    return f"Binaries from outside Python and its packages: none ({count} checked, from {where})"


if __name__ == "__main__":  # pragma: no cover - a quick look at this environment
    for what, folder in allowed_roots():
        print(what, folder)
