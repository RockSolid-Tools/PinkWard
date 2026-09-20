#Requires -Version 5.1
<#
.SYNOPSIS
    Runs PinkWard on this PC without installing anything, and wipes every trace
    when you are done.

.DESCRIPTION
    Downloads PinkWard and, if this PC has no Python, a portable one into a
    temporary folder, scans the drive you pick and opens the dashboard in your
    browser. Once you press Enter in the console it deletes the whole temporary
    folder. Nothing is installed and nothing is left behind.

    Run it with:
        irm https://raw.githubusercontent.com/RockSolid-Tools/PinkWard/main/pinkward.ps1 | iex

    It then asks which drive to scan and lets you turn the options on or off.
    To skip the menu, pass what you want:
        iex "& { $(irm https://raw.githubusercontent.com/RockSolid-Tools/PinkWard/main/pinkward.ps1) } -Path 'D:\'"

.PARAMETER Path
    Drive or folder to scan. Defaults to the Windows drive.

.PARAMETER Source
    Zip holding PinkWard's source. A URL or a local path.

.PARAMETER Sha256
    Optional SHA256 of that zip. When given, it must match or nothing runs.

.PARAMETER Portable
    Always download the portable Python, even if this PC already has one.

.PARAMETER Admin
    Re-run elevated, so folders that need administrator rights are scanned too.

.PARAMETER NoOpen
    Do not open the browser; just print the link.

.PARAMETER Keep
    Keep the temporary folder instead of deleting it (for troubleshooting).

.PARAMETER Report
    Also print the full report in the console. Off by default: the dashboard
    says all of it better.
#>
[CmdletBinding()]
param(
    [string]$Path = "$env:SystemDrive\",
    [string]$Source = "https://github.com/RockSolid-Tools/PinkWard/archive/refs/heads/main.zip",
    [string]$Sha256,
    [string]$BootstrapUrl = "https://raw.githubusercontent.com/RockSolid-Tools/PinkWard/main/pinkward.ps1",
    [switch]$Portable,
    [switch]$Admin,
    [switch]$NoOpen,
    [switch]$Keep,
    [switch]$Report
)

$ErrorActionPreference = "Stop"

# Portable Python, pinned to a version and its published checksum. Whatever is
# downloaded has to match this, or the run stops.
$PythonVersion = "3.14.7"
$PythonHash = @{
    amd64 = "D297E5FF019966817AD8502465176139F2D3D840FA4ED84B13BED399A6AB1F15"
    arm64 = "F6773983C8959D4281E48C4540CB0BDD23E42391E4E951CE17E7CEB52658F21C"
}

function Write-Step([string]$text) { Write-Host "  $text" -ForegroundColor Cyan }
function Write-Note([string]$text) { Write-Host "  $text" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Download {
    param([string]$Url, [string]$Dest)
    for ($try = 1; $try -le 3; $try++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -TimeoutSec 180
            return
        } catch {
            if ($try -eq 3) { throw "Could not download $Url : $($_.Exception.Message)" }
            Start-Sleep -Seconds (2 * $try)
        }
    }
}

function Expand-Zip {
    param([string]$Zip, [string]$Dest)
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest)
    } catch {
        Expand-Archive -Path $Zip -DestinationPath $Dest -Force
    }
}

function Get-LocalPython {
    # A Python already on this PC, if it is 3.8 or newer. Windows ships a fake
    # "python" that only opens the Microsoft Store, so we ask it for its version.
    foreach ($candidate in @(@("py", @("-3")), @("python", @()))) {
        $exe = $candidate[0]
        $prefix = $candidate[1]
        try {
            $probe = @($prefix) + @("-c", "import sys;print(sys.version_info[0],sys.version_info[1])")
            $out = & $exe @probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -match "^(\d+) (\d+)$") {
                if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 8) {
                    return , (@($exe) + $prefix)
                }
            }
        } catch { }
    }
    return $null
}

