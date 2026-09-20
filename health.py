"""What Windows knows about the drives: health, wear and logged errors.

Everything here comes from Windows itself (the Storage module, WMI and the
System event log), asked for in one PowerShell call that runs while the disk
is being scanned, so it costs no extra time. Nothing is inferred and nothing
is written: if Windows does not know, the dashboard says so.

Two of the numbers that matter most on an SSD, the wear level and the power-on
hours, are only handed out to an administrator. Without elevation the rest
still works and the dashboard explains what is missing and why.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading

# Windows hands these out per physical disk; the ones marked (admin) come back
# empty unless the process is elevated.
QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$out = [ordered]@{}
$out.admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$disks = @()
foreach ($d in Get-PhysicalDisk) {
    $rc = $d | Get-StorageReliabilityCounter
    $disks += [ordered]@{
        id       = "$($d.DeviceId)"
        name     = "$($d.FriendlyName)"
        media    = "$($d.MediaType)"
        bus      = "$($d.BusType)"
        size     = [int64]$d.Size
        health   = "$($d.HealthStatus)"
        op       = ($d.OperationalStatus -join ', ')
        spindle  = [int]$d.SpindleSpeed
        wear     = $rc.Wear
        temp     = $rc.Temperature
        tempMax  = $rc.TemperatureMax
        hours    = $rc.PowerOnHours
        starts   = $rc.StartStopCycleCount
        readErr  = $rc.ReadErrorsUncorrected
        writeErr = $rc.WriteErrorsUncorrected
    }
}
$out.disks = $disks

$out.volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    [ordered]@{
        letter = "$($_.DriveLetter)"
        label  = "$($_.FileSystemLabel)"
        fs     = "$($_.FileSystemType)"
        health = "$($_.HealthStatus)"
        size   = [int64]$_.Size
        free   = [int64]$_.SizeRemaining
    }
})

$out.map = @(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object {
    [ordered]@{ disk = "$($_.DiskNumber)"; letter = "$($_.DriveLetter)" }
})

$out.win32 = @(Get-CimInstance Win32_DiskDrive | ForEach-Object {
    [ordered]@{ id = "$($_.Index)"; model = "$($_.Model)"; status = "$($_.Status)" }
})

$out.smart = @(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus |
    ForEach-Object { [ordered]@{ instance = "$($_.InstanceName)"; predict = [bool]$_.PredictFailure } })

try {
    $out.events = @(Get-WinEvent -FilterHashtable @{
            LogName = 'System'
            ProviderName = @('disk', 'Ntfs', 'volmgr', 'storahci', 'stornvme', 'iaStorA')
            StartTime = (Get-Date).AddDays(-30)
        } -MaxEvents 100 -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{
            time     = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm')
            id       = [int]$_.Id
            provider = "$($_.ProviderName)"
            level    = "$($_.LevelDisplayName)"
            message  = ("$($_.Message)" -split "`n")[0].Trim()
        }
    })
} catch { $out.events = @() }

$out | ConvertTo-Json -Depth 5 -Compress
"""

TIMEOUT = 25.0
EVENT_DAYS = 30
# Event ids that mean "the disk had trouble", not just noise.
BAD_EVENTS = {7, 9, 11, 15, 51, 52, 55, 98, 129, 140, 153, 157}


def _as_list(value) -> list:
    """PowerShell turns a one-item array into a plain object; undo that."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value):
    """PowerShell writes empty strings and nulls for what it will not tell us."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def collect(timeout: float = TIMEOUT) -> dict:
    """Ask Windows. Returns the raw answer, or {"error": ...} if it cannot."""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-Command", QUERY],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"error": "PowerShell is not available on this PC."}
    except subprocess.TimeoutExpired:
        return {"error": "Windows took too long to answer."}
    except OSError as exc:
        return {"error": f"Could not ask Windows: {exc}"}

    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        err = proc.stderr.decode("utf-8", "replace").strip()
        return {"error": err.splitlines()[0] if err else "Windows returned nothing."}
    try:
        data = json.loads(text)
    except ValueError:
        return {"error": "Windows returned something unreadable."}
    return data if isinstance(data, dict) else {"error": "Unexpected answer."}


class Probe:
    """Runs the query on its own thread while the disk is being scanned."""

    __slots__ = ("enabled", "timeout", "_thread", "_raw")

    def __init__(self, enabled: bool = True, timeout: float = TIMEOUT) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self._thread = None
        self._raw: dict = {}

    def start(self) -> "Probe":
        if not self.enabled:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        self._raw = collect(self.timeout)

    def result(self, target: str) -> dict:
        """Wait for the answer (it is normally long done) and tidy it up."""
        if not self.enabled:
            return {}
        if self._thread is not None:
            self._thread.join(self.timeout)
            if self._thread.is_alive():
                return {"error": "Windows took too long to answer."}
        return normalize(self._raw, target)


