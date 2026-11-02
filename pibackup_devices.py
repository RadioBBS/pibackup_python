"""
pibackup_python – Geraeteliste und Voreinstellungen.

Projekt:     pibackup_python
Modul:       pibackup_devices.py
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
Liest lsblk-JSON, findet die Startdisk (Geraet von /) und schlaegt
eine externe Disk plus Backupnamen Hostname-Datum vor.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
    python3 pibackup_python.py devices
    python3 pibackup_python.py defaults
"""

from __future__ import annotations

import json
import socket
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pibackup_common import (
    PibackupError,
    find_tool,
    is_linux,
    partition_path,
    run_cmd,
    safe_backup_stem,
)
import project_meta as meta

SKIP_PREFIXES = ("loop", "ram", "zram", "dm-")
PROTECTED_MOUNTS = {"/", "/boot", "/boot/firmware"}


@dataclass
class BlockDevice:
    """Ein physisches Blockgeraet mit Partitionen."""

    path: str
    name: str
    size: int
    model: str
    tran: str
    removable: bool
    is_system: bool
    mountpoints: list[str] = field(default_factory=list)
    partitions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Defaults:
    """Voreinstellungen der GUI und von backup ohne Extra-Flags."""

    mode: str
    source_disk: str
    start_partition: str
    target_dir: str
    backup_name: str
    shrink: bool
    compress: bool
    compress_level: int
    note: str = ""


def _blk_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return list(node.get("children") or [])


def _collect_mounts(node: dict[str, Any], into: list[str]) -> None:
    mount = node.get("mountpoint")
    if mount:
        into.append(str(mount))
    for child in _blk_children(node):
        _collect_mounts(child, into)


