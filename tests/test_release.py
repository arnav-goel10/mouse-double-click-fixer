"""Releases: the version and its changelog entry, the release page, the pinned
build tools, the workflows' guarantees, and `tools/sign_release.py publish`
(with gh, ditto and codesign faked: it never touches GitHub here).

On a tag build (GITHUB_REF=refs/tags/v...), the tag must be the app's version
and the changelog must have a dated entry for it. Elsewhere an "Unreleased"
entry is enough.
"""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import fnmatch
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import app
from app.update_signature import parse_claim, parse_public_key, verify
from tools import ci_update_key, release_notes, sign_release
from tools.sign_release import (
    APP_REQUIREMENT,
    ARTIFACTS,
    CHECKSUMS,
    INJECTION_CHECK,
    MAC_NOTICES,
    MAC_ZIP,
    RELEASE_FILES,
    SIGNATURE,
    WINDOWS_NOTICES,
    ReleaseError,
    keygen,
    parse_tag,
    publish_release,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "owner/doubleclick-fixer"
REF = os.environ.get("GITHUB_REF", "")
TAG = REF[len("refs/tags/") :] if REF.startswith("refs/tags/") else ""


def changelog() -> str:
    return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def entry_heading(version: str) -> "re.Match | None":
    return re.search(rf"^## {re.escape(version)}(?: — (?P<date>\d{{4}}-\d{{2}}-\d{{2}}))?$", changelog(), re.MULTILINE)


class VersionTests(unittest.TestCase):
    def test_the_app_version_is_three_numbers(self) -> None:
        self.assertRegex(app.__version__, r"^\d+\.\d+\.\d+$")

    @unittest.skipUnless(TAG, "not a tag build")
    def test_the_tag_is_the_app_version(self) -> None:
        _tag, version, _prerelease = parse_tag(TAG)
        self.assertEqual(version, app.__version__, f"{TAG} doesn't match app/__init__.py")

    def test_the_changelog_has_an_entry_for_it(self) -> None:
        heading = entry_heading(app.__version__)
        if TAG:
            self.assertIsNotNone(heading, f"CHANGELOG.md needs a '## {app.__version__} — <date>' entry")
            if "-" not in TAG:
                self.assertTrue(heading.group("date"), f"the {app.__version__} entry needs its release date")
        elif heading is None:
            first = re.search(r"^## (.+)$", changelog(), re.MULTILINE)
            self.assertEqual(
                first and first.group(1).strip(), release_notes.UNRELEASED,
                f"CHANGELOG.md needs an entry for {app.__version__}, or an Unreleased one at the top",
            )

    def test_the_release_page_builds_for_it(self) -> None:
        version = parse_tag(TAG)[0][1:] if TAG else app.__version__
        if entry_heading(app.__version__) is None:
            self.skipTest("the changelog has only an Unreleased entry for this version so far")
        page = release_notes.notes(version, REPOSITORY)
        self.assertIn(f"https://github.com/{REPOSITORY}/releases/download/v{version}/DoubleClickFixer.dmg", page)
        self.assertIn("## What's new", page)
        first_line = release_notes.changelog_section(app.__version__, changelog()).splitlines()[0]
        self.assertIn(first_line.strip()[:40], page)


class ReleaseNotesTests(unittest.TestCase):
    CHANGELOG = (
        "# Changelog\n\n## Unreleased\n\n- Coming soon.\n\n"
        "## 1.0.0 — 2026-11-01\n\n- **One point oh.** It\n  wraps.\n\n"
        "## 0.5.3 — 2026-10-08\n\n- Older.\n"
    )

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "installer").mkdir()
        (root / "installer" / "release_notes.md").write_text(
            "## Download\n\n[Mac](https://github.com/__REPO__/releases/download/v__VERSION__/DoubleClickFixer.dmg)\n",
            encoding="utf-8",
        )
        (root / "CHANGELOG.md").write_text(self.CHANGELOG, encoding="utf-8")
        patcher = mock.patch.object(release_notes, "ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_release_page(self) -> None:
        page = release_notes.notes("1.0.0", REPOSITORY)
        self.assertIn("releases/download/v1.0.0/DoubleClickFixer.dmg", page)
        self.assertIn("- **One point oh.** It wraps.", page)
        self.assertIn("compare/v0.5.3...v1.0.0", page)
        self.assertIn("[THIRD_PARTY_NOTICES-macos.md](https://github.com/owner/doubleclick-fixer/releases/download/v1.0.0/THIRD_PARTY_NOTICES-macos.md) (macOS)", page)
        self.assertIn("[THIRD_PARTY_NOTICES-windows.md](https://github.com/owner/doubleclick-fixer/releases/download/v1.0.0/THIRD_PARTY_NOTICES-windows.md) (Windows)", page)
        self.assertEqual(
            {name for _label, name in release_notes.NOTICES}, {MAC_NOTICES, WINDOWS_NOTICES}, "the files a release carries"
        )
        self.assertNotIn("pre-release", page)
        self.assertNotIn("newer version", page)

    def test_releases_before_1_0_have_no_notices_file_to_link(self) -> None:
        self.assertNotIn("THIRD_PARTY_NOTICES", release_notes.notes("0.5.3", REPOSITORY))

    def test_an_older_page_points_at_the_latest(self) -> None:
        page = release_notes.notes("0.5.3", REPOSITORY, latest="1.0.0")
        self.assertTrue(page.startswith("> **A newer version is available:** [DoubleClick Fixer 1.0.0]"))

    def test_a_pre_release_uses_its_own_entry_then_its_versions_then_unreleased(self) -> None:
        page = release_notes.notes("1.0.0-rc.1", REPOSITORY)
        self.assertIn("This is a pre-release", page)
        self.assertIn("releases/download/v1.0.0-rc.1/DoubleClickFixer.dmg", page)
        self.assertIn("One point oh", page)
        self.assertIn("compare/v0.5.3...v1.0.0-rc.1", page)
        page = release_notes.notes("1.1.0-beta.2", REPOSITORY)
        self.assertIn("Coming soon.", page)
        self.assertIn("compare/v1.0.0...v1.1.0-beta.2", page)
        with self.assertRaises(SystemExit):
            release_notes.notes("1.1.0", REPOSITORY)  # a full release needs its own entry

    def gh(self, releases, latest="v1.0.0"):
        calls = []

        def run(*arguments):
            calls.append(arguments)
            if arguments[0] == "api":
                return latest + "\n"
            if arguments[:2] == ("release", "list"):
                return json.dumps(releases)
            if arguments[:2] == ("release", "edit"):
                page = Path(arguments[arguments.index("--notes-file") + 1]).read_text(encoding="utf-8")
                self.assertIn(f"[DoubleClick Fixer {latest[1:]}]", page)
                return ""
            raise AssertionError(f"unexpected gh {arguments}")

        return run, calls

    def test_older_pages_are_pointed_at_the_latest(self) -> None:
        releases = [
            {"tagName": "v1.1.0-beta.2", "isDraft": False},  # newer than the latest: left alone
            {"tagName": "v1.0.0", "isDraft": False},
            {"tagName": "v1.1.0", "isDraft": True},
            {"tagName": "v1.0.0-rc.1", "isDraft": False},
            {"tagName": "v0.5.3", "isDraft": False},
            {"tagName": "v0.1.9", "isDraft": False},  # no changelog entry
            {"tagName": "nightly", "isDraft": False},
        ]
        gh, calls = self.gh(releases)
        said = []
        self.assertEqual(release_notes.point_older(REPOSITORY, gh=gh, say=said.append), ["v1.0.0-rc.1", "v0.5.3"])
        self.assertFalse([call for call in calls if call[:2] == ("release", "edit")], "a dry run edited a page")
        gh, calls = self.gh(releases)
        self.assertEqual(release_notes.point_older(REPOSITORY, apply=True, gh=gh, say=said.append), ["v1.0.0-rc.1", "v0.5.3"])
        edited = [call[2] for call in calls if call[:2] == ("release", "edit")]
        self.assertEqual(edited, ["v1.0.0-rc.1", "v0.5.3"])
        self.assertTrue(any("v0.1.9: left alone" in line for line in said))
        self.assertTrue(any(line.startswith("v1.1.0-beta.2: left alone") for line in said))

    def test_a_pre_release_of_the_next_version_is_never_told_an_older_one_is_newer(self) -> None:
        for tag, latest, pointed in (
            ("v1.1.0-rc.1", "v1.0.0", False),
            ("v1.0.1-rc.1", "v1.0.0", False),
            ("v1.0.0-rc.1", "v1.0.0", True),  # 1.0.0 came after its own release candidate
            ("v0.5.3", "v1.0.0", True),
        ):
            with self.subTest(tag=tag, latest=latest):
                gh, _calls = self.gh([{"tagName": tag, "isDraft": False}], latest=latest)
                changed = release_notes.point_older(REPOSITORY, gh=gh, say=lambda _line: None)
                self.assertEqual(changed, [tag] if pointed else [])

    def test_a_latest_release_without_a_version_tag_changes_nothing(self) -> None:
        gh, calls = self.gh([{"tagName": "v0.5.3", "isDraft": False}], latest="nightly")
        with self.assertRaises(SystemExit):
            release_notes.point_older(REPOSITORY, apply=True, gh=gh, say=lambda _line: None)
        self.assertEqual(len(calls), 1)

    def test_command_line(self) -> None:
        with mock.patch.object(release_notes, "point_older") as point_older:
            self.assertEqual(release_notes.main(["--point-older", REPOSITORY]), 0)
            point_older.assert_called_with(REPOSITORY, apply=False)
            self.assertEqual(release_notes.main(["--point-older", REPOSITORY, "--apply"]), 0)
            point_older.assert_called_with(REPOSITORY, apply=True)
            with self.assertRaises(SystemExit):
                release_notes.main(["--point-older", REPOSITORY, "--yes"])
        output = io.StringIO()
        with redirect_stdout(output):
            release_notes.main(["0.5.3", REPOSITORY, "--latest", "1.0.0"])
        self.assertIn("A newer version is available", output.getvalue())


def requirements(path: Path) -> dict:
    """name -> (version, marker, hashes) for each requirement in a pip file."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    found = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("--hash") else line
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)\s*(?:;\s*([^-][^\n]*?))?\s*((?:--hash=\S+\s*)*)$", line.strip())
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            marker = re.sub(r"\s+", " ", (match.group(3) or "").replace("'", '"')).strip()
            found[name] = (match.group(2), marker, re.findall(r"--hash=sha256:([0-9a-f]{64})", match.group(4)))
    return found


class PinnedBuildTests(unittest.TestCase):
    """Builds install requirements-build.txt with --require-hashes: every
    package pinned, every file's hash listed."""

    def setUp(self) -> None:
        self.lock = requirements(ROOT / "requirements-build.txt")

    def test_every_package_is_pinned_with_hashes(self) -> None:
        self.assertGreater(len(self.lock), 10)
        for name, (_version, _marker, hashes) in self.lock.items():
            self.assertTrue(hashes, f"{name} has no hash in requirements-build.txt")
        entries = re.findall(r"^[A-Za-z0-9]", (ROOT / "requirements-build.txt").read_text(encoding="utf-8"), re.MULTILINE)
        self.assertEqual(len(entries), len(self.lock), "a line in requirements-build.txt isn't a pinned requirement")

    def test_it_installs_what_requirements_txt_pins(self) -> None:
        # Regenerate it (see requirements-build.in) after changing requirements.txt.
        for name, (version, marker, _hashes) in requirements(ROOT / "requirements.txt").items():
            self.assertIn(name, self.lock, f"{name} is missing from requirements-build.txt")
            self.assertEqual(self.lock[name][:2], (version, marker), f"requirements-build.txt pins {name} differently")

    def test_the_build_tools_are_pinned_as_requirements_build_in_says(self) -> None:
        wanted = requirements(ROOT / "requirements-build.in")
        for name in ("pyinstaller", "pyinstaller-hooks-contrib", "dmgbuild"):
            self.assertIn(name, wanted)
        for name, (version, _marker, _hashes) in wanted.items():
            self.assertEqual(self.lock[name][0], version, f"requirements-build.txt is stale for {name}")

    def test_builds_install_only_the_pinned_files(self) -> None:
        places = [
            *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
            ROOT / "installer" / "build_macos.sh",
            ROOT / "installer" / "build_windows.ps1",
        ]
        for path in places:
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.search(r"\bpip install\b", line) and not line.lstrip().startswith("#"):
                    self.assertIn("--require-hashes", line, f"{path.name}: {line.strip()}")
                    self.assertIn("requirements-build.txt", line, f"{path.name}: {line.strip()}")


class WorkflowTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.search(r"\buses:\s*(\S+)(.*)$", line)
                if match and not match.group(1).startswith("./"):
                    self.assertRegex(
                        match.group(1) + match.group(2), r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$",
                        f"{path.name}: {line.strip()}",
                    )

    def test_a_release_waits_for_every_check_and_is_only_ever_a_draft(self) -> None:
        release = self.workflow("release.yml")
        self.assertIn("workflow_call:", self.workflow("ci.yml"))
        self.assertRegex(release, r"\n  checks:\n    uses: \./\.github/workflows/ci\.yml\n")
        self.assertIn("needs: [checks, windows, macos, windows-e2e]", release)
        self.assertRegex(release, r"\npermissions:\n  contents: read\n")
        self.assertEqual(release.count("contents: write"), 1, "only the publish job may write")
        self.assertRegex(release, r"\nconcurrency:\n  group: release\n")
        self.assertIn("gh release create", release)
        self.assertIn("--draft", release)
        self.assertIn("--latest=false", release)
        self.assertNotIn("make_latest: true", release)
        self.assertEqual(release.count("secrets."), 2, "the signing secrets belong to the macOS job alone")
        self.assertIn(f"expected='{APP_REQUIREMENT}'", release)

    def test_a_manual_run_never_loads_the_certificate_or_puts_up_a_draft(self) -> None:
        # workflow_dispatch can run on a tag too: only the push of one counts.
        pushed_tag = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"
        macos, publish = job(self.workflow("release.yml"), "macos"), job(self.workflow("release.yml"), "publish")
        self.assertIn(f"environment: ${{{{ {pushed_tag} && 'release' || '' }}}}", macos)
        load = step(macos, "Load the signing certificate")
        self.assertIn(f"if: {pushed_tag}\n", load)
        self.assertIn("secrets.MACOS_SIGNING_P12", load)
        self.assertIn(f"if: {pushed_tag}\n", step(macos, "Confirm the app keeps the designated requirement"))
        self.assertIn(f"\n    if: {pushed_tag}\n", publish)

    def test_the_concurrency_comment_says_what_github_does(self) -> None:
        release = self.workflow("release.yml")
        comment = release[: release.index("\nconcurrency:")].rsplit("\n\n", 1)[1]
        self.assertIn("cancel-in-progress: false", release)
        self.assertIn("a newer run cancels the one already waiting", comment)
        self.assertNotIn("waits for the first", comment)

    def test_the_macos_build_runs_the_injection_check_itself(self) -> None:
        build = (ROOT / "installer" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn('\nbash tools/macos_injection_check.sh "$signed"\n', build)
        self.assertNotIn("macos_injection_check", self.workflow("release.yml"))

    def test_release_files_carry_each_platforms_notices(self) -> None:
        release = self.workflow("release.yml")
        self.assertIn(
            "Copy-Item build\\notices\\THIRD_PARTY_NOTICES.md release\\THIRD_PARTY_NOTICES-windows.md",
            step(job(release, "windows"), "Collect the release files"),
        )
        self.assertIn('Source: "..\\build\\notices\\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"',
                      (ROOT / "installer" / "windows.iss").read_text(encoding="utf-8"))
        self.assertIn(
            'cp "dist/DoubleClick Fixer.app/Contents/Resources/THIRD_PARTY_NOTICES.md" release/THIRD_PARTY_NOTICES-macos.md',
            step(job(release, "macos"), "Collect the release files"),
        )
        self.assertEqual(ARTIFACTS["DoubleClickFixer-windows"][-1], WINDOWS_NOTICES)
        self.assertEqual(ARTIFACTS["DoubleClickFixer-macos"][-1], MAC_NOTICES)

    def test_dependabot_leaves_the_hash_locked_pins_alone(self) -> None:
        # It can't regenerate requirements-build.txt (uv pip compile, with
        # hashes; see requirements-build.in), so it isn't asked to.
        config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"package-ecosystem: (\S+)", config), ["github-actions"])

    def test_ci_and_releases_use_the_same_inno_setup(self) -> None:
        versions = {re.search(r'INNO_SETUP_VERSION: "([\d.]+)"', self.workflow(name)).group(1) for name in ("ci.yml", "release.yml")}
        self.assertEqual(len(versions), 1)

    def test_the_release_files_are_the_ones_publish_expects(self) -> None:
        expected = " ".join(sorted(set(RELEASE_FILES) - {CHECKSUMS}, key=lambda name: name.encode()))
        self.assertIn(f'expected="{expected}"', self.workflow("release.yml"))
        self.assertIn(MAC_NOTICES, expected)
        self.assertIn(WINDOWS_NOTICES, expected)

    def test_the_draft_summary_gives_the_commands_that_publish_it(self) -> None:
        put_up = step(job(self.workflow("release.yml"), "publish"), "Put up the draft")
        self.assertIn('echo "python3 tools/sign_release.py publish $GITHUB_REF_NAME --dry-run"', put_up)
        self.assertIn('echo "python3 tools/sign_release.py publish $GITHUB_REF_NAME"', put_up)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', put_up)


