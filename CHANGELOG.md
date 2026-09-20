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
- **Age**: the scan now records when each file was last written and adds it up
  per folder, in five bands from "this month" to "over 2 years". It costs
  nothing to collect (the scan already reads that) and it answers the question
  size alone cannot: what is big *and* cold.
- The treemap can be coloured three ways, with a switch above it: what it is
  (can it be cleaned), content (which kind of file fills it) or age. The
  legend follows the choice, and the choice is remembered.
- The map and the table below it light up together: hovering a row outlines
  its block, and the other way round.
- `--no-report` and `--no-health` on the program, `-Report` on the launcher,
  and `--version`.
- This changelog and the `VERSION` file.

### Changed

- The console stays quiet: the one-line command no longer prints the whole
  report, only the link to the dashboard. `-Report` brings it back.
- The advanced view stops drawing blocks too small to read: the tail becomes
  one "N smaller items" block. Long names are shortened in the middle
  (`e000a9dd…1.nvph`), where the telling part usually is.
- The detail panel says what can be cleaned under the folder you are looking
  at, how much of it has not been touched in over a year, and when what is
  inside was last written. The table gained a sortable column for the same.
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
