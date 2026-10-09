"""What a build decided about this copy of the app, fixed when it was built.

Nothing here is read from the environment, the command line or files next to
the app: a built copy can change only by being rebuilt.
"""

from __future__ import annotations

#: CI's throwaway update key, as a minisign key line, in CI's end-to-end build
#: of the Windows app only; empty in every other build and from source.
#: doubleclick-fixer.spec freezes a start-up hook into that one build, which
#: sets this before the app's own code runs (tools/ci_update_key.py writes the
#: hook). release.yml refuses to build with it and checks that no release
#: build carries the hook. app/updater.py trusts the key only in a frozen
#: Windows app.
CI_UPDATE_KEY = ""
