# pibackup_python

```
pibackup_python – Python-CLI und Browser-GUI fuer Raspberry-Pi-Backup.

Projekt:     pibackup_python
Modul:       README.md
Version:     1.0.1
Stand:       2026-11-02
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.11)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Historie
--------
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.
```

## Zweck

Eigenstaendiges Python-Produkt: Backup, Restore und Klonen der ersten zwei
Raspberry-Pi-Partitionen (Startpartition plus Root) mit Verkleinern und
zstd. Die Engine ist die CLI und braucht root nur zum Schreiben. Die GUI
laeuft als normaler User im Browser (`127.0.0.1`) und startet die Engine
per `pkexec` oder `sudo` nur fuer die Schreibaktion.

Repo: https://github.com/RadioBBS/pibackup_python

Fachliche Herkunft (kein Datei-Import): https://github.com/RaspberryFpc/pibackup

## Voraussetzungen

- Linux (Raspberry Pi OS / Debian), Python 3.11 oder neuer
- Debian-Pakete, nicht PyPI:

```bash
sudo apt install python3 e2fsprogs util-linux mount zstd policykit-1
```

`requirements.txt` bleibt leer: Live-Lesen des Blockgeraets nutzt die
Standardbibliothek; `resize2fs`/`e2fsck` haben kein sicheres Python-Modul;
Packen nutzt das System-`zstd`.

`--help` / `--version` / `devices` / `defaults` ohne sudo. GUI nur Loopback.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```


## Installation

Aufruf ueber python, kein chmod.

Keine Extra-Systempakete.

Abhaengigkeitsdatei (auch ohne Paketzeilen):

```bash
python -m pip install -r requirements.txt
```

## Aufruf

```bash
python3 pibackup_python.py --help
python3 pibackup_python.py --version
python3 pibackup_python.py devices
python3 pibackup_python.py defaults
python3 pibackup_python.py backup --dry-run
sudo python3 pibackup_python.py backup --yes
python3 pibackup_python_gui.py
```

Voreinstellung Backup: Startdisk, Zielvorschlag externe Disk, Dateiname
`Hostname-YYYY-MM-DD.img.zst`, Verkleinern und Packen.

```bash
sudo python3 pibackup_python.py restore --image /mnt/usb/host-2026-08-24.img.zst --dest /dev/sdb --yes
sudo python3 pibackup_python.py clone --source /dev/mmcblk0 --dest /dev/sdb --yes
python3 pibackup_python_gui.py --bind 127.0.0.1 --port 8787
```

GUI: `http://127.0.0.1:8787/` – als Desktop-User, nicht als root.

## Risiken

Falsches Zielgeraet loescht Daten. Restore/Clone nicht auf eine Disk mit
gemountetem `/` oder `/boot`. `--dry-run` zeigt Schritte, `--yes` ist fuer
jede Schreibaktion Pflicht. Live-Backup liest das Geraet roh (wie ein
Imaging-Lauf); fuer Dateisystem-Konsistenz das System nach Moeglichkeit
ruhig halten. Dauer: Minuten bis Stunden je nach Medium.

## Tests

```bash
python -m unittest tests.test_pibackup_python
```

Ohne Blockgeraete und ohne Produktionsdisks.

## Architektur

Siehe `docs/ARCHITECTURE.md`.

## Lizenz

MIT – siehe `LICENSE`.