def job(workflow: str, name: str) -> str:
    """One job of a workflow, as text."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", workflow, re.MULTILINE | re.DOTALL)
    assert match, f"no job {name}"
    return match.group(0)


def step(job_text: str, name: str) -> str:
    """The step of a job whose name starts with `name`, as text."""
    match = re.search(rf"^      - (?:name: )?{re.escape(name)}.*?(?=^      - |\Z)", job_text, re.MULTILINE | re.DOTALL)
    assert match, f"no step {name}"
    return match.group(0)


class CiUpdateKeyGuardTests(unittest.TestCase):
    """doubleclick-fixer.spec's guard for CI's update key, run as each Windows
    job runs it for a tag. On a tag, release.yml calls ci.yml, so CI's
    windows-install job builds with GITHUB_REF=refs/tags/v1.0.1 too."""

    TAG = {"GITHUB_REF": "refs/tags/v1.0.1", "GITHUB_REF_NAME": "v1.0.1", "GITHUB_REF_TYPE": "tag", "GITHUB_ACTIONS": "true"}

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.public = ci_update_key.make(self.root / "dcf-ci-key")
        self.hooks = self.root / "build" / "ci-update-key"

    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def guard(self, environ: dict, platform: str = "win32") -> list[str]:
        with redirect_stdout(io.StringIO()):
            return ci_update_key.spec_runtime_hooks(environ, platform, self.hooks)

    def test_the_spec_runs_this_guard_and_never_looks_at_the_ref(self) -> None:
        spec = (ROOT / "doubleclick-fixer.spec").read_text(encoding="utf-8")
        self.assertIn('ci_update_key.spec_runtime_hooks(os.environ, sys.platform, Path("build", "ci-update-key"))', spec)
        self.assertIn("runtime_hooks=RUNTIME_HOOKS", spec)
        self.assertNotIn("GITHUB_REF", spec)
        self.assertNotIn("DCF_CI_UPDATE_KEY", spec.replace("# CI's end-to-end Windows build alone: with DCF_CI_UPDATE_KEY", ""))

    def test_ci_install_job_on_a_tag_builds_the_key_in(self) -> None:
        build = step(job(self.workflow("ci.yml"), "windows-install"), "Build the exe and the installer")
        self.assertIn('$env:DCF_CI_UPDATE_KEY = "$env:RUNNER_TEMP\\dcf-ci-key\\dcf-ci-update-key.pub"', build)
        hooks = self.guard({**self.TAG, ci_update_key.ENV: str(self.public)})
        self.assertEqual(hooks, [str((self.hooks / ci_update_key.HOOK_FILE).resolve())])
        key_line = self.public.read_text(encoding="ascii").splitlines()[-1]
        self.assertIn(f"app.build_flags.CI_UPDATE_KEY = {key_line!r}", Path(hooks[0]).read_text(encoding="ascii"))
        # Its builds must then carry the hook where the release check looks.
        guard = step(job(self.workflow("ci.yml"), "windows-install"), "The release guard finds CI's key in each build")
        self.assertIn('foreach ($build in "dist\\DoubleClickFixer.exe", "dist\\onedir")', guard)
        self.assertIn("if ($LASTEXITCODE -ne 1)", guard)

    def test_the_release_windows_job_on_a_tag_never_does(self) -> None:
        windows = job(self.workflow("release.yml"), "windows")
        refuse = step(windows, "Refuse CI's throwaway update key")
        self.assertIn('if ($env:DCF_CI_UPDATE_KEY) { throw', refuse)
        self.assertEqual(self.workflow("release.yml").count("DCF_CI_UPDATE_KEY"), 2, "only the refusal names it")
        check = step(windows, "Confirm no build carries CI's update key")
        self.assertIn("python tools/ci_update_key.py check dist\\DoubleClickFixer.exe dist\\onedir", check)
        order = [windows.index(text) for text in (refuse, "installer\\build_windows.ps1", check, "Collect the release files")]
        self.assertEqual(order, sorted(order))
        (self.hooks).mkdir(parents=True)
        (self.hooks / ci_update_key.HOOK_FILE).write_text("left over", encoding="ascii")
        self.assertEqual(self.guard(dict(self.TAG)), [])
        self.assertFalse((self.hooks / ci_update_key.HOOK_FILE).exists())

    def test_a_macos_build_ignores_the_variable(self) -> None:
        for environ in ({**self.TAG, ci_update_key.ENV: str(self.public)}, {ci_update_key.ENV: "not even a file"}):
            with self.subTest(environ=environ):
                self.assertEqual(self.guard(environ, platform="darwin"), [])
                self.assertFalse(self.hooks.exists() and any(self.hooks.iterdir()))


@unittest.skipIf(sys.platform == "win32" or shutil.which("bash") is None, "a bash script")
class InjectionCheckScriptTests(unittest.TestCase):
    """tools/macos_injection_check.sh with csrutil, clang, codesign, env and
    the app's self-test faked: which legs run with System Integrity Protection
    on and off, and what fails."""

    #: Stands in for the app's --self-test. FAKE_LOADS names the canaries that
    #: get in, each only under the conditions its leg sets up: dyld (and cwd,
    #: which rests on the same dyld policy) only get in when SIP is off or the
    #: app isn't hardened; cwd when started in the folder of canaries named as
    #: Qt's OpenSSL backend asks for its libraries.
    APP = """#!/bin/sh -p
