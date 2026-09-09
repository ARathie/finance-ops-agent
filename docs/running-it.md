# Running it day to day

This is the operator's page: how the agent is installed so it runs by itself, what it writes down, how backups work, how someone finds out when it stops, and what to do when something looks wrong.

There are two stages, and the difference matters:

- **Stage 1 — the real-life test on a Mac.** Temporary. Good for one or two billing cycles in dry run while Kevin checks the agent's work. It depends on that Mac staying on, awake, and logged in, which is exactly why it is not the real deployment.
- **Stage 2 — production on a server.** The agent runs as a container on a small always-on Linux server (or any container host with a persistent disk), with backups off the machine and a heartbeat that tells someone when it stops. Nothing depends on anybody's personal computer (decision 22).

## What "running" means

One run is one `fops run`. It reads whatever new mail has arrived, does the checks, writes down anything it is going to send, and sends it. A run takes seconds when there is nothing new. Nothing is left half-done: if a run stops in the middle, the next one picks up exactly where it stopped and does not send anything twice (the one rare exception is explained in `emails.md`: it asks Kevin rather than guessing).

The schedule is **every 15 minutes**. Two runs can never overlap: each run takes a lock on `data/run.lock` first, and a run that finds the lock held says so and stops, leaving the work to the next one. The lock belongs to the process, so a run that is killed outright still releases it; there is no such thing as a stuck lock to clean up by hand.

`fops serve` (PR 14) is the long-running form: one process that does a run every 15 minutes, a backup once a night, and a heartbeat check-in after each. It is what the container runs in stage 2. In stage 1, launchd runs `fops run` on a timer instead.

The mode comes from `FOPS_MODE` in `.env`. **`dry_run` is the stop button.** While it is set to `dry_run`, nothing can be sent to a client and nothing can be created in QuickBooks, not even with a command-line flag asking for it. To stop the agent acting, set `FOPS_MODE=dry_run` and the next run is harmless; you do not have to uninstall anything.

## Stage 1 — the real-life test on a Mac (temporary)

### The first cycle, step by step

1. `uv sync`. Copy `.env.example` to `.env` and fill in: `FOPS_MODE=dry_run`, `FOPS_TIMEZONE`, `FOPS_ENGAGEMENT_LIST`, `FOPS_ADMIN_EMAIL`, `FOPS_AGENT_MAILBOX`, the `MAIL_*` settings for the agent's mailbox at Rackspace, `MAIL_START_DATE`, and `ANTHROPIC_API_KEY`.
2. Kevin's engagement list, filled in from `templates/engagements-template.xlsx`, at the path in `FOPS_ENGAGEMENT_LIST`.
3. `uv run fops doctor --send-test-email`: every line passes and Kevin gets the test email.
4. Load the launchd job below, `launchctl kickstart` it, then `uv run fops status`.
5. Kevin tells consultants to send their timesheets to the agent's address.
6. For the whole cycle, Kevin compares each "timesheet received" and "would invoice" email to what he did by hand, answers reviews by replying, and keeps the tally in `first-cycle.md`.
7. When stage 2 is up, `launchctl bootout` the job so only one agent is running.

### The launchd job

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

Four things about the Mac that are easy to get wrong, and the reason stage 1 is temporary:

- **A LaunchAgent only runs while the user is logged in.** Leave the Mac logged in. Locking the screen is fine; logging out is not.
- **A sleeping Mac does not run anything.** In System Settings → Displays → Advanced (or Energy Saver on older versions) prevent sleeping when the display is off, and leave it plugged in. launchd runs a missed 15-minute interval once on wake rather than catching up on all of them.
- **`uv` is not on launchd's `PATH`.** Give the full path to `uv` and set `PATH` in the plist, as above; `/opt/homebrew/bin` is Apple silicon, `/usr/local/bin` an Intel Mac.
- **The settings come from `.env` in the working directory**, not from the shell. Anything set in `~/.zshrc` is invisible to launchd.

## Stage 2 — production on a server

### What it needs

- **A small always-on Linux server.** 1 vCPU, 1 GB of memory, and 20 GB of disk is plenty. Ubuntu LTS. Rented from a cloud provider (DigitalOcean, Hetzner, AWS Lightsail, and Azure all work; which one is an open question). Anything that runs Docker and keeps its disk works. Not a laptop, not a desktop, not anyone's personal machine.
- **Docker**, with Compose.
- **Somewhere off the server for backups:** an object-storage bucket (Backblaze B2, AWS S3, DigitalOcean Spaces, or the like) that the business controls, with `rclone` on the server configured to reach it.
- **A heartbeat check:** a free healthchecks.io check (or similar) that expects a ping every 15 minutes with a 45-minute grace period and emails Ash and Kevin when it does not get one. Its URL goes in `FOPS_HEARTBEAT_URL`.
- **Outbound network only.** The agent makes outgoing connections to Rackspace (IMAP and SMTP), Anthropic, QuickBooks, the heartbeat service, and the backup bucket. It listens on no port. The firewall allows SSH in and nothing else, and SSH is by key only. Turn on unattended security updates.

### Install

