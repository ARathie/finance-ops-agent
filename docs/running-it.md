# Running it day to day

This is the operator's page: how the agent is installed so it runs by itself, what it writes down, how backups work, how someone finds out when it stops, and what to do when something looks wrong.

There are two stages, and the difference matters:

- **Stage 1 — the real-life test on a Mac.** Temporary. Good for one or two billing cycles in dry run while Kevin checks the agent's work. It depends on that Mac staying on, awake, and logged in, which is exactly why it is not the real deployment.
- **Stage 2 — production on a server.** The agent runs as a container on a small always-on Linux server (or any container host with a persistent disk), with backups off the machine and a heartbeat that tells someone when it stops. Nothing depends on anybody's personal computer (decision 22).

## What "running" means

One run is one `fops run`. It reads whatever new mail has arrived, does the checks, writes down anything it is going to send, and sends it. A run takes seconds when there is nothing new. Nothing is left half-done: if a run stops in the middle, the next one picks up exactly where it stopped and does not send anything twice (the one rare exception is explained in `emails.md`: it asks Kevin rather than guessing).

The schedule is **every 15 minutes**. Two runs can never overlap: each run takes a lock on `data/run.lock` first, and a run that finds the lock held says so and stops, leaving the work to the next one. The lock belongs to the process, so a run that is killed outright still releases it; there is no such thing as a stuck lock to clean up by hand.

`fops serve` (PR 14) is the long-running form: one process that does a run every 15 minutes, a backup once a night, and a heartbeat check-in after each. It is what the container runs in stage 2. In stage 1, launchd runs `fops run` on a timer instead.

The mode comes from `FOPS_MODE` in `.env`. Production runs with `FOPS_MODE=ask_first` (decision 57): every invoice waits for Kevin's "approve". **`dry_run` is the stop button.** While it is set to `dry_run`, nothing can be sent to a client and nothing can be created in QuickBooks, not even with a command-line flag asking for it. To stop the agent acting, set `FOPS_MODE=dry_run` and the next run is harmless; you do not have to uninstall anything.

## Getting it onto a machine

Both stages need the same two things on the machine that will run the agent: the code, and a `.env` file holding the settings and the passwords.

The code comes from GitHub and is the same everywhere. **`.env` never does.** It is made by hand on each machine, it holds the mailbox password and the Anthropic key, and it is never committed: the repository is public, and `.gitignore` refuses to upload it. What is committed is `.env.example`, the blank template with every setting listed and every secret left empty, so copying it is always the first move.

Every `fops` command reads `.env` from the folder it runs in, so copying the template and filling it in is all that is needed to run one by hand. Anything already set in the environment wins over the file, which is what lets launchd, `env_file`, and systemd supply the settings in the two stages below, and what keeps `FOPS_MODE=dry_run fops run` working as an override.

So the key lives in as many places as there are machines running the agent, and nowhere else. Today that is the Mac; from stage 2 it is the server; while both run, each has its own copy. Rotating the key means changing it in each `.env` and restarting.

On a Mac, starting from nothing:

```
# 1. The tools: Homebrew, then git and uv.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git uv

# 2. The code.
git clone https://github.com/ARathie/finance-ops-agent
cd finance-ops-agent
uv sync

# 3. The settings, including the key.
cp .env.example .env
chmod 600 .env    # only this user can read it
open -e .env      # fill in ANTHROPIC_API_KEY and the rest, save, close

# 4. Prove it.
uv run fops doctor
```

`fops doctor` prints one line per check, names anything still missing, and sends nothing to anybody.

**Reading the mailbox again from a date.** A run looks at mail that arrived on or after `MAIL_START_DATE` *and* that it has not already walked past, so moving the date alone does nothing about older mail. `uv run fops dry-run --since 2026-09-09` moves both: it reads from that date and forgets how far it had got. Nothing is handled or sent twice -- a message already stored is skipped by its Message-ID -- but it only sees the **inbox**, so anything the agent has already filed into `Agent/Processed`, `Agent/Needs Review` or `Agent/Ignored` has to be dragged back to the inbox first. `ok claude api: ... answers` means the key works. On the server it is the same idea with Docker instead of `uv`; stage 2 below has the commands.

