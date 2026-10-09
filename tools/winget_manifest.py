"""Write the winget manifests for a published release.

    python3 tools/winget_manifest.py 1.0.0 [--out DIR] [--checksums FOLDER] [--repo OWNER/NAME]

The Windows Package Manager (winget) installs what the manifests in its
community repository, microsoft/winget-pkgs, describe: for each version, a
version manifest, an installer manifest and a default-locale manifest. This
writes the three for a published release into build/winget/ (or --out), in
the repository's own layout,

    manifests/a/ArnavGoel/MouseDoubleClickFixer/1.0.0/
        ArnavGoel.MouseDoubleClickFixer.yaml
        ArnavGoel.MouseDoubleClickFixer.installer.yaml
        ArnavGoel.MouseDoubleClickFixer.locale.en-US.yaml

ready for `wingetcreate submit` or a pull request (docs/DISTRIBUTION.md).

The installer's hash comes from the release's SHA256SUMS.txt, downloaded with
`gh` together with SHA256SUMS.txt.minisig and accepted only when one of the
release keys built into the app (RELEASE_KEYS in app/updater.py) signed it
for this version, as installed copies require. With --checksums FOLDER both
files are read from a folder instead, such as one `gh release download`
filled.

Each manifest is checked against winget's JSON schema for its manifest
version, kept in tools/winget-schema/, and, stricter than the schema, may
use only the fields the schema names. The YAML written is read back and must
give the same values.

Only the installer is listed. The portable exe updates itself in place,
which winget, tracking a portable package by the file it put down, would not
expect; the installed copy updates through the installer, whose uninstall
entry winget reads the installed version from, so the two never disagree.
Where the facts live: the installer's identity, scope, architecture and
minimum Windows version in installer/windows.iss, the app's name in
app/__init__.py, the copyright in LICENSE and the release date in
CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.update_signature import PublicKey, SignatureError, parse_claim, verify  # noqa: E402
from tools.sign_release import (  # noqa: E402
    CHECKSUMS,
    SIGNATURE,
    Commands,
    ReleaseError,
    default_repository,
    trusted_keys,
)

PACKAGE_IDENTIFIER = "ArnavGoel.MouseDoubleClickFixer"
PUBLISHER = "Arnav Goel"
#: The newest manifest version the community repository's own tools write
#: (wingetcreate 1.12, komac 2.16). winget-cli also has a 1.28 schema, which
#: its documentation says the 1.28 client doesn't fully support.
MANIFEST_VERSION = "1.12.0"
LOCALE = "en-US"
INSTALLER = "DoubleClickFixer-Setup.exe"
#: `winget install double-click-fixer`. A moniker must be unique in the
#: community repository: check `winget search --moniker double-click-fixer`
#: finds nothing before the first submission.
MONIKER = "double-click-fixer"
TAGS = ("mouse", "double-click", "click", "debounce", "switch-bounce", "chatter", "mouse-fix", "utility")
SHORT_DESCRIPTION = "Stops a worn mouse from double-clicking when you click once."
DESCRIPTION = (
    "A worn mouse switch bounces, so one click arrives as two. Mouse Double-Click Fixer filters the "
    "extra clicks (switch bounce, or chatter) and leaves your real double-clicks alone. It runs in the "
    "notification area, needs no driver, and is free and open source."
)
SCHEMA_DIR = ROOT / "tools" / "winget-schema"
ISS = ROOT / "installer" / "windows.iss"


# -- the facts the manifests state --------------------------------------------------

def iss_settings(source: Path = ISS) -> dict[str, str]:
    """installer/windows.iss's [Setup] directives."""
    settings: dict[str, str] = {}
    section = ""
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Setup" and "=" in line and not line.startswith(";"):
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    return settings


