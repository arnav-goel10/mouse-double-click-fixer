## What changed

## Why

## How it was tested

- [ ] `python -m unittest discover -s tests` passes
- [ ] New test modules start with the `_isolation` import block, and nothing
      else that imports app code ran without a temporary home and temp folder
- [ ] Tried on macOS
- [ ] Tried on Windows
- [ ] Changes to click handling (`app/core.py`, `app/platform.py`) include tests
