"""
pibackup_python – Orchestrierung Backup, Restore, Clone.

Projekt:     pibackup_python
Modul:       pibackup_engine.py
Version:     1.0.0
Stand:       2026-08-24
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.10)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Beschreibung
------------
Fuehrt die Schreibaktionen aus: Image der ersten zwei Partitionen,
Verkleinern, Packen; Restore auf ein Geraet; Klonen Disk zu Disk.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.

Aufruf / Nutzung
----------------
Nur ueber pibackup_python.py (root) oder die GUI per pkexec/sudo.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pibackup_common import (
    CancelFlag,
    PibackupError,
    Reporter,
    chown_to_invoker,
    is_root,
    partition_path,
    require_linux,
    validate_disk,
)
from pibackup_devices import (
    build_defaults,
    list_devices,
    suggested_backup_path,
)
from pibackup_exclude import apply_exclude, bundled_exclude
from pibackup_linux import (
    compress_zstd,
    copy_range,
    decompress_zstd_to,
    device_has_protected_mount,
    e2fsck,
    enable_ssh_on_boot,
    ensure_space,
    ext_block_size,
    fill_empty_blocks,
    loop_offset_size,
    losetup_detach,
    losetup_image,
    mount_image_part,
    mount_partition,
    resize2fs_min,
    truncate_file,
    umount_path,
    unmount_disk_partitions,
)
from pibackup_mbr import apply_partuuid_files, read_mbr, write_mbr
import project_meta as meta

TEMP_P1 = "/tmp/pibackup_p1"
TEMP_P2 = "/tmp/pibackup_p2"


@dataclass
class BackupOptions:
    """Parameter einer Sicherung."""

    source: str = ""
    dest: str = ""
    shrink: bool = True
    compress: bool = True
    compress_level: int = meta.DEFAULT_COMPRESS_LEVEL
    exclude: str = ""
    remove_ssh: bool = False
    remove_dhcp: bool = False
    keep_img: bool = False
    fill_empty: bool = True
    dry_run: bool = False
    yes: bool = False


@dataclass
class RestoreOptions:
    """Parameter einer Wiederherstellung."""

    image: str = ""
    dest: str = ""
    change_id: bool = True
    enable_ssh: bool = False
    dry_run: bool = False
    yes: bool = False
    new_signature: int | None = None


@dataclass
class CloneOptions:
    """Parameter eines Klons."""

    source: str = ""
    dest: str = ""
    change_id: bool = True
    dry_run: bool = False
    yes: bool = False
    new_signature: int | None = None


@dataclass
class Plan:
    """Lesbare Schritte vor --yes."""

    action: str
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _require_yes(yes: bool, dry_run: bool) -> None:
    if dry_run or yes:
        return
    raise PibackupError(
        "Destruktive Aktion: bitte --dry-run zur Vorschau oder --yes zum Ausfuehren."
    )


def _require_root_for_write(dry_run: bool) -> None:
    if dry_run:
        return
    if not is_root():
        raise PibackupError(
            "Schreibaktionen als root starten (sudo/pkexec). "
            "Die GUI hebt die Rechte nur fuer diesen Lauf an."
        )


def fill_backup_defaults(opts: BackupOptions) -> BackupOptions:
    """Ergaenzt Quelle und Ziel aus den Voreinstellungen.

    Parameter:
        opts: vom CLI, Felder duerfen leer sein.
    Rueckgabewert:
        Kopie mit gesetzten Defaults.
    Fehlerfaelle:
        PibackupError wenn kein Ziel ermittelbar und dest leer.
    Beispiel:
        fill_backup_defaults(BackupOptions())
    """
    defaults = build_defaults()
    source = opts.source or defaults.source_disk
    dest = opts.dest
    if not dest:
        dest = suggested_backup_path(defaults)
    if not dest:
        raise PibackupError("Kein Zielpfad. --dest oder eine externe Disk setzen.")
    return BackupOptions(
        source=source,
        dest=dest,
        shrink=opts.shrink,
        compress=opts.compress,
        compress_level=opts.compress_level,
        exclude=opts.exclude,
        remove_ssh=opts.remove_ssh,
        remove_dhcp=opts.remove_dhcp,
        keep_img=opts.keep_img,
        fill_empty=opts.fill_empty,
        dry_run=opts.dry_run,
        yes=opts.yes,
    )


def plan_backup(opts: BackupOptions) -> Plan:
    """Schrittfolge einer Sicherung.

    Parameter:
        opts: vollstaendige Optionen.
    Rueckgabewert:
        Plan
    Fehlerfaelle:
        keine
    Beispiel:
        plan_backup(fill_backup_defaults(BackupOptions()))
    """
    filled = fill_backup_defaults(opts)
    steps = [
        f"Quelle (Startdisk) {filled.source} Partition 1+2 nach Image",
        f"Ziel {filled.dest}",
    ]
    if filled.exclude:
        steps.append(f"Exclude {filled.exclude}")
    if filled.remove_ssh:
        steps.append("SSH-Reste entfernen")
    if filled.remove_dhcp:
        steps.append("DHCP-Reste entfernen")
    if filled.shrink:
        steps.append("Root-Dateisystem verkleinern (resize2fs -M)")
    if filled.fill_empty:
        steps.append("Leere Bloecke mit 0xFF fuellen")
    if filled.compress:
        steps.append(f"Mit zstd -{filled.compress_level} packen")
    warnings = [
        "Image-Erstellung liest das Quellgeraet roh; Ziel nicht auf / oder /boot legen."
    ]
    return Plan("backup", steps, warnings)


def plan_restore(opts: RestoreOptions) -> Plan:
    """Schrittfolge Restore.

    Parameter:
        opts: Image und Zielgeraet.
    Rueckgabewert:
        Plan
    Fehlerfaelle:
        keine
    Beispiel:
        plan_restore(RestoreOptions(image="a.img.zst", dest="/dev/sdb"))
    """
    steps = [
        f"Image {opts.image} nach {opts.dest} schreiben",
        "Zielpartitionen aushaengen",
    ]
    if opts.change_id:
        steps.append("Neue PARTUUID in MBR, cmdline.txt und fstab")
    if opts.enable_ssh:
        steps.append("SSH-Datei auf der Startpartition anlegen")
    warnings = [
        f"ALLE Daten auf {opts.dest} (Bereich bis Ende Partition 2) werden ueberschrieben."
    ]
    return Plan("restore", steps, warnings)


def plan_clone(opts: CloneOptions) -> Plan:
    """Schrittfolge Klon.

    Parameter:
        opts: Quell- und Zielgeraet.
    Rueckgabewert:
        Plan
    Fehlerfaelle:
        keine
    Beispiel:
        plan_clone(CloneOptions(source="/dev/mmcblk0", dest="/dev/sdb"))
    """
    steps = [
        f"Klon {opts.source} -> {opts.dest} (MBR + Partition 1 und 2)",
        "Ziel aushaengen, PARTUUID anpassen" if opts.change_id else "Ziel aushaengen",
    ]
    warnings = [f"Ziel {opts.dest} wird ueberschrieben."]
    return Plan("clone", steps, warnings)


def _img_path(dest: str, compress: bool) -> tuple[Path, Path]:
    dest_path = Path(dest)
    if dest_path.suffix == ".zst":
        img = dest_path.with_suffix("")
        zst = dest_path
        return img, zst
    if compress:
        return dest_path, Path(str(dest_path) + ".zst")
    return dest_path, dest_path


def _apply_excludes(mount: Path, opts: BackupOptions, reporter: Reporter) -> None:
    paths: list[Path] = []
    if opts.exclude:
        paths.append(Path(opts.exclude))
    if opts.remove_ssh:
        paths.append(bundled_exclude("ssh-cleanup.exclude"))
    if opts.remove_dhcp:
        paths.append(bundled_exclude("dhcp-cleanup.exclude"))
    for item in paths:
        reporter.log(f"Exclude: {item}")
        apply_exclude(item, mount)


def run_backup(opts: BackupOptions, reporter: Reporter, cancel: CancelFlag) -> Path:
    """Erzeugt das Image.

    Parameter:
        opts: BackupOptions
        reporter: Fortschritt
        cancel: Abbruch
    Rueckgabewert:
        Pfad der fertigen Datei (.img oder .img.zst)
    Fehlerfaelle:
        PibackupError, CancelledError
    Beispiel:
        run_backup(opts, reporter, CancelFlag())
    """
    filled = fill_backup_defaults(opts)
    _require_yes(filled.yes, filled.dry_run)
    if filled.dry_run:
        for step in plan_backup(filled).steps:
            reporter.log(step)
        return Path(filled.dest)
    require_linux()
    _require_root_for_write(False)
    source = validate_disk(filled.source)
    img, zst = _img_path(filled.dest, filled.compress)
    img.parent.mkdir(parents=True, exist_ok=True)
    mbr = read_mbr(source)
    nbytes = mbr.image_bytes()
    ensure_space(img, nbytes)
    reporter.log(f"Kopiere {nbytes} Byte von {source} nach {img}")
    copy_range(source, str(img), nbytes, create_dest=True, reporter=reporter, cancel=cancel)
    mbr_img = read_mbr(img)
    mbr_img.clear_extra_partitions()
    write_mbr(img, mbr_img)
    _shrink_and_pack(img, zst, filled, reporter, cancel)
    result = zst if filled.compress else img
    chown_to_invoker(result)
    reporter.log(f"Fertig: {result}")
    return result


def _shrink_and_pack(
    img: Path,
    zst: Path,
    opts: BackupOptions,
    reporter: Reporter,
    cancel: CancelFlag,
) -> None:
    mbr = read_mbr(img)
    off, size = loop_offset_size(mbr, 1)
    loop = losetup_image(img, off, size)
    try:
        cancel.raise_if_set()
        e2fsck(loop, reporter)
        losetup_detach(loop)
        loop = ""
        mount_image_part(img, off, size, Path(TEMP_P2))
        try:
            _apply_excludes(Path(TEMP_P2), opts, reporter)
        finally:
            umount_path(Path(TEMP_P2))
        if opts.shrink:
            loop = losetup_image(img, off, size)
            e2fsck(loop, reporter)
            blocks = resize2fs_min(loop, reporter)
            bsize = ext_block_size(loop)
            e2fsck(loop, reporter)
            sectors = (blocks * bsize) // meta.SECTOR
            mbr.set_p2_sectors(sectors)
            write_mbr(img, mbr)
            new_size = mbr.image_bytes()
            truncate_file(img, new_size)
            reporter.log(f"Image verkleinert auf {new_size} Byte")
    finally:
        if loop:
            losetup_detach(loop)
    if opts.fill_empty:
        _fill_both(img, reporter)
    if opts.compress:
        cancel.raise_if_set()
        compress_zstd(img, zst, opts.compress_level, reporter)
        chown_to_invoker(zst)
        if not opts.keep_img:
            img.unlink(missing_ok=True)


def _fill_both(img: Path, reporter: Reporter) -> None:
    mbr = read_mbr(img)
    for index, mp in ((1, TEMP_P2), (0, TEMP_P1)):
        off, size = loop_offset_size(mbr, index)
        mount_image_part(img, off, size, Path(mp))
        try:
            fill_empty_blocks(Path(mp), reporter)
        finally:
            umount_path(Path(mp))


def _prepare_image_file(image: str, reporter: Reporter) -> tuple[Path, Path | None]:
    path = Path(image)
    if not path.is_file():
        raise PibackupError(f"Image nicht gefunden: {image}")
    if path.name.endswith(".img.zst") or path.suffix == ".zst":
        handle, tmp_name = tempfile.mkstemp(prefix="pibackup_", suffix=".img")
        os.close(handle)
        tmp = Path(tmp_name)
        decompress_zstd_to(path, tmp, reporter)
        return tmp, tmp
    return path, None


def run_restore(opts: RestoreOptions, reporter: Reporter, cancel: CancelFlag) -> None:
    """Schreibt ein Image auf ein Geraet.

    Parameter:
        opts: RestoreOptions
        reporter, cancel: Fortschritt/Abbruch
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        run_restore(opts, reporter, CancelFlag())
    """
    _require_yes(opts.yes, opts.dry_run)
    if opts.dry_run:
        for step in plan_restore(opts).steps:
            reporter.log(step)
        return
    require_linux()
    _require_root_for_write(False)
    dest = validate_disk(opts.dest)
    if device_has_protected_mount(dest):
        raise PibackupError(f"Ziel {dest} hat / oder /boot gemountet.")
    work, tmp = _prepare_image_file(opts.image, reporter)
    try:
        mbr = read_mbr(work)
        nbytes = mbr.image_bytes()
        unmount_disk_partitions(dest, reporter)
        reporter.log(f"Schreibe {nbytes} Byte nach {dest}")
        copy_range(str(work), dest, nbytes, reporter=reporter, cancel=cancel)
        if opts.change_id:
            sig = opts.new_signature if opts.new_signature is not None else os.urandom(4)
            signature = int.from_bytes(sig, "little") if isinstance(sig, bytes) else int(sig)
            _rewrite_ids(dest, signature, reporter)
        if opts.enable_ssh:
            _ssh_on_device(dest, reporter)
        reporter.log("Restore abgeschlossen.")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _rewrite_ids(disk: str, signature: int, reporter: Reporter) -> None:
    mbr = read_mbr(disk)
    mbr.set_disk_signature(signature)
    write_mbr(disk, mbr)
    p1, p2 = Path(TEMP_P1), Path(TEMP_P2)
    mount_partition(partition_path(disk, 1), p1)
    try:
        mount_partition(partition_path(disk, 2), p2)
        try:
            apply_partuuid_files(p1, p2, signature)
            reporter.log(f"PARTUUID gesetzt: {signature:08x}")
        finally:
            umount_path(p2)
    finally:
        umount_path(p1)


def _ssh_on_device(disk: str, reporter: Reporter) -> None:
    mount_partition(partition_path(disk, 1), Path(TEMP_P1))
    try:
        enable_ssh_on_boot(Path(TEMP_P1))
        reporter.log("SSH auf der Startpartition aktiviert (Datei ssh).")
    finally:
        umount_path(Path(TEMP_P1))


def run_clone(opts: CloneOptions, reporter: Reporter, cancel: CancelFlag) -> None:
    """Kopiert Partition 1+2 auf ein anderes Geraet.

    Parameter:
        opts: CloneOptions
        reporter, cancel: Fortschritt/Abbruch
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        run_clone(opts, reporter, CancelFlag())
    """
    _require_yes(opts.yes, opts.dry_run)
    if opts.dry_run:
        for step in plan_clone(opts).steps:
            reporter.log(step)
        return
    require_linux()
    _require_root_for_write(False)
    source = validate_disk(opts.source)
    dest = validate_disk(opts.dest)
    if source == dest:
        raise PibackupError("Quelle und Ziel sind identisch.")
    if device_has_protected_mount(dest):
        raise PibackupError(f"Ziel {dest} hat / oder /boot gemountet.")
    mbr = read_mbr(source)
    nbytes = mbr.image_bytes()
    unmount_disk_partitions(dest, reporter)
    reporter.log(f"Klon {source} -> {dest}, {nbytes} Byte")
    copy_range(source, dest, nbytes, reporter=reporter, cancel=cancel)
    mbr.clear_extra_partitions()
    write_mbr(dest, mbr)
    if opts.change_id:
        sig = opts.new_signature if opts.new_signature is not None else os.urandom(4)
        signature = int.from_bytes(sig, "little") if isinstance(sig, bytes) else int(sig)
        _rewrite_ids(dest, signature, reporter)
    reporter.log("Klon abgeschlossen.")


def inspect_path(path: str) -> dict:
    """Liest MBR-Infos eines Images oder Geraets.

    Parameter:
        path: Datei oder /dev/...
    Rueckgabewert:
        dict mit Partitionen.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        inspect_path("disk.img")
    """
    mbr = read_mbr(path)
    parts = []
    for idx, entry in enumerate(mbr.partitions, start=1):
        parts.append(
            {
                "index": idx,
                "type": f"0x{entry.part_type:02x}",
                "start_lba": entry.start_lba,
                "sectors": entry.sectors,
                "bytes": entry.sectors * meta.SECTOR,
                "empty": entry.empty,
            }
        )
    return {
        "path": path,
        "disk_signature": f"{mbr.disk_signature:08x}",
        "image_bytes": mbr.image_bytes() if not mbr.partitions[1].empty else 0,
        "partitions": parts,
    }


def list_disks_payload() -> dict:
    """Geraete plus Defaults fuer CLI --json.

    Parameter:
        keine
    Rueckgabewert:
        dict
    Fehlerfaelle:
        PibackupError
    Beispiel:
        list_disks_payload()
    """
    devices = list_devices()
    defaults = build_defaults(devices)
    return {
        "devices": [
            {
                "path": item.path,
                "size": item.size,
                "model": item.model,
                "tran": item.tran,
                "removable": item.removable,
                "is_system": item.is_system,
                "mountpoints": item.mountpoints,
            }
            for item in devices
        ],
        "defaults": {
            "mode": defaults.mode,
            "source_disk": defaults.source_disk,
            "start_partition": defaults.start_partition,
            "target_dir": defaults.target_dir,
            "backup_name": defaults.backup_name,
            "shrink": defaults.shrink,
            "compress": defaults.compress,
            "compress_level": defaults.compress_level,
            "suggested_path": suggested_backup_path(defaults),
            "note": defaults.note,
        },
    }