## Stage 1 — the real-life test on a Mac (temporary)

### The first cycle, step by step

1. `uv sync`. Copy `.env.example` to `.env` and fill in: `FOPS_MODE=dry_run`, `FOPS_TIMEZONE`, `FOPS_ENGAGEMENT_LIST`, `FOPS_ADMIN_EMAIL`, `FOPS_AGENT_MAILBOX`, the `MAIL_*` settings for the agent's mailbox at Rackspace, `MAIL_START_DATE`, and `ANTHROPIC_API_KEY`.
2. Kevin's engagement list, filled in from `templates/engagements-template.xlsx`, at the path in `FOPS_ENGAGEMENT_LIST`.
3. `uv run fops doctor --send-test-email`: every line passes and Kevin gets the test email.
4. Load the launchd job below, `launchctl kickstart` it, then `uv run fops status`.
5. Kevin tells consultants to send their timesheets to the agent's address.
6. For the whole cycle, Kevin compares each "timesheet received" and "would invoice" email to what he did by hand, answers reviews by replying, and keeps the tally in `first-cycle.md`.
7. When stage 2 is up, `launchctl bootout` the job so only one agent is running.

Going straight to stage 2 (decision 71) skips all of this: steps 1 to 3 and 5 to 6 are the same on the server, with `docker compose exec fops` in front of each `fops` command.

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

This is where the agent runs for real, and where the first cycle can run too (decision 71): set `FOPS_MODE=dry_run` and it is the same test as stage 1 without the Mac. Kevin installs nothing. He reads and answers the agent's emails on his laptop or phone, exactly as he would with the Mac.

### What it needs

- **A small always-on Linux server.** 1 vCPU, 1 GB of memory, and 20 GB of disk is plenty. Ubuntu LTS. Rented from a cloud provider (DigitalOcean, Hetzner, AWS Lightsail, and Azure all work; which one is an open question). Anything that runs Docker and keeps its disk works. Not a laptop, not a desktop, not anyone's personal machine.
- **Docker**, with Compose.
- **Somewhere off the server for backups:** an object-storage bucket (Backblaze B2, AWS S3, DigitalOcean Spaces, or the like) that the business controls. The image carries `rclone`; you give it the bucket's keys once (below).
- **A heartbeat check:** a free healthchecks.io check (or similar) with a 15-minute period and a 45-minute grace period that emails Ash and Kevin when the pings stop. Its ping URL goes in `FOPS_HEARTBEAT_URL`.
- **Outbound network only.** The agent makes outgoing connections to Rackspace (IMAP and SMTP), Anthropic, QuickBooks, the heartbeat service, and the backup bucket. It listens on no port. The firewall allows SSH in and nothing else, and SSH is by key only. Turn on unattended security updates.

### The server, once

On a fresh Ubuntu LTS server, as a user that can `sudo`:

```
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 unattended-upgrades git rclone
sudo dpkg-reconfigure -plow unattended-upgrades     # answer Yes
sudo usermod -aG docker "$USER"                     # then log out and back in
sudo ufw allow OpenSSH && sudo ufw enable           # SSH in, nothing else
```

In `/etc/ssh/sshd_config` make sure `PasswordAuthentication no` is set, then `sudo systemctl restart ssh`. Note the server's public IP address: it goes into the Intuit app profile ("where your app is hosted").

### Install

```
git clone https://github.com/ARathie/finance-ops-agent.git && cd finance-ops-agent
cp .env.example .env && chmod 600 .env       # fill in every setting (FOPS_DATA_DIR is set by Compose)
mkdir -p engagements rclone
cp <the workbook> engagements/engagements.xlsx  # manual mode only; see below
rclone config --config rclone/rclone.conf    # one remote for the bucket, e.g. named b2
sudo chown -R 10001 rclone                   # the container's user reads it; nobody else
docker compose pull                          # the image CI built for the latest release tag
docker compose up -d
docker compose exec fops fops doctor         # every line must pass
docker compose logs -f                       # watch the first runs
```

