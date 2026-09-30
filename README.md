# hoisty

Runs Ansible playbooks and pyinfra deploys from Slack or a web UI, with every
variable, SSH key and token held in an encrypted store that only a human-typed
passphrase can open.

## Security

Full analysis, residual risks and deployment requirements: [THREAT-MODEL.md](THREAT-MODEL.md).

## Requirements

- Ubuntu 24.04 LTS or newer. Only LTS releases are supported; the package
  vendors a virtualenv built against that release's Python, so build one .deb
  per release.
- git, openssh-client, and a Slack app with Socket Mode. Ansible and pyinfra
  ship inside the package.
- Target hosts reachable over SSH from the deploy box.

## Install & Packages

```
sudo apt install ./hoisty_0.1.0_amd64.deb
```

Installed to `/var/lib/hoisty`: `app/` and `venv/` root-owned,
`data/`, `backups/` and `projects/` owned by the `hoisty` service account. The
CLI is `/usr/bin/hoisty`; it points at the packaged store automatically.

```
# 1. create the store and the first admin (as the service account)
sudo -u hoisty hoisty init

# 2. a project, its deploy key, its hosts and its variables
sudo -u hoisty hoisty project-add myproj --dir /var/lib/hoisty/projects/myproj \
    --remote https://github.com/org/myproj.git --branch main
sudo -u hoisty hoisty cred-gen deploy-key --project myproj   # prints the public key
sudo -u hoisty hoisty host-add myproj web1 --address 10.0.0.5 --groups web \
    --key "$(ssh-keyscan -t ed25519 10.0.0.5 2>/dev/null | cut -d' ' -f2-)"
sudo -u hoisty hoisty secret-set db_password --project myproj --env prod
sudo -u hoisty hoisty sync

# 3. must print "no problems found" before anything is enabled
sudo -u hoisty hoisty doctor

# 4. start. Both daemons come up locked and do nothing until unlocked.
systemctl enable --now hoisty@bot hoisty@web
sudo hoisty unlock
```

Environments are created in the web UI (admin). Every user registers 2FA on
first web login by typing the shown secret into their authenticator. Reach the
web UI over `ssh -L 8080:127.0.0.1:8080 deploybox`.

## Unlocking & Global Password

The global password is never stored, so the daemons cannot start themselves
after a reboot. They come up *locked* and completely inert, which is safe
because the Slack tokens are inside the encrypted store too:

```
$ systemctl status hoisty@bot
  Active: active (running)
  Status: "locked - waiting for: hoisty unlock"

$ sudo hoisty unlock
Global password: ********
ok

$ hoisty status
unlocked
```

`hoisty unlock` talks to `/run/hoisty/unlock.sock`. Only uid 0 may drive it: the
socket is 0600 inside a 0700 directory, and the daemon checks the peer's
credentials as well, because a playbook that goes bad runs as the service
account. The passphrase never touches a tty, a file or a command line.

## Running from a checkout

For development, without the package:

```
make setup                     # ./venv from the hash-pinned requirements.txt
make init                      # data/, both databases, the first admin
make bot                       # starts locked
python manage.py unlock        # in a second shell
make web                       # 127.0.0.1:8080
make test                      # every test file, one process each
make deb                       # build the package (needs network and dpkg-dev)
```

## Production notes

`doctor` only reports, it changes nothing. It passes when the service account
owns `data/`, `backups/` and `projects/` (0700) and nothing else: `app/` and
`venv/` belong to root, so a playbook that goes bad cannot rewrite the program
that is next handed the passphrase. The unit enforces the same at runtime with
`ProtectSystem=strict`, which mounts everything except those three paths
read-only for the running daemon, and `dpkg -V hoisty` (which `doctor` runs)
reports any packaged file that has changed since install.

Findings doctor still lists after this are host posture (swap on disk, IOMMU
off, unmitigated CPU bugs) and need a kernel or BIOS change, not a chmod.

## Slack app

Socket Mode on. Bot token scopes: `chat:write`, `app_mentions:read`,
`groups:write`. Event subscription: `app_mention`. Store the tokens:

```
python manage.py secret-set slack_app_token      # xapp-...
python manage.py secret-set slack_bot_token      # xoxb-...
python manage.py user-add alice --slack-id U0123456 --admin
```

## CLI Commands

| Command | Purpose |
|---|---|
| `init`, `migrate`, `doctor` | Create the store, apply migrations, check the host |
| `bot`, `web [--tls-cert --tls-key]` | Run the daemons (both start locked) |
| `unlock`, `status` | Give the running daemon the password; ask if it is locked |
| `user-add`, `user-list`, `user-reset-2fa` | Web and Slack users |
| `secret-set`, `secret-list`, `vars-import`, `vars-export` | Variables per scope (`--project`, `--env`) |
| `cred-set`, `cred-gen`, `cred-list`, `cred-rm` | SSH keys and GitHub PATs |
| `project-add`, `project-list`, `sync` | Repos and checkouts |
| `host-add <project> <name> [--address --groups --key]`, `host-list`, `host-rm` | A project's target hosts |
| `backup`, `import <zip>`, `rekey` | Sealed backup; replace data/ with a zip (backs up first); passphrase change |
| `schedule-add`, `schedule-list` | Daily backup, git pull or prune at HH:MM |

## Tests

```
make test              # every file, one process each
make test T=web        # one file: auth, backup, credentials, crypto, deploy,
                       # migrate, unlock, variables, web
```

`test_deploy.py` runs a real playbook against localhost and scans `/proc` for
leaked secrets, stray ssh-agents and orphaned forks. Layout and invariants for
contributors: `AGENTS.md`.