for canary in $FAKE_LOADS; do
  case "$canary" in
    dyld) [ -n "$FAKE_DYLD_INSERT_LIBRARIES" ] && echo loaded > "$DCF_CANARY_DIR/dcf-canary-dyld" ;;
    cwd) [ -f libcrypto.so.3 ] && [ -f libssl.3.dylib ] && echo loaded > "$DCF_CANARY_DIR/dcf-canary-cwd" ;;
    openssl) [ -n "$OPENSSL_CONF" ] && echo loaded > "$DCF_CANARY_DIR/dcf-canary-openssl" ;;
  esac
done
exit 0
"""
    #: Runs the app as env does, with each DYLD_* variable renamed FAKE_DYLD_*:
    #: a real loader would otherwise act on the fake canary (SIP off) or drop
    #: the variable (SIP on, when it starts /bin/sh) before the app could look.
    ENV = """for argument do
  shift
  case "$argument" in
    DYLD_*=*) set -- "$@" "FAKE_$argument" ;;
    *) set -- "$@" "$argument" ;;
  esac
done
exec /usr/bin/env "$@"
"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()
        programs = {
            "csrutil": 'echo "System Integrity Protection status: $FAKE_SIP."',
            "clang": 'while [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done',
            "codesign": "exit 0",
            "env": self.ENV,
        }
        for name, body in programs.items():
            (self.fakes / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            (self.fakes / name).chmod(0o755)
        self.app = self.root / "DoubleClick Fixer.app"
        binary = self.app / "Contents" / "MacOS" / "DoubleClickFixer"
        binary.parent.mkdir(parents=True)
        binary.write_text(self.APP, encoding="utf-8")
        binary.chmod(0o755)
        (self.root / "marks").mkdir()
        (self.root / "tmp").mkdir()

    def check(self, sip: str, loads: str = "", *options: str) -> subprocess.CompletedProcess:
        environment = {
            "PATH": f"{self.fakes}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.root), "TMPDIR": str(self.root / "tmp"),
            "FAKE_SIP": sip, "FAKE_LOADS": loads, "DCF_CANARY_DIR": str(self.root / "marks"),
        }
        for mark in (self.root / "marks").iterdir():
            mark.unlink()
        return subprocess.run(
            ["bash", str(INJECTION_CHECK), str(self.app), *options],
            env=environment, capture_output=True, text=True, timeout=60, check=False,
        )

    def test_with_sip_on_every_leg_runs(self) -> None:
        result = self.check("enabled")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== dyld: DYLD_INSERT_LIBRARIES=", result.stdout)
        self.assertIn("== cwd: \n", result.stdout)
        self.assertIn("-> canary cwd did not fire", result.stdout)
        self.assertIn("== openssl: OPENSSL_CONF=", result.stdout)
        self.assertIn("== path: PATH=", result.stdout)
        self.assertIn("with System Integrity Protection enabled", result.stdout)
        for leg in ("dyld", "cwd"):
            with self.subTest(leg=leg):
                result = self.check("enabled", leg, "--require-sip")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"canary {leg} FIRED", result.stdout)
                self.assertIn(f"FAILED ({leg})", result.stderr)

    def test_with_sip_off_the_dyld_and_cwd_legs_are_skipped_and_loudly(self) -> None:
        result = self.check("disabled", "dyld cwd")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== dyld: SKIPPED (System Integrity Protection is disabled)", result.stdout)
        self.assertIn("== cwd: SKIPPED (System Integrity Protection is disabled)", result.stdout)
        self.assertNotIn("DYLD_INSERT_LIBRARIES=", result.stdout)
        self.assertNotIn("canary cwd", result.stdout)
        self.assertIn("WARNING: System Integrity Protection is disabled", result.stderr)
        self.assertIn("the dyld and cwd legs prove nothing here", result.stderr.replace("\n", " "))
        self.assertIn("OK, WITHOUT THE DYLD AND CWD LEGS", result.stdout)
        self.assertIn("== openssl: OPENSSL_CONF=", result.stdout)
        self.assertIn("== path: PATH=", result.stdout)
        # The other legs still run, and still fail the check.
        result = self.check("disabled", "openssl")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED (openssl)", result.stderr)

    def test_require_sip_refuses_a_mac_without_it(self) -> None:
        for sip in ("disabled", "unknown (Custom Configuration)"):
            with self.subTest(sip=sip):
                result = self.check(sip, "", "--require-sip")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("System Integrity Protection isn't reported enabled", result.stderr)
                self.assertNotIn("== openssl", result.stdout)
        result = self.check("enabled", "", "--require-sip")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_unknown_sip_status_still_runs_the_dyld_and_cwd_legs(self) -> None:
        result = self.check("unknown (Custom Configuration)", "dyld cwd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED (dyld cwd)", result.stderr)


