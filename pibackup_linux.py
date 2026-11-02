"""
pibackup_python – Linux-Block-I/O, Loop, Shrink, zstd.

Projekt:     pibackup_python
Modul:       pibackup_linux.py
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
Kopiert Bytebereiche, hängt Images loop-weise ein, prueft ext4,
verkleinert mit resize2fs und packt mit zstd.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
Wird nur von der Engine auf Linux aufgerufen.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from pibackup_common import (
    CancelFlag,
    PibackupError,
    Reporter,
    find_tool,
    run_cmd,
)
from pibackup_devices import PROTECTED_MOUNTS
from pibackup_mbr import Mbr
import project_meta as meta

FILL_NAME = ".pibackup_fill"


def open_raw(path: str, write: bool, create: bool = False) -> int:
    """Oeffnet Datei oder Blockgeraet binaer.

    Parameter:
        path: Pfad.
        write: Schreibzugriff.
        create: O_CREAT|O_TRUNC fuer Image-Dateien.
    Rueckgabewert:
        Dateideskriptor.
    Fehlerfaelle:
        OSError
    Beispiel:
        fd = open_raw("/dev/sda", False)
    """
    flags = os.O_RDONLY
    if write and create:
        flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC
    elif write:
        flags = os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    return os.open(path, flags, 0o644)


def copy_range(
    source: str,
    dest: str,
    length: int,
    src_off: int = 0,
    dst_off: int = 0,
    create_dest: bool = False,
    reporter: Reporter | None = None,
    cancel: CancelFlag | None = None,
) -> None:
    """Kopiert length Bytes mit Fortschritt.

    Parameter:
        source, dest: Pfade.
        length: Bytezahl.
        src_off, dst_off: Offsets.
        create_dest: Zieldatei neu anlegen.
        reporter, cancel: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError, CancelledError, OSError
    Beispiel:
        copy_range("/dev/mmcblk0", "out.img", n, create_dest=True)
    """
    if length <= 0:
        raise PibackupError("Kopierlaenge muss groesser 0 sein.")
    src_fd = open_raw(source, False)
    dst_fd = open_raw(dest, True, create=create_dest)
    try:
        os.lseek(src_fd, src_off, os.SEEK_SET)
        os.lseek(dst_fd, dst_off, os.SEEK_SET)
        remaining = length
        done = 0
        last = 0.0
        while remaining > 0:
            if cancel is not None:
                cancel.raise_if_set()
            chunk = min(meta.BUFFER_SIZE, remaining)
            data = os.read(src_fd, chunk)
            if not data:
                raise PibackupError("Unerwartetes Dateiende beim Lesen.")
            written = 0
            while written < len(data):
                n = os.write(dst_fd, data[written:])
                if n <= 0:
                    raise PibackupError("Schreibfehler am Ziel.")
                written += n
            remaining -= len(data)
            done += len(data)
            now = time.monotonic()
            if reporter is not None and now - last >= 0.4:
                percent = 100.0 * done / length
                reporter.progress(percent, f"{done // (1024 * 1024)} MiB kopiert", done, length)
                last = now
        if hasattr(os, "fsync"):
            os.fsync(dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def ensure_space(dest_file: Path, needed: int) -> None:
    """Prueft freien Speicher am Zielverzeichnis.

    Parameter:
        dest_file: Ausgabedatei.
        needed: Bytes unkomprimiert.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError bei zu wenig Platz.
    Beispiel:
        ensure_space(Path("/mnt/usb/x.img"), 8_000_000_000)
    """
    parent = dest_file.parent if dest_file.parent.exists() else dest_file.parent.parent
    if not parent.exists():
        raise PibackupError(f"Zielverzeichnis fehlt: {dest_file.parent}")
    free = shutil.disk_usage(parent).free
    if free < needed:
        raise PibackupError(
            f"Zu wenig Speicher: frei {free} Byte, noetig {needed} Byte."
        )


def device_has_protected_mount(disk: str) -> bool:
    """True wenn / oder /boot auf dem Geraet haengen.

    Parameter:
        disk: /dev/...
    Rueckgabewert:
        bool
    Fehlerfaelle:
        PibackupError bei lsblk-Fehler.
    Beispiel:
        if device_has_protected_mount("/dev/sda"): ...
    """
    lsblk = find_tool("lsblk")
    result = run_cmd([lsblk, "-n", "-o", "MOUNTPOINT", disk])
    mounts = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return bool(mounts & PROTECTED_MOUNTS)


def unmount_disk_partitions(disk: str, reporter: Reporter | None = None) -> None:
    """Haengt Partitionen 1..4 aus, Fehler werden geloggt.

    Parameter:
        disk: /dev/...
        reporter: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        keine (umount-Fehler sind nicht fatal, wenn schon frei).
    Beispiel:
        unmount_disk_partitions("/dev/sdb")
    """
    umount = find_tool("umount")
    from pibackup_common import partition_path

    for number in range(1, 5):
        part = partition_path(disk, number)
        result = run_cmd([umount, part])
        if result.returncode == 0 and reporter is not None:
            reporter.log(f"Aushaengen: {part}")


def loop_offset_size(mbr: Mbr, part_index: int) -> tuple[int, int]:
    """Offset und Laenge einer Partition im Image.

    Parameter:
        mbr: geparster MBR.
        part_index: 0-basiert.
    Rueckgabewert:
        (offset, size) in Byte.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        off, size = loop_offset_size(mbr, 1)
    """
    entry = mbr.partitions[part_index]
    if entry.empty:
        raise PibackupError(f"Partition {part_index + 1} ist leer.")
    return entry.start_lba * meta.SECTOR, entry.sectors * meta.SECTOR


def losetup_image(image: Path, offset: int, size: int) -> str:
    """Bindet einen Image-Ausschnitt als Loop.

    Parameter:
        image: .img-Datei.
        offset, size: Byte.
    Rueckgabewert:
        /dev/loopN
    Fehlerfaelle:
        PibackupError
    Beispiel:
        loop = losetup_image(Path("a.img"), off, size)
    """
    losetup = find_tool("losetup")
    result = run_cmd(
        [
            losetup,
            "--show",
            "-f",
            "-o",
            str(offset),
            "--sizelimit",
            str(size),
            str(image),
        ]
    )
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or "losetup fehlgeschlagen.")
    loop = result.stdout.strip()
    if not loop.startswith("/dev/loop"):
        raise PibackupError(f"Unerwartetes losetup-Ergebnis: {loop!r}")
    return loop


def losetup_detach(loop: str) -> None:
    """Gibt ein Loop-Geraet frei.

    Parameter:
        loop: /dev/loopN
    Rueckgabewert:
        None
    Fehlerfaelle:
        keine
    Beispiel:
        losetup_detach("/dev/loop0")
    """
    losetup = find_tool("losetup")
    run_cmd([losetup, "-d", loop])


def e2fsck(device: str, reporter: Reporter | None = None) -> None:
    """Dateisystempruefung ext4.

    Parameter:
        device: Partition oder Loop.
        reporter: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError bei rc > 2.
    Beispiel:
        e2fsck("/dev/loop0")
    """
    tool = find_tool("e2fsck")
    result = run_cmd([tool, "-fy", device])
    text = (result.stdout + result.stderr).strip()
    if reporter is not None and text:
        for line in text.splitlines()[-8:]:
            reporter.log(line)
    if result.returncode > 2:
        raise PibackupError(f"e2fsck Fehlercode {result.returncode}")


def resize2fs_min(device: str, reporter: Reporter | None = None) -> int:
    """Verkleinert auf Minimum, liefert neue Blockzahl.

    Parameter:
        device: Loop der Root-Partition.
        reporter: optional.
    Rueckgabewert:
        Blockanzahl nach resize2fs -M.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        blocks = resize2fs_min("/dev/loop0")
    """
    tool = find_tool("resize2fs")
    result = run_cmd([tool, "-M", "-p", device])
    text = result.stdout + result.stderr
    if reporter is not None:
        reporter.log("resize2fs -M")
        for line in text.splitlines()[-6:]:
            reporter.log(line)
    match = re.search(r"is now\s+(\d+)\s+\(", text, re.IGNORECASE)
    if not match:
        raise PibackupError("resize2fs lieferte keine neue Blockzahl ('is now').")
    return int(match.group(1))


def ext_block_size(device: str) -> int:
    """BLOCK_SIZE aus blkid.

    Parameter:
        device: Loop oder Partition.
    Rueckgabewert:
        Bytes, typisch 4096.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        ext_block_size("/dev/loop0")
    """
    blkid = find_tool("blkid")
    result = run_cmd([blkid, "-s", "BLOCK_SIZE", "-o", "value", device])
    text = result.stdout.strip()
    if not text.isdigit():
        raise PibackupError("BLOCK_SIZE konnte nicht gelesen werden.")
    return int(text)


def truncate_file(path: Path, size: int) -> None:
    """Setzt die Dateigroesse.

    Parameter:
        path: Image.
        size: neue Laenge.
    Rueckgabewert:
        None
    Fehlerfaelle:
        OSError
    Beispiel:
        truncate_file(Path("a.img"), 1000)
    """
    with path.open("rb+") as handle:
        handle.truncate(size)


def mount_image_part(image: Path, offset: int, size: int, mountpoint: Path) -> None:
    """Mountet eine Image-Partition.

    Parameter:
        image: .img
        offset, size: Byte.
        mountpoint: Verzeichnis.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        mount_image_part(img, off, size, Path("/tmp/p2"))
    """
    mountpoint.mkdir(parents=True, exist_ok=True)
    mount = find_tool("mount")
    opts = f"loop,offset={offset},sizelimit={size}"
    result = run_cmd([mount, "-o", opts, str(image), str(mountpoint)])
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or f"mount fehlgeschlagen: {mountpoint}")


def mount_partition(part: str, mountpoint: Path) -> None:
    """Mountet ein Blockgeraet.

    Parameter:
        part: /dev/sdX1
        mountpoint: Ziel.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        mount_partition("/dev/sdb1", Path("/tmp/p1"))
    """
    mountpoint.mkdir(parents=True, exist_ok=True)
    mount = find_tool("mount")
    result = run_cmd([mount, part, str(mountpoint)])
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or f"mount fehlgeschlagen: {part}")


def umount_path(mountpoint: Path) -> None:
    """Aushaengen, sync.

    Parameter:
        mountpoint: Verzeichnis.
    Rueckgabewert:
        None
    Fehlerfaelle:
        keine
    Beispiel:
        umount_path(Path("/tmp/p2"))
    """
    umount = find_tool("umount")
    run_cmd([umount, str(mountpoint)])
    run_cmd([find_tool("sync")])


def fill_empty_blocks(mountpoint: Path, reporter: Reporter | None = None) -> None:
    """Schreibt 0xFF bis das Dateisystem voll ist, loescht die Datei.

    Parameter:
        mountpoint: gemountete Partition.
        reporter: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        OSError (volle Platte ist erwartet und wird gefangen).
    Beispiel:
        fill_empty_blocks(Path("/tmp/p2"))
    """
    filler = mountpoint / FILL_NAME
    blob = b"\xff" * meta.BUFFER_SIZE
    if reporter is not None:
        reporter.log(f"Leere Bloecke mit 0xFF fuellen ({mountpoint})")
    try:
        with filler.open("wb") as handle:
            while True:
                handle.write(blob)
    except OSError:
        pass
    if filler.exists():
        filler.unlink()
    run_cmd([find_tool("sync")])


def compress_zstd(
    source: Path,
    dest: Path,
    level: int,
    reporter: Reporter | None = None,
) -> None:
    """Packt mit dem zstd-Binary.

    Parameter:
        source: .img
        dest: .img.zst
        level: 1..19, Default 2.
        reporter: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        compress_zstd(Path("a.img"), Path("a.img.zst"), 2)
    """
    zstd = find_tool("zstd")
    if reporter is not None:
        reporter.log(f"Komprimiere mit zstd -{level}")
    result = run_cmd([zstd, f"-{level}", "-T0", "-f", str(source), "-o", str(dest)])
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or "zstd fehlgeschlagen.")


def decompress_zstd_to(source: Path, dest: Path, reporter: Reporter | None = None) -> None:
    """Entpackt .zst nach dest.

    Parameter:
        source: .img.zst
        dest: .img oder Geraet – hier immer Datei.
        reporter: optional.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        decompress_zstd_to(Path("a.img.zst"), Path("a.img"))
    """
    zstd = find_tool("zstd")
    if reporter is not None:
        reporter.log("Entpacke zstd")
    result = run_cmd([zstd, "-d", "-f", str(source), "-o", str(dest)])
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or "zstd -d fehlgeschlagen.")


def enable_ssh_on_boot(boot_mount: Path) -> None:
    """Legt die Datei ssh auf der Startpartition an.

    Parameter:
        boot_mount: FAT-Boot.
    Rueckgabewert:
        None
    Fehlerfaelle:
        OSError
    Beispiel:
        enable_ssh_on_boot(Path("/tmp/p1"))
    """
    (boot_mount / "ssh").write_text("", encoding="utf-8")
