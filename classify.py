"""What everything is: file types and known Windows folders worth cleaning.

Two independent classifications:

  * CATEGORIES - kind of content, by extension (video, games, programs...).
                 The scanner adds them up per folder to answer "what is in here".
  * RULES      - known folders and files (temp files, caches, Windows Update,
                 node_modules, games...) with what they are, whether they can
                 be deleted and how to clean them properly.

Rule levels:

  safe    regenerates on its own or is no longer needed: fine to delete
  review  can free a lot of space, but it is the user's call
  tool    do not delete by hand: Windows has a tool for it
  system  Windows or the installed programs need it: leave alone
  info    only explains what the folder is

Some "safe" rules carry `clean`: the dashboard can delete those itself (see
cleaner.py). Only where deleting by hand is the usual practice; when there is
an official tool (npm, pip, the browser itself, Windows Update...) the rule
keeps the note and gets no button.

Windows only: every pattern is a Windows path.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase, translate

# --- Content categories ------------------------------------------------------
# The order is fixed: each category always gets the same color in the dashboard.

CATEGORIES = (
    ("video", "Video and audio"),
    ("image", "Images"),
    ("document", "Documents"),
    ("archive", "Archives and installers"),
    ("disk", "Virtual disks"),
    ("program", "Programs"),
    ("game", "Games"),
    ("code", "Development"),
    ("other", "Other"),
)
N_CATEGORIES = len(CATEGORIES)
OTHER = N_CATEGORIES - 1

_EXTENSIONS = {
    "video": "mp4 m4v mkv avi mov wmv webm flv f4v mpg mpeg m2ts mts vob 3gp ogv "
             "rm rmvb divx mxf mp3 wav flac aac ogg oga m4a wma opus aif aiff ape "
             "alac mid midi amr",
    "image": "jpg jpeg jfif png gif bmp tif tiff webp heic heif avif jxl raw cr2 cr3 "
             "nef arw dng orf rw2 raf srw pef psd psb xcf kra ai eps svg ico icns "
             "tga dds exr hdr",
    "document": "pdf doc docx docm dot dotx odt ott rtf txt md xls xlsx xlsm xlsb ods "
                "csv tsv ppt pptx pptm pps ppsx odp key pages numbers epub mobi azw "
                "azw3 djvu xps oxps one pst ost msg eml mbox vsd vsdx pub tex",
    "archive": "zip rar 7z tar gz tgz bz2 tbz2 xz txz zst lz4 lzma cab msi msp msu "
               "msix msixbundle appx appxbundle iso img dmg esd wim swm apk xapk "
               "aab vsix deb rpm",
    "disk": "vhd vhdx avhd avhdx vmdk vdi qcow qcow2 hdd vmem vmsn vmss nvram",
    "program": "exe dll sys drv ocx cpl scr mui efi so dylib pyd winmd ax tlb olb com",
    "game": "ucas utoc pak vpk pkg ttarch2 client bk2 bik usm wad dem forge rpf "
            "assets resource ress bundle ba2 bsa esm esp big cpk acb awb wem bnk "
            "pck fsb xnb gma vpp nsp xci",
    "code": "py pyc pyo pyi pyw ipynb js mjs cjs jsx ts tsx cts map json json5 "
            "java class jar kt kts scala groovy gradle c h cc cpp cxx hpp hh hxx cs "
            "vb fs go rs rb php swift m mm lua pl pm r rmd dart sh bash zsh ps1 "
            "psm1 psd1 bat cmd sql html htm css scss sass less vue svelte astro xml "
            "xsd xsl yml yaml toml ini cfg conf lock o obj lib a pdb idb ilk ipch "
            "pch exp whl egg nupkg wasm node sln csproj vcxproj vbproj fsproj props "
            "targets cmake mk",
}

EXT_CATEGORY: dict[str, int] = {}
for _index, (_key, _label) in enumerate(CATEGORIES):
    for _ext in _EXTENSIONS.get(_key, "").split():
        EXT_CATEGORY["." + _ext] = _index


def category_of(ext: str) -> int:
    """Category index for an already lowercased extension ('.mp4')."""
    return EXT_CATEGORY.get(ext, OTHER)


# --- Cleanup levels ----------------------------------------------------------

LEVELS = {
    "safe": ("Safe to delete",
             "Caches, temp files and dumps: they come back on their own or are "
             "no longer needed."),
    "review": ("Worth a look",
               "Can free a lot of space, but it is your call: games, downloads, "
               "project dependencies..."),
    "tool": ("Use the right tool",
             "Do not delete this by hand: use the Windows tool or command shown."),
    "system": ("Leave alone", "Windows or your installed programs need it."),
    "info": ("What this is", ""),
}
ACTIONABLE = ("safe", "review", "tool")
MIN_FINDING = 1 << 20   # anything smaller is not worth listing as cleanup


class Rule:
    """A known folder or file: what it is and what to do with it."""

    __slots__ = ("id", "patterns", "level", "label", "desc", "how", "stop", "unless",
                 "clean", "close")

    def __init__(self, patterns, level, label, desc, how, stop, unless, clean,
                 close) -> None:
        self.id = -1
        self.patterns = patterns
        self.level = level
        self.label = label
        self.desc = desc
        self.how = how
        # Once matched, nothing below is classified: its contents are already
        # covered (and nothing gets counted twice in the cleanup totals).
        self.stop = stop
        self.unless = unless
        # How the dashboard can clean it: "contents" (what is inside, the folder
        # stays), "folder" (the whole folder), "file" or None.
        self.clean = clean
        self.close = close    # what to close first ("your games"...)


class Finding:
    """Something that can be cleaned: a whole folder or a single file."""

    __slots__ = ("node", "file", "rule", "size")

    def __init__(self, node, file, rule, size) -> None:
        self.node = node      # DirNode of the folder (or the one holding the file)
        self.file = file      # file name, or None when it is the whole folder
        self.rule = rule
        self.size = size


class Classification:
    def __init__(self) -> None:
        self.dirs: dict = {}               # DirNode -> Rule
        self.files: dict = {}              # (DirNode, name) -> Rule
        self.findings: list[Finding] = []  # actionable ones, largest first

    def totals(self) -> dict[str, list[int]]:
        """level -> [bytes, number of places], actionable levels only."""
        out = {level: [0, 0] for level in ACTIONABLE}
        for finding in self.findings:
            slot = out[finding.rule.level]
            slot[0] += finding.size
            slot[1] += 1
        return out

    def groups(self) -> list[tuple[Rule, int, list[Finding]]]:
        """Findings grouped by rule: (rule, bytes, findings), largest first."""
        by_rule: dict[int, list] = {}
        for finding in self.findings:
            slot = by_rule.setdefault(finding.rule.id, [finding.rule, 0, []])
            slot[1] += finding.size
            slot[2].append(finding)
        return sorted((tuple(g) for g in by_rule.values()),
                      key=lambda g: g[1], reverse=True)


# --- Patterns ----------------------------------------------------------------
# Paths use '/' and are matched case-insensitively. Special components:
#   **   any number of folders (including none)
#   *    any single folder (wildcards allowed too: 'UE_*', '*.dmp')
#   ?:   a Windows drive root (C:, D:...)

DIR_RULES: list[Rule] = []
FILE_RULES: list[Rule] = []
_DIR_INDEX: dict[str, list[Rule]] = {}       # by folder name
_DIR_CHILD_OF: dict[str, list[Rule]] = {}    # '.../parent/*' patterns, by parent
_DIR_GLOB: list[tuple[str, Rule]] = []       # last component with wildcards
_DIR_GLOB_RE: "re.Pattern | None" = None
_FILE_NAMES: dict[str, list[Rule]] = {}
_FILE_EXTS: dict[str, list[Rule]] = {}

# Locations of installed programs: their node_modules or virtual environments
# are part of the program, not regenerable dependencies of a project.
_INSTALLED = ("?:/program files/**", "?:/program files (x86)/**", "?:/programdata/**",
              "?:/windows/**", "**/appdata/**", "**/.vscode/**", "**/.vscode-insiders/**",
              "**/.vscode-server/**", "**/.cursor/**", "**/.windsurf/**",
              "**/.antigravity-ide/**", "**/.nvm/**", "**/scoop/**", "**/chocolatey/**")


def _compile(pattern: str) -> list[str]:
    return [part for part in pattern.lower().split("/") if part]


def _is_glob(part: str) -> bool:
    return "*" in part or "?" in part or "[" in part


def _add(target: list[Rule], patterns, level, label, desc, how="", *, stop=None,
         unless=(), clean=None, close=None) -> None:
    if isinstance(patterns, str):
        patterns = (patterns,)
    rule = Rule([_compile(p) for p in patterns], level, label, desc, how,
                level in ACTIONABLE if stop is None else stop,
                [_compile(p) for p in unless], clean, close)
    rule.id = len(DIR_RULES) + len(FILE_RULES)
    target.append(rule)
    for pattern in rule.patterns:
        last = pattern[-1]
        if target is DIR_RULES:
            if last == "*":
                _DIR_CHILD_OF.setdefault(pattern[-2], []).append(rule)
            elif _is_glob(last):
                _DIR_GLOB.append((last, rule))
            else:
                _DIR_INDEX.setdefault(last, []).append(rule)
        elif last.startswith("*."):
            _FILE_EXTS.setdefault(last[1:], []).append(rule)
        else:
            _FILE_NAMES.setdefault(last, []).append(rule)


def _dir(*args, **kwargs) -> None:
    _add(DIR_RULES, *args, **kwargs)


def _file(*args, **kwargs) -> None:
    _add(FILE_RULES, *args, **kwargs)


# Order matters: for the same folder name the first matching rule wins, so the
# specific ones come before the generic ones.

# -- Windows itself --
_dir(("?:/Windows/Temp", "?:/Windows/SystemTemp"), "safe", "Windows temp files",
     "Temporary files left by the system and by installers that run as administrator.",
     "Settings > System > Storage > Temporary files, or delete the contents as "
     "administrator.")
_dir("?:/Windows/SoftwareDistribution/Download", "safe", "Windows Update downloads",
     "Update packages that were already downloaded. Windows downloads them again if "
     "it ever needs them.",
     "As administrator: `net stop wuauserv`, empty the folder and `net start wuauserv`.")
_dir("**/DeliveryOptimization/Cache", "safe", "Delivery Optimization files",
     "Copies of updates that Windows shares with other PCs on your network.",
     "Settings > System > Storage > Temporary files > Delivery Optimization Files.")
_dir("?:/Windows/Minidump", "safe", "Blue screen minidumps",
     "Diagnostic data saved on every blue screen.",
     "Safe to empty (Disk Cleanup > System error memory dump files).")
_dir("?:/Windows/LiveKernelReports", "safe", "Kernel diagnostic reports",
     "Dumps Windows writes when a driver stops responding.",
     "Safe to empty as administrator.")
_dir(("?:/ProgramData/Microsoft/Windows/WER", "**/AppData/Local/Microsoft/Windows/WER"),
     "safe", "Windows error reports",
     "Crash reports from programs, either queued to send or already archived.",
     "Disk Cleanup > Windows error reports, or empty the folder.",
     clean="contents")
_dir("?:/Windows/Panther", "safe", "Windows setup logs",
     "Logs from the last Windows installation or upgrade.",
     "Safe to empty as administrator once Windows is running fine.")
_dir("?:/PerfLogs", "safe", "Performance logs",
     "Reports from the Performance Monitor. Usually empty.",
     "Safe to empty.")
_dir("?:/Windows/Logs", "review", "Windows logs",
     "Setup and servicing logs. They are usually small; when they grow it is "
     "normally the CBS ones.",
     "Old logs can be deleted as administrator.")
_dir("?:/Windows/WinSxS", "tool", "Component store (WinSxS)",
     "Windows components and previous versions of updates. Many files are hard "
     "links shared with System32, so they are counted twice here: it takes up "
     "less than it looks.",
     "As administrator: `Dism /Online /Cleanup-Image /StartComponentCleanup`. To see "
     "its real size: `Dism /Online /Cleanup-Image /AnalyzeComponentStore`.")
_dir("?:/Windows/servicing/LCU", "tool", "Cumulative update packages",
     "Packages for Windows cumulative updates. Component cleanup removes the old ones.",
     "As administrator: `Dism /Online /Cleanup-Image /StartComponentCleanup`.")
_dir("?:/Windows/Installer", "system", "Installer cache",
     "Copies of the installers (.msi and .msp) of your installed programs. Windows "
     "uses them to repair, update and uninstall.",
     "Never delete this by hand. If it is large, uninstall programs you do not use.")
_dir("?:/Windows/System32/DriverStore", "system", "Driver store",
     "Copies of every driver installed on this PC.",
     "Do not delete by hand. Old drivers go with Disk Cleanup > Clean up system "
     "files > Device driver packages.")
_dir("?:/Windows/System32", "system", "Windows system files",
     "The core of Windows: drivers, libraries and services.", "Leave alone.")
_dir("?:/Windows/assembly", "system", ".NET assemblies",
     "Shared .NET Framework libraries and their precompiled versions.", "Leave alone.")
_dir("?:/Windows/Prefetch", "system", "Prefetch data",
     "Small files Windows uses to start your programs faster.",
     "Leave alone: Windows keeps this folder trimmed by itself.")
_dir("?:/Windows/CSC", "system", "Offline files cache",
     "Local copies of network folders made available offline.",
     "Manage it from Control Panel > Sync Center > Manage offline files.")
_dir("?:/Windows", "system", "Windows",
     "The operating system folder.",
     "Do not delete anything by hand. To free space use Settings > System > Storage "
     "or Disk Cleanup.")
_dir("?:/Windows.old", "tool", "Previous Windows installation",
     "A copy of your previous Windows so you can roll back after an upgrade.",
     "Settings > System > Storage > Temporary files > Previous Windows installation(s). "
     "Windows deletes it on its own after a few days.")
_dir(("?:/$WINDOWS.~BT", "?:/$WINDOWS.~WS"),
     "tool", "Windows upgrade leftovers",
     "Setup files left behind after a Windows upgrade.",
     "Disk Cleanup (as administrator) > Clean up system files > Temporary Windows "
     "installation files.")
_dir("?:/Recovery", "system", "Windows recovery",
     "The recovery environment used to repair or reset Windows.", "Leave alone.")
_dir("?:/System Volume Information", "tool", "Restore points and shadow copies",
     "System restore points and shadow copies of the drive.",
     "Control Panel > System > System Protection: choose the drive, Configure, and "
     "set Max Usage or Delete.")
_dir("?:/$Recycle.Bin", "review", "Recycle Bin",
     "What you already deleted. It keeps taking up space until you empty it.",
     "Right-click the Recycle Bin > Empty, once you are sure you need nothing in it.")
_dir("?:/ProgramData/Microsoft/Search/Data", "tool", "Windows Search index",
     "The index behind search in the Start menu and File Explorer.",
     "Control Panel > Indexing Options > Advanced > Rebuild. You can also index "
     "fewer folders there.")
_dir(("?:/ProgramData/Microsoft/Windows Defender",
      "?:/Program Files/Windows Defender"), "system", "Microsoft Defender",
     "Definitions and data for the antivirus built into Windows.", "Leave alone.")
_dir(("?:/ProgramData/Avast Software", "?:/ProgramData/AVG", "?:/ProgramData/McAfee",
      "?:/ProgramData/Norton", "?:/ProgramData/Kaspersky Lab",
      "?:/ProgramData/Bitdefender", "?:/ProgramData/ESET"),
     "system", "Antivirus data",
     "Data and quarantine of your antivirus.",
     "Manage it from the antivirus itself; do not delete these files by hand.")
_dir("?:/ProgramData/Package Cache",
     "system", "Installer package cache",
     "Installers kept by Visual Studio, .NET, the C++ redistributables and others; "
     "they are used to repair and uninstall.",
     "Do not delete by hand: you could end up unable to update or uninstall those "
     "programs.")
_dir("?:/ProgramData/Microsoft/Windows/Virtual Hard Disks", "review", "Hyper-V disks",
     "Virtual machine disks for Hyper-V.",
     "Delete the machines you do not use from Hyper-V Manager.")
_dir("?:/ProgramData/NVIDIA Corporation/Downloader", "safe", "NVIDIA driver downloads",
     "Driver installers downloaded by the NVIDIA app.", "Safe to empty.")
_dir("?:/ProgramData", "info", "Program data",
     "Shared data for installed programs: settings, caches, licenses.",
     "Do not delete the whole folder; there are specific caches inside that can go.")
_dir(("?:/AMD", "?:/NVIDIA", "?:/Intel"), "safe", "Driver installer leftovers",
     "Files the graphics or chipset installer extracts and never cleans up.",
     "Safe to delete: the driver is already installed.")
_dir("?:/Program Files/WindowsApps", "system", "Microsoft Store apps",
     "Store and system apps. This is a protected folder.",
     "Uninstall from Settings > Apps; from there you can also move some apps to "
     "another drive.")
_dir(("?:/Program Files", "?:/Program Files (x86)"), "system", "Installed programs",
     "Where your programs are installed.",
     "Do not delete folders by hand: uninstall from Settings > Apps > Installed apps.")

# -- Your user folder --
_dir("?:/Users/*/AppData/Local/Programs", "system", "Programs installed for your user",
     "Programs that install just for your user (VS Code, Discord, etc.).",
     "Do not delete folders by hand: uninstall from Settings > Apps.",
     stop=True)
_dir("**/AppData/Local/Temp", "safe", "Your temp files",
     "Temporary files left by installers and apps. Almost nothing in here is ever "
     "used again.",
     "Delete the contents (anything in use is skipped) or use Settings > System > "
     "Storage > Temporary files.",
     clean="contents")
_dir("**/AppData/Local/CrashDumps", "safe", "Crash dumps",
     "Memory dumps written when a program closes unexpectedly. Only useful for "
     "debugging.",
     "Safe to empty.", clean="contents")
_dir("**/AppData/Local/Microsoft/Windows/INetCache", "safe", "Internet cache",
     "Cache for Internet Explorer, legacy Edge and the web parts of Office.",
     "Safe to empty; it comes back.", clean="contents")
_dir("**/AppData/Local/Microsoft/Windows/Explorer", "safe", "Thumbnail cache",
     "Thumbnails and icons File Explorer keeps so folders open faster.",
     "Disk Cleanup > Thumbnails. It rebuilds as you browse your folders.")
_dir("**/AppData/Local/Microsoft/Windows/WebCache", "safe", "Web cache database",
     "Shared browsing cache database used by Windows components.",
     "Disk Cleanup > Temporary Internet Files, with Explorer closed.")
_dir("**/AppData/Local/D3DSCache", "safe", "DirectX shader cache",
     "Precompiled DirectX shaders.",
     "Disk Cleanup > DirectX Shader Cache. It comes back.",
     clean="contents", close="your games")
_dir(("**/AppData/Local/NVIDIA/DXCache", "**/AppData/Local/NVIDIA/GLCache",
      "**/AppData/LocalLow/NVIDIA/DXCache", "**/AppData/LocalLow/NVIDIA/GLCache",
      "**/AppData/LocalLow/NVIDIA/PerDriverVersion/DXCache",
      "**/AppData/LocalLow/NVIDIA/PerDriverVersion/GLCache",
      "?:/ProgramData/NVIDIA Corporation/NV_Cache",
      "**/AppData/Local/AMD/DxCache", "**/AppData/Local/AMD/DxcCache",
      "**/AppData/Local/AMD/GLCache", "**/AppData/Local/AMD/VkCache",
      "**/AppData/Local/Intel/ShaderCache"),
     "safe", "Graphics driver shader cache",
     "Shaders your graphics driver precompiles for games.",
     "Safe to empty. Games compile them again (the first few launches may be a "
     "little slower).",
     clean="contents", close="your games")

# -- Browsers --
_dir(("**/User Data/*/Cache", "**/User Data/*/Code Cache", "**/User Data/*/GPUCache",
      "**/User Data/*/Service Worker/CacheStorage", "**/User Data/ShaderCache",
      "**/User Data/GrShaderCache", "**/Opera Software/*/Cache",
      "**/Opera Software/*/Code Cache", "**/Opera Software/*/GPUCache",
      "**/Mozilla/Firefox/Profiles/*/cache2"),
     "safe", "Browser cache",
     "Pages, scripts and images your browser keeps so sites load faster.",
     "From the browser: Clear browsing data > Cached images and files.")
_dir("**/User Data/OptGuideOnDeviceModel", "review", "Chrome on-device AI model",
     "The local AI model (Gemini Nano) behind some Chrome features.",
     "If you delete it, Chrome downloads it again while its AI features stay on.")
_dir(("**/Google/Chrome/User Data", "**/Microsoft/Edge/User Data",
      "**/BraveSoftware/Brave-Browser/User Data", "**/Vivaldi/User Data",
      "**/Chromium/User Data", "**/Mozilla/Firefox/Profiles", "**/Opera Software"),
     "info", "Browser profile",
     "Your browser profiles: history, extensions, site data and the cache.",
     "From the browser: Settings > Privacy > Clear browsing data. Old profiles you "
     "no longer use can be removed from the browser too.")

# -- Microsoft Store apps --
_dir("**/AppData/Local/Packages/*/TempState", "safe", "Store app temp files",
     "Temporary files from an app installed through the Microsoft Store.",
     "Safe to empty with the app closed.", clean="contents")
# These two only label the folder: what is worth cleaning sits inside them
# (Spotify's cache, an Electron cache...), so they must not stop the walk.
_dir("**/AppData/Local/Packages/*/LocalCache", "info", "Store app cache",
     "Cached data of a Microsoft Store app. Some apps also keep downloads here.",
     "Look inside: the caches in there are usually safe to empty, and each app "
     "can normally clear them from its own settings.")
_dir("**/AppData/Local/Packages/*/LocalState", "info", "Store app data",
     "Data of a Microsoft Store app: settings, messages, downloaded media.",
     "Clean it from the app itself, or uninstall the app from Settings > Apps.")
_dir("?:/Users/*/AppData/Local/Packages", "info", "Microsoft Store app data",
     "One folder per installed Store app.",
     "Uninstall apps you do not use from Settings > Apps > Installed apps.")

# -- Package managers: they have their own cleanup command, which is the
#    recommended way, so they carry no `clean`. --
_dir(("**/AppData/Local/pip/Cache", "**/pip/Cache"), "safe", "pip cache",
     "Python packages downloaded by pip.", "`pip cache purge`")
_dir(("**/AppData/Local/npm-cache", "**/AppData/Roaming/npm-cache", "**/.npm/_cacache"),
     "safe", "npm cache",
     "Node packages downloaded by npm.", "`npm cache clean --force`")
_dir("**/AppData/Local/Yarn/Cache", "safe", "Yarn cache",
     "Node packages downloaded by Yarn.", "`yarn cache clean`")
_dir(("**/AppData/Local/pnpm/store", "**/.local/share/pnpm/store"),
     "safe", "pnpm store",
     "Packages shared by every project that uses pnpm.",
     "`pnpm store prune` removes only what no project uses.")
_dir(("**/AppData/Local/NuGet/v3-cache", "**/.nuget/packages"), "safe", "NuGet cache",
     ".NET packages downloaded for your projects.",
     "`dotnet nuget locals all --clear`")
_dir("**/AppData/Local/go-build", "safe", "Go build cache",
     "Build results Go reuses between compiles.", "`go clean -cache`")
_dir("**/AppData/Local/uv/cache", "safe", "uv cache",
     "Python packages downloaded by uv.", "`uv cache clean`")
_dir("**/AppData/Local/Cypress/Cache", "safe", "Cypress binaries",
     "Cypress versions downloaded for your tests.",
     "`npx cypress cache prune` keeps only the current one.")
_dir(("**/AppData/Local/electron/Cache", "**/AppData/Local/electron-builder/Cache"),
     "safe", "Electron cache",
     "Electron binaries downloaded while building apps.",
     "Safe to empty; it downloads again on the next build.", clean="contents")
_dir("**/AppData/Local/ms-playwright", "review", "Playwright browsers",
     "Browsers Playwright downloads to run tests.",
     "Reinstall them with `npx playwright install`.")
_dir("**/AppData/Roaming/npm/node_modules", "review", "Global npm packages",
     "Node tools installed with npm install -g.",
     "List them with `npm ls -g --depth=0` and remove what you do not use with "
     "`npm uninstall -g <package>`.")
_dir("**/AppData/Local/Composer", "safe", "Composer cache",
     "PHP packages downloaded by Composer.", "`composer clear-cache`")
_dir("**/scoop/cache", "safe", "Scoop download cache",
     "Installers Scoop keeps after installing an app.", "`scoop cache rm *`")
_dir("?:/ProgramData/chocolatey/lib", "review", "Chocolatey packages",
     "Programs installed with Chocolatey.",
     "`choco list --local-only` lists them; remove with `choco uninstall <package>`.")
_dir(("**/vcpkg/packages", "**/vcpkg/buildtrees", "**/vcpkg/downloads"),
     "safe", "vcpkg build files",
     "Sources and build output for C++ libraries installed with vcpkg.",
     "`vcpkg remove --outdated` and deleting buildtrees/downloads is the usual way.")
_dir(("**/anaconda3/pkgs", "**/miniconda3/pkgs", "**/miniforge3/pkgs", "**/.conda/pkgs"),
     "safe", "conda package cache",
     "Packages conda keeps after installing them.", "`conda clean --all`")

# -- Development --
_dir("**/node_modules", "review", "Node dependencies",
     "Packages of a JavaScript project. They come back with npm install (or pnpm/yarn).",
     "Delete the ones from projects you no longer work on; `npx npkill` lists them all.",
     unless=_INSTALLED)
_dir(("**/.venv", "**/venv"), "review", "Python virtual environment",
     "Packages installed for one Python project. It can be recreated from "
     "requirements.txt or pyproject.toml.",
     "Delete the ones from projects you no longer work on.", unless=_INSTALLED)
_dir(("**/.next", "**/.nuxt", "**/.turbo", "**/.parcel-cache", "**/.angular",
      "**/.svelte-kit", "**/.astro"),
     "safe", "Build cache",
     "A folder the project's build tool generates; it is rebuilt when you build again.",
     "Safe to delete.", unless=_INSTALLED,
     clean="folder", close="the project's dev server if it is running")
_dir(("**/.pytest_cache", "**/.mypy_cache", "**/.ruff_cache", "**/.tox", "**/__pycache__"),
     "safe", "Python tool cache",
     "Caches written by pytest, mypy, ruff or the Python interpreter itself.",
     "Safe to delete; the tools write them again.", unless=_INSTALLED, clean="folder")
_dir("**/.vs", "safe", "Visual Studio local cache",
     "Per-project IDE data: IntelliSense, indexes, debugging.",
     "Safe to delete with Visual Studio closed.", unless=_INSTALLED,
     clean="folder", close="Visual Studio")
# Gradle, Maven and Cargo have no official cleanup command: deleting the folder
# is the usual way.
_dir(("**/.gradle/caches", "**/.gradle/wrapper/dists"), "safe", "Gradle cache",
     "Dependencies and Gradle distributions that were downloaded.",
     "Safe to empty; Gradle downloads what it needs again.",
     clean="contents", close="your IDE and Gradle (`gradle --stop`)")
_dir("**/.m2/repository", "safe", "Maven repository",
     "Java dependencies downloaded by Maven.",
     "Safe to empty; Maven downloads what it needs again.",
     clean="contents", close="your IDE")
_dir(("**/.cargo/registry", "**/.cargo/git"), "safe", "Cargo cache",
     "Rust crates that were downloaded.", "Safe to empty; Cargo downloads them again.",
     clean="contents")
_dir("**/go/pkg/mod", "safe", "Go modules",
     "Go modules downloaded for your projects.", "`go clean -modcache`")
_dir(("**/AppData/Local/JetBrains/*/caches", "**/AppData/Local/JetBrains/*/index",
      "**/AppData/Local/JetBrains/*/log"),
     "safe", "JetBrains IDE cache",
     "Indexes and caches of IntelliJ, PyCharm, WebStorm, Rider...",
     "From the IDE: File > Invalidate Caches / Restart.")
_dir("**/AppData/Local/JetBrains/Toolbox/apps", "review", "JetBrains IDE versions",
     "IDE versions installed by JetBrains Toolbox, including older ones.",
     "In Toolbox, remove the versions you no longer use (Settings > Clean up).")
_dir("**/AppData/Local/JetBrains", "info", "JetBrains data",
     "Settings, caches and IDE versions from JetBrains tools.",
     "Inside there are caches and old versions that can go.")
_dir("**/AppData/Local/Microsoft/VisualStudio", "info", "Visual Studio user data",
     "Settings, extensions and caches for your Visual Studio installs.",
     "Old versions can be removed from the Visual Studio Installer; the caches "
     "inside are rebuilt on their own.")
_dir("**/AppData/Local/Unity/cache", "safe", "Unity cache",
     "Packages and assets Unity keeps so projects open faster.",
     "Safe to empty; Unity downloads them again.")
_dir("**/Unity/Hub/Editor", "review", "Unity editor versions",
     "Full Unity editor installs, one folder per version.",
     "Remove versions you no longer use from Unity Hub > Installs.")
_dir("**/AppData/Local/UnrealEngine/*/DerivedDataCache", "safe",
     "Unreal derived data cache",
     "Compiled shaders and assets Unreal reuses between builds.",
     "Safe to empty with the editor closed; it is rebuilt on the next build.",
     clean="contents", close="Unreal Editor")
_dir("**/AppData/Local/CrashReportClient", "safe", "Unreal crash reports",
     "Crash reports from Unreal Engine and games built with it.",
     "Safe to empty.", clean="contents")
_dir("**/AppData/Local/Android/Sdk", "review", "Android SDK",
     "Tools, platforms and system images for Android.",
     "Remove versions you do not use from Android Studio > SDK Manager; the "
     "system images are the big ones.")
_dir("**/.android/avd", "review", "Android emulators",
     "Virtual devices from Android Studio.",
     "Delete the ones you do not use from Android Studio > Device Manager.")
_dir(("**/AppData/Local/Docker/wsl", "**/AppData/Local/DockerDesktop"),
     "tool", "Docker Desktop disk",
     "The virtual disk where Docker keeps images, containers and volumes.",
     "Free space with `docker system prune -a`. The .vhdx file does not shrink on "
     "its own: compact it afterwards (`Optimize-VHD` or diskpart).")
_dir("**/AppData/Local/wsl", "tool", "WSL distributions",
     "Disks of the Linux distributions installed with WSL.",
     "Free space inside Linux, then compact the disk: "
     "`wsl --manage <distro> --set-sparse true`.")
_dir(("**/.vscode/extensions", "**/.vscode-insiders/extensions", "**/.vscode-server",
      "**/.cursor/extensions", "**/.windsurf/extensions", "**/.antigravity-ide/extensions"),
     "review", "Editor extensions",
     "Extensions installed in VS Code or another editor based on it.",
     "Uninstall the ones you do not use from the editor itself.")
_dir("**/AppData/Roaming/Code/User/workspaceStorage", "review", "VS Code workspace data",
     "Per-folder state VS Code keeps: open editors, history, extension data.",
     "Entries for folders that no longer exist can be deleted.")
_dir("**/.ollama/models", "review", "Ollama models",
     "AI models downloaded with Ollama.",
     "List them with `ollama list` and remove what you do not use with "
     "`ollama rm <model>`.")
_dir(("**/.cache/huggingface", "**/AppData/Local/huggingface"),
     "review", "Hugging Face models",
     "Models and datasets that were downloaded; they download again if needed.",
     "`huggingface-cli delete-cache` lets you pick what to remove.")
_dir(("**/.cache/torch", "**/AppData/Local/torch"), "review", "PyTorch model cache",
     "Pretrained models downloaded by PyTorch.",
     "Safe to delete the ones you no longer use; they download again.")
_dir("**/.vagrant.d/boxes", "review", "Vagrant boxes",
     "Base images for Vagrant virtual machines.", "`vagrant box prune`")
_dir("**/VirtualBox VMs", "review", "VirtualBox machines",
     "Disks and settings of your virtual machines.",
     "Delete the ones you do not use from VirtualBox (Remove > Delete all files).")
_dir("?:/Users/*/Documents/Virtual Machines", "review", "VMware machines",
     "Disks and settings of your virtual machines.",
     "Delete the ones you do not use from VMware (Manage > Delete from disk).")
_dir("?:/Users/*/source/repos", "info", "Visual Studio projects",
     "Where Visual Studio puts new projects.",
     "Inside, the bin, obj and .vs folders are rebuilt when you build.")

# -- Games --
_dir("**/steamapps/shadercache", "safe", "Steam shader cache",
     "Precompiled shaders for your Steam games.",
     "Safe to delete; Steam downloads or compiles them again.")
_dir(("**/steamapps/downloading", "**/steamapps/temp"), "safe",
     "Unfinished Steam downloads",
     "Leftovers from Steam downloads and updates.",
     "Safe to delete with Steam closed.")
_dir("**/steamapps/workshop", "review", "Steam Workshop content",
     "Mods, maps and other content downloaded from the Workshop.",
     "They go away when you unsubscribe in the Workshop or uninstall the game.")
_dir("**/steamapps/common", "review", "Steam games",
     "Games installed with Steam.",
     "Uninstall what you do not play from your Steam Library (right-click > Manage "
     "> Uninstall).")
_dir(("**/Steam/appcache", "**/Steam/config/htmlcache", "**/Steam/logs",
      "**/Steam/depotcache"),
     "safe", "Steam client cache",
     "Caches and logs of the Steam client itself.",
     "Safe to delete with Steam closed; it rebuilds them.")
_dir("**/Epic Games/UE_*", "review", "Unreal Engine",
     "Full Unreal Engine installs, one folder per version.",
     "Remove versions you do not use from the Epic Games Launcher > Unreal Engine "
     "> Library.")
_dir("**/Epic Games", "review", "Epic Games",
     "Games from Epic Games and its launcher.",
     "Uninstall what you do not play from Epic Games Launcher > Library.")
_dir("**/EpicGamesLauncher/Saved/webcache*", "safe", "Epic launcher cache",
     "Web cache of the Epic Games Launcher store pages.",
     "Safe to delete with the launcher closed.")
_dir("**/Riot Games", "review", "Riot games",
     "League of Legends, Valorant, Teamfight Tactics and the Riot client.",
     "Uninstall what you do not play from Settings > Apps.")
_dir("**/Ubisoft Game Launcher/games", "review", "Ubisoft games",
     "Games installed with Ubisoft Connect.", "Uninstall from Ubisoft Connect.")
_dir("**/Ubisoft Game Launcher/cache", "safe", "Ubisoft launcher cache",
     "Cache of the Ubisoft Connect launcher.",
     "Safe to delete with the launcher closed.")
_dir(("**/GOG Galaxy/Games", "?:/GOG Games"), "review", "GOG games",
     "Games installed with GOG Galaxy.", "Uninstall from GOG Galaxy.")
_dir(("**/EA Games", "**/EA Desktop", "**/Origin Games"), "review", "EA games",
     "Games installed with the EA app or Origin.", "Uninstall from the EA app.")
_dir(("**/World of Warcraft", "**/Overwatch", "**/Diablo IV", "**/Diablo III",
      "**/Call of Duty", "**/Hearthstone", "**/StarCraft II", "**/Heroes of the Storm"),
     "review", "Blizzard game",
     "A game installed through the Battle.net launcher.",
     "Uninstall it from Battle.net (gear icon next to Play > Uninstall).")
_dir(("?:/Program Files (x86)/Battle.net", "?:/ProgramData/Battle.net"),
     "system", "Battle.net launcher",
     "The Battle.net client and its agent.",
     "Uninstall from Settings > Apps if you no longer play Blizzard games.")
_dir("**/Rockstar Games", "review", "Rockstar games",
     "GTA, Red Dead Redemption and the Rockstar launcher.",
     "Uninstall what you do not play from the Rockstar launcher or Settings > Apps.")
_dir("**/Amazon Games/Library", "review", "Amazon Games",
     "Games installed with the Amazon Games app.",
     "Uninstall from the Amazon Games app > Library.")
_dir("?:/XboxGames", "review", "Xbox games",
     "Xbox and PC Game Pass games.", "Uninstall from the Xbox app.")
_dir("**/AppData/Local/Roblox", "review", "Roblox",
     "The Roblox player and the versions it keeps.",
     "Old versions can be deleted; Roblox downloads the current one again.")
_dir("**/AppData/Roaming/.minecraft", "review", "Minecraft",
     "Minecraft (Java Edition): versions, mods, resource packs and your worlds.",
     "Old versions and mod packs can go; keep the `saves` folder, those are your "
     "worlds.")
_dir("**/itch/apps", "review", "itch.io games",
     "Games installed with the itch.io app.", "Uninstall from the itch.io app.")
_dir(("?:/ProgramData/BlueStacks_nxt", "?:/ProgramData/BlueStacks", "**/LDPlayer*",
      "**/MEmu*", "**/NoxPlayer*", "**/Nox_share*"),
     "review", "Android emulator",
     "An Android emulator and its virtual disks. They grow a lot with every "
     "installed game.",
     "Remove instances you do not use from the emulator's own multi-instance "
     "manager, or uninstall it from Settings > Apps.")
_dir("**/AppData/Local/Google/Play Games", "review", "Google Play Games",
     "Android games and the Google Play Games emulator for PC.",
     "Uninstall games you do not play from the app itself; if you no longer use it, "
     "uninstall it from Settings > Apps.")
_dir("?:/Games", "review", "Games folder",
     "Games installed in their own folder.",
     "Uninstall them from their launcher or from Settings > Apps.")
_dir(("?:/Temp", "?:/tmp"), "review", "Temp folder",
     "A temp folder created by hand or by some installer.",
     "Check what is in it; it can normally be emptied.")

# -- Cloud storage --
_dir(("?:/Users/*/OneDrive", "?:/Users/*/OneDrive*"), "info", "OneDrive",
     "Your OneDrive folder.",
     "Right-click > Free up space keeps files online only without deleting them.")
_dir("**/AppData/Local/Microsoft/OneDrive", "system", "OneDrive app",
     "The OneDrive program itself and its logs.",
     "Manage it from OneDrive > Settings; do not delete these files by hand.")
_dir("**/AppData/Local/Google/DriveFS", "review", "Google Drive cache",
     "Local copies of your Google Drive files, plus its cache.",
     "In Google Drive > Settings you can lower the cache limit; the files stay in "
     "the cloud.")
_dir("**/Dropbox/.dropbox.cache", "safe", "Dropbox cache",
     "Copies Dropbox keeps of recently synced files.",
     "Safe to empty with Dropbox closed; it rebuilds itself.")
_dir(("?:/Users/*/Dropbox", "?:/Users/*/Google Drive", "?:/Users/*/iCloudDrive",
      "?:/Users/*/MEGA", "?:/Users/*/pCloudDrive", "?:/Users/*/Box", "?:/Users/*/Sync"),
     "info", "Synced cloud folder",
     "A folder synced with a cloud service: it takes up space here and in the cloud.",
     "In the app's settings you can choose which folders stay on this PC.")
_dir("**/AppData/Local/Mega Limited", "review", "MEGA data",
     "Cache and local data of MEGAsync.",
     "Clear it from MEGAsync > Settings.")

# -- Apps --
_dir(("**/Spotify/Data", "**/Spotify/Storage"), "safe", "Spotify cache",
     "Songs and data Spotify keeps so it does not download them again. Downloads "
     "saved for offline listening live here too.",
     "From Spotify: Settings > Storage > Clear cache.",
     clean="contents", close="Spotify")
_dir(("**/Adobe/Common/Media Cache Files", "**/Adobe/Common/Media Cache",
      "**/Adobe/Common/Peak Files"),
     "safe", "Adobe media cache",
     "Conformed audio and previews from Premiere and After Effects.",
     "From Premiere: Preferences > Media Cache > Delete.",
     clean="contents", close="Premiere and After Effects")
_dir(("**/AppData/Local/Adobe", "**/AppData/Roaming/Adobe"), "info", "Adobe app data",
     "Settings, presets and caches of your Adobe apps.",
     "Inside there are caches that can be emptied from each app's preferences.")
_dir("**/CacheClip", "safe", "DaVinci Resolve render cache",
     "Rendered clips and optimized media DaVinci Resolve writes while you edit.",
     "From Resolve: Playback > Delete Render Cache > All.")
_dir("**/AppData/Local/Microsoft/Office/16.0/OfficeFileCache", "safe",
     "Office file cache",
     "Copies of the documents Office syncs with OneDrive or SharePoint.",
     "Safe to empty with Office closed; it syncs again.")
_dir("**/AppData/Local/Microsoft/Outlook", "review", "Outlook data",
     "Your local mail copy (.ost) and data files (.pst).",
     "The .ost shrinks if you sync fewer months of mail (Account Settings > "
     "Download email for the past...).")
_dir("**/AppData/Local/Microsoft/OneNote", "review", "OneNote cache",
     "Local copy of your notebooks so they work offline.",
     "In OneNote: File > Options > Save & Backup > Optimize all files now.")
_dir(("**/AppData/Local/Microsoft/Teams", "**/AppData/Roaming/Microsoft/Teams"),
     "info", "Teams data",
     "Cache and local data of Microsoft Teams.",
     "The cache folders inside can be emptied with Teams closed; it rebuilds "
     "them on the next sign-in.")
_dir("**/MobileSync/Backup", "review", "iPhone/iPad backups",
     "Backups of Apple devices made with iTunes, Apple Devices or Finder.",
     "Delete old ones from iTunes or the Apple Devices app (Manage backups).")
_dir("**/Apple Computer/iTunes/iPhone Software Updates", "safe", "iOS update files",
     "iOS installers downloaded to update your devices.",
     "Safe to delete; they download again when needed.")
_dir("**/Plex Media Server/Metadata", "review", "Plex metadata",
     "Covers, previews and metadata for your Plex library.",
     "In Plex: Settings > Library, turn off the preview thumbnails you do not need, "
     "then Clean Bundles and Empty Trash.")
_dir("**/Telegram Desktop/tdata", "info", "Telegram data",
     "Your Telegram session and the media it has cached.",
     "In Telegram: Settings > Advanced > Manage local storage, to limit or clear it. "
     "Deleting this folder by hand signs you out.")
_dir("**/AppData/Roaming/discord", "info", "Discord data",
     "Discord settings and cache.",
     "The Cache folders inside can be emptied; Discord rebuilds them.")

# -- Generic app cache: catches programs there is no specific rule for --
_dir(("**/AppData/**/Cache", "**/AppData/**/Code Cache", "**/AppData/**/GPUCache",
      "**/AppData/**/cache2", "**/AppData/**/Service Worker/CacheStorage",
      "**/AppData/**/CachedData", "**/AppData/**/CachedExtensionVSIXs",
      "**/AppData/**/caches", "**/AppData/**/ShaderCache", "**/AppData/**/htmlcache",
      "**/AppData/**/Crashpad"),
     "safe", "App cache",
     "Data an app keeps to go faster; it comes back on its own.",
     "Close the app and empty the folder.",
     clean="contents", close="the app it belongs to")

# -- Personal folders --
_dir("?:/Users/*/Downloads", "review", "Downloads",
     "Everything you have downloaded: installers, archives, documents...",
     "Sort by size and delete what you no longer need; installers you already ran "
     "are dead weight.")
_dir("?:/Users/*/Videos/NVIDIA", "review", "NVIDIA recordings",
     "Clips and gameplay recordings made with the NVIDIA app (ShadowPlay and "
     "instant replay).",
     "Delete what you do not want to keep. In the NVIDIA overlay (Alt+Z) you can "
     "change the target folder and the instant replay length.")
_dir("?:/Users/*/Videos/Captures", "review", "Xbox Game Bar captures",
     "Clips and screenshots made with the Xbox Game Bar (Win+G).",
     "Delete what you do not want to keep.")
_dir("?:/Users/*/Videos/Radeon ReLive", "review", "AMD ReLive recordings",
     "Clips and gameplay recordings made with the AMD software.",
     "Delete what you do not want to keep.")
_dir(("?:/Users/*/Documents", "?:/Users/*/Desktop", "?:/Users/*/Pictures",
      "?:/Users/*/Videos", "?:/Users/*/Music", "?:/Users/*/Saved Games",
      "?:/Users/*/3D Objects"),
     "info", "Your personal files",
     "Documents, photos, videos, music and game saves.",
     "Nobody knows better than you what can go: look at the largest files and at "
     "duplicates.")
_dir("?:/Users/Public", "info", "Shared user folder",
     "Files shared between the users of this PC.",
     "Some programs and games install shared data here.")
_dir(("?:/Users/*/AppData/Local", "?:/Users/*/AppData/Roaming",
      "?:/Users/*/AppData/LocalLow"),
     "info", "App data",
     "Settings, caches and data for your programs.",
     "Do not delete the whole folder; inside there are caches and temp files "
     "that can go.")
_dir("?:/Users/*/AppData", "info", "App data",
     "Settings, caches and data for this user's programs.",
     "Do not delete the whole folder; inside there are caches and temp files "
     "that can go.")
_dir("?:/Users", "info", "Users",
     "The personal folder of each user on this PC.")
_dir("?:/Users/*", "info", "User folder",
     "One user's personal folder: documents, downloads, desktop and the settings "
     "and caches of their programs.",
     "What usually frees the most: Downloads, videos and app caches.")

# -- Single files --
_file("?:/pagefile.sys", "tool", "Virtual memory (pagefile.sys)",
      "Windows uses it as an extension of your RAM.",
      "Do not delete it: set its size in Advanced system settings > Performance > "
      "Advanced > Virtual memory. Leaving it on automatic is usually best.")
_file("?:/hiberfil.sys", "tool", "Hibernation (hiberfil.sys)",
      "Holds the contents of your memory when hibernating, and powers fast startup.",
      "As administrator: `powercfg /h off` removes it (you lose hibernation and fast "
      "startup) and `powercfg /h /type reduced` shrinks it.")
_file("?:/swapfile.sys", "system", "App swap file (swapfile.sys)",
      "Windows uses it for Store apps.",
      "Leave alone: it is tied to the page file.")
_file("?:/Windows/MEMORY.DMP", "safe", "Full memory dump",
      "Memory dump from the last blue screen.",
      "Safe to delete (Disk Cleanup > System error memory dump files).")
_file("**/ext4.vhdx", "tool", "WSL virtual disk",
      "The disk of a Linux distribution (WSL). It grows as you use it but never "
      "shrinks on its own.",
      "Free space inside Linux and compact it: `wsl --manage <distro> "
      "--set-sparse true`, or `Optimize-VHD` with WSL shut down (`wsl --shutdown`).")
_file("**/docker_data.vhdx", "tool", "Docker Desktop disk",
      "The virtual disk where Docker keeps images, containers and volumes.",
      "Free space with `docker system prune -a`. The file does not shrink on its "
      "own: compact it afterwards (`Optimize-VHD` or diskpart).")
_file(("**/*.dmp", "**/*.mdmp", "**/*.hdmp"), "safe", "Memory dump",
      "A diagnostic dump from a program or from Windows.", "Safe to delete.",
      clean="file")
_file(("**/*.crdownload", "**/*.part", "**/*.partial"), "safe", "Unfinished download",
      "A half-finished download from Chrome, Edge or Firefox.",
      "Safe to delete if no download is in progress.",
      clean="file", close="any download in progress in your browser")
_file(("**/*.tmp", "**/*.temp"), "safe", "Temp file",
      "A temporary file some app never cleaned up.",
      "Safe to delete if it is not in use.", clean="file")
_file("**/*.etl", "safe", "Trace log",
      "An event trace Windows or a driver wrote for diagnostics.",
      "Safe to delete.")
_file("**/*.iso", "review", "Disc image",
      "A disc image, usually the installer for a system or a program.",
      "If you already used it and will not need it again, delete it.")
_file(("**/*.vhd", "**/*.vhdx", "**/*.vmdk", "**/*.vdi", "**/*.qcow2"), "review",
      "Virtual disk",
      "The disk of a virtual machine or of some app (WSL, Docker, emulators...). "
      "The folder it sits in tells you which program owns it.",
      "Delete it from the program that created it, not by hand: if that program "
      "still uses it, it would stop working.")
_file(("**/*.bak", "**/*.old"), "review", "Old backup copy",
      "A copy some program left behind when updating or editing a file.",
      "If you no longer need it, delete it.")
_file("**/*.log", "review", "Log file", "A log file from some program.",
      "If it is old, it can be deleted.")

RULES = sorted(DIR_RULES + FILE_RULES, key=lambda r: r.id)

if _DIR_GLOB:
    _DIR_GLOB_RE = re.compile("|".join(translate(pat) for pat, _rule in _DIR_GLOB))


# --- Matching ----------------------------------------------------------------

def _comp_ok(pat: str, comp: str) -> bool:
    if pat == "*":
        return True
    if pat == "?:":
        return len(comp) == 2 and comp[1] == ":" and comp[0].isalpha()
    if _is_glob(pat):
        return fnmatchcase(comp, pat)
    return pat == comp


def _match(pat: list[str], comps: list[str], i: int = 0, j: int = 0) -> bool:
    while i < len(pat):
        part = pat[i]
        if part == "**":
            if i == len(pat) - 1:
                return True
            return any(_match(pat, comps, i + 1, k) for k in range(j, len(comps) + 1))
        if j >= len(comps) or not _comp_ok(part, comps[j]):
            return False
        i += 1
        j += 1
    return j == len(comps)


def _root_components(path: str) -> list[str]:
    drive, rest = os.path.splitdrive(path)
    if os.altsep:
        rest = rest.replace(os.altsep, os.sep)
    parts = [p.lower() for p in rest.split(os.sep) if p]
    return ([drive.lower()] + parts) if drive else parts


def components(node) -> list[str]:
    """Path of a DirNode as a list of lowercased components."""
    parts = []
    while node.parent is not None:
        parts.append(node.name.lower())
        node = node.parent
    parts.reverse()
    return _root_components(node.name) + parts


def _applies(rule: Rule, comps: list[str]) -> bool:
    return (any(_match(p, comps) for p in rule.patterns)
            and not any(_match(p, comps) for p in rule.unless))


def _candidates(name: str, parent_name: str) -> list[Rule]:
    """Rules that could match a folder, in definition order."""
    rules = _DIR_INDEX.get(name)
    extra = _DIR_CHILD_OF.get(parent_name)
    globs = None
    if _DIR_GLOB_RE is not None and _DIR_GLOB_RE.match(name):
        globs = [rule for pat, rule in _DIR_GLOB if fnmatchcase(name, pat)]
    if not extra and not globs:
        return rules or []
    out = list(rules or []) + (extra or []) + (globs or [])
    out.sort(key=lambda r: r.id)
    return out


def match_dir(node) -> Rule | None:
    comps = None
    if node.parent is None:
        comps = components(node)
        name = comps[-1] if comps else ""
        parent_name = comps[-2] if len(comps) > 1 else ""
    else:
        name = node.name.lower()
        parent = node.parent
        if parent.parent is not None:
            parent_name = parent.name.lower()
        else:
            parent_name = (_root_components(parent.name) or [""])[-1]
    rules = _candidates(name, parent_name)
    if not rules:
        return None
    if comps is None:
        comps = components(node)
    for rule in rules:
        if _applies(rule, comps):
            return rule
    return None


def _file_candidates(lower_name: str) -> list[Rule]:
    return (_FILE_NAMES.get(lower_name, []) +
            _FILE_EXTS.get(os.path.splitext(lower_name)[1], []))


def match_path(path: str, is_dir: bool) -> Rule | None:
    """The rule for a full path (without looking at its parent folders)."""
    comps = _root_components(path)
    if not comps:
        return None
    if is_dir:
        rules = _candidates(comps[-1], comps[-2] if len(comps) > 1 else "")
    else:
        rules = _file_candidates(comps[-1])
    return next((r for r in rules if _applies(r, comps)), None)


def classify(root) -> Classification:
    """Walk the tree and label the known folders and large files."""
    out = Classification()
    stack = [root]
    while stack:
        node = stack.pop()
        rule = match_dir(node)
        if rule is not None:
            out.dirs[node] = rule
            if rule.level in ACTIONABLE and node.total >= MIN_FINDING:
                out.findings.append(Finding(node, None, rule, node.total))
            if rule.stop:
                continue
        comps = None
        for size, name in node.big_files:
            lower = name.lower()
            rules = _file_candidates(lower)
            if not rules:
                continue
            if comps is None:
                comps = components(node)
            frule = next((r for r in rules if _applies(r, comps + [lower])), None)
            if frule is None:
                continue
            out.files[(node, name)] = frule
            if frule.level in ACTIONABLE and size >= MIN_FINDING:
                out.findings.append(Finding(node, name, frule, size))
        stack.extend(node.children)
    out.findings.sort(key=lambda f: f.size, reverse=True)
    return out