def installer_facts(source: Path = ISS) -> dict[str, str]:
    """What the installer is, from installer/windows.iss, refusing a setup
    this tool would describe wrongly."""
    setup = iss_settings(source)
    app_id = re.fullmatch(r"\{(\{[0-9A-F-]{36}\})", setup.get("AppId", ""))
    if app_id is None:
        raise ReleaseError(f"{source} has no AppId like {{{{GUID}}.")
    if setup.get("PrivilegesRequired") != "lowest":
        raise ReleaseError(f"{source} no longer installs for the current user only; change the manifest's Scope.")
    if setup.get("ArchitecturesAllowed") != "x64compatible":
        raise ReleaseError(f"{source} no longer installs x64 only; change the manifest's Architecture.")
    minimum = setup.get("MinVersion", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", minimum):
        raise ReleaseError(f"{source} has no MinVersion like 10.0.17763.")
    # Inno Setup names the uninstall entry UninstallDisplayName, or else AppVerName.
    listed = setup.get("UninstallDisplayName") or setup.get("AppVerName", "")
    if listed.count("{#AppVersion}") != 1 or re.search(r"\{(?!#AppVersion\})", listed):
        raise ReleaseError(f"{source}'s uninstall entry is named {listed!r}; this tool expects the name and {{#AppVersion}}.")
    return {
        # Inno Setup's uninstall entry is the AppId with "_is1": how winget
        # finds the installed copy and the version it is at.
        "product_code": app_id.group(1) + "_is1",
        "name": setup["AppName"],
        "publisher": setup["AppPublisher"],
        "minimum_os": minimum + ".0",
        "listed_as": listed,
        "output": setup["OutputBaseFilename"] + ".exe",
    }


def copyright_line(source: Path = ROOT / "LICENSE") -> str:
    match = re.search(r"^Copyright \(c\) .+$", source.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ReleaseError(f"{source} has no copyright line.")
    return match.group(0)


def release_date(version: str, source: Path = ROOT / "CHANGELOG.md") -> str:
    match = re.search(rf"^## {re.escape(version)} — (\d{{4}}-\d{{2}}-\d{{2}})$", source.read_text(encoding="utf-8"),
                      re.MULTILINE)
    if match is None:
        raise ReleaseError(f"{source.name} has no dated entry for {version}; run this at the release's tag or later.")
    return match.group(1)


def parse_checksums(text: str) -> dict[str, str]:
    listed = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/\\]+)", line.strip())
        if match:
            listed[match.group(2)] = match.group(1)
    return listed


def signed_checksums(folder: Path, version: str, keys: list[PublicKey]) -> dict[str, str]:
    """SHA256SUMS.txt's hashes, once its signature is one installed copies
    would accept for this version."""
    try:
        checksums = (folder / CHECKSUMS).read_bytes()
        signature = (folder / SIGNATURE).read_bytes()
    except OSError as error:
        raise ReleaseError(f"{error}; a published release has both {CHECKSUMS} and {SIGNATURE}.") from error
    try:
        comment = verify(checksums, signature, keys)
    except SignatureError as error:
        raise ReleaseError(f"{CHECKSUMS} isn't signed by a release key: {error}") from error
    claim = parse_claim(comment)
    if claim is None or claim.version != version:
        raise ReleaseError(f"{CHECKSUMS} is signed for {comment!r}, not version {version}.")
    return parse_checksums(checksums.decode("utf-8", errors="replace"))


def download_checksums(version: str, repository: str, folder: Path, commands: Optional[Commands] = None) -> Path:
    (commands or Commands()).run(
        "gh", "release", "download", f"v{version}", "--repo", repository,
        "--pattern", CHECKSUMS, "--pattern", SIGNATURE, "--dir", str(folder),
    )
    return folder


