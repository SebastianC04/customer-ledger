# TARJETAS — USB Install Guide

A complete walkthrough from the code to a working desktop icon on a target computer.
Two stages: **Stage A** happens on your own machine. **Stage B** happens on-site, at the
target computer. Updating a computer that already has TARJETAS is covered in Stage B too.

> **Note:** the `build_installer/` scripts mentioned below are not included in this public
> repo. They live in a separate private repo (`customer-ledger-build`), which also keeps
> each finished installer as a GitHub Release. Clone it into a `build_installer/` folder
> next to `backend/` and `frontend/`:
> `git clone https://github.com/SebastianC04/customer-ledger-build.git build_installer`

---

## Stage A — build the installer (on your machine)

### 1. Get the project files
Download or clone the project to a folder on your computer — e.g.
`C:\Users\<you>\Projects\customer-ledger`.

### 2. Install prerequisites (one-time)
- **Python 3.10+** — if you don't already have it: https://python.org/downloads (during
  install, check "Add Python to PATH").
- **Inno Setup** (free) — https://jrsoftware.org/isinfo.php — download and run its
  installer with defaults.

### 3. Build the standalone .exe
Open a terminal (Command Prompt or PowerShell) and run:
```
cd C:\Users\<you>\Projects\customer-ledger\build_installer
build.bat
```
This installs the Python dependencies and runs PyInstaller. It takes a minute or two.
When it finishes, you'll have:
```
build_installer\dist\TARJETAS.exe
```
If it fails right away with lines like `'ho' is not recognized as an internal or external
command`, `build.bat` has Unix line endings (this can happen after copying it through Git
or Git Bash). Re-save it with Windows (CRLF) line endings, e.g. in Notepad or VS Code,
and run it again.

### 4. Build the installer wizard
Open **Inno Setup Compiler** (installed in step 2), then **File → Open** and select:
```
build_installer\installer.iss
```
Click **Build → Compile** (or just press **F9**). When it finishes, you'll have:
```
build_installer\Output\TARJETASSetup.exe
```
**This one file is everything you need for Stage B.** For a new release, bump
`AppVersion` in `installer.iss` first so it's clear which version is installed.

### 5. (Recommended) Test it yourself first
Before taking it on-site, double-click `TARJETASSetup.exe` on your own machine,
install it, confirm the desktop icon appears and the app opens correctly, then open
**Manage Backups** in the app and click **Backup Now** to confirm backups work.
Uninstall it afterward from Windows Settings → Apps if you don't want it cluttering your
own machine (this won't affect the installer file itself — you can install it again as
many times as you like).

Note that installing it on your own machine uses your own
`%LOCALAPPDATA%\CustomerLedger` folder, so anything you type in while testing stays on
your machine — it never travels with the installer.

---

## Stage B — install on the target computer (on-site)

### 1. Copy the installer to the flash drive
Copy just this one file onto the USB drive:
```
TARJETASSetup.exe
```
(No need to copy the rest of the project — the installer is fully self-contained.)

### 2. At the target computer
1. Plug in the USB drive.
2. **Close TARJETAS** if it's open (updating while it runs can fail to replace the file).
3. Open the USB drive in File Explorer and double-click `TARJETASSetup.exe`.
4. Windows may show a "Windows protected your PC" SmartScreen warning, since this
   installer isn't digitally signed (that's normal for a small in-house tool, not a
   sign of a problem) — click **More info**, then **Run anyway**.
5. Click through the install wizard: **Next → Next → Install → Finish**. It installs to
   the current user's own folder and doesn't require admin rights.
6. If "Launch TARJETAS now" was left checked, the app opens automatically at the
   end of setup. Otherwise, find the **TARJETAS** icon on the desktop and double-click it.

### 3. Updating a computer that already has TARJETAS
Run the new `TARJETASSetup.exe` exactly as above — it installs over the old version.
**The customers, cards and backups are kept**: they live in
`%LOCALAPPDATA%\CustomerLedger`, separate from the program, and the new version updates
that data automatically the first time it opens. Nothing needs to be exported first, but
it doesn't hurt to click **Manage Backups → Backup Now** in the old version before
updating.

After the first launch of the new version:
- If a **yellow notice** says some items or cards "may have the wrong date", click
  **Review**. Older versions saved anything added after 5 PM (4 PM in winter) with the
  next day's date. The review window shows each one with its current and corrected date;
  untick anything that looks wrong, then click **Fix selected dates**. A backup is taken
  first ("Before date fix" in Manage Backups), so it can be undone. Cards listed as
  "Can't tell" should be compared with the paper card before ticking them.
- Backups made by the old version still show in **Manage Backups** and can still be
  restored.

### 4. First launch — what to check
- The app should open in its own window (not a browser tab) and land directly on the
  customer list — that's the whole app, nothing else to start.
- If the window never appears: this machine likely needs the WebView2 Runtime (see the
  note below). If it appears but stays blank, another program on the computer may be
  using port 8000, which TARJETAS needs.
- Create one test customer, add a test item, confirm it saves. Then open the test card
  and click **Delete customer** (top right) to remove it — a backup is taken first.

### 5. Sample data (optional, for trying the app — not for the shop)
- `seed_customers.py` + `customers_seed.json` add a few made-up customers to a running
  app. The target computer would need Python installed to run it.
- `seed_demo_data.py` fills a **development** copy (`backend/ledger.db`) with ~120
  made-up customers and ~2,000 entries for practice. It doesn't affect an installed app.

The database is created fresh on first launch on a new computer, so seeded data doesn't
travel with the installer.

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
- **Automatic backups**: while the app is open, one backup every hour from 10:30 AM to
  7:00 PM, only when something changed. At the end of each day that day's backups are
  combined into one (the last one), unless a restore, a date fix or a customer delete
  happened that day — then all of that day's backups are kept. Backups older than 30 days
  are removed.
- **Backup Now**: inside **Manage Backups** (no longer in the top bar). Use it anytime,
  for example before a big change.
- **Restoring a backup**: click **Manage Backups**, find the one you want by date/time,
  click **Restore**. It asks before doing anything and saves your current data as a fresh
  backup first ("Before restore"), so a restore is itself undoable if you pick the wrong
  one. Backups labeled "Before date fix" or "Before delete" are the automatic safety
  copies taken right before those actions.
- **Deleting a customer**: open the card, click **Delete customer**. A "Before delete"
  backup is taken first; restore it to bring the customer back.
- **Uninstalling**: Windows Settings → Apps → TARJETAS → Uninstall (or the
  Start Menu shortcut Inno Setup also creates). This removes the program, not the data
  in `%LOCALAPPDATA%\CustomerLedger`.
