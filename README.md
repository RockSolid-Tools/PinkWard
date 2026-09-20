# PinkWard

Scans a drive or a folder and tells you **what takes up the most space, where
it is, and what can be cleaned**.

When it finishes it does two things: prints the report in the console and,
at the end, gives you the **link to a dashboard** that opens in your browser,
with charts, folders you can walk through, what each thing is and a button to
delete what is safe.

Windows only. No dependencies: Python 3.8+ and the standard library.

## Run it anywhere, without installing anything

From a PowerShell window (no administrator rights needed):

```
irm https://raw.githubusercontent.com/RockSolid-Tools/PinkWard/main/pinkward.ps1 | iex
```

It asks first:

```
  PinkWard
  what is taking up your disk, and what is safe to delete

   > [1]  C:   Windows        217.9 GB free of 930.5 GB
     [2]  D:   Games          412.0 GB free of 2.0 TB
     [F]  another folder...

     [A]  run as administrator .... off   scans system folders too
     [P]  bring its own Python .... off   ignores the one installed here
     [O]  open the browser ........ on    the link is printed either way
     [K]  keep the temp folder .... off   normally everything is wiped

   Enter to start  ·  number or letter to change  ·  Q to quit
```

Pick a drive, flip whatever you need and press Enter. PinkWard downloads
itself into a temporary folder, plus a portable Python if this PC has none
(pinned to one version and checked against its SHA256), scans, and opens the
dashboard. The console stays out of the way: it only prints the link. When
you press Enter again it deletes the whole folder, so nothing is installed
and nothing is left behind, not the report and not the cleanup log either.

To skip the menu, pass what you want:

```
iex "& { $(irm https://raw.githubusercontent.com/RockSolid-Tools/PinkWard/main/pinkward.ps1) } -Path 'D:\'"
```

| Option | What it does |
|---|---|
| `-Path <folder>` | Drive or folder to scan (the Windows drive by default) |
| `-Admin` | Re-run elevated, so folders needing administrator rights are scanned too |
| `-Portable` | Always use the portable Python, even if this PC already has one |
| `-NoOpen` | Do not open the browser, just print the link |
| `-Keep` | Keep the temporary folder, for troubleshooting |
| `-Report` | Also print the full report in the console |

Passing any of them skips the menu, which is also what happens when the
console cannot read keys (a script, a redirected terminal).

Two things worth knowing: it runs out of `%TEMP%` and it deletes files, so a
strict antivirus may take a second look, and on a locked-down work PC it can
be blocked outright. And since everything goes away at the end, there is no
record of what was deleted; run it from a copy on disk if you want to keep
`reports\cleanup.log`.

To host your own copy, put the project in a repository and replace the two
`CHANGE-ME` URLs at the top of `pinkward.ps1` (`-Source`, the zip of the
code, and `-BootstrapUrl`, this script) with yours.

## Use it from a copy on disk

```
python pinkward.py                 # the current drive (C:\)
python pinkward.py C:\Users\me     # one folder
python pinkward.py D:\ --open      # and open the dashboard in the browser
```

Or double-click `analyze.bat`, which scans your system drive and opens the
dashboard.

At the end of the report you get the dashboard link,
`http://127.0.0.1:<port>/?t=...`: Ctrl+click it from Windows Terminal or the
VS Code terminal. PinkWard serves it **only on your own PC** while it keeps
running, which is what makes deleting from it possible; press Enter in the
console when you are done. It also leaves a copy in `reports\<path>.html` you
can look at later without PinkWard (that copy cannot delete anything). With
the output redirected, or with `--no-serve`, only the copy is written.

### Options

| Option | What it does |
|---|---|
| `-t, --top N` | How many entries to show in each ranking (20 by default) |
| `-d, --depth N` | Folder levels in the first breakdown (1 by default) |
| `--dashboard FILE` | Where to save the dashboard (`reports\<path>.html` by default). `--html` still works as an alias |
| `--no-dashboard` | Console report only |
| `--open` | Open the dashboard in your browser when it finishes |
| `--no-serve` | Only write the dashboard file, do not serve it (nothing can be deleted from it) |
| `-x, --exclude NAME` | Skip a folder by name (can be repeated) |
| `--min-file SIZE` | Smallest single file worth listing (`1MB`, `500KB`, `2GB`) |
| `--logical` | Use the logical size instead of the real size on disk |
| `--count-cloud` | Count OneDrive "online only" files as taking up space |
| `--follow-links` | Follow symlinks and junctions (may count the same data twice) |
| `--no-report` | Console keeps only the dashboard link |
| `--no-health` | Do not ask Windows about the health of the drives |
| `--no-color`, `--quiet` | Output without color / without the progress line |