function Remove-Stale {
    # Folders left behind by a run whose window was closed instead of quit.
    # Each run drops its process id in owner.pid, so an orphan is spotted right
    # away and a folder still in use by another run is left alone.
    foreach ($dir in Get-ChildItem -Path $env:TEMP -Directory -Filter "pinkward-*" -ErrorAction SilentlyContinue) {
        $marker = Join-Path $dir.FullName "owner.pid"
        $busy = $false
        if (Test-Path $marker) {
            $owner = Get-Content $marker -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($owner -match "^\d+$" -and (Get-Process -Id ([int]$owner) -ErrorAction SilentlyContinue)) {
                $busy = $true
            }
        } elseif ($dir.LastWriteTime -gt (Get-Date).AddHours(-1)) {
            $busy = $true   # no marker and recent: somebody else may be using it
        }
        if (-not $busy) { Remove-Item $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

# --- menu (the tests pull this block out and drive it with fake keys) --------
function Format-Size([double]$bytes) {
    $units = "B", "KB", "MB", "GB", "TB"
    $i = 0
    while ($bytes -ge 1024 -and $i -lt 4) { $bytes /= 1024; $i++ }
    # Invariant culture, so it reads the same as the rest of PinkWard
    # instead of following the machine's decimal separator.
    [string]::Format([cultureinfo]::InvariantCulture, "{0:N1} {1}", $bytes, $units[$i])
}

function Read-Choices {
    <#
        The menu: pick a drive, flip the options, Enter to start. Returns the
        choices, or $null if the user quits. ReadKey is a parameter so the
        tests can feed it keystrokes.
    #>
    param([hashtable]$opt, [scriptblock]$ReadKey = { [Console]::ReadKey($true) })
    $drives = @([IO.DriveInfo]::GetDrives() |
        Where-Object { $_.IsReady -and $_.DriveType -eq "Fixed" })
    if (-not $drives.Count) { return $opt }
    $sel = 0
    for ($i = 0; $i -lt $drives.Count; $i++) {
        if ($drives[$i].Name -eq "$env:SystemDrive\") { $sel = $i }
    }
    $onOff = { param($b) if ($b) { "on " } else { "off" } }

    while ($true) {
        Write-Host ""
        Write-Host "  PinkWard" -ForegroundColor White
        Write-Host "  what is taking up your disk, and what is safe to delete" -ForegroundColor DarkGray
        Write-Host ""
        for ($i = 0; $i -lt $drives.Count; $i++) {
            $d = $drives[$i]
            $here = (-not $opt.Path) -and ($i -eq $sel)
            $label = if ($d.VolumeLabel) { $d.VolumeLabel } else { "" }
            Write-Host ("   {0} [{1}]  {2,-3} {3,-14} {4} free of {5}" -f `
                    $(if ($here) { ">" } else { " " }), ($i + 1), $d.Name.TrimEnd("\"),
                    $label, (Format-Size $d.AvailableFreeSpace), (Format-Size $d.TotalSize)
            ) -ForegroundColor $(if ($here) { "White" } else { "Gray" })
        }
        if ($opt.Path) {
            Write-Host ("   > [F]  " + $opt.Path) -ForegroundColor White
        } else {
            Write-Host "     [F]  another folder..." -ForegroundColor Gray
        }
        Write-Host ""
        Write-Host ("     [A]  run as administrator .... " + (& $onOff $opt.Admin) +
                    "   scans system folders too") -ForegroundColor Gray
        Write-Host ("     [P]  bring its own Python .... " + (& $onOff $opt.Portable) +
                    "   ignores the one installed here") -ForegroundColor Gray
        Write-Host ("     [O]  open the browser ........ " + (& $onOff (-not $opt.NoOpen)) +
                    "   the link is printed either way") -ForegroundColor Gray
        Write-Host ("     [K]  keep the temp folder .... " + (& $onOff $opt.Keep) +
                    "   normally everything is wiped") -ForegroundColor Gray
        Write-Host ""
        Write-Host "   Enter to start  ·  number or letter to change  ·  Q to quit" -ForegroundColor DarkGray

        $key = & $ReadKey
        $ch = "$($key.KeyChar)".ToUpper()
        if ($key.Key -eq "Enter") {
            if (-not $opt.Path) { $opt.Path = $drives[$sel].Name }
            return $opt
        }
        if ($key.Key -eq "Escape" -or $ch -eq "Q") { return $null }
        if ($ch -match "^[1-9]$") {
            $n = [int]$ch
            if ($n -le $drives.Count) { $sel = $n - 1; $opt.Path = $null }
        } elseif ($ch -eq "A") { $opt.Admin = -not $opt.Admin }
        elseif ($ch -eq "P") { $opt.Portable = -not $opt.Portable }
        elseif ($ch -eq "O") { $opt.NoOpen = -not $opt.NoOpen }
        elseif ($ch -eq "K") { $opt.Keep = -not $opt.Keep }
        elseif ($ch -eq "F") {
            Write-Host ""
            $typed = Read-Host "   Folder to scan"
            $typed = $typed.Trim('"').Trim()
            if ($typed -and (Test-Path -LiteralPath $typed)) { $opt.Path = $typed }
            elseif ($typed) { Write-Warning "   Not a folder: $typed" }
        }
    }
}
# --- end menu ---------------------------------------------------------------

if ($env:OS -ne "Windows_NT") { throw "PinkWard only runs on Windows." }
if ($Source -like "*CHANGE-ME*") {
    throw "Set -Source to the zip holding PinkWard (or edit the default in this script)."
}

# With no options given, and a console that can read keys, ask.
$given = @("Path", "Admin", "Portable", "NoOpen", "Keep", "Report") |
    Where-Object { $PSBoundParameters.ContainsKey($_) }
$canAsk = $false
try { $canAsk = -not [Console]::IsInputRedirected } catch { $canAsk = $false }
if (-not $given -and $canAsk) {
    $choice = Read-Choices @{ Path = $null; Admin = $false; Portable = $false;
                              NoOpen = $false; Keep = $false }
    if ($null -eq $choice) { Write-Host ""; return }
    $Path = $choice.Path
    $Admin = [switch]$choice.Admin
    $Portable = [switch]$choice.Portable
    $NoOpen = [switch]$choice.NoOpen
    $Keep = [switch]$choice.Keep
}

if ($Admin -and -not (Test-Admin)) {
    if ($BootstrapUrl -like "*CHANGE-ME*") { throw "-Admin needs -BootstrapUrl to be set." }
    Write-Step "Asking for administrator rights..."
    $inner = "& ([scriptblock]::Create((irm '$BootstrapUrl'))) -Path '$Path' -Source '$Source'"
    if ($Portable) { $inner += " -Portable" }
    if ($NoOpen) { $inner += " -NoOpen" }
    if ($Keep) { $inner += " -Keep" }
    if ($Report) { $inner += " -Report" }
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-NoExit", "-Command", $inner)
    return
}

$progress = $ProgressPreference
$ProgressPreference = "SilentlyContinue"   # makes downloads far faster on PS 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

Remove-Stale
$work = Join-Path $env:TEMP ("pinkward-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $work -Force | Out-Null
Set-Content -Path (Join-Path $work "owner.pid") -Value $PID -Encoding ASCII
$app = Join-Path $work "app"

Write-Host ""
Write-Note "nothing is installed; everything runs from $work"
Write-Host ""

try {
    # --- PinkWard itself ---------------------------------------------------
    Write-Step "Getting PinkWard..."
    $zip = Join-Path $work "PinkWard.zip"
    if (Test-Path $Source) { Copy-Item -LiteralPath $Source -Destination $zip }
    else { Get-Download -Url $Source -Dest $zip }
    if ($Sha256) {
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash
        if ($got -ne $Sha256.ToUpper()) { throw "The PinkWard zip does not match its checksum." }
    }
    $unzipped = Join-Path $work "src"
    Expand-Zip -Zip $zip -Dest $unzipped
    $entry = Get-ChildItem -Path $unzipped -Filter "pinkward.py" -Recurse -File |
        Select-Object -First 1
    if (-not $entry) { throw "pinkward.py is not in that zip." }
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    Copy-Item -Path (Join-Path $entry.DirectoryName "*") -Destination $app -Recurse -Force
    Remove-Item $zip, $unzipped -Recurse -Force -ErrorAction SilentlyContinue

    # --- Python ------------------------------------------------------------
    $python = $null
    if (-not $Portable) {
        $local = Get-LocalPython
        if ($local) {
            $python = $local
            Write-Note ("using the Python already on this PC (" + ($local -join " ") + ")")
        }
    }
    if (-not $python) {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
        Write-Step "Downloading portable Python $PythonVersion ($arch, about 12 MB)..."
        $pyZip = Join-Path $work "python.zip"
        Get-Download -Url "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-$arch.zip" -Dest $pyZip
        $got = (Get-FileHash $pyZip -Algorithm SHA256).Hash
        if ($got -ne $PythonHash[$arch]) { throw "The Python download does not match its checksum." }
        # It goes next to PinkWard's files, so its own folder is on sys.path.
        Expand-Zip -Zip $pyZip -Dest $app
        Remove-Item $pyZip -Force -ErrorAction SilentlyContinue
        $python = @((Join-Path $app "python.exe"))
    }

    # --- run ---------------------------------------------------------------
    # One flat array, built with +=, so splatting never splits an argument
    # ("-3" passed as a bare string would reach python.exe as "-" and "3",
    # and "python -" reads the program from stdin and hangs).
    $runArgs = @()
    if ($python.Length -gt 1) { $runArgs += $python[1..($python.Length - 1)] }
    $runArgs += (Join-Path $app "pinkward.py")
    $runArgs += $Path
    if (-not $NoOpen) { $runArgs += "--open" }
    if (-not $Report) { $runArgs += "--no-report" }
    & $python[0] @runArgs
    $code = $LASTEXITCODE
} finally {
    $ProgressPreference = $progress
    Write-Host ""
    if ($Keep) {
        Write-Note "kept for troubleshooting: $work"
    } else {
        Write-Step "Cleaning up..."
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $work) {
            Start-Sleep -Milliseconds 700
            Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $work) { Write-Warning "Could not remove $work; delete it by hand." }
        else { Write-Note "done: PinkWard left nothing on this PC." }
    }
    Write-Host ""
}

if ($code) { exit $code }
