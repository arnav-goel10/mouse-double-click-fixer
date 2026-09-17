"""Launch DoubleClick Fixer.

`--minimized` starts it hidden in the menu bar / notification area, which is
what the start-at-login entry uses.
"""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
