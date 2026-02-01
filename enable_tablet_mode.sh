#!/bin/bash

# 1. Enable the Tablet Mode Sensor
TARGET_DEVICE=$(xinput list --name-only | grep -iE "Intel Virtual Button|Intel HID events" | head -n 1)

if [ -n "$TARGET_DEVICE" ]; then
    xinput enable "$TARGET_DEVICE"
fi

# 2. Unlock Taskbar (Restore default behavior)
gsettings set org.gnome.shell.extensions.dash-to-dock dock-fixed false
gsettings set org.gnome.shell.extensions.dash-to-dock autohide true

# 3. Notify user
notify-send "Tablet Mode: ENABLED" "Sensors active | Taskbar auto-hides"
