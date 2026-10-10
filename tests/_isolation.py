"""Keep every test away from the real machine.

Imported first by every test module, before any app code. It points the home
folder, temporary folder and app-data folders at a throwaway directory, and
gives the single-instance channel a name of its own, so no test (or a script
built from tests) can read or overwrite the user's settings, write their login
item, or reach and quit a copy of the app they have running. An earlier probe
script did both, which is why this exists.
"""

import atexit
import os
import shutil
import tempfile

if not os.environ.get("DCF_TEST_ISOLATED"):
    _root = tempfile.mkdtemp(prefix="dcf-tests-")
    for _name in ("home", "tmp", "appdata", "localappdata", "config"):
        os.makedirs(os.path.join(_root, _name))
    os.environ.update(
        DCF_TEST_ISOLATED=_root,
        HOME=os.path.join(_root, "home"),
        USERPROFILE=os.path.join(_root, "home"),
        TMPDIR=os.path.join(_root, "tmp") + os.sep,
        TEMP=os.path.join(_root, "tmp"),
        TMP=os.path.join(_root, "tmp"),
        APPDATA=os.path.join(_root, "appdata"),
        LOCALAPPDATA=os.path.join(_root, "localappdata"),
        XDG_CONFIG_HOME=os.path.join(_root, "config"),
        DCF_INSTANCE_SUFFIX=f"-test-{os.getpid()}",
    )
    tempfile.tempdir = None  # recomputed from TMPDIR on next use
    atexit.register(shutil.rmtree, _root, True)