```
# on the server, as a user in the docker group
git clone https://github.com/ARathie/finance-ops-agent.git && cd finance-ops-agent
cp .env.example .env && chmod 600 .env       # fill in every setting; FOPS_DATA_DIR=/data
mkdir -p engagements && cp <the workbook> engagements/engagements.xlsx
docker compose pull                          # the image CI built for the latest release tag
docker compose up -d
docker compose exec fops fops doctor         # every line must pass
docker compose logs -f                       # watch the first runs
```

`docker-compose.yml` (in the repo) defines one service, `fops`, running `fops serve` with `restart: unless-stopped`, `env_file: .env`, a named volume mounted at `/data`, the engagement list mounted read-only from `./engagements`, and the rclone configuration mounted read-only.

### The engagement list on a server

Kevin edits the workbook on his own computer. Getting it to the server is an open question with a default: a shared folder (OneDrive, Dropbox, or Google Drive) that the server pulls from with `rclone` before every run, into the mounted path. The fallback is a copy by hand with `scp` after each change. Either way the agent re-reads the list every run and emails Kevin if a row is wrong, so a broken edit is caught within 15 minutes.

### Updates

A release is a git tag. CI builds the image and publishes it. On the server: `docker compose pull && docker compose up -d && docker compose exec fops fops doctor`. Database migrations run when the container starts.

### Secrets

`.env` lives on the server, readable by root and the docker user only, never in the image and never in git. QuickBooks tokens live in the data volume. To rotate the mailbox password: change it at Rackspace, change `.env`, `docker compose up -d`.

### Knowing it is alive

The heartbeat is the first signal. If the agent has not checked in for 45 minutes (the server is down, the container crashed, Rackspace is unreachable, the disk is full) Ash and Kevin get an email from the check service. Nothing else can be relied on for this, because a dead agent cannot email anyone. The "something needs attention" emails cover the softer problems: QuickBooks needs reconnecting, a row in the engagement list is wrong, sends keep failing.

### Without Docker

If the server should run the agent directly, install `uv`, clone the repo, put `.env` in place, and use a systemd service that keeps `fops serve` running:

`~/.config/systemd/user/fops.service`:

```ini
[Unit]
Description=Icon Technologies billing agent
After=network-online.target

[Service]
WorkingDirectory=%h/finance-ops-agent
EnvironmentFile=%h/finance-ops-agent/.env
ExecStart=/usr/local/bin/uv run fops serve
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

```
loginctl enable-linger "$USER"      # keep user units running with nobody logged in
systemctl --user daemon-reload
systemctl --user enable --now fops.service
journalctl --user -u fops.service -n 50
```

## Logs

Every run appends JSON lines to `data/fops.log`, one object per line, with the run's counts, the item ids, the review codes, and the statuses. Deliberately **not** in the log: email bodies, attachment contents, rates, and amounts. Money belongs in the tracking sheet and in the emails, where Kevin can see it in context, not in a log file. The file is rotated daily and 30 days are kept; in stage 2, `docker compose logs` shows the same lines.

```
tail -f data/fops.log                                  # watch a run
grep '"what": "review opened"' data/fops.log            # everything the agent asked about
```

## Backups

`fops backup` writes `data/backups/fops-backup-YYYY-MM-DD.zip` containing everything the agent cannot rebuild: the database, the stored timesheets and invoice PDFs, the QuickBooks tokens, and the mailbox position. It checkpoints the database first, so the copy is complete rather than missing the last few minutes of work.

- In stage 2, `fops serve` runs the backup nightly and uploads it to the bucket in `FOPS_BACKUP_TARGET`; daily backups are kept 90 days and the first of each month forever (financial records). In stage 1, copy the zip somewhere off the Mac; if that is OneDrive or SharePoint, turn **Files On Demand off** for that folder, or the "backup" is a placeholder that points at the machine you are backing up.
- **Try a restore now and then**, say once a quarter, on a scratch machine. `fops restore <zip>` unpacks into an empty data folder and refuses to overwrite one that is not empty (`--force` if you mean it). A backup nobody has restored is not a backup.

## Warnings that need a person

- **The QuickBooks connection ages out.** The refresh token lasts about 100 days; after 80 the end of every run prints a warning saying how many days are left. Run `fops qbo-connect` to renew it. If it does expire, the agent keeps working on everything except QuickBooks and reports the failures as reviews.
- **A review email from the agent** always means it wants Kevin, not the operator.
- **Sends that keep failing** become a `SEND_FAILED` review after three attempts across runs. Check the mailbox login with `fops doctor`.
- **A heartbeat alert** means the agent has not run for 45 minutes. See below.

## When something looks wrong

1. `fops doctor` (in stage 2: `docker compose exec fops fops doctor`): settings, engagement list, database, the mailbox login, QuickBooks, the heartbeat URL, the backup target. Sends nothing to a client.
2. `fops status`: every item, its status, and the open reviews.
3. `tail -50 data/fops.log`, or `docker compose logs --tail 50`: what the last runs did.
4. Nothing in the log for an hour, or a heartbeat alert? In stage 2: `docker compose ps` (is the container up?), disk space (`df -h`), and whether Rackspace is reachable from the server. In stage 1: the Mac went to sleep, logged out, or the launchd job is not loaded (`launchctl print`).
5. If you need the agent to stop acting immediately, set `FOPS_MODE=dry_run` in `.env` and restart (`docker compose up -d`, or just wait for the next launchd run). The next run reads and reports but sends nothing.