## What you get

1. **Space by folder** — how much each first-level folder weighs.
2. **Space hotspots** — the folders where the weight *actually piles up*. A
   plain ranking by size only repeats parents (`C:\`, `C:\Users`,
   `C:\Users\me`...). Here the folders that are mere containers are filtered
   out: if a single child holds more than 55% of the size, the parent drops
   out and the child shows up instead.
3. **Largest files** with their full path.
4. **By file type** — where the space goes, per extension.
5. **Cleanup** — what it recognises and how much it takes, in three levels:
   - **Safe to delete**: caches, temp files, memory dumps, Windows Update
     downloads... They come back on their own or are no longer needed.
   - **Worth a look**: installed games, Downloads, `node_modules`, virtual
     environments, virtual machine disks, gameplay recordings... A lot of
     space, but you decide.
   - **Needs a Windows tool**: WinSxS, `pagefile.sys`, `hiberfil.sys`, the
     WSL and Docker disks, `Windows.old`... Not to be deleted by hand; the
     dashboard tells you which command or setting to use.
6. **Notes** — sparse files, skipped links, unreadable folders.

## The dashboard

A single page, no external resources and no internet, with a light and a dark
theme:

- **Summary**: how much can go with no risk, how much is worth a look, drive
  space and what was scanned.
- **Explore**, with two views (it remembers the last one you used):
  - **Simple** (the default): how big the folder is, what is inside by type,
    how much can be freed in there and the list of what takes up the most,
    largest first, with bars and plain-language tags (*Safe to delete*,
    *Worth a look*, *Leave alone*...).
  - **Advanced**: a nested treemap coloured by whether things can be cleaned,
    a detail panel (what it is, what it holds) and the full sortable table,
    with a clickable path to walk back up.
- **Cleanup**: everything recognised, grouped, with what it is, the safe way
  to clean it and each location (*Show* takes you there; *Delete* on what the
  app can clean itself).
- **Large files**: the biggest files in the whole scan.
- **File types**: pick a type (games, video, programs...) or an extension and
  see its largest files; click one to jump to its folder.
- **Disk health**: what Windows itself reports about the drives in this PC.
  Its own health check, the volume check and the storage messages it logged in
  the last 30 days. Wear, temperature and power-on hours only show up when
  PinkWard runs as administrator, because Windows hands those to an elevated
  process alone. Nothing here is measured or changed by PinkWard.
- **Search** (press `/`) by folder or file name.

To keep it from growing without limit it includes the ~120,000 largest items
one by one; smaller things show up grouped ("37 more files"). On a 725 GB
drive with 900,000 files it weighs about 7.5 MB and opens in under a second.

## Deleting from the dashboard

The *Delete* button only shows up on things marked **Safe to delete** where
deleting by hand is also the usual practice: temp files, crash dumps, error
reports and caches (shader, app, Spotify, build...). Precautions:

- **Only inside your user folder**: nothing from the system, nothing that
  needs administrator rights.
- **Where an official tool exists, that tool wins**: npm, pip, Windows
  Update, the browser's own cache... No button there, just the note.
- **It always asks first**, saying what goes, how much and what to close.
- For folders it deletes **what is inside** and keeps the folder (programs
  expect to find it). **Anything in use is skipped**, never forced.
- **Links, junctions and symlinks are never followed**, and right before
  deleting it checks again that the path is still what the scan said.
- It is permanent, no trip through the Recycle Bin: this is content that
  comes back on its own, and the Recycle Bin would free no space.
- Everything is written down in `reports\cleanup.log`.
- No PowerShell and no external programs: Python does it itself.

After each delete the dashboard **updates itself**: the safe-to-delete
figure, the cleanup groups, the folder sizes along the whole path and the
free space on the drive. Only the File types tab keeps the totals from the
scan, and the footer says so.

To make deleting possible the dashboard is served on `http://127.0.0.1`
(never visible from another machine), with a random token per scan. Other
sites open in your browser cannot trigger a delete: every request needs that
token, and the `Host` and `Origin` headers are checked. The browser only
sends ids; what gets deleted, and where, is decided by the scan.

## What it recognises

About 120 rules covering Windows itself (WinSxS, Windows Update, restore
points, the search index...), your user folder (temp files, crash dumps,
thumbnails, shader caches), browsers, Microsoft Store apps, package managers
(pip, npm, yarn, pnpm, NuGet, Go, uv, conda, Composer, Scoop, Chocolatey,
vcpkg), dev tools (Gradle, Maven, Cargo, JetBrains, Visual Studio, Unity,
Unreal, Android SDK, Docker, WSL), game launchers (Steam, Epic, EA, Ubisoft,
GOG, Battle.net, Rockstar, Xbox, Amazon, itch.io, Roblox, Minecraft, Android
emulators), cloud storage (OneDrive, Google Drive, Dropbox, MEGA, iCloud) and
apps such as Spotify, Adobe, DaVinci Resolve, Office, Outlook, Teams, Plex or
Telegram.

Programs it has never heard of are still caught by the generic rules: any
`Cache`, `GPUCache` or `Code Cache` folder inside AppData, any
`node_modules`, any `.dmp` or `.tmp` file, a Steam library on any drive.
Whatever is left shows up with its size and content types, just without the
"what is this" note.

To teach it a new folder, add a rule in `classify.py` (`_dir(...)` or
`_file(...)`): path pattern, level, what it is and what to do. If the app
should be able to delete it too, add `clean="contents"` (and `close="..."` if
something has to be closed first).

## Why the sizes are trustworthy

Measuring "what it takes up" on Windows is not reading `st_size`. This tool
reports the **space really used on disk** by default:

- **Sparse files.** An emulator or virtual machine image can claim 512 GB and
  use 4 GB. Resolved with `GetCompressedFileSize`.
- **NTFS compression.** Same treatment: the compressed size counts, not the
  logical one.
- **Cluster slack.** A 1-byte file still eats a whole cluster (4 KB on most
  drives). With hundreds of thousands of small files the difference is real.
- **Files resident in the MFT.** NTFS keeps very small files inside the MFT
  record itself, with no cluster of their own. The cut-off is
  `NTFS_RESIDENT_LIMIT = 700` bytes, picked by measuring ~6,000 real files:
  it gets the exact size right 98.4% of the time.
- **OneDrive "online only".** They use no local space, so they are not
  counted (use `--count-cloud` to include them).
- **Junctions and symlinks.** Not followed, so nothing is counted twice.

Checked against `GetFileInformationByHandleEx` (the real allocation Windows
reports) over real folders: off by -0.015% and -0.056%.

With `--logical` you get logical sizes back, which is what most tools report
and what you see in a file's properties.

## Known limits

- Without administrator rights some system folders cannot be read; the report
  says how many and how much of the drive it covered. Run it from an
  administrator terminal to cover everything.
- The size on disk is exact for sparse and compressed files, and a very close
  estimate (~0.05%) for the rest.
- `pagefile.sys`, `hiberfil.sys` and `swapfile.sys` count as normal files:
  they do use real space, but they are not simply deletable.

## Layout

| File | What is in it |
|---|---|
| `pinkward.py` | Entry point and command-line options |
| `scanner.py` | Tree walk and size measurement |
| `classify.py` | What everything is: file types and known folders to clean |
| `health.py` | Asks Windows about the drives: health, wear and logged errors |
| `VERSION`, `version.py` | The version number, in one place |
| `CHANGELOG.md` | What changed in each version |
| `report.py` | Terminal report |
| `dashboard.py` | Builds the dashboard out of the scan |
| `dashboard_template.html` | Dashboard template (HTML, CSS and JS) |
| `server.py` | Serves the dashboard locally and takes the cleanup requests |
| `cleaner.py` | Safe deletion of what the app is allowed to clean |
| `reports\` | Generated dashboards and `cleanup.log` |
| `analyze.bat` | Quick launcher for a copy on disk |
| `pinkward.ps1` | One-command launcher: downloads, runs and wipes itself |
