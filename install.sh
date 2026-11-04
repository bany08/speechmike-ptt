#!/bin/sh
# Installs speechmike-ptt for the current user. Use --uninstall to remove it.
# Only the udev rule needs sudo. The daemon itself runs as the normal user.
set -eu

SERVICE=speechmike-ptt.service
RULE=70-speechmike.rules
RULE_PATH=/etc/udev/rules.d/$RULE
DATA_DIR=$HOME/.local/share/speechmike-ptt
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
AUTOSTART=${XDG_CONFIG_HOME:-$HOME/.config}/autostart/speechmike-ptt-tray.desktop
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: $1 not found. $2" >&2
        exit 1
    fi
}

reload_udev() {
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=hidraw
}

install_all() {
    require python3 "Python 3.9 or newer is required."
    require pactl "Install the package that provides pactl (pulseaudio-utils)."
    require systemctl "A systemd user session is required."

    echo "Installing the program to $DATA_DIR"
    mkdir -p "$DATA_DIR/speechmike_ptt"
    cp "$SOURCE_DIR"/speechmike_ptt/*.py "$DATA_DIR/speechmike_ptt/"
    mkdir -p "$DATA_DIR/speechmike_ptt/icons"
    cp "$SOURCE_DIR"/speechmike_ptt/icons/*.svg "$DATA_DIR/speechmike_ptt/icons/"

    echo "Installing the service to $UNIT_DIR"
    mkdir -p "$UNIT_DIR"
    cp "$SOURCE_DIR/packaging/$SERVICE" "$UNIT_DIR/"

    echo "Installing the udev rule to $RULE_PATH (needs sudo)"
    sudo install -m 0644 "$SOURCE_DIR/packaging/$RULE" "$RULE_PATH"
    reload_udev

    if python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null; then
        echo "Adding the status icon to your autostart"
        mkdir -p "$(dirname "$AUTOSTART")"
        sed "s|@DATA_DIR@|$DATA_DIR|" "$SOURCE_DIR/packaging/speechmike-ptt-tray.desktop" >"$AUTOSTART"
    else
        echo "Skipping the status icon: PyGObject or AyatanaAppIndicator3 is missing"
    fi

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE"
    systemctl --user restart "$SERVICE"

    echo
    echo "Done. The service is running."
    echo "Status: systemctl --user status speechmike-ptt"
    echo "Log:    journalctl --user -u speechmike-ptt -f"
}

uninstall_all() {
    require systemctl "A systemd user session is required."

    echo "Stopping and removing the service"
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_DIR/$SERVICE" "$AUTOSTART"
    systemctl --user daemon-reload

    echo "Removing $DATA_DIR"
    rm -rf "$DATA_DIR"

    echo "Removing the udev rule $RULE_PATH (needs sudo)"
    sudo rm -f "$RULE_PATH"
    reload_udev

    echo
    echo "Done. The microphone stays muted until you unmute it in your sound settings."
    echo "Your configuration in ~/.config/speechmike-ptt was kept."
}

case "${1:-}" in
    "") install_all ;;
    --uninstall) uninstall_all ;;
    *)
        echo "Usage: $0 [--uninstall]" >&2
        exit 2
        ;;
esac
