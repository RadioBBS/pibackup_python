# pibackup_python – Architektur

```
pibackup_python – Trennung Engine (root) und Browser-GUI (User).

Projekt:     pibackup_python
Modul:       docs/ARCHITECTURE.md
Version:     1.0.1
Stand:       2026-11-02
Abhaengig:   Linux-Werkzeuge lsblk, e2fsck, resize2fs, losetup, mount, umount, zstd, partprobe; Python-Standardbibliothek (Python >= 3.11)
Bezug:       requirements.txt (leer – Stdlib only)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Historie
--------
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.
```

## Schichten

1. `project_meta.py` – eine Versionsquelle
2. `pibackup_devices.py` – `lsblk`, Startdisk, externe Disk, Hostname-Datum
3. `pibackup_mbr.py` / `pibackup_exclude.py` / `pibackup_linux.py` – Domaene
4. `pibackup_engine.py` – Backup, Restore, Clone
5. `pibackup_python.py` – CLI (root fuer Schreibaktionen)
6. `pibackup_python_gui.py` – HTTP nur `127.0.0.1`, User-Prozess
7. `pibackup_priv.py` – `pkexec` bevorzugt, sonst `sudo`, nur fuer backup/restore/clone

## Datenfluss Backup

MBR lesen → Bytes bis Ende Partition 2 kopieren → Exclude auf Loop-Mount →
`resize2fs -M` → Image kuerzen → leere Bloecke `0xFF` → `zstd`.

Restore/Clone schreiben dasselbe Fenster auf das Zielgeraet und setzen
optional eine neue PARTUUID in MBR, `cmdline.txt` und `fstab`.

## Rechte

Die GUI startet **nicht** die ganze Sitzung als root. Nur der Engine-Prozess
fuer die bestaetigte Schreibaktion laeuft erhoeht. Abbruch: Cancel-Datei im
Temp-Verzeichnis, vom User beschreibbar.

## Bindung

Kein LAN-Bind. Token in der HTML-Seite gegen fremde lokale POST-Requests.
