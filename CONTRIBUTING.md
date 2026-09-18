# Contributing

## Development

Use Python 3.11 or newer.

```text
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate       # macOS
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Keep click classification platform-neutral in `app/core.py`. Keep native hooks, startup integration and permission checks isolated in `app/platform.py`, `app/startup.py` and `app/permissions.py`. UI code talks to `app/controller.py`, never to the hook directly. Changes affecting global input capture must include focused tests and manual platform notes.

## Pull requests

Describe behavior changes, platform coverage, permission requirements, and how you tested them. Do not include personal settings files, generated `build/` or `dist/` output, or credentials.
