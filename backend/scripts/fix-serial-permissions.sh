#!/usr/bin/env bash
# Permanent fix for "Permission denied" on /dev/ttyACM0 (or ttyUSB0) when the
# GRBL backend tries to open the serial port on Linux/Jetson.
#
# Does two things, both persistent across reboots and replugs:
#   1. Adds your user to the 'dialout' group (needed once, ever).
#   2. Installs a udev rule that grants 'dialout' read/write on any
#      ttyACM*/ttyUSB* device, independent of whether this distro's own
#      default rules already cover it.
#
# Usage: bash backend/scripts/fix-serial-permissions.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEEDS_RELOGIN=0

# ---- 1. Group membership ----
GROUP="dialout"
if ! getent group "$GROUP" >/dev/null 2>&1; then
  GROUP="uucp"  # some non-Debian distros (e.g. Arch) use this instead
fi

if ! getent group "$GROUP" >/dev/null 2>&1; then
  echo "Neither 'dialout' nor 'uucp' group exists on this system."
  echo "Check 'ls -l /dev/ttyACM0' (once the board is plugged in) for the actual owning group,"
  echo "then: sudo usermod -aG <that group> \$USER"
  exit 1
fi

if id -nG "$USER" | grep -qw "$GROUP"; then
  echo "OK: $USER is already in the '$GROUP' group."
else
  echo "Adding $USER to the '$GROUP' group..."
  sudo usermod -aG "$GROUP" "$USER"
  NEEDS_RELOGIN=1
fi

# ---- 2. udev rule, so permissions are correct on every future plug/reboot ----
RULE_SRC="$SCRIPT_DIR/99-grbl-serial.rules"
RULE_DST="/etc/udev/rules.d/99-grbl-serial.rules"

if [ -f "$RULE_DST" ] && cmp -s "$RULE_SRC" "$RULE_DST"; then
  echo "OK: udev rule already installed at $RULE_DST."
else
  echo "Installing udev rule to $RULE_DST..."
  sudo cp "$RULE_SRC" "$RULE_DST"
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=tty
  echo "udev rule installed and reloaded — takes effect immediately, no reboot needed."
fi

# ---- WSL note ----
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo
  echo "Note: this looks like WSL. USB devices aren't visible to WSL by default —"
  echo "you also need usbipd-win on the Windows host to attach the board:"
  echo "  https://github.com/dorssel/usbipd-win"
  echo "And udev only runs in WSL if systemd is enabled (systemd=true in /etc/wsl.conf,"
  echo "WSL >= 0.67.6 / Windows 11). Without it, this udev rule has nothing to apply to."
fi

echo
if [ "$NEEDS_RELOGIN" = "1" ]; then
  echo "One remaining step: log out and back in (or reboot) so YOUR CURRENT SESSION"
  echo "picks up the new group membership — this is a one-time requirement, not"
  echo "something you'll hit again. After that:"
  echo "  cd backend && uvicorn main:app --reload --port 8000"
else
  echo "Nothing further needed — group membership and the udev rule are both in place."
fi

echo
echo "Serial devices currently visible:"
if ! ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null; then
  echo "  (none found — plug in the GRBL controller, then run this script again)"
fi
