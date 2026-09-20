# Changelog

What changed in each version of PinkWard. The number lives in `VERSION`.

## 0.0.5 - 2026-09-20

### Added

- **The figures follow the drive you pick.** The switch moved out of the path
  and sits above the summary, where what it changes is in sight: safe to
  delete, worth a look, needs a tool, the drive and what was scanned are all
  about the drive in view, and so are the Cleanup tab and Large files. Walking
  into a drive in Explore picks it too, so the two never disagree.
- Deleting everything safe then works on that drive alone, and the button
  says which one.
- **The simple view puts a folder against the whole drive**: one bar to
  scale showing what it takes, what else is in use and what is still free,
  above the breakdown of what is inside. A size on its own says nothing about
  whether you have a problem; next to the drive it does.

### Fixed

- Reloading the dashboard brought back everything that had already been
  deleted, sizes and all: the page carries the scan as it was taken, and a
  reload read it again from scratch. PinkWard now hands the page what it has
  deleted since, every time it serves it, so a reload shows the disk as it is.

## 0.0.4 - 2026-09-20

### Added

- **Several drives in one scan**: `pinkward.py C:\ D:\`, `--all-drives`, or as
  many as you like from the launcher menu, where a number now turns a drive on
  or off instead of picking just one. They land in a single dashboard whose
  totals add up, with a switch next to the path to go from one drive to
  another or see them together. A folder inside another one you also asked for
  is dropped, so nothing is counted twice.
- **Delete everything safe**, at the top of the Cleanup tab: it lists what it
  is about to delete grouped by kind, says how much that is, and then works
  through it in batches while telling you how far it got.
- Rules now say how far the app may delete. Beyond your user folder it reaches
  the places that normally live elsewhere (Steam on another drive, the Windows
  temp folder) and no further: a drive root, `C:\Windows`, `C:\Users`,
  `Program Files` and the like are refused as such, however well a rule seems
  to match them.
- Rules that need administrator (Windows' own temp files, the update
  downloads, blue screen dumps) get a Delete button when PinkWard runs
  elevated, and keep the note when it does not, so no button is offered that
  could only half-work.

### Changed

- Many more of the things marked safe can now be deleted from the app: the
  browser caches, npm, pip, NuGet, Yarn, pnpm, uv, Go, Composer, conda,
  Cypress, Scoop, vcpkg, the Steam, Epic and Ubisoft launcher caches, the
  JetBrains and Unity caches, the Dropbox cache, the iOS update files and the
  driver installer leftovers. On the machine this was built on that is 9 GB
  that used to be a note.
- What can lose something still has no button, on purpose: the Office file
  cache (it can hold changes not yet synced), the render caches that cost
  hours to rebuild, and the thumbnail and web caches Explorer keeps locked.
- The detail panel no longer breaks the age down; the treemap still colours by
  it and the table still sorts by it.
- Cleaning goes in batches of 20 places per request, so no single request holds
  the connection while a whole drive is emptied.

### Fixed

- `analyze.bat` with no arguments scanned nothing: `"C:\"` reached Python as
  `C:"`, because the backslash escapes the closing quote. It also takes
  several paths now (`analyze.bat C:\ D:\`).

## 0.0.3 - 2026-09-20

### Added

- **Age**: the scan now records when each file was last written and adds it up
  per folder, in five bands from "this month" to "over 2 years". It costs
  nothing to collect (the scan already reads that) and it answers the question
  size alone cannot: what is big *and* cold.
- The treemap can be coloured three ways, with a switch above it: what it is
  (can it be cleaned), content (which kind of file fills it) or age. The
  legend follows the choice, and the choice is remembered.
- The map and the table below it light up together: hovering a row outlines
  its block, and the other way round.
- The dashboard link goes on the clipboard by itself, because in an elevated
  console Ctrl+click usually does nothing. `--no-clipboard` turns it off.

### Changed

- The advanced view stops drawing blocks too small to read: the tail becomes
  one "N smaller items" block. Long names are shortened in the middle
  (`e000a9dd…1.nvph`), where the telling part usually is.
- The detail panel says what can be cleaned under the folder you are looking
  at, how much of it has not been touched in over a year, and when what is
  inside was last written. The table gained a sortable column for the same.
- Ctrl+C while the dashboard is being served explains that it quits rather
  than copies; a second one quits.

### Fixed

- Opening the dashboard from an elevated console started the browser elevated
  too, with a different profile, and some browsers refuse outright. The link
  now goes through explorer, which opens it as whoever is sitting at the PC.

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