def parse_lsblk_json(payload: str, system_disk: str) -> list[BlockDevice]:
    """Wandelt lsblk -J in BlockDevice-Liste.

    Parameter:
        payload: JSON von lsblk.
        system_disk: z. B. /dev/mmcblk0.
    Rueckgabewert:
        Nur echte Disks, ohne loop/ram.
    Fehlerfaelle:
        PibackupError bei ungueltigem JSON.
    Beispiel:
        parse_lsblk_json(text, "/dev/mmcblk0")
    """
    try:
        tree = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PibackupError("lsblk-JSON ungueltig.") from exc
    devices: list[BlockDevice] = []
    for node in tree.get("blockdevices") or []:
        name = str(node.get("name") or "")
        if any(name.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if str(node.get("type") or "disk") not in ("disk", "rom"):
            continue
        path = str(node.get("path") or f"/dev/{name}")
        mounts: list[str] = []
        _collect_mounts(node, mounts)
        parts = []
        for child in _blk_children(node):
            parts.append(
                {
                    "path": child.get("path") or f"/dev/{child.get('name')}",
                    "size": int(child.get("size") or 0),
                    "fstype": child.get("fstype") or "",
                    "mountpoint": child.get("mountpoint") or "",
                    "label": child.get("label") or "",
                }
            )
        devices.append(
            BlockDevice(
                path=path,
                name=name,
                size=int(node.get("size") or 0),
                model=str(node.get("model") or "").strip(),
                tran=str(node.get("tran") or ""),
                removable=bool(node.get("rm") or node.get("hotplug")),
                is_system=path == system_disk,
                mountpoints=mounts,
                partitions=parts,
            )
        )
    return devices


def system_disk_from_mounts(payload: str) -> str:
    """PKNAME bzw. parent der Partition mit mountpoint /.

    Parameter:
        payload: lsblk-JSON.
    Rueckgabewert:
        /dev/... oder leer.
    Fehlerfaelle:
        PibackupError bei ungueltigem JSON.
    Beispiel:
        system_disk_from_mounts(text)
    """
    try:
        tree = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PibackupError("lsblk-JSON ungueltig.") from exc
    found = ""

    def walk(node: dict[str, Any], parent: str) -> None:
        nonlocal found
        path = str(node.get("path") or "")
        name = str(node.get("name") or "")
        current = path or (f"/dev/{name}" if name else parent)
        if node.get("mountpoint") == "/":
            pk = node.get("pkname")
            if pk:
                found = f"/dev/{pk}"
            elif parent:
                found = parent
            else:
                found = current
            return
        for child in _blk_children(node):
            walk(child, current if str(node.get("type")) == "disk" else parent)

    for node in tree.get("blockdevices") or []:
        walk(node, "")
    return found


def writable_mounts(device: BlockDevice) -> list[str]:
    """Mountpoints auf dem Geraet, die nicht Systempartitionen sind.

    Parameter:
        device: BlockDevice.
    Rueckgabewert:
        Liste von Pfaden.
    Fehlerfaelle:
        keine
    Beispiel:
        writable_mounts(dev)
    """
    result = []
    for mount in device.mountpoints:
        if mount in PROTECTED_MOUNTS or not mount:
            continue
        result.append(mount)
    for part in device.partitions:
        mount = str(part.get("mountpoint") or "")
        if mount and mount not in PROTECTED_MOUNTS and mount not in result:
            result.append(mount)
    return result


def suggest_external(devices: list[BlockDevice]) -> tuple[str, str]:
    """Externe Disk und ein beschreibbares Zielverzeichnis.

    Parameter:
        devices: von parse_lsblk_json.
    Rueckgabewert:
        (disk_path, mount_dir), leer wenn nichts passt.
    Fehlerfaelle:
        keine
    Beispiel:
        disk, folder = suggest_external(devs)
    """
    ranked = [
        item
        for item in devices
        if not item.is_system and item.tran != "loop"
    ]
    ranked.sort(
        key=lambda item: (
            0 if item.tran == "usb" or item.removable else 1,
            0 if writable_mounts(item) else 1,
            -item.size,
        )
    )
    for item in ranked:
        mounts = writable_mounts(item)
        if mounts:
            return item.path, mounts[0]
    if ranked:
        return ranked[0].path, ""
    return "", ""


def backup_filename(hostname: str | None = None, day: date | None = None) -> str:
    """Hostname-Datum.img.zst.

    Parameter:
        hostname: sonst socket.gethostname().
        day: sonst heute.
    Rueckgabewert:
        Dateiname.
    Fehlerfaelle:
        keine
    Beispiel:
        backup_filename("pi")
    """
    host = hostname if hostname is not None else socket.gethostname()
    stamp = (day or date.today()).isoformat()
    return safe_backup_stem(host, stamp) + ".img.zst"


def load_lsblk() -> str:
    """Ruft lsblk -J -b auf.

    Parameter:
        keine
    Rueckgabewert:
        JSON-Text.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        load_lsblk()
    """
    lsblk = find_tool("lsblk")
    result = run_cmd(
        [
            lsblk,
            "-J",
            "-b",
            "-o",
            "NAME,PATH,SIZE,TYPE,RM,TRAN,MOUNTPOINT,FSTYPE,PKNAME,MODEL,HOTPLUG,LABEL",
        ]
    )
    if result.returncode != 0:
        raise PibackupError(result.stderr.strip() or "lsblk fehlgeschlagen.")
    return result.stdout


def list_devices() -> list[BlockDevice]:
    """Physische Disks des Systems.

    Parameter:
        keine
    Rueckgabewert:
        Liste, leer ausserhalb Linux.
    Fehlerfaelle:
        PibackupError bei lsblk-Fehlern.
    Beispiel:
        list_devices()
    """
    if not is_linux():
        return []
    payload = load_lsblk()
    system = system_disk_from_mounts(payload)
    return parse_lsblk_json(payload, system)


def build_defaults(devices: list[BlockDevice] | None = None) -> Defaults:
    """Voreinstellung: Backup, Startdisk, externe Disk, Shrink+Pack.

    Parameter:
        devices: optional bereits geladen.
    Rueckgabewert:
        Defaults
    Fehlerfaelle:
        keine (leere Pfade wenn keine Disks).
    Beispiel:
        build_defaults()
    """
    items = devices if devices is not None else list_devices()
    source = next((item.path for item in items if item.is_system), "")
    if not source and items:
        source = items[0].path
    start = partition_path(source, 1) if source else ""
    _disk, target_dir = suggest_external(items)
    name = backup_filename()
    note = ""
    if not is_linux():
        note = "Unter diesem Betriebssystem keine Blockgeraete; Defaults nur als Vorschau."
    elif not target_dir:
        note = "Keine gemountete externe Disk gefunden. Zielverzeichnis bitte setzen."
    return Defaults(
        mode=meta.DEFAULT_MODE,
        source_disk=source,
        start_partition=start,
        target_dir=target_dir,
        backup_name=name,
        shrink=True,
        compress=True,
        compress_level=meta.DEFAULT_COMPRESS_LEVEL,
        note=note,
    )


def suggested_backup_path(defaults: Defaults) -> str:
    """Vollpfad Zielverzeichnis plus Dateiname.

    Parameter:
        defaults: aus build_defaults.
    Rueckgabewert:
        POSIX-Pfad oder nur der Dateiname.
    Fehlerfaelle:
        keine
    Beispiel:
        suggested_backup_path(build_defaults())
    """
    if defaults.target_dir:
        return str(Path(defaults.target_dir) / defaults.backup_name)
    return defaults.backup_name


def devices_as_dicts(devices: list[BlockDevice]) -> list[dict[str, Any]]:
    """JSON-taugliche Geraeteliste.

    Parameter:
        devices: BlockDevice-Liste.
    Rueckgabewert:
        dict-Liste.
    Fehlerfaelle:
        keine
    Beispiel:
        json.dumps(devices_as_dicts(list_devices()))
    """
    return [asdict(item) for item in devices]
