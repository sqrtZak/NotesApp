#!/bin/bash

# 1. Disable the Tablet Mode Sensor (Keyboard stays active)
TARGET_DEVICE=$(xinput list --name-only | grep -iE "Intel Virtual Button|Intel HID events" | head -n 1)

if [ -n "$TARGET_DEVICE" ]; then
    xinput disable "$TARGET_DEVICE"
fi

# 2. Lock the Taskbar (Prevent auto-hide)
# This forces the dock to remain visible even when windows overlap
gsettings set org.gnome.shell.extensions.dash-to-dock dock-fixed true
gsettings set org.gnome.shell.extensions.dash-to-dock autohide false

# 3. Notify user
notify-send "Tablet Mode: DISABLED" "Taskbar locked"