If `docker compose pull` says the image is private or not found, either make the package public on GitHub (the repository's **Packages** → `finance-ops-agent` → Package settings → Change visibility), or log in once with `docker login ghcr.io` and a token that can read packages. `docker compose build` builds the same image from the code on the server instead.

`docker-compose.yml` (in the repo) defines one service, `fops`, running `fops serve` with `restart: unless-stopped`, `env_file: .env`, a named volume `fops-data` mounted at `/data` (the database, stored timesheets, invoice PDFs, QuickBooks tokens, `fops.log`, and the last week of backups), the engagement list mounted read-only from `./engagements`, and the rclone configuration mounted read-only from `./rclone`. Compose fixes `FOPS_DATA_DIR=/data` and `FOPS_ENGAGEMENT_LIST=/engagements/engagements.xlsx` whatever `.env` says. It gives the agent 60 seconds to stop before killing it, and keeps Docker's own log to five 10 MB files.

The settings that are only about the server, in `.env`:

| Setting | What it is | Default |
|---|---|---|
| `FOPS_RUN_EVERY_MINUTES` | Minutes between runs | 15 |
| `FOPS_BACKUP_AT` | The nightly backup, in Icon's time (`FOPS_TIMEZONE`) | 02:30 |
| `FOPS_HEARTBEAT_URL` | The check service's ping URL; must be https. Blank: nobody is told if it stops | blank |
| `FOPS_BACKUP_TARGET` | The rclone remote and bucket, e.g. `b2:icon-fops-backups`. Blank: backups stay on the server only | blank |

`fops doctor` has a line for each of the last two: the heartbeat (it does not ping it, so running the doctor from anywhere else never makes a dead agent look alive), and the backup target (it lists the bucket, so a wrong key or name fails there and then).

### QuickBooks from the server

`docker compose exec fops fops qbo-connect` works over SSH: it prints the sign-in address, you open it on your own computer, and paste back the address the browser lands on (decision 51). The tokens are written to the data volume and backed up with everything else.

### What `fops serve` does

- **A run straight away, then one every `FOPS_RUN_EVERY_MINUTES`.** A run that takes longer than that is followed by one more at once, never by a queue of them. Each run takes the run lock, so a `docker compose exec fops fops run` by hand while it is going waits its turn rather than running alongside.
- **A backup once a day at `FOPS_BACKUP_AT`**, or as soon as it starts if today's was missed. The zip is written to `/data/backups` (the last seven are kept there), then copied to `FOPS_BACKUP_TARGET`, where daily backups are kept 90 days and the first of each month forever (financial records). A backup that fails is tried again an hour later.
- **A heartbeat ping after each run and backup that finished cleanly.** A failed run is logged (`"what": "run failed"`) and the service carries on; no ping is sent, so a run that keeps failing is noticed by the check service just as a stopped server is.
- **A clean stop.** `docker compose stop`, `docker compose up -d` after an update, and a server reboot all send SIGTERM. Between runs it stops at once. Mid-run it finishes the step in hand -- one email being handled, one email being sent -- and stops; nothing is left half-sent, and the next run carries on (decision 71).
- **Logs** go to `/data/fops.log` (rotated at midnight, 30 days kept) and to `docker compose logs`.

### The engagement list on a server

With QuickBooks Online connected (`FOPS_ACCOUNTING=quickbooks`) there is nothing to copy: the engagements come from QuickBooks alone (decision 70) and `./engagements` stays empty.

In manual mode Kevin edits the workbook on his own computer and it has to reach `engagements/engagements.xlsx` on the server. Until a shared folder is set up (open question: OneDrive, Dropbox, or Google Drive, pulled with `rclone` before every run), copy it by hand after each change, from his computer: `scp engagements.xlsx <user>@<server>:finance-ops-agent/engagements/`. The agent re-reads the list every run and emails Kevin if a row is wrong, so a broken edit is caught within 15 minutes.

### Updates

A release is a git tag (`git tag v0.2.0 && git push origin v0.2.0`). CI builds the image, checks it, and publishes it to GitHub Container Registry as both `0.2.0` and `latest`. On the server:

```
git pull                                     # docker-compose.yml and .env.example may have changed
docker compose pull && docker compose up -d
docker compose exec fops fops doctor
```

Database migrations run when the container starts. To pin a version rather than follow `latest`, add `FOPS_VERSION=0.2.0` to `.env`: Compose reads that file for the image tag too.

### Secrets

`.env` lives on the server, readable by its owner only (`chmod 600`), never in the image and never in git; `.dockerignore` keeps it out of every build and CI checks the image for one. The bucket's keys are in `rclone/rclone.conf`, readable by the container's user only. QuickBooks tokens live in the data volume. To rotate the mailbox password: change it at Rackspace, change `.env`, `docker compose up -d`.

### Knowing it is alive

The heartbeat is the first signal. If the agent has not checked in for 45 minutes (the server is down, the container crashed, Rackspace is unreachable, the disk is full, every run is failing) Ash and Kevin get an email from the check service, and another when it starts again. Nothing else can be relied on for this, because a dead agent cannot email anyone. The "something needs attention" emails cover the softer problems: QuickBooks needs reconnecting, a row in the engagement list is wrong, sends keep failing.

To prove it once: `docker compose stop`, wait an hour, see the alert arrive; `docker compose start`, see it clear.

### Restoring a server backup

On a scratch machine set up as under **Install** (the clone, a `.env` with `FOPS_MODE=dry_run`, and the bucket's `rclone/rclone.conf`), but without `docker compose up`:

```
rclone copy --config rclone/rclone.conf b2:icon-fops-backups/fops-backup-2026-11-03.zip .
docker compose run --rm -v "$PWD":/backup:ro fops fops restore /backup/fops-backup-2026-11-03.zip
docker compose run --rm fops fops status
```

`fops status` there should list the same items as `docker compose exec fops fops status` on the server. Never `up` the scratch copy: two agents on one mailbox could both send (`failure-modes.md` R3).

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

## Where the engagement list lives

`fops engagements` says which of the two the agent is reading -- the workbook at `FOPS_ENGAGEMENT_LIST`, or its own store -- and what is in it.

`fops engagements import` copies the workbook into the store, after which the agent does not open that file again (decision 40). A workbook with problems is refused rather than imported. `fops engagements forget` puts the agent back on the file, which is the way back if an import turns out to be wrong.

`fops doctor` names the source on its engagement list line, so a change to the workbook that seems to do nothing is explained rather than mysterious.

## The agent's mail folders

`Agent/Processed`, `Agent/Needs Review` and `Agent/Ignored` say what the agent made of each message: read and used, could not use, set aside. They are a courtesy for a person looking at the mailbox -- the database is the record -- and they do not track where an invoice has got to. A timesheet read cleanly is filed as processed before its invoice is made.

When something goes wrong afterwards, though, the email is moved back to `Needs Review` (decision 35), so a message in the processed folder never means "the invoice went out". To see where an invoice has got to, use `fops status`, the tracking sheet, or the Monday summary.

## Putting a timesheet through again (testing)

`fops forget` lists the timesheet items the agent is holding, with their ids. `fops forget <id>` takes one out along with everything about it -- its timesheets, invoices, payment instructions, reviews, outgoing rows and history -- **and the stored emails its timesheets came on**, because a message the agent has already stored is skipped on redelivery, so leaving them would mean the same email is never read again.

It is a testing tool. It refuses an item whose invoice actually reached the client (`--force` overrides), because forgetting it here changes nothing in QuickBooks or in the client's inbox and only loses the agent's own record. Invoices already made in the accounting system are named on the way out and have to be removed there by hand.

Two things it does not do. The timesheet file itself is still in whichever mailbox folder the agent moved it to (`INBOX.Agent.Processed`), so move it back to the inbox or forward a fresh copy to send it through again. And the stored attachment files under `data/` stay where they are: they are named by their contents, nothing points at them once the rows are gone, and deleting one another item shares would be worse than leaving an orphan.

Run the forget first, **then** move the email back. An email moved back while the agent still has it on record is skipped as already handled, and the agent reads past it. `fops diagnose` says when that has happened and what to do.

Take a backup first. `fops backup` writes `data/backups/fops-backup-YYYY-MM-DD.zip` containing everything the agent cannot rebuild: the database, the stored timesheets and invoice PDFs, the QuickBooks tokens, and the mailbox position. It checkpoints the database first, so the copy is complete rather than missing the last few minutes of work.

- In stage 2, `fops serve` runs the backup nightly and uploads it to the bucket in `FOPS_BACKUP_TARGET`; daily backups are kept 90 days and the first of each month forever (financial records). In stage 1, copy the zip somewhere off the Mac; if that is OneDrive or SharePoint, turn **Files On Demand off** for that folder, or the "backup" is a placeholder that points at the machine you are backing up.
- **Try a restore now and then**, say once a quarter, on a scratch machine. `fops restore <zip>` unpacks into an empty data folder and refuses to overwrite one that is not empty (`--force` if you mean it). A backup nobody has restored is not a backup.

## Warnings that need a person

- **A QuickBooks check says a field is missing that QuickBooks plainly shows.** `fops qbo-show customer "<name>"` (or `vendor`, `product`, `term`) prints the record exactly as QuickBooks returns it. It writes nothing and touches no client, and it is the quickest way to settle what a field is called and whether it is there.
- **A QuickBooks check fails.** Nothing on the server is wrong: something in QuickBooks itself is missing or does not match. `quickbooks-setup.md` has a section per check, with the exact line the doctor prints when that part is not right.
- **The QuickBooks connection ages out.** The refresh token lasts about 100 days; after 80 the end of every run prints a warning saying how many days are left. Run `fops qbo-connect` to renew it. If it does expire, the agent keeps working on everything except QuickBooks and reports the failures as reviews.
- **A review email from the agent** always means it wants Kevin, not the operator.
- **Sends that keep failing** become a `SEND_FAILED` review after three attempts across runs. Check the mailbox login with `fops doctor`.
- **A heartbeat alert** means the agent has not run for 45 minutes. See below.

## When something looks wrong

1. `fops doctor` (in stage 2: `docker compose exec fops fops doctor`): settings, engagement list, database, the mailbox login, QuickBooks, the heartbeat URL, the backup target. Sends nothing to a client. A failing QuickBooks check is something set up in the QuickBooks web app rather than anything on the server; `quickbooks-setup.md` says what each one wants, and the doctor names it on the way out.
2. `fops diagnose`: what is stuck and why, in plain words, with what to do about each one. It changes nothing. It covers:
   - invoices in the agent's records that QuickBooks does not have. This is what makes "could not check paid invoices" appear on every run.
   - who holds an invoice number QuickBooks refused as a duplicate: the agent's own invoice for a forgotten item, another item, or one made by hand.
   - emails in the inbox the next run will not read, and why: already handled, dated before the mail start date, or already read past.
   - items that match no engagement any more, and engagements waiting on suspiciously many months.

   `fops diagnose --item 6` shows everything about one item: its invoices, reviews, emails and history. `fops diagnose --number 083126MT-MK` says which QuickBooks invoice holds a number and whose work it is. `--no-mail` skips the inbox.
3. `fops status`: every item, its status, and the open reviews.

   The agent runs the same checks itself (decision 61). When something gets stuck, Kevin's review email says what it found and offers lettered options, and each `fops.log` line `investigated` or `investigation looked` records what it looked at. The investigator uses the same Anthropic key as the reader, at most three times a run, and only when a review is about to go out.
4. `tail -50 data/fops.log`, or `docker compose logs --tail 50`: what the last runs did.
5. Nothing in the log for an hour, or a heartbeat alert? In stage 2: `docker compose ps` (is the container up?), disk space (`df -h`), and whether Rackspace is reachable from the server. In stage 1: the Mac went to sleep, logged out, or the launchd job is not loaded (`launchctl print`).
6. If you need the agent to stop acting immediately, set `FOPS_MODE=dry_run` in `.env` and restart (`docker compose up -d`, or just wait for the next launchd run). The next run reads and reports but sends nothing.
