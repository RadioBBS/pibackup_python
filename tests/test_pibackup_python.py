"""
pibackup_python – Unittests ohne echte Blockgeraete.

Projekt:     pibackup_python
Modul:       tests/test_pibackup_python.py
Version:     1.0.1
Stand:       2026-11-02
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.11)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Beschreibung
------------
Prueft Metadatenabgleich, MBR, Exclude, Defaults, CLI und GUI-API
ohne Raspberry Pi und ohne Schreibzugriff auf Disks.

Historie
--------
Version 1.0.0 – 2026-08-24 – Tests fuer Python-Engine und Browser-GUI.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
  python -m unittest tests.test_pibackup_python
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pibackup_common as common  # noqa: E402
import pibackup_devices as devices  # noqa: E402
import pibackup_engine as engine  # noqa: E402
import pibackup_exclude as exclude  # noqa: E402
import pibackup_python as cli  # noqa: E402
import pibackup_python_gui as gui  # noqa: E402
import pibackup_mbr as mbrmod  # noqa: E402
import pibackup_priv as priv  # noqa: E402
import project_meta as meta  # noqa: E402

LSBLK_SAMPLE = json.dumps(
    {
        "blockdevices": [
            {
                "name": "mmcblk0",
                "path": "/dev/mmcblk0",
                "size": 32000000000,
                "type": "disk",
                "rm": False,
                "tran": "mmc",
                "model": "SD",
                "children": [
                    {
                        "name": "mmcblk0p1",
                        "path": "/dev/mmcblk0p1",
                        "size": 256000000,
                        "fstype": "vfat",
                        "mountpoint": "/boot/firmware",
                        "pkname": "mmcblk0",
                    },
                    {
                        "name": "mmcblk0p2",
                        "path": "/dev/mmcblk0p2",
                        "size": 31000000000,
                        "fstype": "ext4",
                        "mountpoint": "/",
                        "pkname": "mmcblk0",
                    },
                ],
            },
            {
                "name": "sda",
                "path": "/dev/sda",
                "size": 64000000000,
                "type": "disk",
                "rm": True,
                "tran": "usb",
                "model": "USB DISK",
                "children": [
                    {
                        "name": "sda1",
                        "path": "/dev/sda1",
                        "size": 64000000000,
                        "fstype": "exfat",
                        "mountpoint": "/media/pi/BACKUP",
                        "label": "BACKUP",
                    }
                ],
            },
            {"name": "loop0", "path": "/dev/loop0", "size": 1, "type": "loop"},
        ]
    }
)


def make_mbr_bytes() -> bytes:
    """Minimaler DOS-MBR mit zwei Partitionen.

    Parameter:
        keine
    Rueckgabewert:
        512 Byte
    Fehlerfaelle:
        keine
    Beispiel:
        make_mbr_bytes()
    """
    raw = bytearray(512)
    raw[510:512] = b"\x55\xaa"
    raw[0x1B8:0x1BC] = struct.pack("<I", 0xAABBCCDD)
    p1 = bytearray(16)
    p1[4] = 0x0C
    p1[8:12] = struct.pack("<I", 8192)
    p1[12:16] = struct.pack("<I", 524288)
    p2 = bytearray(16)
    p2[4] = 0x83
    p2[8:12] = struct.pack("<I", 532480)
    p2[12:16] = struct.pack("<I", 200000)
    raw[0x1BE:0x1CE] = p1
    raw[0x1CE:0x1DE] = p2
    return bytes(raw)


def yaml_value(text: str, key: str) -> str:
    """Einfacher YAML-Zeilenleser fuer Tests.

    Parameter:
        text: Dateiinhalt
        key: Schluessel
    Rueckgabewert:
        Wert ohne Anfuehrungszeichen
    Fehlerfaelle:
        AssertionError indirekt wenn leer
    Beispiel:
        yaml_value(raw, "version")
    """
    for line in text.splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        left, right = line.split(":", 1)
        if left.strip() == key:
            return right.strip().strip('"').strip("'")
    return ""


class MetaTests(unittest.TestCase):
    """Abgleich PROJECT.yaml / pyproject.toml / CITATION.cff."""

    def test_versions_match(self) -> None:
        """Eine Versionsnummer in allen Manifesten.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_versions_match()
        """
        yaml_text = (ROOT / "PROJECT.yaml").read_text(encoding="utf-8")
        toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertEqual(yaml_value(yaml_text, "version"), meta.VERSION)
        self.assertEqual(yaml_value(yaml_text, "stand"), meta.STAND)
        self.assertIn(f'version = "{meta.VERSION}"', toml)
        self.assertEqual(yaml_value(cff, "version"), meta.VERSION)
        self.assertEqual(yaml_value(cff, "date-released"), meta.STAND)


class MbrTests(unittest.TestCase):
    """MBR und PARTUUID."""

    def test_parse_and_clear(self) -> None:
        """Zwei Partitionen, Extra-Slots loeschen.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_parse_and_clear()
        """
        parsed = mbrmod.parse_mbr(make_mbr_bytes())
        self.assertEqual(parsed.disk_signature, 0xAABBCCDD)
        self.assertFalse(parsed.partitions[1].empty)
        size = parsed.image_bytes()
        self.assertEqual(size, (532480 + 200000) * 512)
        parsed.clear_extra_partitions()
        self.assertTrue(parsed.partitions[2].empty)

    def test_partuuid_rewrite(self) -> None:
        """cmdline und fstab bekommen die neue Disk-ID.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_partuuid_rewrite()
        """
        cmd = "console=tty1 root=PARTUUID=deadbeef-02 rootfstype=ext4"
        out = mbrmod.replace_partuuid_cmdline(cmd, 0xAABBCCDD)
        self.assertIn("root=PARTUUID=aabbccdd-02", out)
        fstab = "PARTUUID=deadbeef-01  /boot  vfat\nPARTUUID=deadbeef-02  /  ext4\n"
        changed = mbrmod.replace_partuuid_fstab(fstab, 0xAABBCCDD)
        self.assertIn("PARTUUID=aabbccdd-01", changed)
        self.assertIn("PARTUUID=aabbccdd-02", changed)


class DeviceTests(unittest.TestCase):
    """lsblk-Defaults."""

    def test_system_and_external(self) -> None:
        """Startdisk mmcblk0, Ziel USB-Mount, Hostname-Datum.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_system_and_external()
        """
        system = devices.system_disk_from_mounts(LSBLK_SAMPLE)
        self.assertEqual(system, "/dev/mmcblk0")
        items = devices.parse_lsblk_json(LSBLK_SAMPLE, system)
        names = {item.path for item in items}
        self.assertIn("/dev/mmcblk0", names)
        self.assertIn("/dev/sda", names)
        self.assertNotIn("/dev/loop0", names)
        disk, mount = devices.suggest_external(items)
        self.assertEqual(disk, "/dev/sda")
        self.assertEqual(mount, "/media/pi/BACKUP")
        built = devices.build_defaults(items)
        self.assertEqual(built.mode, "backup")
        self.assertEqual(built.source_disk, "/dev/mmcblk0")
        self.assertEqual(built.start_partition, "/dev/mmcblk0p1")
        self.assertTrue(built.shrink)
        self.assertTrue(built.compress)
        name = devices.backup_filename("Mein Pi", date(2026, 8, 24))
        self.assertEqual(name, "Mein-Pi-2026-08-24.img.zst")


class ExcludeTests(unittest.TestCase):
    """Parser und Sandbox."""

    def test_parse_and_escape(self) -> None:
        """Kommandos parsen, Escape ablehnen.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_parse_and_escape()
        """
        entries = exclude.parse_exclude_text("file = /var/swap\nallintree = /tmp\n")
        self.assertEqual(entries[0].command, "file")
        with self.assertRaises(common.PibackupError):
            exclude.parse_exclude_text("file = /tmp/../etc/passwd\n")
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            (mount / "keep.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(common.PibackupError):
                exclude.resolve_inside(mount, "/../outside")

    def test_apply_file(self) -> None:
        """file loescht nur im Mount.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_apply_file()
        """
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            target = mount / "var"
            target.mkdir()
            (target / "swap").write_text("1", encoding="utf-8")
            spec = Path(tmp) / "x.exclude"
            spec.write_text("file = /var/swap\n", encoding="utf-8")
            exclude.apply_exclude(spec, mount)
            self.assertFalse((target / "swap").exists())


class CliTests(unittest.TestCase):
    """--help und --version."""

    def test_help_and_version(self) -> None:
        """Hilfe und Version ohne sudo.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_help_and_version()
        """
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("--Ende", out.getvalue())
        self.assertIn("sudo python3 pibackup_python.py backup --yes", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(meta.VERSION, out.getvalue())

    def test_backup_dry_run(self) -> None:
        """backup --dry-run braucht kein root.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_backup_dry_run()
        """
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["backup", "--dry-run", "--dest", "/tmp/demo.img.zst"])
        self.assertEqual(code, 0)
        self.assertIn("verkleinern", out.getvalue())
        self.assertIn("zstd", out.getvalue())

    def test_restore_requires_flags(self) -> None:
        """restore ohne --image ist ein Fehler.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_restore_requires_flags()
        """
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["restore", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("image", err.getvalue())

    def test_invalid_disk(self) -> None:
        """Partition statt Disk wird abgelehnt.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_invalid_disk()
        """
        with self.assertRaises(common.PibackupError):
            common.validate_disk("/dev/sda1")
        self.assertEqual(common.validate_disk("/dev/mmcblk0"), "/dev/mmcblk0")


class PrivTests(unittest.TestCase):
    """pkexec/sudo-Prefix."""

    def test_write_commands(self) -> None:
        """Nur Schreibbefehle brauchen Rechte; pkexec wird bevorzugt.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_write_commands()
        """
        from unittest.mock import patch

        self.assertTrue(priv.needs_privilege("backup"))
        self.assertFalse(priv.needs_privilege("devices"))
        self.assertTrue(priv.engine_script().is_file())
        with patch("pibackup_priv.is_root", return_value=False):
            with patch(
                "pibackup_priv.shutil.which",
                side_effect=lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
            ):
                wrapped = priv.wrap_engine(["backup", "--yes"])
        self.assertEqual(wrapped[0], "/usr/bin/pkexec")
        self.assertIn("pibackup_python.py", wrapped[2])
        self.assertIn("backup", wrapped)


class EnginePlanTests(unittest.TestCase):
    """Plaene ohne I/O."""

    def test_plan_contains_shrink_pack(self) -> None:
        """Default-Backup plant Shrink und zstd.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_plan_contains_shrink_pack()
        """
        opts = engine.BackupOptions(source="/dev/mmcblk0", dest="/tmp/x.img.zst")
        plan = engine.plan_backup(opts)
        text = " ".join(plan.steps)
        self.assertIn("resize2fs", text)
        self.assertIn("zstd", text)


class GuiTests(unittest.TestCase):
    """Lokaler HTTP-Dienst."""

    def test_meta_and_page(self) -> None:
        """GET /api/meta und Startseite.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_meta_and_page()
        """
        httpd = gui.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            with urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["version"], meta.VERSION)
            self.assertEqual(data["token"], gui.TOKEN)
            with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
                html = resp.read().decode("utf-8")
            self.assertIn("Backup", html)
            self.assertIn("Verkleinern", html)
            self.assertNotIn("{{TOKEN}}", html)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_rejects_lan_bind(self) -> None:
        """Kein Bind ausser Loopback.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_rejects_lan_bind()
        """
        with self.assertRaises(common.PibackupError):
            gui.make_server("0.0.0.0", 8787)

    def test_gui_help(self) -> None:
        """GUI --help ohne Server.

        Parameter:
            keine
        Rueckgabewert:
            keine
        Fehlerfaelle:
            AssertionError
        Beispiel:
            test_gui_help()
        """
        out = io.StringIO()
        with redirect_stdout(out):
            code = gui.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("127.0.0.1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
