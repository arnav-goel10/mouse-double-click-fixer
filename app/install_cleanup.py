"""Best-effort cleanup of the matching macOS drag-to-install disk image."""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _fingerprint(bundle: Path) -> tuple[bytes, str]:
    metadata = (bundle / 'Contents/Info.plist').read_bytes()
    info = plistlib.loads(metadata)
    if info.get('CFBundleIdentifier') != 'com.doubleclickfixer.app':
        raise ValueError('Not a DoubleClick Fixer bundle')
    executable = info['CFBundleExecutable']
    if executable != 'DoubleClickFixer':
        raise ValueError('Unexpected executable')
    with (bundle / 'Contents/MacOS' / executable).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return metadata, digest


def _trash(path: Path) -> None:
    # Use macOS Trash semantics, including collision handling and other volumes.
    import objc
    from Foundation import NSFileManager, NSURL

    with objc.autorelease_pool():
        ok, _url, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(path)), None, None
        )
        if not ok:
            raise OSError(str(error))


def cleanup(installed: Path, downloads: Path, volumes: Path = Path('/Volumes')) -> None:
    """Eject only an identical installer; trash its image only in Downloads.

    No forced unmounts, no elevation, and no effect on normal app startup if
    permissions, disk state, or image metadata prevent cleanup.
    """
    try:
        installed = installed.resolve()
        if installed.is_relative_to(volumes.resolve()):
            return
        fingerprint = _fingerprint(installed)
        result = subprocess.run(['/usr/bin/hdiutil', 'info', '-plist'],
                                capture_output=True, check=True, timeout=10)
        images = plistlib.loads(result.stdout).get('images', [])
        for image in images:
            image_path = Path(image.get('image-path', ''))
            if not image_path.is_absolute() or image_path.suffix.lower() != '.dmg':
                continue
            for entity in image.get('system-entities', []):
                mount_value = entity.get('mount-point')
                if not mount_value:
                    continue
                mount = Path(mount_value)
                if not mount.is_relative_to(volumes) or mount == volumes:
                    continue
                shortcut = mount / 'Applications'
                if not shortcut.is_symlink() or os.readlink(shortcut) != '/Applications':
                    continue
                try:
                    if _fingerprint(mount / installed.name) != fingerprint:
                        continue
                    # Remember the image identity before unmounting. Never trash a
                    # replacement file or follow a symlink out of Downloads.
                    before = image_path.stat()
                    trashable = (not image_path.is_symlink()
                                 and image_path.resolve().is_relative_to(downloads.resolve()))
                    subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                                   capture_output=True, check=True, timeout=15)
                    if trashable and image_path.stat() == before:
                        _trash(image_path)
                    return
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    log.info('Installer cleanup could not finish', exc_info=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        log.info('Installer cleanup unavailable', exc_info=True)


def start() -> None:
    """Run once per launch, only for a packaged app installed in Applications."""
    if platform.system() != 'Darwin' or not getattr(sys, 'frozen', False):
        return
    executable = Path(sys.executable).resolve()
    bundle = executable.parent.parent.parent
    if bundle.parent not in (Path('/Applications'), Path.home() / 'Applications'):
        return
    threading.Thread(target=cleanup, args=(bundle, Path.home() / 'Downloads'),
                     name='installer-cleanup', daemon=True).start()
