# Backups

Set up on the **Settings › Data and backup** page.

## How they are made
- A consistent copy made with SQLite's online backup API while recording continues
  (never copy `fermentation.db` with the file system while the service runs).
- Written as a temporary file, checked (`PRAGMA integrity_check`), then renamed to
  `fm2-backup-YYYYMMDD-HHMMSS.db`. The newest N are kept.
- Optional: `csv/<code>_controller.csv` and `csv/<code>_manual.csv` for every started
  experiment, readable without this software (overwritten at each backup).
- Every 1, 6 or 24 h, plus (optional) at each "End of production", plus "Back up now".

## Choosing the destination (still to decide)
The service runs as `NT SERVICE\fm2`, **with nobody logged in**. So:
- The account must be able to **write** to the destination folder. For a local folder:
  `icacls "<folder>" /grant "NT SERVICE\fm2:(OI)(CI)M"` (elevated PowerShell).
- A folder synced by a **desktop cloud client** (OneDrive, Google Drive, …) only reaches the
  cloud while that client runs, i.e. while its user is logged in on the PC. Good options:
  - an institutional **network share** (the virtual account authenticates as the PC; IT may need
    to grant the computer account access), or
  - a local folder on a second disk + a sync client that runs as a service, or
  - a local folder synced by a dedicated account that stays logged in.
- Never use the live data folder `C:\ProgramData\FermentationManager\data`, and never open the
  live database from a synced folder.

## Restore
1. Stop the service (elevated): `Stop-Service fm2`.
2. Move the current `C:\ProgramData\FermentationManager\data\fermentation.db` (and any
   `-wal` / `-shm` files next to it) to a safe place — do not delete them.
3. Copy the chosen `fm2-backup-….db` to `C:\ProgramData\FermentationManager\data\fermentation.db`.
4. `Start-Service fm2`, open the app and check the experiments.
