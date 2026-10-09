"""tools/winget_manifest.py: the winget manifests for a release, from its
signed SHA256SUMS.txt, checked against winget's schema (with gh faked: it
never touches GitHub here, and signs with throwaway keys only)."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import io
import json
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import app
from app.update_signature import parse_public_key
from tools import sign_release, winget_manifest
from tools.sign_release import CHECKSUMS, SIGNATURE, ReleaseError, keygen, read_secret_key
from tools.winget_manifest import (
    ANNOTATIONS,
    FORMATS,
    INSTALLER,
    KEYWORDS,
    MANIFEST_VERSION,
    PACKAGE_IDENTIFIER,
    SCHEMA_DIR,
    SchemaError,
    from_yaml,
    load_schema,
    make_manifests,
    schema_problems,
    to_yaml,
    yaml_scalar,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "arnav-goel10/mouse-double-click-fixer"
VERSION = "1.0.0"
SETUP_HASH = "0123456789abcdef" * 4
MANIFEST_TYPES = ("version", "installer", "defaultLocale")


def changelog_with(version: str, date: str) -> str:
    return f"# Changelog\n\n## {version} — {date}\n\n- Things.\n"


class Release:
    """A folder with SHA256SUMS.txt and its signature, as a published
    release has them, signed with a throwaway key."""

    def __init__(self, root: Path, name: str = "test") -> None:
        secret, public = keygen(name, root / "keys", root / "public")
        self.key_id, self.seed = read_secret_key(secret)
        self.key = parse_public_key(public.read_text(encoding="ascii"))
        self.folder = root / f"release-{name}"
        self.folder.mkdir()

    def write(self, listed: dict, comment: str = f"dcf {VERSION}") -> Path:
        checksums = "".join(f"{digest}  {name}\n" for name, digest in listed.items()).encode()
        (self.folder / CHECKSUMS).write_bytes(checksums)
        (self.folder / SIGNATURE).write_bytes(sign_release.signature_text(checksums, self.key_id, self.seed, comment))
        return self.folder


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.release = Release(self.root)
        self.out = self.root / "out"
        self.listed = {"DoubleClickFixer.exe": "ab" * 32, INSTALLER: SETUP_HASH, "DoubleClickFixer.dmg": "cd" * 32}
        self.changelog = self.root / "CHANGELOG.md"
        self.changelog.write_text(changelog_with(VERSION, "2026-10-10"), encoding="utf-8")

    def make(self, version: str = VERSION, **options) -> Path:
        if "checksums_folder" not in options:
            options["checksums_folder"] = self.release.write(self.listed)
        return make_manifests(version, self.out, REPOSITORY, keys=[self.release.key], changelog=self.changelog, **options)

    def read(self, folder: Path) -> dict:
        return {path.name: from_yaml(path.read_text(encoding="utf-8")) for path in sorted(folder.iterdir())}

    def test_a_signed_release_gives_three_manifests_in_the_repositorys_layout(self) -> None:
        folder = self.make()
        self.assertEqual(folder, self.out / "manifests" / "a" / "ArnavGoel" / "MouseDoubleClickFixer" / VERSION)
        manifests = self.read(folder)
        self.assertEqual(sorted(manifests), [
            f"{PACKAGE_IDENTIFIER}.installer.yaml", f"{PACKAGE_IDENTIFIER}.locale.en-US.yaml", f"{PACKAGE_IDENTIFIER}.yaml",
        ])
        version = manifests[f"{PACKAGE_IDENTIFIER}.yaml"]
        installer = manifests[f"{PACKAGE_IDENTIFIER}.installer.yaml"]
        locale = manifests[f"{PACKAGE_IDENTIFIER}.locale.en-US.yaml"]
        for manifest in (version, installer, locale):
            self.assertEqual((manifest["PackageIdentifier"], manifest["PackageVersion"]), (PACKAGE_IDENTIFIER, VERSION))
            self.assertEqual(manifest["ManifestVersion"], MANIFEST_VERSION)
            self.assertEqual(schema_problems(manifest, load_schema(manifest["ManifestType"])), [])
        self.assertEqual(version["DefaultLocale"], "en-US")
        self.assertEqual(installer["Installers"], [{
            "Architecture": "x64",
            "InstallerUrl": f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{INSTALLER}",
            "InstallerSha256": SETUP_HASH.upper(),
        }])
        self.assertEqual((installer["InstallerType"], installer["Scope"]), ("inno", "user"))
        self.assertEqual(installer["ReleaseDate"], "2026-10-10")
        self.assertEqual(locale["PackageName"], app.DISPLAY_NAME)
        self.assertEqual(locale["ReleaseNotesUrl"], f"https://github.com/{REPOSITORY}/releases/tag/v{VERSION}")
        self.assertEqual(locale["LicenseUrl"], f"https://github.com/{REPOSITORY}/blob/v{VERSION}/LICENSE")
        self.assertEqual(locale["Moniker"], "double-click-fixer")
        self.assertIn("Copyright (c) 2026 Arnav Goel", (ROOT / "LICENSE").read_text(encoding="utf-8"))
        self.assertEqual(locale["Copyright"], "Copyright (c) 2026 Arnav Goel")
        # Each file names the schema it follows, for editors and reviewers.
        text = (folder / f"{PACKAGE_IDENTIFIER}.installer.yaml").read_text(encoding="utf-8")
        self.assertIn(f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.installer.{MANIFEST_VERSION}.schema.json\n", text)

    def test_the_installer_is_described_as_installer_windows_iss_builds_it(self) -> None:
        iss = (ROOT / "installer" / "windows.iss").read_text(encoding="utf-8")
        app_id = re.search(r"^AppId=\{(\{[0-9A-F-]{36}\})$", iss, re.MULTILINE).group(1)
        installer = self.read(self.make())[f"{PACKAGE_IDENTIFIER}.installer.yaml"]
        # Inno Setup's uninstall entry: what winget reads the installed version from.
        self.assertEqual(installer["ProductCode"], app_id + "_is1")
        self.assertEqual(installer["AppsAndFeaturesEntries"], [{
            "DisplayName": f"{app.DISPLAY_NAME} {VERSION}", "Publisher": "Mouse Double-Click Fixer", "ProductCode": app_id + "_is1",
        }])
        self.assertIn(f"\nAppVerName={app.DISPLAY_NAME} {{#AppVersion}}\n", iss)
        self.assertIn("\nMinVersion=10.0.17763\n", iss)
        self.assertEqual(installer["MinimumOSVersion"], "10.0.17763.0")
        # The silent install winget runs is the one CI's install test runs.
        self.assertNotIn("InstallerSwitches", installer)
        self.assertIn('"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"',
                      (ROOT / "tools" / "windows_install_e2e.ps1").read_text(encoding="utf-8"))

    def test_a_setup_it_would_describe_wrongly_is_refused(self) -> None:
        iss = (ROOT / "installer" / "windows.iss").read_text(encoding="utf-8")
        for old, new in (("PrivilegesRequired=lowest", "PrivilegesRequired=admin"),
                         ("ArchitecturesAllowed=x64compatible", "ArchitecturesAllowed=x86compatible"),
                         ("AppVerName=Mouse Double-Click Fixer {#AppVersion}", "AppVerName={#SetupSetting(\"AppName\")}")):
            with self.subTest(new=new):
                changed = self.root / f"{len(new)}.iss"
                changed.write_text(iss.replace(old, new), encoding="utf-8")
                with self.assertRaises(ReleaseError):
                    winget_manifest.installer_facts(changed)

    def test_only_a_release_key_signature_for_this_version_is_trusted(self) -> None:
        other = Release(self.root, "other")
        with self.assertRaisesRegex(ReleaseError, "isn't signed by a release key"):
            self.make(checksums_folder=other.write(self.listed))
        with self.assertRaisesRegex(ReleaseError, "signed for 'dcf 1.0.1', not version 1.0.0"):
            self.make(checksums_folder=self.release.write(self.listed, comment="dcf 1.0.1"))
        folder = self.release.write(self.listed)
        (folder / CHECKSUMS).write_text(f"{'ee' * 32}  {INSTALLER}\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseError, "isn't signed by a release key"):
            self.make(checksums_folder=folder)
        (folder / SIGNATURE).unlink()
        with self.assertRaisesRegex(ReleaseError, "a published release has both"):
            self.make(checksums_folder=folder)
        self.assertFalse(self.out.exists())

    def test_the_checksums_must_list_the_installer(self) -> None:
        del self.listed[INSTALLER]
        with self.assertRaisesRegex(ReleaseError, f"doesn't list {INSTALLER}"):
            self.make()

    def test_full_releases_only(self) -> None:
        for version in ("1.1.0-rc.1", "1.0", "v1.0.0", "1.0.0.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ReleaseError, "full releases only"):
                self.make(version)

    def test_the_release_date_comes_from_the_changelog(self) -> None:
        self.assertEqual(winget_manifest.release_date("1.0.0", self.changelog), "2026-10-10")
        self.changelog.write_text("# Changelog\n\n## Unreleased\n\n## 0.5.3 — 2026-09-01\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseError, "no dated entry for 1.0.0"):
            self.make()

    def test_by_default_it_downloads_the_checksums_and_their_signature(self) -> None:
        commands = mock.Mock()
        signed = self.release.write(self.listed)

        def download(version: str, repository: str, folder: Path) -> Path:
            winget_manifest.download_checksums(version, repository, folder, commands)
            for name in (CHECKSUMS, SIGNATURE):
                shutil.copy(signed / name, folder / name)
            return folder

        folder = self.make(checksums_folder=None, download=download)
        commands.run.assert_called_once_with(
            "gh", "release", "download", "v1.0.0", "--repo", REPOSITORY,
            "--pattern", CHECKSUMS, "--pattern", SIGNATURE, "--dir", mock.ANY,
        )
        self.assertEqual(self.read(folder)[f"{PACKAGE_IDENTIFIER}.installer.yaml"]["Installers"][0]["InstallerSha256"],
                         SETUP_HASH.upper())

    def test_a_second_run_replaces_the_versions_folder(self) -> None:
        folder = self.make()
        (folder / "stale.yaml").write_text("left over", encoding="utf-8")
        self.assertEqual(self.make(), folder)
        self.assertEqual(len(list(folder.iterdir())), 3)

    def test_command_line(self) -> None:
        folder = self.release.write(self.listed)
        with mock.patch.object(winget_manifest, "trusted_keys", return_value=[self.release.key]), \
                redirect_stdout(io.StringIO()) as said:
            # The real changelog's 1.0.0 entry is dated.
            code = winget_manifest.main([VERSION, "--checksums", str(folder), "--out", str(self.out), "--repo", REPOSITORY])
        self.assertEqual(code, 0)
        self.assertIn("winget validate --manifest", said.getvalue())
        with redirect_stderr(io.StringIO()) as complained:
            code = winget_manifest.main([VERSION, "--checksums", str(self.root / "missing"), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("error:", complained.getvalue())


class SchemaTests(unittest.TestCase):
    """The schema checker, on the vendored winget schemas."""

    def good(self) -> dict:
        return winget_manifest.manifests(VERSION, SETUP_HASH, REPOSITORY, "2026-10-10")

    def installer(self) -> dict:
        return self.good()[f"{PACKAGE_IDENTIFIER}.installer.yaml"]

    def locale(self) -> dict:
        return self.good()[f"{PACKAGE_IDENTIFIER}.locale.en-US.yaml"]

    def problems(self, manifest: dict) -> list:
        return schema_problems(manifest, load_schema(manifest["ManifestType"]))

    def test_the_vendored_schemas_are_this_manifest_version_with_their_licence(self) -> None:
        for kind in MANIFEST_TYPES:
            schema = load_schema(kind)
            self.assertEqual(schema["$schema"], "http://json-schema.org/draft-07/schema#")
            self.assertTrue(schema["$id"].endswith(f".{MANIFEST_VERSION}.schema.json"), schema["$id"])
            self.assertEqual(schema["properties"]["ManifestVersion"]["default"], MANIFEST_VERSION)
        licence = (SCHEMA_DIR / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Copyright (c) Microsoft Corporation", licence)
        self.assertIn("winget-cli", (SCHEMA_DIR / "README.md").read_text(encoding="utf-8"))

    def test_the_checker_applies_every_keyword_the_schemas_use(self) -> None:
        def walk(schema: dict, where: str) -> None:
            self.assertLessEqual(set(schema), KEYWORDS | ANNOTATIONS, where)
            self.assertIn(schema.get("format", "date"), FORMATS, where)
            if "$ref" in schema:
                self.assertRegex(schema["$ref"], r"^#/definitions/\w+$", where)
            for name in ("properties", "definitions"):
                for key, value in schema.get(name, {}).items():
                    walk(value, f"{where}.{key}")
            for name in ("items", "not"):
                if name in schema:
                    walk(schema[name], f"{where}.{name}")
            for index, option in enumerate(schema.get("oneOf", [])):
                walk(option, f"{where}.oneOf[{index}]")

        for kind in MANIFEST_TYPES:
            walk(load_schema(kind), kind)
        with self.assertRaisesRegex(SchemaError, "patternProperties"):
            schema_problems({}, {"type": "object", "patternProperties": {}})
        with self.assertRaisesRegex(SchemaError, "format"):
            schema_problems("x", {"type": "string", "format": "uri"})

    def test_good_manifests_pass(self) -> None:
        for name, manifest in self.good().items():
            self.assertEqual(self.problems(manifest), [], name)

    def test_what_the_schema_refuses_is_refused(self) -> None:
        cases = {
            "InstallerSha256": lambda m: m["Installers"][0].update(InstallerSha256="00"),
            "not one of": lambda m: m.update(InstallerType="setup.exe"),
            "has no Installers": lambda m: m.pop("Installers"),
            "not string": lambda m: m.update(MinimumOSVersion=10),
            "may not be 0": lambda m: m.update(InstallerSuccessCodes=[0]),
            "not a date": lambda m: m.update(ReleaseDate="2026-13-01"),
            "lists something twice": lambda m: m.update(InstallModes=["silent", "silent"]),
            "not exactly one": lambda m: m.update(Markets={"AllowedMarkets": ["US"], "ExcludedMarkets": ["GB"]}),
            "fewer than 1 items": lambda m: m.update(Installers=[]),
            "not 'installer'": lambda m: m.update(ManifestType="version"),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                manifest = self.installer()
                change(manifest)
                problems = schema_problems(manifest, load_schema("installer"))
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_the_locale_limits_hold(self) -> None:
        for expected, change in {
            "longer than 256": lambda m: m.update(ShortDescription="x" * 257),
            "doesn't match": lambda m: m.update(PackageIdentifier="no dots"),
            "more than 16 items": lambda m: m.update(Tags=[f"tag{n}" for n in range(17)]),
            "Tags[0] is shorter than 1": lambda m: m.update(Tags=[""]),
        }.items():
            with self.subTest(expected):
                manifest = self.locale()
                change(manifest)
                problems = schema_problems(manifest, load_schema("defaultLocale"))
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_a_misspelt_field_is_refused_though_the_schema_allows_it(self) -> None:
        manifest = self.locale()
        manifest["ReleaseNoteUrl"] = manifest.pop("ReleaseNotesUrl")
        self.assertEqual(self.problems(manifest), [f"ReleaseNoteUrl isn't a field of a {MANIFEST_VERSION} manifest"])
        manifest = self.installer()
        manifest["Installers"][0]["InstallerSha"] = SETUP_HASH
        self.assertEqual(self.problems(manifest), [f"InstallerSha isn't a field of a {MANIFEST_VERSION} manifest"])

    def test_ecmascript_end_anchor(self) -> None:
        # In a JSON schema's pattern, $ doesn't match before a final newline.
        self.assertTrue(schema_problems("ABC\n", {"type": "string", "pattern": "^[A-Z]+$"}))
        self.assertFalse(schema_problems("ABC", {"type": "string", "pattern": "^[A-Z]+$"}))


class YamlTests(unittest.TestCase):
    def test_values_that_would_read_back_as_something_else_are_quoted(self) -> None:
        for value in ("yes", "No", "on", "null", "~", "1.0", "1e3", "0x1F", "017", "2026-10-10", "1:20", ".inf",
                      "a: b", "x #y", "ends:", "ends ", "", "{6B0E}_is1", "-dash", "naïve", "two\nlines", "y",
                      "tab\there", "#hash", "'quoted'", '"double"', "[list]", "&anchor", "*alias", "!tag", "@at"):
            with self.subTest(value=value):
                data = {"Key": value, "List": [value, {"Inner": value}]}
                self.assertEqual(from_yaml(to_yaml(data, "schema")), data)
                self.assertNotEqual(yaml_scalar(value), value)

    def test_ordinary_values_stay_plain(self) -> None:
        for value in ("1.0.0", "10.0.17763.0", "ArnavGoel.MouseDoubleClickFixer", "x64", "https://github.com/a/b",
                      "Copyright (c) 2026 Arnav Goel", "Mouse Double-Click Fixer 1.0.0", "it's"):
            self.assertEqual(yaml_scalar(value), value)

    def test_the_layout_is_the_community_repositorys(self) -> None:
        text = to_yaml({"A": "x", "B": ["v"], "C": [{"D": "z", "E": "w"}]}, "https://example.com/s.json")
        self.assertEqual(text, "# Created with tools/winget_manifest.py (github.com/arnav-goel10/mouse-double-click-fixer)\n"
                               "# yaml-language-server: $schema=https://example.com/s.json\n\n"
                               "A: x\nB:\n- v\nC:\n- D: z\n  E: w\n")

    def test_the_reader_refuses_what_the_writer_never_writes(self) -> None:
        for text in ("A: 'open\n", "A:\n", "A: x\nA: y\n", "A:x\n", "- x\n", "A: x\n  B: y\n"):
            with self.subTest(text=text), self.assertRaises((ValueError, json.JSONDecodeError)):
                from_yaml(text)


if __name__ == "__main__":
    unittest.main()