def manifests(version: str, setup_sha256: str, repository: str, date: str) -> dict[str, dict]:
    """The three manifests, by file name."""
    facts = installer_facts()
    if facts["output"] != INSTALLER:
        raise ReleaseError(f"installer/windows.iss builds {facts['output']}, not {INSTALLER}.")
    site = f"https://github.com/{repository}"
    tag = f"v{version}"
    common = {"PackageIdentifier": PACKAGE_IDENTIFIER, "PackageVersion": version}
    version_manifest = {
        **common,
        "DefaultLocale": LOCALE,
        "ManifestType": "version",
        "ManifestVersion": MANIFEST_VERSION,
    }
    installer_manifest = {
        **common,
        "Platform": ["Windows.Desktop"],
        "MinimumOSVersion": facts["minimum_os"],
        # winget runs an Inno Setup installer with its own switches
        # (/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART for a silent
        # install): what tools/windows_install_e2e.ps1 installs with, over
        # running copies, in CI.
        "InstallerType": "inno",
        "Scope": "user",
        "InstallModes": ["interactive", "silent", "silentWithProgress"],
        "UpgradeBehavior": "install",
        "ProductCode": facts["product_code"],
        "ReleaseDate": date,
        # What the uninstall entry says, which differs from the package's
        # name (it carries the version) and publisher.
        "AppsAndFeaturesEntries": [{
            "DisplayName": facts["listed_as"].replace("{#AppVersion}", version),
            "Publisher": facts["publisher"],
            "ProductCode": facts["product_code"],
        }],
        "Installers": [{
            "Architecture": "x64",
            "InstallerUrl": f"{site}/releases/download/{tag}/{INSTALLER}",
            "InstallerSha256": setup_sha256.upper(),
        }],
        "ManifestType": "installer",
        "ManifestVersion": MANIFEST_VERSION,
    }
    locale_manifest = {
        **common,
        "PackageLocale": LOCALE,
        "Publisher": PUBLISHER,
        "PublisherUrl": f"https://github.com/{repository.split('/')[0]}",
        "PublisherSupportUrl": f"{site}/issues",
        "PrivacyUrl": f"{site}#privacy",
        "Author": PUBLISHER,
        "PackageName": facts["name"],
        "PackageUrl": site,
        "License": "MIT",
        "LicenseUrl": f"{site}/blob/{tag}/LICENSE",
        "Copyright": copyright_line(),
        "ShortDescription": SHORT_DESCRIPTION,
        "Description": DESCRIPTION,
        "Moniker": MONIKER,
        "Tags": list(TAGS),
        "ReleaseNotesUrl": f"{site}/releases/tag/{tag}",
        "Documentations": [{
            "DocumentLabel": "Getting started",
            "DocumentUrl": f"{site}/blob/{tag}/docs/GETTING_STARTED.md",
        }],
        "ManifestType": "defaultLocale",
        "ManifestVersion": MANIFEST_VERSION,
    }
    return {
        f"{PACKAGE_IDENTIFIER}.yaml": version_manifest,
        f"{PACKAGE_IDENTIFIER}.installer.yaml": installer_manifest,
        f"{PACKAGE_IDENTIFIER}.locale.{LOCALE}.yaml": locale_manifest,
    }


# -- the schema -------------------------------------------------------------------

class SchemaError(RuntimeError):
    """The schema uses something this checker doesn't understand, so it
    can't vouch for a manifest."""


#: Keywords that only describe; the checker passes over them.
ANNOTATIONS = frozenset({"$schema", "$id", "title", "description", "default", "definitions"})
#: Every keyword the checker applies. A schema using any other is refused,
#: rather than half-checked.
KEYWORDS = frozenset({
    "$ref", "type", "enum", "const", "not", "oneOf", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "minItems", "maxItems", "uniqueItems", "items", "required", "properties",
})
FORMATS = frozenset({"date", "long"})


def load_schema(manifest_type: str) -> dict:
    return json.loads((SCHEMA_DIR / f"manifest.{manifest_type}.{MANIFEST_VERSION}.json").read_text(encoding="utf-8"))


def _is(value: object, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}[kind])


def _pattern(pattern: str) -> re.Pattern:
    # JSON Schema patterns are ECMAScript's, where $ never matches before a
    # final newline as Python's does.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


