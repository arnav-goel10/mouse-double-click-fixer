try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

# The app's name: Mouse Double-Click Fixer wherever people see it, from one
# constant, and the identifiers installed copies depend on left as they were.

import ast
import re
import unittest
from pathlib import Path

import app
from app import DISPLAY_NAME, FORMER_DISPLAY_NAME

ROOT = Path(__file__).resolve().parents[1]
#: The name before 1.0, however it is spaced or cased ("Double-Click" is the
#: new spelling and doesn't match).
FORMER = re.compile(r"double ?click fixer", re.IGNORECASE)


def string_literals(path: Path) -> list[tuple[int, str]]:
    """Every string literal in a module except docstrings, with its line.
    f-strings count by their literal parts."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DisplayNameTests(unittest.TestCase):
    def test_the_name(self) -> None:
        self.assertEqual(DISPLAY_NAME, "Mouse Double-Click Fixer")
        self.assertEqual(FORMER_DISPLAY_NAME, "DoubleClick Fixer")

    def test_no_text_in_the_app_uses_the_old_name(self) -> None:
        """Only FORMER_DISPLAY_NAME itself spells it, for the code that has to
        recognise a copy from before 1.0 (install_cleanup, the updater's
        swap, crash.log's old launch lines)."""
        allowed = {("app/__init__.py", FORMER_DISPLAY_NAME)}
        found = [
            f"{path.relative_to(ROOT).as_posix()}:{line}: {text!r}"
            for path in sorted((ROOT / "app").rglob("*.py"))
            for line, text in string_literals(path)
            if FORMER.search(text) and (path.relative_to(ROOT).as_posix(), text) not in allowed
        ]
        self.assertEqual(found, [], "use DISPLAY_NAME")

    def test_the_name_is_spelled_out_only_once(self) -> None:
        """Every window, menu, notification and message takes it from
        DISPLAY_NAME, so a later rename is one line."""
        found = [
            f"{path.relative_to(ROOT).as_posix()}:{line}: {text!r}"
            for path in sorted((ROOT / "app").rglob("*.py"))
            for line, text in string_literals(path)
            if DISPLAY_NAME.lower() in text.lower() and path != ROOT / "app" / "__init__.py"
        ]
        self.assertEqual(found, [])

    def test_the_build_tools_read_it_from_the_app(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import make_notices
            import release_notes
        finally:
            sys.path.remove(str(ROOT / "tools"))
        self.assertEqual(release_notes.NAME, DISPLAY_NAME)
        self.assertEqual(make_notices.app_name(), DISPLAY_NAME)
        self.assertIn('NAME="$(sed -n \'s/^DISPLAY_NAME = "\\(.*\\)"$/\\1/p\' app/__init__.py)"', read("installer/build_macos.sh"))
        self.assertIn('NAME = re.search(r\'^DISPLAY_NAME = "([^"]+)"\'', read("doubleclick-fixer.spec"))


class PackagingTests(unittest.TestCase):
    """What the bundle, the installers and the release workflow call the app."""

    def test_the_mac_bundle(self) -> None:
        spec = read("doubleclick-fixer.spec")
        for line in ('name=f"{NAME}.app"', '"CFBundleName": NAME', '"CFBundleDisplayName": NAME'):
            self.assertIn(line, spec)
        build = read("installer/build_macos.sh")
        self.assertIn('APP="dist/$NAME.app"', build)
        self.assertIn('"$NAME" dist/DoubleClickFixer.dmg', build, "the disk image's volume name")
        app_path = f"dist/{DISPLAY_NAME}.app"
        self.assertIn(f'app = defines.get("app", "{app_path}")', read("installer/dmg_settings.py"))
        self.assertIn(f'app="{app_path}"', read("tools/macos_injection_check.sh"))
        self.assertEqual(read(".github/workflows/release.yml").count(app_path), 3)
        self.assertIn(f'--title "{DISPLAY_NAME} ${{GITHUB_REF_NAME#v}}"', read(".github/workflows/release.yml"))

    def test_the_windows_exe_and_installer(self) -> None:
        spec = read("doubleclick-fixer.spec")
        for field in ("CompanyName", "FileDescription", "ProductName"):
            self.assertIn(f'StringStruct("{field}", NAME)', spec)
        iss = read("installer/windows.iss")
        for line in (
            f"AppName={DISPLAY_NAME}",
            f"AppVerName={DISPLAY_NAME} {{#AppVersion}}",
            f"DefaultDirName={{autopf}}\\{DISPLAY_NAME}",
            f"DefaultGroupName={DISPLAY_NAME}",
            # An update keeps a Start menu folder the user chose, moves the
            # default one from before 1.0, and never asks for one again.
            "UsePreviousGroup=not PreviousGroupIsFormerDefault",
            f"  Result := CompareText(Group, '{FORMER_DISPLAY_NAME}') = 0;",
            "  Result := (PageID = wpSelectProgramGroup) and IsUpgrade;",
            f'Name: "{{group}}\\{DISPLAY_NAME}"; Filename: "{{app}}\\DoubleClickFixer.exe"',
            f'Name: "{{autodesktop}}\\{DISPLAY_NAME}"; Filename: "{{app}}\\DoubleClickFixer.exe"; Tasks: desktopicon',
            # An update removes the shortcuts from before 1.0.
            f'Type: files; Name: "{{autoprograms}}\\{FORMER_DISPLAY_NAME}\\{FORMER_DISPLAY_NAME}.lnk"',
            f'Type: dirifempty; Name: "{{autoprograms}}\\{FORMER_DISPLAY_NAME}"',
            f'Type: files; Name: "{{group}}\\{FORMER_DISPLAY_NAME}.lnk"',
            f'Type: files; Name: "{{autodesktop}}\\{FORMER_DISPLAY_NAME}.lnk"; Tasks: desktopicon',
        ):
            self.assertIn(line, iss)
        # Upgrades keep the folder they were installed in (UsePreviousAppDir
        # is on unless turned off).
        self.assertIsNone(re.search(r"(?mi)^UsePreviousAppDir\s*=", iss))
        # Nothing else in it names the app the old way: only comments, the
        # [InstallDelete] lines and the check above may.
        compat = f"  Result := CompareText(Group, '{FORMER_DISPLAY_NAME}') = 0;"
        directives = [
            line for line in iss.splitlines() if not line.lstrip().startswith((";", "//", "Type:")) and line != compat
        ]
        self.assertEqual([line for line in directives if FORMER_DISPLAY_NAME in line], [])

    def test_the_mac_uninstaller_removes_the_app_under_either_name(self) -> None:
        script = read("installer/uninstall_macos.sh")
        for name in (DISPLAY_NAME, FORMER_DISPLAY_NAME):
            self.assertIn(f'rm -rf "$folder/{name}.app"', script)
        self.assertIn('for folder in /Applications "$HOME/Applications"; do', script)


class KeptIdentifierTests(unittest.TestCase):
    """Renaming any of these would cost installed copies their permission,
    settings, login item or updates."""

    def test_they_are_unchanged(self) -> None:
        from app import settings, startup, updater
        from app.main import LOCK_NAME, SERVER_NAME
        from app.ui import menu_bar_mac

        spec = read("doubleclick-fixer.spec")
        self.assertIn('bundle_identifier="com.doubleclickfixer.app"', spec)
        self.assertEqual(spec.count('name="DoubleClickFixer"'), 5, "the executable, on every platform")
        self.assertEqual(updater.BUNDLE_ID, "com.doubleclickfixer.app")
        self.assertEqual(startup.LAUNCH_AGENT_LABEL, "com.doubleclickfixer.app")
        self.assertEqual(startup.APP_NAME, "DoubleClickFixer")  # the Run and StartupApproved value
        self.assertEqual(menu_bar_mac.AUTOSAVE_NAME, "com.doubleclickfixer.app.status-item")
        self.assertTrue(SERVER_NAME.startswith("doubleclick-fixer-single-instance"))
        self.assertEqual(LOCK_NAME, "doubleclick-fixer.lock")
        self.assertEqual(settings.LEGACY_PATH.name, ".doubleclick-fixer.json")
        self.assertEqual(settings.config_dir().name.lower().replace("-", ""), "doubleclickfixer")
        self.assertEqual(
            (updater.MAC_ASSET, updater.WINDOWS_INSTALLER_ASSET, updater.WINDOWS_PORTABLE_ASSET),
            ("DoubleClickFixer-macos.zip", "DoubleClickFixer-Setup.exe", "DoubleClickFixer.exe"),
        )
        self.assertEqual(updater.REPOSITORY, "arnav-goel10/mouse-double-click-fixer")
        iss = read("installer/windows.iss")
        self.assertIn("AppId={{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}", iss)
        self.assertIn('ValueName: "DoubleClickFixer"', iss)
        self.assertIn("OutputBaseFilename=DoubleClickFixer-Setup", iss)
        self.assertIn('Type: filesandordirs; Name: "{userappdata}\\DoubleClickFixer"', iss)


if __name__ == "__main__":
    unittest.main()
