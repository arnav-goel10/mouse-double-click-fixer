import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import install_cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.installed = self.root / 'Applications' / 'DoubleClick Fixer.app'
        self.mount = self.root / 'Volumes' / 'DoubleClick Fixer'
        self.downloads = self.root / 'Downloads'
        self.downloads.mkdir()
        self.dmg = self.downloads / 'DoubleClickFixer.dmg'
        self.dmg.write_bytes(b'disk image')
        self.make_bundle(self.installed)
        self.source = self.mount / self.installed.name
        self.make_bundle(self.source)
        (self.mount / 'Applications').symlink_to('/Applications')
        self.info = {'images': [{'image-path': str(self.dmg), 'system-entities': [
            {'mount-point': str(self.mount)}]}]}

    def make_bundle(self, bundle):
        (bundle / 'Contents/MacOS').mkdir(parents=True)
        (bundle / 'Contents/MacOS/DoubleClickFixer').write_bytes(b'signed executable')
        (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.doubleclickfixer.app',
            'CFBundleExecutable': 'DoubleClickFixer', 'CFBundleVersion': '0.3.0'}))

    def run_cleanup(self, detach_ok=True):
        actions = []
        def run(args, **kwargs):
            if args[1] == 'info':
                return subprocess.CompletedProcess(args, 0, plistlib.dumps(self.info))
            actions.append(('detach', args[-1]))
            if not detach_ok:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, b'')
        with patch.object(install_cleanup.subprocess, 'run', side_effect=run), patch.object(
            install_cleanup, '_trash', side_effect=lambda p: actions.append(('trash', str(p)))
        ):
            install_cleanup.cleanup(self.installed, self.downloads, self.root / 'Volumes')
        return actions

    def test_matching_installer_is_ejected_before_trashing_download(self):
        self.assertEqual(self.run_cleanup(), [('detach', str(self.mount)), ('trash', str(self.dmg))])

    def test_busy_disk_is_not_forced_or_trashed(self):
        self.assertEqual(self.run_cleanup(False), [('detach', str(self.mount))])

    def test_different_binary_is_left_alone(self):
        (self.source / 'Contents/MacOS/DoubleClickFixer').write_bytes(b'other release')
        self.assertEqual(self.run_cleanup(), [])

    def test_running_from_disk_image_is_left_alone(self):
        self.installed = self.source
        self.assertEqual(self.run_cleanup(), [])

    def test_image_outside_downloads_is_ejected_but_not_trashed(self):
        other = self.root / 'archive.dmg'
        self.dmg.rename(other)
        self.info['images'][0]['image-path'] = str(other)
        self.assertEqual(self.run_cleanup(), [('detach', str(self.mount))])

    def test_unrelated_volume_without_installer_layout_is_left_alone(self):
        (self.mount / 'Applications').unlink()
        self.assertEqual(self.run_cleanup(), [])

    def test_invalid_hdiutil_output_is_nonfatal(self):
        with patch.object(install_cleanup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'bad')):
            install_cleanup.cleanup(self.installed, self.downloads, self.root / 'Volumes')

    def test_symlinked_download_is_not_trashed(self):
        archive = self.root / 'archive.dmg'
        self.dmg.rename(archive)
        self.dmg.symlink_to(archive)
        self.assertEqual(self.run_cleanup(), [('detach', str(self.mount))])

    def test_source_and_disk_image_launches_do_not_start_cleanup(self):
        with patch.object(install_cleanup.platform, 'system', return_value='Darwin'), patch.object(
            install_cleanup.threading, 'Thread'
        ) as thread:
            with patch.object(install_cleanup.sys, 'frozen', False, create=True):
                install_cleanup.start()
            with patch.object(install_cleanup.sys, 'frozen', True, create=True), patch.object(
                install_cleanup.sys, 'executable', '/Volumes/Installer/DoubleClick Fixer.app/Contents/MacOS/DoubleClickFixer'
            ):
                install_cleanup.start()
            thread.assert_not_called()