def schema_problems(value: object, schema: dict, root: Optional[dict] = None, where: str = "") -> list[str]:
    """Why `value` doesn't satisfy the (draft-07) schema; empty if it does.
    `where` names the value in the messages: a field's path, like
    Installers[0].InstallerSha256."""
    root = schema if root is None else root
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/definitions/"):
            raise SchemaError(f"unsupported $ref {reference}")
        return schema_problems(value, root["definitions"][reference.rsplit("/", 1)[1]], root, where)
    unknown = set(schema) - KEYWORDS - ANNOTATIONS
    if unknown:
        raise SchemaError(f"the schema uses {sorted(unknown)}, which this checker doesn't apply")
    path, where = where, where or "the manifest"
    if "type" in schema:
        kinds = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if not any(_is(value, kind) for kind in kinds):
            return [f"{where} is {value!r}, not {' or '.join(kinds)}"]
    problems: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{where} is {value!r}, not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        problems.append(f"{where} is {value!r}, not {schema['const']!r}")
    if "not" in schema and not schema_problems(value, schema["not"], root, where):
        problems.append(f"{where} may not be {value!r}")
    if "oneOf" in schema:
        matching = sum(not schema_problems(value, option, root, where) for option in schema["oneOf"])
        if matching != 1:
            problems.append(f"{where} matches {matching} of the alternatives, not exactly one")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            problems.append(f"{where} is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where} is longer than {schema['maxLength']} characters")
        if "pattern" in schema and not _pattern(schema["pattern"]).search(value):
            problems.append(f"{where} is {value!r}, which doesn't match {schema['pattern']}")
        if "format" in schema:
            if schema["format"] not in FORMATS:
                raise SchemaError(f"unsupported format {schema['format']}")
            if schema["format"] == "date":
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError(value)
                    datetime.date.fromisoformat(value)
                except ValueError:
                    problems.append(f"{where} is {value!r}, not a date like 2026-10-09")
    if _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{where} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{where} is above {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{where} has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{where} has more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            problems.append(f"{where} lists something twice")
        if "items" in schema:
            for index, item in enumerate(value):
                problems += schema_problems(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        for name in schema.get("required", ()):
            if name not in value:
                problems.append(f"{where} has no {name}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                problems += schema_problems(item, properties[name], root, f"{path}.{name}" if path else name)
            elif "type" in schema:
                # Stricter than the schema, which allows any extra field:
                # winget ignores a misspelt one, so it would vanish silently.
                problems.append(f"{name} isn't a field of a {MANIFEST_VERSION} manifest")
    return problems


# -- YAML -------------------------------------------------------------------------

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/:@()+,'-]*")
_NOT_A_STRING = re.compile(
    r"(?i)true|false|yes|no|on|off|y|n|null|~"                         # booleans and null (YAML 1.1 and 1.2)
    r"|[-+]?(?:\d[\d_]*)?(?:\.\d*)?(?:e[-+]?\d+)?"                     # numbers
    r"|0x[0-9a-f_]+|0o?[0-7_]+|[-+]?\.(?:inf|nan)"
    r"|\d{4}-\d\d?-\d\d?(?:[Tt ].*)?"                                   # dates and times
    r"|[-+]?\d[\d_]*(?::[0-5]?\d)+(?:\.\d*)?"                          # base 60
)


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise TypeError(f"no YAML for {value!r}")
    if (_PLAIN.fullmatch(value) and not _NOT_A_STRING.fullmatch(value) and ": " not in value
            and " #" not in value and not value.endswith((" ", ":"))):
        return value
    if value.isprintable():
        return "'" + value.replace("'", "''") + "'"
    return json.dumps(value, ensure_ascii=False)


def _yaml_lines(data: dict, indent: int) -> list[str]:
    pad = " " * indent
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines += [f"{pad}{key}:"] + _yaml_lines(value, indent + 2)
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                if isinstance(item, dict):
                    entry = _yaml_lines(item, indent + 2)
                    lines += [f"{pad}- {entry[0].lstrip()}"] + entry[1:]
                else:
                    lines.append(f"{pad}- {yaml_scalar(item)}")
        else:
            lines.append(f"{pad}{key}: {yaml_scalar(value)}")
    return lines


def to_yaml(data: dict, schema_url: str) -> str:
    header = [
        "# Created with tools/winget_manifest.py (github.com/arnav-goel10/mouse-double-click-fixer)",
        f"# yaml-language-server: $schema={schema_url}",
        "",
    ]
    return "\n".join(header + _yaml_lines(data, 0)) + "\n"


def _parse_scalar(text: str) -> object:
    if text.startswith("'"):
        if not (len(text) >= 2 and text.endswith("'")):
            raise ValueError(f"unterminated {text}")
        return text[1:-1].replace("''", "'")
    if text.startswith('"'):
        return json.loads(text)
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_item(line: str, indent: int) -> bool:
    return _indent(line) == indent and line[indent:].startswith("- ")


def _mapping(lines: list[str], at: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    while at < len(lines) and _indent(lines[at]) == indent and not _is_item(lines[at], indent):
        key, colon, rest = lines[at][indent:].partition(":")
        if not colon or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key) or key in result:
            raise ValueError(f"line {lines[at]!r}")
        at += 1
        if rest.strip():
            if not rest.startswith(" "):
                raise ValueError(f"line {key}:{rest}")
            result[key] = _parse_scalar(rest.strip())
        elif at < len(lines) and _is_item(lines[at], indent):
            result[key], at = _sequence(lines, at, indent)
        elif at < len(lines) and _indent(lines[at]) > indent:
            result[key], at = _mapping(lines, at, _indent(lines[at]))
        else:
            raise ValueError(f"{key} has no value")
    return result, at


def _sequence(lines: list[str], at: int, indent: int) -> tuple[list, int]:
    items: list = []
    while at < len(lines) and _is_item(lines[at], indent):
        content = lines[at][indent + 2:]
        if re.match(r"[A-Za-z][A-Za-z0-9]*:( |$)", content):
            lines[at] = " " * (indent + 2) + content
            item, at = _mapping(lines, at, indent + 2)
        else:
            item, at = _parse_scalar(content), at + 1
        items.append(item)
    return items, at


def from_yaml(text: str) -> dict:
    """Read back the YAML that to_yaml writes (that subset only)."""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    data, at = _mapping(lines, 0, 0)
    if at != len(lines):
        raise ValueError(f"line {lines[at]!r}")
    return data


# -- the whole job ----------------------------------------------------------------

def write_manifests(version: str, checksums: dict[str, str], out: Path, repository: str, date: str) -> Path:
    """Check the manifests and write them; the folder they are in."""
    if INSTALLER not in checksums:
        raise ReleaseError(f"{CHECKSUMS} doesn't list {INSTALLER}.")
    texts = {}
    for name, data in manifests(version, checksums[INSTALLER], repository, date).items():
        schema = load_schema(data["ManifestType"])
        problems = schema_problems(data, schema)
        if problems:
            raise ReleaseError(f"{name} doesn't fit winget's {MANIFEST_VERSION} schema:\n  " + "\n  ".join(problems))
        text = to_yaml(data, schema["$id"])
        if from_yaml(text) != data:
            raise ReleaseError(f"{name} doesn't read back as what was written.")
        texts[name] = text
    publisher, package = PACKAGE_IDENTIFIER.split(".", 1)
    folder = out / "manifests" / publisher[0].lower() / publisher / package / version
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name, text in texts.items():
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
    return folder


def make_manifests(
    version: str,
    out: Path,
    repository: Optional[str] = None,
    checksums_folder: Optional[Path] = None,
    keys: Optional[list[PublicKey]] = None,
    download: Callable[[str, str, Path], Path] = download_checksums,
    changelog: Path = ROOT / "CHANGELOG.md",
) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError(f"{version!r} isn't a release version like 1.0.1; winget lists full releases only.")
    repository = repository or default_repository()
    date = release_date(version, changelog)
    keys = trusted_keys() if keys is None else keys
    if checksums_folder is not None:
        return write_manifests(version, signed_checksums(checksums_folder, version, keys), out, repository, date)
    with tempfile.TemporaryDirectory() as temporary:
        folder = download(version, repository, Path(temporary))
        return write_manifests(version, signed_checksums(folder, version, keys), out, repository, date)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0], epilog="See docs/DISTRIBUTION.md.")
    parser.add_argument("version", help="the published release, like 1.0.0")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "winget", help="where to write (default: build/winget)")
    parser.add_argument("--checksums", type=Path, help=f"a folder holding {CHECKSUMS} and {SIGNATURE} (default: download them)")
    parser.add_argument("--repo", help="OWNER/NAME (default: the one installed copies update from)")
    arguments = parser.parse_args(argv)
    try:
        folder = make_manifests(arguments.version.removeprefix("v"), arguments.out, arguments.repo, arguments.checksums)
    except (ReleaseError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote the manifests for {arguments.version} to {folder}")
    print(f"Check them on Windows with:  winget validate --manifest {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
