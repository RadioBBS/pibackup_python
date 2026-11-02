"""
pibackup_python – MBR lesen/schreiben und PARTUUID.

Projekt:     pibackup_python
Modul:       pibackup_mbr.py
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
DOS-MBR (512 Byte): Signatur, vier Partitionseintraege, Disk-ID.
Anpassung von cmdline.txt und fstab auf eine neue PARTUUID.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
Wird von der Engine importiert.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from pibackup_common import PibackupError
import project_meta as meta

BOOT_MAGIC = b"\x55\xaa"
ENTRY_OFFSET = 0x1BE
SIGNATURE_OFFSET = 0x1B8
ENTRY_SIZE = 16


@dataclass
class PartitionEntry:
    """Ein MBR-Slot (1..4)."""

    status: int
    part_type: int
    start_lba: int
    sectors: int

    @property
    def empty(self) -> bool:
        """True wenn unbenutzt.

        Parameter:
            keine
        Rueckgabewert:
            bool
        Fehlerfaelle:
            keine
        Beispiel:
            if entry.empty: ...
        """
        return self.part_type == 0 or self.sectors == 0

    @property
    def end_lba(self) -> int:
        """Letzter LBA plus eins (exklusiv).

        Parameter:
            keine
        Rueckgabewert:
            int
        Fehlerfaelle:
            keine
        Beispiel:
            size = entry.end_lba * 512
        """
        return self.start_lba + self.sectors


@dataclass
class Mbr:
    """Kompletter MBR mit Disk-Signatur."""

    raw: bytearray
    disk_signature: int
    partitions: list[PartitionEntry]

    def image_bytes(self) -> int:
        """Bytezahl bis Ende Partition 2 (Index 1).

        Parameter:
            keine
        Rueckgabewert:
            Groesse in Byte.
        Fehlerfaelle:
            PibackupError wenn p1/p2 fehlen.
        Beispiel:
            n = mbr.image_bytes()
        """
        if len(self.partitions) < 2:
            raise PibackupError("MBR enthaelt keine zwei Partitionen.")
        p2 = self.partitions[1]
        if p2.empty:
            raise PibackupError("Partition 2 (Root) fehlt im MBR.")
        return p2.end_lba * meta.SECTOR

    def clear_extra_partitions(self) -> None:
        """Loescht Slot 3 und 4 im Puffer.

        Parameter:
            keine
        Rueckgabewert:
            None
        Fehlerfaelle:
            keine
        Beispiel:
            mbr.clear_extra_partitions()
        """
        for index in (2, 3):
            start = ENTRY_OFFSET + index * ENTRY_SIZE
            self.raw[start : start + ENTRY_SIZE] = b"\x00" * ENTRY_SIZE
            self.partitions[index] = PartitionEntry(0, 0, 0, 0)

    def set_p2_sectors(self, sectors: int) -> None:
        """Setzt die Groesse der Root-Partition.

        Parameter:
            sectors: neue Sektorzahl.
        Rueckgabewert:
            None
        Fehlerfaelle:
            PibackupError bei ungueltiger Groesse.
        Beispiel:
            mbr.set_p2_sectors(100000)
        """
        if sectors <= 0:
            raise PibackupError("Ungueltige Root-Partitionsgroesse.")
        self.partitions[1].sectors = sectors
        self._write_entry(1, self.partitions[1])

    def set_disk_signature(self, signature: int) -> None:
        """Schreibt die 32-Bit-Disk-ID.

        Parameter:
            signature: 0 .. 0xFFFFFFFF.
        Rueckgabewert:
            None
        Fehlerfaelle:
            PibackupError ausserhalb des Bereichs.
        Beispiel:
            mbr.set_disk_signature(0x1234ABCD)
        """
        if not 0 <= signature <= 0xFFFFFFFF:
            raise PibackupError("Disk-Signatur ausserhalb 32 Bit.")
        self.disk_signature = signature
        self.raw[SIGNATURE_OFFSET : SIGNATURE_OFFSET + 4] = struct.pack(
            "<I", signature
        )

    def _write_entry(self, index: int, entry: PartitionEntry) -> None:
        start = ENTRY_OFFSET + index * ENTRY_SIZE
        blob = bytearray(self.raw[start : start + ENTRY_SIZE])
        blob[0] = entry.status & 0xFF
        blob[4] = entry.part_type & 0xFF
        blob[8:12] = struct.pack("<I", entry.start_lba)
        blob[12:16] = struct.pack("<I", entry.sectors)
        self.raw[start : start + ENTRY_SIZE] = blob


def _parse_entry(blob: bytes) -> PartitionEntry:
    status = blob[0]
    part_type = blob[4]
    start_lba = struct.unpack_from("<I", blob, 8)[0]
    sectors = struct.unpack_from("<I", blob, 12)[0]
    return PartitionEntry(status, part_type, start_lba, sectors)


def parse_mbr(data: bytes) -> Mbr:
    """Parst 512 Byte MBR.

    Parameter:
        data: genau ein Sektor.
    Rueckgabewert:
        Mbr
    Fehlerfaelle:
        PibackupError bei zu kurz oder ohne 55AA.
    Beispiel:
        mbr = parse_mbr(raw)
    """
    if len(data) < meta.SECTOR:
        raise PibackupError("MBR zu kurz (weniger als 512 Byte).")
    if data[510:512] != BOOT_MAGIC:
        raise PibackupError("Kein DOS-MBR (Signatur 55AA fehlt).")
    raw = bytearray(data[: meta.SECTOR])
    signature = struct.unpack_from("<I", raw, SIGNATURE_OFFSET)[0]
    parts = [
        _parse_entry(bytes(raw[ENTRY_OFFSET + i * ENTRY_SIZE :][:ENTRY_SIZE]))
        for i in range(4)
    ]
    return Mbr(raw, signature, parts)


def read_mbr(path: str | Path) -> Mbr:
    """Liest den ersten Sektor einer Datei oder eines Geraets.

    Parameter:
        path: Image oder /dev/...
    Rueckgabewert:
        Mbr
    Fehlerfaelle:
        OSError, PibackupError
    Beispiel:
        mbr = read_mbr("/dev/mmcblk0")
    """
    with Path(path).open("rb") as handle:
        data = handle.read(meta.SECTOR)
    return parse_mbr(data)


def write_mbr(path: str | Path, mbr: Mbr) -> None:
    """Schreibt 512 Byte an Offset 0.

    Parameter:
        path: Image oder Geraet.
        mbr: geaenderter MBR.
    Rueckgabewert:
        None
    Fehlerfaelle:
        OSError
    Beispiel:
        write_mbr("disk.img", mbr)
    """
    with Path(path).open("rb+") as handle:
        handle.seek(0)
        handle.write(bytes(mbr.raw[: meta.SECTOR]))
        handle.flush()


def partuuid(signature: int, index: int) -> str:
    """PARTUUID wie Raspberry Pi (disk-xx).

    Parameter:
        signature: Disk-ID.
        index: Partitionsnummer 1..4.
    Rueckgabewert:
        Kleinbuchstaben-Hex.
    Fehlerfaelle:
        keine
    Beispiel:
        partuuid(0xAABBCCDD, 2)
    """
    return f"{signature:08x}-{index:02d}"


def replace_partuuid_cmdline(text: str, signature: int) -> str:
    """Ersetzt root=PARTUUID=xxxxxxxx im cmdline.txt-Inhalt.

    Parameter:
        text: Dateiinhalt.
        signature: neue Disk-ID.
    Rueckgabewert:
        geaenderter Text.
    Fehlerfaelle:
        PibackupError wenn das Muster fehlt.
    Beispiel:
        replace_partuuid_cmdline(raw, 0x12345678)
    """
    new_id = f"{signature:08x}"
    pattern = re.compile(r"root=PARTUUID=[0-9A-Fa-f]+", re.IGNORECASE)
    if not pattern.search(text):
        raise PibackupError("cmdline.txt enthaelt kein root=PARTUUID=.")
    return pattern.sub(f"root=PARTUUID={new_id}", text, count=1)


def replace_partuuid_fstab(text: str, signature: int) -> str:
    """Ersetzt PARTUUID=xxxxxxxx-0n in fstab.

    Parameter:
        text: fstab-Inhalt.
        signature: neue Disk-ID.
    Rueckgabewert:
        geaenderter Text.
    Fehlerfaelle:
        keine (unveraendert wenn kein Treffer).
    Beispiel:
        replace_partuuid_fstab(raw, 0x12345678)
    """
    new_id = f"{signature:08x}"

    def _sub(match: re.Match[str]) -> str:
        suffix = match.group(1)
        return f"PARTUUID={new_id}-{suffix}"

    return re.sub(
        r"PARTUUID=[0-9A-Fa-f]+-(\d{2})",
        _sub,
        text,
        flags=re.IGNORECASE,
    )


def apply_partuuid_files(boot_mount: Path, root_mount: Path, signature: int) -> None:
    """Schreibt cmdline.txt und /etc/fstab.

    Parameter:
        boot_mount: gemountete Startpartition.
        root_mount: gemountete Root-Partition.
        signature: neue Disk-ID.
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError, OSError
    Beispiel:
        apply_partuuid_files(Path("/mnt/p1"), Path("/mnt/p2"), sig)
    """
    cmdline = boot_mount / "cmdline.txt"
    if cmdline.is_file():
        raw = cmdline.read_text(encoding="utf-8", errors="replace")
        cmdline.write_text(replace_partuuid_cmdline(raw, signature), encoding="utf-8")
    fstab = root_mount / "etc" / "fstab"
    if fstab.is_file():
        raw = fstab.read_text(encoding="utf-8", errors="replace")
        fstab.write_text(replace_partuuid_fstab(raw, signature), encoding="utf-8")