def drive_letter(path: str) -> str:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return drive[0].upper() if drive[1:2] == ":" else ""


def normalize(raw: dict, target: str) -> dict:
    """Turn the raw answer into the few things the dashboard shows."""
    if not raw or raw.get("error"):
        return {"error": (raw or {}).get("error", "No answer from Windows."),
                "disks": [], "volumes": [], "events": [], "admin": False,
                "eventDays": EVENT_DAYS}

    letter = drive_letter(target)
    by_disk: dict[str, list[str]] = {}
    for row in _as_list(raw.get("map")):
        by_disk.setdefault(str(row.get("disk")), []).append(str(row.get("letter")))

    win32 = {str(row.get("id")): row for row in _as_list(raw.get("win32"))}
    # The SMART instance name carries the disk number as its second field.
    smart: dict[str, bool] = {}
    for row in _as_list(raw.get("smart")):
        parts = str(row.get("instance", "")).split("_")
        if len(parts) > 1 and parts[1].isdigit():
            smart[parts[1]] = bool(row.get("predict"))

    volumes = []
    for row in _as_list(raw.get("volumes")):
        volumes.append({
            "letter": row.get("letter", ""),
            "label": row.get("label", ""),
            "fs": row.get("fs", ""),
            "health": row.get("health", ""),
            "size": _number(row.get("size")) or 0,
            "free": _number(row.get("free")) or 0,
            "scanned": row.get("letter", "") == letter,
        })
    volume_health = {v["letter"]: v["health"] for v in volumes}

    events = []
    for row in _as_list(raw.get("events")):
        ident = _number(row.get("id"))
        events.append({
            "time": row.get("time", ""),
            "id": ident,
            "provider": row.get("provider", ""),
            "level": row.get("level", ""),
            "message": (row.get("message") or "")[:300],
            "serious": ident in BAD_EVENTS and row.get("level") in ("Error", "Critical"),
        })
    events.sort(key=lambda e: e["time"], reverse=True)
    serious_events = sum(1 for e in events if e["serious"])

    disks = []
    for row in _as_list(raw.get("disks")):
        ident = str(row.get("id"))
        letters = sorted(by_disk.get(ident, []))
        wear = _number(row.get("wear"))
        read_err = _number(row.get("readErr"))
        write_err = _number(row.get("writeErr"))
        status = (win32.get(ident, {}).get("status") or "").strip()
        predict = smart.get(ident)
        health = (row.get("health") or "").strip()

        notes = []
        level = "good"
        if health.lower() in ("unhealthy", "failed"):
            level = "bad"
            notes.append("Windows reports this disk as unhealthy.")
        elif health.lower() == "warning":
            level = "warn"
            notes.append("Windows reports a warning on this disk.")
        elif not health:
            level = "unknown"
        if predict:
            level = "bad"
            notes.append("The disk's own SMART check predicts a failure.")
        if status and status.upper() not in ("OK", ""):
            level = "bad" if level != "bad" else level
            notes.append(f"Windows reports its status as {status}.")
        if read_err or write_err:
            level = "bad"
            notes.append(f"{read_err or 0} read and {write_err or 0} write errors "
                         "could not be corrected.")
        if wear is not None and wear >= 80:
            level = "bad" if wear >= 95 else "warn"
            notes.append("Most of the write endurance this SSD was rated for is used up.")
        temp = _number(row.get("temp"))
        if temp is not None and temp >= 70:
            level = "warn" if level == "good" else level
            notes.append(f"It is running hot ({temp} C).")
        for letter_code in letters:
            if volume_health.get(letter_code, "Healthy") not in ("Healthy", ""):
                level = "warn" if level == "good" else level
                notes.append(f"Volume {letter_code}: needs a check "
                             f"({volume_health.get(letter_code)}).")

        disks.append({
            "id": ident,
            "name": row.get("name") or win32.get(ident, {}).get("model") or "Disk " + ident,
            "media": row.get("media") or "",
            "bus": row.get("bus") or "",
            "size": _number(row.get("size")) or 0,
            "health": health,
            "status": status,
            "spindle": _number(row.get("spindle")) or 0,
            "wear": wear,
            "life": (100 - wear) if wear is not None else None,
            "temp": temp,
            "tempMax": _number(row.get("tempMax")),
            "hours": _number(row.get("hours")),
            "starts": _number(row.get("starts")),
            "readErr": read_err,
            "writeErr": write_err,
            "predict": predict,
            "letters": letters,
            "scanned": letter in letters,
            "level": level,
            "notes": notes,
        })

    # The disk holding what was scanned goes first.
    disks.sort(key=lambda d: (not d["scanned"], d["id"]))
    return {
        "error": "",
        "admin": bool(raw.get("admin")),
        "disks": disks,
        "volumes": volumes,
        "events": events[:40],
        "seriousEvents": serious_events,
        "eventDays": EVENT_DAYS,
        "scannedLetter": letter,
    }
