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
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import app
from app.update_signature import parse_claim, parse_public_key, verify
from tools import release_notes, sign_release
from tools.sign_release import (
    APP_REQUIREMENT,
    ARTIFACTS,
    CHECKSUMS,
    MAC_ZIP,
    RELEASE_FILES,
    SIGNATURE,
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
        self.assertIn("releases/download/v1.0.0/THIRD_PARTY_NOTICES.md", page)
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
        self.assertIn("environment: ${{ startsWith(github.ref, 'refs/tags/') && 'release' || '' }}", release)
        self.assertEqual(release.count("secrets."), 2, "the signing secrets belong to the macOS job alone")
        self.assertIn(f"expected='{APP_REQUIREMENT}'", release)

    def test_ci_and_releases_use_the_same_inno_setup(self) -> None:
        versions = {re.search(r'INNO_SETUP_VERSION: "([\d.]+)"', self.workflow(name)).group(1) for name in ("ci.yml", "release.yml")}
        self.assertEqual(len(versions), 1)

    def test_the_release_files_are_the_ones_publish_expects(self) -> None:
        expected = " ".join(sorted(set(RELEASE_FILES) - {CHECKSUMS}, key=lambda name: name.encode()))
        self.assertIn(f'expected="{expected}"', self.workflow("release.yml"))


# -- tools/sign_release.py publish ------------------------------------------------------

def app_zip(version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DoubleClick Fixer.app/Contents/Info.plist", plistlib.dumps({"CFBundleShortVersionString": version}))
        archive.writestr("DoubleClick Fixer.app/Contents/MacOS/DoubleClickFixer", b"binary " + version.encode())
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGitHub:
    """GitHub (through gh), ditto and codesign, as `publish` uses them."""

    def __init__(self, tag: str = "v1.0.1") -> None:
        self.tag = tag
        self.version = tag[1:].split("-")[0]
        self.commit = "c0ffee" * 6 + "abcd"
        self.draft = True
        self.prerelease = "-" in tag
        self.requirement = APP_REQUIREMENT
        self.codesign_ok = True
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
            "THIRD_PARTY_NOTICES.md": b"# Notices\n",
        }
        self.artifacts = {name: {file: built[file] for file in files} for name, files in ARTIFACTS.items()}
        for name, data in built.items():
            self.put(name, data)
        self.put(CHECKSUMS, "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(built.items())).encode())
        self.runs = [self.run_record(4242)]

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
        del self.github.assets["THIRD_PARTY_NOTICES.md"]
        self.refused("The draft has no THIRD_PARTY_NOTICES.md")
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


if __name__ == "__main__":
    unittest.main()
