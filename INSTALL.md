# Installing the monitor on a machine

How to run `tools/monitor.py` as a service on a headless Linux host, so the
display shows the machine's status automatically and reconnects on its own.

Prerequisites: the ESP32 is **already flashed** with the firmware (see the
README — flashing needs PlatformIO and can be done on any machine), and is
plugged into the host via USB.

## 1. Dependencies

The daemon only needs Python 3 and `pyserial`:

```bash
sudo apt install python3-serial      # Debian/Ubuntu
# or:  pip install pyserial
```

## 2. Serial port permission

The user that runs the daemon must be able to open the serial device. Add it to
the `dialout` group and log out/in:

```bash
sudo usermod -aG dialout "$USER"
```

## 3. Stable device name (recommended)

`/dev/ttyACM0` can change number if there are several USB-serial devices. Create
a udev rule so the ESP32-C3 always gets a fixed symlink `/dev/display-ctl`:

```bash
sudo tee /etc/udev/rules.d/99-display-ctl.rules >/dev/null <<'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", \
  SYMLINK+="display-ctl", GROUP="dialout", MODE="0660"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Replug the USB cable; `/dev/display-ctl` should now exist.

## 4. Install the files

```bash
sudo mkdir -p /opt/server-display
sudo cp -r tools /opt/server-display/         # monitor.py + displayctl.py
```

Quick test (Ctrl+C to stop; it exits with LED/buzzer off):

```bash
python3 /opt/server-display/tools/monitor.py /dev/display-ctl
```

## 5. systemd service (run on boot)

```bash
sudo tee /etc/systemd/system/display-monitor.service >/dev/null <<EOF
[Unit]
Description=display-ctl host monitor
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 /opt/server-display/tools/monitor.py /dev/display-ctl
Restart=always
RestartSec=3
User=$USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now display-monitor.service
```

> If you skipped step 3, use `/dev/ttyACM0` instead of `/dev/display-ctl`.
> `Restart=always` makes the service come back if the USB re-enumerates.

## 6. Verify

```bash
systemctl status display-monitor.service
journalctl -u display-monitor.service -f
```

## Options

Pass extra flags in `ExecStart`:

| Flag | Meaning |
|------|---------|
| `--page-secs N` | seconds each page stays on screen (default 8) |
| `--refresh N` | refresh interval within a page (default 1) |
| `--disk PATH` | filesystem to report (default `/`) |
| `--no-alerts` | do not drive LED/buzzer/alert images |

Alert thresholds live in the `THRESH` dict at the top of `tools/monitor.py`.

## Uninstall

```bash
sudo systemctl disable --now display-monitor.service
sudo rm /etc/systemd/system/display-monitor.service /etc/udev/rules.d/99-display-ctl.rules
sudo rm -rf /opt/server-display
```
