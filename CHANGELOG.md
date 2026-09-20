# Changelog

What changed in each version of PinkWard. The number lives in `VERSION`.

## 0.0.2 - 2026-09-20

### Added

- A menu in the launcher. Running the one-line command now asks which drive to
  scan and lets you turn on administrator rights, the portable Python, opening
  the browser and keeping the temporary folder. Enter starts, `Q` quits before
  anything is downloaded.
- **Disk health** tab: what Windows itself reports about the drives in this PC
  - its health check, the volume check, and the storage messages it logged in
  the last 30 days. Wear, temperature and power-on hours appear when PinkWard
  runs as administrator, because Windows only hands those to an elevated
  process. The query runs while the disk is being scanned, so it costs no
  extra time, and PinkWard neither measures nor changes anything there.
- `--no-report` and `--no-health` on the program, `-Report` on the launcher,
  and `--version`.
- This changelog and the `VERSION` file.

### Changed

- The console stays quiet: the one-line command no longer prints the whole
  report, only the link to the dashboard. `-Report` brings it back.
- The launcher no longer offers a "copy to look at later" that its own cleanup
  was about to delete.
- Sizes in the menu use a dot as the decimal mark, like the rest of PinkWard,
  instead of following the machine's language.

### Fixed

- The launcher split `py -3` into two arguments, so Python was started as
  `python -`, waited for a program on its input and hung forever.
- A temporary folder left behind by a window that was closed instead of quit
  is now removed by the next run, not an hour later.

## 0.0.1 - 2026-09-19

First version: scans a drive, prints the report in the console and builds a
self-contained dashboard with the treemap, the cleanup list, the large files
and the file types. Caches, temp files and dumps can be deleted from the
dashboard while PinkWard serves it locally. `pinkward.ps1` runs the whole
thing from a temporary folder, with a portable Python when needed, and wipes
itself afterwards.
