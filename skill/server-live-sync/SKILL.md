---
name: server-live-sync
description: Configure, verify, and troubleshoot safe real-time Syncthing mirrors from an explicit project on an SSH-accessible Linux server to a local Windows, macOS, or Linux computer. Use when a user asks Codex to mirror server code or logs locally, add another live-synced project, check synchronization health, or repair a project mirror without copying weights, archives, videos, caches, or virtual environments.
---

# 科研自动化——Server Live Sync

Configure one explicit project at a time. Keep the server folder `sendonly` and the local folder `receiveonly`.

## Configure a project

Collect four values from the request or environment:

- SSH host or alias, such as `gpu-box` or `alice@example.com`
- remote root containing projects, such as `/home/alice/projects`
- project path relative to that root, such as `vla`
- local root, defaulting to `~/code` only when the user does not specify one

Treat `~` as the current local user's home directory, not as a generic project drive. For example, `~/code/vla` normally resolves to `C:\Users\Alice\code\vla` on Windows and `/home/alice/code/vla` on Linux. Before changing anything, resolve and report the exact absolute local path. On Windows, prefer the user's explicit absolute path, such as `D:\Projects\vla`, when provided. The local directory and its final name may differ from the remote project name.

Run the bundled script from this skill directory. Always dry-run first:

```bash
python scripts/live_sync.py add --ssh-host gpu-box --remote-root /home/alice/projects --project vla --local-root ~/code --dry-run
```

If the plan is correct, run it without `--dry-run`:

```bash
python scripts/live_sync.py add --ssh-host gpu-box --remote-root /home/alice/projects --project vla --local-root ~/code
```

Use `--local-name NAME` only when the local directory name should differ. Use `--ignore-file PATH` to supply project-specific exclusions. Re-run the same command to verify an existing configuration.

## Prerequisites

Run `python scripts/live_sync.py doctor --ssh-host HOST` when setup fails. Require local Python 3.9+, SSH, SCP, and Syncthing, plus SSH access to a Linux server with Syncthing installed. Help the user install a missing prerequisite with their platform package manager, but do not silently invoke `sudo`.

The script pairs the two Syncthing devices when needed, enables the remote user service when systemd is available, configures local startup where supported, and stores non-secret project metadata under the user's config directory.

After configuration, inspect `local_startup`, `remote_startup`, and `remote_linger` in the result. Do not claim that reboot startup is ready unless the platform startup step succeeded. On a systemd server, require both `remote_startup=systemd-enabled` and `remote_linger=Linger=yes` for startup without an SSH login. If Linger is disabled, tell the user that an administrator can run `sudo loginctl enable-linger USER`; never run it silently. Explain that the Windows Startup folder launches Syncthing after the user signs in, not before login.

## Safety

- Reject absolute project paths and `..` traversal.
- Never mirror a home directory or broad remote root as a project.
- Never create a missing remote project unless the user explicitly requests it.
- Never overwrite an existing `.stignore`; report that defaults were not installed.
- Exclude model weights, archives, media, caches, dependencies, and virtual environments by default. Keep code and logs included.
- Never run `folder-override` or delete remote files.
- Stop on conflicting folder IDs, paths, or directions.
- Report low server disk space, scan errors, missing markers, or disconnected devices.

## Expected behavior

- Watch server changes after about two seconds; run a fallback full scan every five minutes.
- Catch up automatically after the local computer reconnects.
- Configure each new top-level project once. Do not auto-discover directories.
- Treat an initial transfer still in progress as a valid configured state, and report remaining items and bytes.

## Report

Report the exact remote and local paths, folder ID, both directions, exclusions, startup/service state, connection state, scan errors, remaining transfer size, and disk warning.
