# Running it day to day

This is the operator's page: how the agent is installed so it runs by itself, what it writes down, how backups work, and what to do when something looks wrong. The Mac is the machine this actually runs on; the Linux instructions are for a server if it ever moves to one.

## What "running" means

One run is one `fops run`. It reads whatever new mail has arrived, does the checks, writes down anything it is going to send, and sends it. A run takes seconds when there is nothing new. Nothing is left half-done: if a run stops in the middle, the next one picks up exactly where it stopped and never sends anything twice.

The schedule is **every 15 minutes**. Two runs can never overlap: each run takes a lock on `data/run.lock` first, and a run that finds the lock held says so and stops, leaving the work to the next one. The lock belongs to the process, so a run that is killed outright still releases it — there is no such thing as a stuck lock to clean up by hand.

The mode comes from `FOPS_MODE` in `.env`. **`dry_run` is the stop button.** While it is set to `dry_run`, nothing can be sent to a client and nothing can be created in QuickBooks — not even with a command-line flag asking for it. To stop the agent acting, set `FOPS_MODE=dry_run` and the next run is harmless; you do not have to uninstall anything.

## On the Mac (launchd)

Two jobs: the run every 15 minutes, and a backup once a night.

`~/Library/LaunchAgents/com.icontechnologies.fops.run.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.icontechnologies.fops.run</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/uv</string>
    <string>run</string>
    <string>fops</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key> <string>/Users/kevin/finance-ops-agent</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key> <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartInterval</key>    <integer>900</integer>
  <key>RunAtLoad</key>        <true/>
  <key>StandardOutPath</key>  <string>/Users/kevin/finance-ops-agent/data/launchd.out.log</string>
  <key>StandardErrorPath</key><string>/Users/kevin/finance-ops-agent/data/launchd.err.log</string>
</dict>
</plist>
```

The nightly backup is the same file with `Label` `com.icontechnologies.fops.backup`, `fops backup` instead of `fops run`, and `StartCalendarInterval` (`Hour` 2, `Minute` 0) instead of `StartInterval`.

Load, check, and run one now:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.icontechnologies.fops.run.plist
launchctl print gui/$(id -u)/com.icontechnologies.fops.run
launchctl kickstart -k gui/$(id -u)/com.icontechnologies.fops.run
```

To stop it: `launchctl bootout gui/$(id -u)/com.icontechnologies.fops.run`.

Four things about the Mac that are easy to get wrong:

- **A LaunchAgent only runs while the user is logged in.** Leave the Mac logged in. Locking the screen is fine; logging out is not.
- **A sleeping Mac does not run anything.** In System Settings → Displays → Advanced (or Energy Saver on older versions) prevent sleeping when the display is off, and leave it plugged in. launchd runs a missed 15-minute interval once on wake rather than catching up on all of them.
- **`uv` is not on launchd's `PATH`.** Give the full path to `uv` and set `PATH` in the plist, as above; `/opt/homebrew/bin` is Apple silicon, `/usr/local/bin` an Intel Mac.
- **The settings come from `.env` in the working directory**, not from the shell. Anything set in `~/.zshrc` is invisible to launchd.

Then confirm it really works, in this order: `uv run fops doctor` (checks every credential and mailbox permission and sends nothing to a client), `launchctl kickstart` as above, and `uv run fops status`.

## On a Linux server (systemd)

`~/.config/systemd/user/fops.service`:

```ini
[Unit]
Description=Icon Technologies billing agent, one run

[Service]
Type=oneshot
WorkingDirectory=%h/finance-ops-agent
EnvironmentFile=%h/finance-ops-agent/.env
ExecStart=/usr/local/bin/uv run fops run
```

`~/.config/systemd/user/fops.timer`:

```ini
[Unit]
Description=Run the billing agent every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

```
loginctl enable-linger "$USER"      # keep user units running with nobody logged in
systemctl --user daemon-reload
systemctl --user enable --now fops.timer
systemctl --user list-timers fops.timer
journalctl --user -u fops.service -n 50
```

A second pair, `fops-backup.service` and `fops-backup.timer` with `OnCalendar=02:00`, does the nightly backup.

## Logs

Every run appends JSON lines to `data/fops.log` — one object per line, with the run's counts, the item ids, the review codes, and the statuses. Deliberately **not** in the log: email bodies, attachment contents, rates, and amounts. Money belongs in the tracking sheet and in the emails, where Kevin can see it in context, not in a log file.

```
tail -f data/fops.log                                  # watch a run
grep '"what": "review opened"' data/fops.log            # everything the agent asked about
```

## Backups

`fops backup` writes `data/backups/fops-backup-YYYY-MM-DD.zip` containing everything the agent cannot rebuild: the database, the stored timesheets and invoice PDFs, the QuickBooks tokens, and the mailbox cursor. It checkpoints the database first, so the copy is complete rather than missing the last few minutes of work.

- Copy the zip somewhere off this machine. If that is OneDrive or SharePoint, turn **Files On Demand off** for that folder, or the "backup" is a placeholder that points at the machine you are backing up.
- **Try a restore now and then** — say once a quarter. `fops restore <zip>` unpacks into an empty data folder and refuses to overwrite one that is not empty (`--force` if you mean it). A backup nobody has restored is not a backup.

## Warnings that need a person

- **The QuickBooks connection ages out.** The refresh token lasts about 100 days; after 80 the end of every run prints a warning saying how many days are left. Run `fops qbo-connect` to renew it. If it does expire, the agent keeps working on everything except QuickBooks and reports the failures as reviews.
- **A review email from the agent** always means it wants Kevin, not the operator.
- **Sends that keep failing** become a `SEND_FAILED` review after three attempts across runs. Check the mailbox and the Microsoft credentials with `fops doctor`.

## When something looks wrong

1. `uv run fops doctor` — settings, engagement list, database, mailbox permissions, QuickBooks. Sends nothing to a client.
2. `uv run fops status` — every item, its status, and the open reviews.
3. `tail -50 data/fops.log` — what the last runs did.
4. Nothing at all in the log for hours? The schedule is not firing: check `launchctl print` (or `systemctl --user list-timers`), that the user is still logged in, and that the machine is not asleep.
5. If you need the agent to stop acting immediately, set `FOPS_MODE=dry_run` in `.env`. The next run will read and report but send nothing.