# -- tools/sign_release.py publish ------------------------------------------------------

MAC_NOTICES_TEXT = b"# Notices for the Mac app\n"


def app_zip(version: str, notices: bytes = MAC_NOTICES_TEXT) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DoubleClick Fixer.app/Contents/Info.plist", plistlib.dumps({"CFBundleShortVersionString": version}))
        archive.writestr("DoubleClick Fixer.app/Contents/MacOS/DoubleClickFixer", b"binary " + version.encode())
        archive.writestr("DoubleClick Fixer.app/Contents/Resources/THIRD_PARTY_NOTICES.md", notices)
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGitHub:
    """GitHub (through gh), ditto, codesign and the injection check, as
    `publish` uses them."""

    def __init__(self, tag: str = "v1.0.1") -> None:
        self.tag = tag
        self.version = tag[1:].split("-")[0]
        self.commit = "c0ffee" * 6 + "abcd"
        self.draft = True
        self.prerelease = "-" in tag
        self.requirement = APP_REQUIREMENT
        self.codesign_ok = True
        self.injection_ok = True
        self.calls: list[tuple] = []
        self.serial = 0
        self.assets: dict = {}
        self.digests: dict = {}  # GitHub's listed digest, when it differs
        self.before_view = None  # called as each `release view` starts
        self.corrupt_upload = False
        built = {
            "DoubleClickFixer.exe": b"portable exe",
            "DoubleClickFixer-Setup.exe": b"installer",
            "DoubleClickFixer.dmg": b"disk image",
            MAC_ZIP: app_zip(self.version),
            MAC_NOTICES: MAC_NOTICES_TEXT,
            WINDOWS_NOTICES: b"# Notices for Windows\n",
        }
        self.artifacts = {name: {file: built[file] for file in files} for name, files in ARTIFACTS.items()}
        for name, data in built.items():
            self.put(name, data)
        self.put(CHECKSUMS, "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(built.items())).encode())
        self.runs = [self.run_record(4242)]

    def rebuild(self, name: str, data: bytes) -> None:
        """As if the run had built `data` as `name`: the draft, the artifact and
        SHA256SUMS.txt all agree on it."""
        self.put(name, data)
        for files in self.artifacts.values():
            if name in files:
                files[name] = data
        built = {other: self.assets[other][1] for other in RELEASE_FILES if other != CHECKSUMS}
        self.put(CHECKSUMS, "".join(f"{sha256(content)}  {other}\n" for other, content in sorted(built.items())).encode())

    def run_record(self, number: int, **changes) -> dict:
        record = dict(databaseId=number, headBranch=self.tag, headSha=self.commit, event="push",
                      conclusion="success", workflowName="Release")
        record.update(changes)
        return record

    def put(self, name: str, data: bytes) -> None:
        self.serial += 1
        self.assets[name] = (f"RA_{self.serial}", data)

    def calls_of(self, *prefix: str) -> list[tuple]:
        return [call for call in self.calls if call[: len(prefix)] == prefix]

    # Commands.run
    def run(self, *command: str, both: bool = False) -> str:
        self.calls.append(command)
        program, arguments = command[0], list(command[1:])
        if program == "ditto":
            with zipfile.ZipFile(arguments[2]) as archive:
                archive.extractall(arguments[3])
            return ""
        if program == "bash":
            assert arguments[0] == str(INJECTION_CHECK) and arguments[2:] == ["--require-sip"], command
            assert (Path(arguments[1]) / "Contents" / "MacOS" / "DoubleClickFixer").is_file(), command
            if not self.injection_ok:
                raise ReleaseError("`bash tools/macos_injection_check.sh` failed (exit 1): FAILED (dyld): the app loaded "
                                   "or ran code named in its environment, or its self-test failed")
            return "OK: no canary fired and the self-test passed, with System Integrity Protection enabled\n"
        if program == "codesign":
            if arguments[0] == "--verify":
                if not self.codesign_ok:
                    raise ReleaseError("`codesign --verify` failed: invalid signature")
                return ""
            return "Executable=/x/DoubleClick Fixer.app/Contents/MacOS/DoubleClickFixer\n" + (
                f"designated => {self.requirement}\n" if self.requirement else ""
            )
        assert program == "gh", command
        option = lambda name: arguments[arguments.index(name) + 1] if name in arguments else None  # noqa: E731
        if arguments[:2] == ["release", "view"]:
            assert arguments[2] == self.tag and option("--repo") == REPOSITORY
            if self.before_view:
                self.before_view()
            listing = {
                "tagName": self.tag, "isDraft": self.draft, "isPrerelease": self.prerelease,
                "assets": [
                    {"name": name, "id": identity, "size": len(data),
                     "digest": self.digests.get(name, "sha256:" + sha256(data))}
                    for name, (identity, data) in self.assets.items()
                ],
            }
            return json.dumps(listing)
        if arguments[0] == "api":
            assert arguments[1] == f"repos/{REPOSITORY}/commits/{self.tag}"
            return self.commit + "\n"
        if arguments[:2] == ["run", "list"]:
            assert option("--workflow") == "release.yml" and option("--branch") == self.tag
            return json.dumps(self.runs)
        if arguments[:2] == ["run", "view"]:
            matching = [run for run in self.runs if str(run["databaseId"]) == arguments[2]]
            if not matching:
                raise ReleaseError("`gh run view` failed: not found")
            return json.dumps(matching[0])
        if arguments[:2] == ["release", "download"]:
            folder = Path(option("--dir"))
            folder.mkdir(parents=True, exist_ok=True)
            for name, (_identity, data) in self.assets.items():
                if option("--pattern") is None or fnmatch.fnmatch(name, option("--pattern")):
                    (folder / name).write_bytes(data)
            return ""
        if arguments[:2] == ["run", "download"]:
            assert arguments[2] in {str(run["databaseId"]) for run in self.runs}
            folder = Path(option("--dir"))
            folder.mkdir(parents=True, exist_ok=True)
            for name, data in self.artifacts[option("--name")].items():
                (folder / name).write_bytes(data)
            return ""
        if arguments[:2] == ["release", "upload"]:
            path = Path(arguments[3])
            self.put(path.name, b"garbled" if self.corrupt_upload else path.read_bytes())
            return ""
        if arguments[:2] == ["release", "edit"]:
            assert "--draft=false" in arguments
            self.draft = False
            return ""
        raise AssertionError(f"unexpected command {command}")


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.secret, public = keygen("test", self.root / "keys", self.root / "public")
        self.keys = [parse_public_key(public.read_text(encoding="ascii"))]
        self.github = FakeGitHub()
        self.said: list[str] = []
        self.workdirs = 0

    def publish(self, tag: str = "v1.0.1", **options) -> Path:
        self.workdirs += 1
        options.setdefault("key_path", self.secret)
        return publish_release(
            tag, workdir=self.root / f"work{self.workdirs}", repository=REPOSITORY, commands=self.github,
            keys=self.keys, say=self.said.append, **options,
        )

    def refused(self, pattern: str, **options) -> None:
        with self.assertRaisesRegex(ReleaseError, pattern):
            self.publish(**options)
        self.assertFalse(self.github.calls_of("gh", "release", "upload"), "uploaded after a refusal")
        self.assertFalse(self.github.calls_of("gh", "release", "edit"), "published after a refusal")

    def test_a_good_draft_is_signed_then_published_as_latest(self) -> None:
        self.publish()
        uploaded = self.github.assets[SIGNATURE][1]
        comment = verify(self.github.assets[CHECKSUMS][1], uploaded, self.keys)
        self.assertEqual(parse_claim(comment).version, "1.0.1")
        self.assertEqual(comment, "dcf 1.0.1")
        calls = self.github.calls
        upload = calls.index(self.github.calls_of("gh", "release", "upload")[0])
        edit = calls.index(self.github.calls_of("gh", "release", "edit")[0])
        self.assertLess(upload, edit, "published before the signature was up")
        self.assertIn("--latest", calls[edit])
        self.assertNotIn("--latest=false", calls[edit])
        self.assertFalse(self.github.draft)
        # It compared with the run that built the tag, and checked the app's signature.
        self.assertEqual(len(self.github.calls_of("gh", "run", "download")), len(ARTIFACTS))
        self.assertTrue(all(call[3] == "4242" for call in self.github.calls_of("gh", "run", "download")))
        self.assertTrue(self.github.calls_of("codesign", "--verify", "--deep", "--strict"))
        self.assertEqual(len(self.github.calls_of("gh", "release", "view")), 2, "the draft wasn't re-read before publishing")
        # The injection check ran, every leg, on the draft's own app, before anything was signed.
        (check,) = self.github.calls_of("bash")
        self.assertEqual(check[1:], (str(INJECTION_CHECK), str(self.root / "work1" / "unpacked" / "DoubleClick Fixer.app"), "--require-sip"))
        self.assertLess(calls.index(check), upload)
        self.assertGreater(calls.index(check), calls.index(self.github.calls_of("codesign", "-d", "-r-")[0]))

    def test_a_dry_run_checks_everything_and_changes_nothing(self) -> None:
        self.publish(dry_run=True)
        self.assertFalse(self.github.calls_of("gh", "release", "upload"))
        self.assertFalse(self.github.calls_of("gh", "release", "edit"))
        self.assertNotIn(SIGNATURE, self.github.assets)
        self.assertTrue(self.github.draft)
        self.assertTrue(self.github.calls_of("codesign", "-d", "-r-"))
        self.assertTrue(any("Dry run" in line for line in self.said))
        # The key is only looked for, never read.
        with mock.patch.object(sign_release, "read_secret_key", side_effect=AssertionError("read the key")):
            self.publish(dry_run=True)
        with self.assertRaisesRegex(ReleaseError, "no secret key"):
            self.publish(dry_run=True, key_path=self.root / "missing.key")

    def test_a_file_the_run_didnt_build_is_refused(self) -> None:
        self.github.put("DoubleClickFixer.exe", b"swapped")
        self.refused(r"DoubleClickFixer.exe on the draft isn't the file the run built")

    def test_a_draft_with_files_missing_or_extra_is_refused(self) -> None:
        del self.github.assets[MAC_NOTICES]
        self.refused("The draft has no THIRD_PARTY_NOTICES-macos.md")
        self.github = FakeGitHub()
        self.github.put("extra.zip", b"?")
        self.refused("a file no release carries: extra.zip")

    def test_an_artifact_with_other_files_is_refused(self) -> None:
        self.github.artifacts["DoubleClickFixer-macos"]["notes.txt"] = b"?"
        self.refused("The DoubleClickFixer-macos artifact holds")

    def test_a_download_that_doesnt_match_githubs_digest_is_refused(self) -> None:
        self.github.digests["DoubleClickFixer.dmg"] = "sha256:" + "0" * 64
        self.refused("DoubleClickFixer.dmg doesn't match the digest GitHub lists")

    def test_checksums_that_dont_match_are_refused(self) -> None:
        name, data = "DoubleClickFixer.exe", b"rebuilt"
        self.github.put(name, data)
        self.github.artifacts["DoubleClickFixer-windows"][name] = data
        self.refused("DoubleClickFixer.exe doesn't match its checksum")

    def test_an_app_of_another_version_is_refused(self) -> None:
        data = app_zip("1.0.0")
        self.github.put(MAC_ZIP, data)
        self.github.artifacts["DoubleClickFixer-macos"][MAC_ZIP] = data
        lines = [line for line in self.github.assets[CHECKSUMS][1].decode().splitlines() if not line.endswith(MAC_ZIP)]
        self.github.put(CHECKSUMS, ("\n".join(lines + [f"{sha256(data)}  {MAC_ZIP}"]) + "\n").encode())
        self.refused("is version 1.0.0, not 1.0.1")

    def test_the_injection_check_must_pass_before_anything_is_signed(self) -> None:
        self.github.injection_ok = False
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with mock.patch.object(sign_release, "read_secret_key", side_effect=AssertionError("read the key")):
                    self.refused(r"failed tools/macos_injection_check\.sh(.|\n)*FAILED \(dyld\)", dry_run=dry_run)
                self.assertNotIn(SIGNATURE, self.github.assets)
        self.assertTrue(INJECTION_CHECK.is_file())
        self.assertIn("--require-sip)", INJECTION_CHECK.read_text(encoding="utf-8"))

    def test_the_mac_notices_must_be_the_ones_inside_the_app(self) -> None:
        self.github.rebuild(MAC_NOTICES, b"# Some other notices\n")
        self.refused("THIRD_PARTY_NOTICES-macos.md on the draft isn't the THIRD_PARTY_NOTICES.md inside the app")
        self.github = FakeGitHub()
        self.github.rebuild(MAC_ZIP, app_zip("1.0.1", notices=b"# Notices from another build\n"))
        self.refused("isn't the THIRD_PARTY_NOTICES.md inside the app")

    def test_only_a_draft_is_published(self) -> None:
        self.github.draft = False
        self.refused("already published")

    def test_the_run_must_be_the_tags_own_successful_build(self) -> None:
        for runs, message in (
            ([], "No successful release.yml run built v1.0.1"),
            ([self.github.run_record(1, conclusion="failure")], "No successful"),
            ([self.github.run_record(1, headSha="f" * 40)], "No successful"),
            ([self.github.run_record(1, event="workflow_dispatch")], "No successful"),
            ([self.github.run_record(1, workflowName="CI")], "No successful"),
            ([self.github.run_record(1), self.github.run_record(2)], "More than one run built v1.0.1"),
        ):
            self.github.runs = runs
            self.refused(message)
        self.assertEqual(self.github.calls_of("gh", "run", "download"), [])
        # Naming one settles it, if it is a good one.
        self.github.runs = [self.github.run_record(1), self.github.run_record(2)]
        self.publish(run_id=2)
        self.assertTrue(all(call[3] == "2" for call in self.github.calls_of("gh", "run", "download")))
        self.github = FakeGitHub()
        self.github.runs = [self.github.run_record(7, conclusion="cancelled")]
        self.refused("Run 7 isn't a successful", run_id=7)

    def test_the_mac_app_must_keep_its_designated_requirement(self) -> None:
        self.github.requirement = 'identifier "com.doubleclickfixer.app" and certificate root = H"0000"'
        self.refused("take the Accessibility permission away")
        self.github = FakeGitHub()
        self.github.requirement = ""
        self.refused(r"designated requirement\n    \(none\)")
        self.github = FakeGitHub()
        self.github.codesign_ok = False
        self.refused("invalid signature")

    def test_a_new_signing_identity_must_be_named(self) -> None:
        moved = 'identifier "com.doubleclickfixer.app" and certificate leaf[subject.OU] = ABCDE12345'
        self.github.requirement = moved
        self.publish(requirement=moved)
        comment = verify(self.github.assets[CHECKSUMS][1], self.github.assets[SIGNATURE][1], self.keys)
        self.assertEqual(parse_claim(comment).requirement, moved)

    def test_a_pre_release_is_checked_but_never_signed(self) -> None:
        self.github = FakeGitHub("v1.1.0-rc.1")
        self.publish("v1.1.0-rc.1")
        self.assertNotIn(SIGNATURE, self.github.assets)
        edit = self.github.calls_of("gh", "release", "edit")[0]
        self.assertIn("--latest=false", edit)
        self.assertTrue(self.github.calls_of("codesign", "-d", "-r-"))
        self.github = FakeGitHub("v1.1.0-rc.2")
        self.github.prerelease = False
        self.refused("not marked as a pre-release", tag="v1.1.0-rc.2")
        self.github = FakeGitHub("v1.1.0-rc.3")
        self.github.put(SIGNATURE, b"signed")
        self.refused("A pre-release mustn't carry", tag="v1.1.0-rc.3")
        self.github = FakeGitHub()
        self.github.prerelease = True
        self.refused("is marked as a pre-release")

    def test_a_good_signature_already_up_is_kept(self) -> None:
        self.publish(dry_run=False)
        signature = self.github.assets[SIGNATURE]
        self.github.draft = True
        self.github.calls.clear()
        self.publish()
        self.assertEqual(self.github.assets[SIGNATURE], signature)
        self.assertFalse(self.github.calls_of("gh", "release", "upload"))
        self.assertTrue(self.github.calls_of("gh", "release", "edit"))
        # One for another version, though, is not.
        self.github.draft = True
        key_id, seed = sign_release.read_secret_key(self.secret)
        self.github.put(SIGNATURE, sign_release.signature_text(self.github.assets[CHECKSUMS][1], key_id, seed, "dcf 1.0.0"))
        self.github.calls.clear()
        self.refused("not version 1.0.1")

    def test_a_draft_that_changes_while_it_is_checked_isnt_published(self) -> None:
        views = []

        def swap() -> None:
            views.append(1)
            if len(views) == 1:
                return
            self.github.put("DoubleClickFixer.dmg", b"disk image")  # same bytes, new upload

        self.github.before_view = swap
        with self.assertRaisesRegex(ReleaseError, "changed on the draft"):
            self.publish()
        self.assertFalse(self.github.calls_of("gh", "release", "edit"))

    def test_a_signature_that_didnt_upload_intact_stops_it(self) -> None:
        self.github.corrupt_upload = True
        with self.assertRaisesRegex(ReleaseError, "isn't the one just uploaded"):
            self.publish()
        self.assertFalse(self.github.calls_of("gh", "release", "edit"))

    def test_tags(self) -> None:
        self.assertEqual(parse_tag("v1.0.1"), ("v1.0.1", "1.0.1", False))
        self.assertEqual(parse_tag("1.0.1"), ("v1.0.1", "1.0.1", False))
        self.assertEqual(parse_tag("v1.1.0-rc.1"), ("v1.1.0-rc.1", "1.1.0", True))
        for text in ("v1.0", "latest", "v1.0.1-", "v1.0.1-rc 1", "v1.0.1.2", ""):
            with self.assertRaises(ReleaseError):
                parse_tag(text)

    def test_the_work_folder_must_be_empty(self) -> None:
        (self.root / "work1").mkdir()
        (self.root / "work1" / "old").write_text("x")
        with self.assertRaisesRegex(ReleaseError, "isn't empty"):
            self.publish()

    def test_the_repository_defaults_to_the_one_copies_update_from(self) -> None:
        self.assertEqual(sign_release.default_repository(), "arnav-goel10/doubleclick-fixer")

    def test_command_line(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sign_release, "Commands", return_value=self.github), \
                mock.patch.object(sign_release, "default_repository", return_value=REPOSITORY), \
                redirect_stdout(output), redirect_stderr(errors):
            work = self.root / "cli"
            arguments = ["publish", "v1.0.1", "--dry-run", "--workdir", str(work), "--key", str(self.secret)]
            self.assertEqual(sign_release.main(arguments), 0)
            self.github.draft = False
            self.assertEqual(sign_release.main(arguments[:3] + ["--workdir", str(self.root / "cli2")]), 1)
        self.assertIn("Dry run: nothing changed", output.getvalue())
        self.assertIn(f"What was checked is in {work}", output.getvalue())
        self.assertIn("already published", errors.getvalue())
        self.assertTrue(self.github.calls_of("gh", "release", "view"))

    def test_help_lists_every_step_of_a_release(self) -> None:
        for arguments in (["--help"], ["publish", "--help"]):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                sign_release.main(arguments)
            text = output.getvalue()
            steps = [
                "git switch main && git pull --ff-only",
                "git merge --no-ff BRANCH",
                '__version__ = "1.0.1"',
                '"## 1.0.1 — YYYY-MM-DD"',
                "GITHUB_REF=refs/tags/v1.0.1 python3 -m unittest tests.test_release",
                "git push origin main",
                'git tag -a v1.0.1 -m "DoubleClick Fixer 1.0.1"',
                "git push origin v1.0.1",
                "until run=\"$(gh run list --workflow release.yml --branch v1.0.1 --event push --limit 1 "
                "--json databaseId --jq '.[].databaseId')\" && [ -n \"$run\" ]; do sleep 5; done",
                'gh run watch "$run" --exit-status',
                "python3 tools/sign_release.py publish v1.0.1 --dry-run",
                "python3 tools/sign_release.py publish v1.0.1",
            ]
            positions = [text.find(step) for step in steps]
            self.assertNotIn(-1, positions, f"{arguments}: {[step for step in steps if step not in text]}")
            self.assertEqual(positions, sorted(positions), "the steps are out of order")


if __name__ == "__main__":
    unittest.main()
