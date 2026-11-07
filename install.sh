#!/usr/bin/env bash
# Installs imv-strip: a thumbnail-strip image viewer built on imv's config for Omarchy/Hyprland.
# Existing files are backed up before they are replaced. Nothing is removed and no packages are installed.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
stamp=$(date +%Y%m%d-%H%M%S)
backup="$HOME/.local/state/imv-strip-backup/$stamp"

targets=(
  "$repo/bin/imv-dir|$HOME/.local/bin/imv-dir"
  "$repo/share/imv-strip/imv-strip.py|$HOME/.local/share/imv-strip/imv-strip.py"
  "$repo/config/imv-config|$HOME/.config/imv/config"
  "$repo/applications/imv.desktop|$HOME/.local/share/applications/imv.desktop"
)

# 1. Dependencies: report anything missing and print the install command, but do not run sudo.
missing=()
for cmd in imv python3 hyprctl; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' 2>/dev/null; then
  missing+=("gtk4 + python-gobject")
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing: ${missing[*]}"
  echo "Install with: sudo pacman -S --needed imv gtk4 python-gobject hyprland"
  echo "Then run this script again."
  exit 1
fi

# 2. Back up whatever is about to be replaced.
for pair in "${targets[@]}"; do
  dst=${pair#*|}
  if [ -e "$dst" ]; then
    mkdir -p "$backup/$(dirname -- "${dst#"$HOME"/}")"
    cp -a "$dst" "$backup/${dst#"$HOME"/}"
    echo "backed up $dst"
  fi
done

# 3. Install.
for pair in "${targets[@]}"; do
  src=${pair%%|*}
  dst=${pair#*|}
  mkdir -p "$(dirname -- "$dst")"
  cp "$src" "$dst"
  echo "installed $dst"
done
chmod +x "$HOME/.local/bin/imv-dir" "$HOME/.local/share/imv-strip/imv-strip.py"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true

# 4. Smoke test: the viewer compiles and the launcher parses.
python3 -m py_compile "$HOME/.local/share/imv-strip/imv-strip.py"
bash -n "$HOME/.local/bin/imv-dir"

echo
echo "Done. Open any image with the default viewer, or run: imv-dir /path/to/image.png"
if [ -d "$backup" ]; then
  echo "Previous files saved in: $backup"
fi
