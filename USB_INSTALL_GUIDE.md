# TARJETAS — USB Install Guide

A complete walkthrough from downloading the code to a working desktop icon on a target
computer. Two stages: **Stage A** happens once, on your own machine. **Stage B** happens
on-site, at the target computer.

> **Note:** the `build_installer/` scripts mentioned below are not included in this public repo.

---

## Stage A — build the installer (on your machine)

### 1. Get the project files
Download and unzip the project to a folder on your computer — e.g.
`C:\Users\<you>\Projects\CustomerLedger`.

### 2. Install prerequisites (one-time)
- **Python 3.9+** — if you don't already have it: https://python.org/downloads (during
  install, check "Add Python to PATH").
- **Inno Setup** (free) — https://jrsoftware.org/isinfo.php — download and run its
  installer with defaults.

### 3. Build the standalone .exe
Open a terminal (Command Prompt or PowerShell) and run:
```
cd C:\Users\<you>\Projects\CustomerLedger\build_installer
build.bat
```
This installs the Python dependencies and runs PyInstaller. It takes a minute or two.
When it finishes, you'll have:
```
build_installer\dist\TARJETAS.exe
```

### 4. Build the installer wizard
Open **Inno Setup Compiler** (installed in step 2), then **File → Open** and select:
```
build_installer\installer.iss
```
Click **Build → Compile** (or just press **F9**). When it finishes, you'll have:
```
build_installer\Output\TARJETASSetup.exe
```
**This one file is everything you need for Stage B.**

### 5. (Recommended) Test it yourself first
Before taking it on-site, double-click `TARJETASSetup.exe` on your own machine,
install it, confirm the desktop icon appears and the app opens correctly, then use
**Manage Backups** in the app to confirm the backup/restore panel works. Uninstall it
afterward from Windows Settings → Apps if you don't want it cluttering your own machine
(this won't affect the installer file itself — you can install it again as many times
as you like).

---

## Stage B — install on the target computer (on-site)

### 1. Copy the installer to the flash drive
Copy just this one file onto the USB drive:
```
TARJETASSetup.exe
```
(No need to copy the rest of the project — the installer is fully self-contained.)

### 2. Load the sample data too (optional)
If you also want to bring over the `customers_seed.json` file and `seed_customers.py`
script to bulk-load test customers once the app is installed, copy those onto the USB
too. This step is entirely optional — skip it if the end user will just be entering real
customers from day one.

### 3. At the target computer
1. Plug in the USB drive.
2. Open it in File Explorer and double-click `TARJETASSetup.exe`.
3. Windows may show a "Windows protected your PC" SmartScreen warning, since this
   installer isn't digitally signed (that's normal for a small in-house tool, not a
   sign of a problem) — click **More info**, then **Run anyway**.
4. Click through the install wizard: **Next → Next → Install → Finish**. It installs to
   the current user's own folder and doesn't require admin rights.
5. If "Launch TARJETAS now" was left checked, the app opens automatically at the
   end of setup. Otherwise, find the new **TARJETAS** icon on the desktop and
   double-click it.

### 4. First launch — what to check
- The app should open in its own window (not a browser tab) and land directly on the
  customer list — that's the whole app, nothing else to start.
- If the window never appears: this machine likely needs the WebView2 Runtime (see the
  note below).
- Create one test customer, add a test item, confirm it saves. Then delete that test
  customer once you're satisfied (there's a Delete button, or just leave it — it's
  harmless either way).

### 5. If loading the seed data
The target computer would need Python installed to run `seed_customers.py` directly —
if the goal is for the end user not to need any dev tools, it's simplest to instead load
the seed data on your *own* machine (the Stage A machine) before building the installer.
Note, though, that the database is created fresh on first launch on the target machine,
so seeded data doesn't travel with the installer automatically — bundling a pre-filled
database into the installer instead of an empty one is a separate step if you want that.

### 6. If the app window doesn't open (WebView2)
This is the one dependency that isn't bundled into the .exe. Windows 10 (2021+) and
Windows 11 normally have it already. If it's missing:
1. On a machine with internet access, download the WebView2 Runtime installer:
   https://developer.microsoft.com/microsoft-edge/webview2/
2. Copy it to the USB drive too, run it on the target computer once, then try the
   TARJETAS icon again.

---

## Day-to-day use after install

- **Opening the app**: double-click the desktop icon. No terminal, no commands.
- **Data location**: `%LOCALAPPDATA%\CustomerLedger\ledger.db` on the target computer.
- **Backups**: happen automatically (on launch, and every 30 minutes while it's open).
  Use the **Backup Now** button anytime for an on-demand one.
- **Restoring a backup**: click **Manage Backups** in the top bar, find the one you
  want by date/time, click **Restore**. It'll confirm before doing anything, and it
  automatically saves your current data as a fresh backup first — so a restore is
  itself undoable if you pick the wrong one.
- **Uninstalling**: Windows Settings → Apps → TARJETAS → Uninstall (or the
  Start Menu shortcut Inno Setup also creates).
