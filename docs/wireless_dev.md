# Wireless development: deploy, debug and watch the Pi from VS Code

No cables except power. Everything goes over SSH using the OpenSSH client built into
Windows 10/11, macOS and Linux.

## SSH config

Create or edit `~/.ssh/config` (Windows: `C:\Users\<you>\.ssh\config`, plain text,
no extension):

```
Host attendance-pi
    HostName attendance-pi.local        # or the Tailscale MagicDNS name, or an IP
    User utkarsh
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30
    ServerAliveCountMax 4
    LocalForward 5678 127.0.0.1:5678    # debugpy tunnel
```

Replace the alias, user and HostName with your Section 0 values, and put the same alias
and user in `.vscode/settings.json` (`attendance.piHost`, `attendance.piUser`): the VS
Code tasks and the debugger attach configuration read them from there.

Test: `ssh attendance-pi hostname`.

## When the campus WiFi gets in the way

Campus networks usually isolate clients and block mDNS, so `attendance-pi.local` may not
resolve and the laptop may not reach the Pi at all. Two fallbacks, both documented in
`scripts/setup_pi.sh` / docs/setup_pi.md:

1. **Tailscale** on the laptop, the Pi and the office computer (`sudo tailscale up` on the
   Pi, the installer on the laptop). Use the MagicDNS names (`attendance-pi`,
   `office-pc`) as `HostName`; traffic is WireGuard-encrypted and works across any
   network, including from home.
2. **Phone hotspot** that both the laptop and the Pi join (`connection.autoconnect-priority`
   makes the Pi fall back to it automatically). `.local` names work on a hotspot.

## VS Code tasks (Terminal → Run Task)

| Task | What it runs |
|---|---|
| `Pi: deploy` | `tools/deploy.py --host <alias>`: warns on uncommitted changes, `git push`, `git pull --ff-only` on the Pi, pip only if `device/requirements.txt` changed, restart, last 50 log lines |
| `Pi: deploy (rsync uncommitted changes)` | same with `--rsync` (needs rsync: WSL on Windows, native on macOS/Linux) |
| `Pi: restart app` | kills the app; the autostart wrapper restarts it in 2 s |
| `Pi: tail logs` | `tail -F ~/.local/state/attendance/device.log` |
| `Pi: open shell` | interactive `ssh` in a VS Code terminal |
| `Pi: run bench` | `tools/bench_pi.py` on the Pi (p50/p95 per stage, temperature, throttling) |

## Debugging the device app from the laptop

1. Start the app on the Pi with debugpy listening (it binds to 127.0.0.1 only):
   `ssh attendance-pi 'pkill -f device.app.main; cd ~/face-attendance && .venv/bin/python -m device.app.main --fullscreen --debug'`
   (or add `--debug` to the wrapper call in `~/.local/bin/run_device.sh` temporarily).
2. Make sure an SSH connection with the `LocalForward 5678` is open (any `Pi:` task or
   `ssh -N attendance-pi`).
3. Run and Debug → **Attach to Pi device app**. Path mappings translate
   `${workspaceFolder}` to `/home/<PI_USER>/face-attendance`, so breakpoints in your
   local files hit on the Pi.

## Remote-SSH

The Remote-SSH extension can open `~/face-attendance` directly on the Pi for quick
edits (`Remote-SSH: Connect to Host… → attendance-pi`). The 2 GB Pi has little RAM
headroom while the device app and the models are loaded: close the app (`pkill -f
device.app.main`) before heavy use, and prefer editing locally + `Pi: deploy` for
normal work.

## Seeing the touch screen remotely

Optional: `scripts/setup_pi.sh --connect` enables **Raspberry Pi Connect** (screen
sharing in the browser at connect.raspberrypi.com), or install `wayvnc` for VNC. Useful
to watch the UI during a demo rehearsal without standing next to the device.
