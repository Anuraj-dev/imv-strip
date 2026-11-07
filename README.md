# imv-strip

A thumbnail-strip image viewer for [Omarchy](https://omarchy.org) (Hyprland). Open an image and you get every image in its folder down the left side, the current one highlighted, and a small `N / total` counter in the corner. Arrow keys step through the folder, the way PowerPoint's slide pane works.

![imv-strip showing a folder of images with the thumbnail strip on the left](docs/strip-viewer.png)

## What it does

- Opens the clicked image with every image in its folder loaded, in natural sort order.
- Thumbnail strip on the left. Click a thumbnail, or press Enter on a focused one, to jump to it.
- Left/Right step through the folder and wrap around, like imv.
- `N / total` counter in the top-left, styled from your imv config.
- Highlight colour follows your Hyprland active border, so it matches the theme.
- Opens as a floating, centred 1200×780 window (rules apply only to that window).
- Falls back to plain `imv` if the viewer is missing or Hyprland isn't running.

## Keys

| Key | Action |
|---|---|
| Left / Right (or h / l) | Previous / next image, wraps around |
| Up / Down | Move without wrapping |
| Home / End | First / last image |
| Space / Backspace | Next / previous |
| PgUp / PgDn | Previous / next (one image at a time) |
| Click, or Enter on a row | Jump to that image |
| f | Toggle fullscreen |
| o | Open the current image in imv (zoom and pan) |
| Ctrl+p | Print the current image |
| Ctrl+r | Rotate 90° clockwise. **Overwrites the file in place.** |
| Ctrl+e | Edit in omasnap, then close |
| q / Esc | Quit. Esc leaves fullscreen first if it is on. |

Trash keys from the imv config are deliberately not included.

## Requirements

- Omarchy (Hyprland). Other Hyprland setups should work, but only Omarchy is tested.
- `imv`, `gtk4`, `python-gobject`, `python3`
- Optional: `JetBrainsMono Nerd Font`, used by the counter and strip labels. Other monospace fonts fall back automatically.

Install the packages:

```sh
sudo pacman -S --needed imv gtk4 python-gobject
```

## Install

```sh
git clone https://github.com/Anuraj-dev/imv-strip.git ~/Projects/imv-strip
cd ~/Projects/imv-strip
bash install.sh
```

`install.sh` copies four files into place:

| Installed file | Purpose |
|---|---|
| `~/.local/bin/imv-dir` | Launcher. Opens the viewer with the folder loaded. |
| `~/.local/share/imv-strip/imv-strip.py` | The viewer. |
| `~/.config/imv/config` | imv settings: counter style and key bindings. |
| `~/.local/share/applications/imv.desktop` | Makes the viewer the default for images. |

Anything it replaces is first copied to `~/.local/state/imv-strip-backup/<timestamp>/`. Nothing is deleted, and no packages are installed.

Try it:

```sh
imv-dir ~/Pictures/some-image.png
```

## Setup prompt for your own Claude / Codex agent

Paste this into an agent on your Omarchy machine:

```text
Set up imv-strip on this Omarchy machine.

1. Confirm this is Omarchy on Hyprland: `hyprctl version` should work and the session should be Wayland.
2. Clone https://github.com/Anuraj-dev/imv-strip into ~/Projects/imv-strip (skip if it already exists and run `git pull` there instead).
3. Check the requirements: imv, gtk4, python-gobject, python3. If any are missing, show me the `sudo pacman -S --needed imv gtk4 python-gobject` command and ask before running it, because it needs sudo.
4. Before running install.sh, read it and read the four target files it replaces: ~/.local/bin/imv-dir, ~/.local/share/imv-strip/imv-strip.py, ~/.config/imv/config, ~/.local/share/applications/imv.desktop. Tell me if any of them already exist and differ from the repo, so I can decide what to keep.
5. Run `bash install.sh`. Do not edit the Hyprland config, and do not delete anything. The script backs up replaced files.
6. Verify: run `python3 -m py_compile ~/.local/share/imv-strip/imv-strip.py` and `bash -n ~/.local/bin/imv-dir`. Then open a folder with a few images using `imv-dir <file>` and confirm the strip and the N / total counter appear.
7. Report what you installed, where the backups are, and anything you could not verify. Do not push or change anything in the repo.
```

## Notes and limits

- The viewer has no zoom or pan. Press `o` to open the current image in imv for that.
- Animated GIFs show only the first frame.
- Ctrl+r rewrites the file. Use a copy if you care about the original.
- The window placement uses `hyprctl eval` at launch. It does not change your Hyprland config.
- Tested on one Omarchy machine only. Other displays and scale factors (fractional scaling, multi-monitor layouts) are not tested.

## Uninstall

Copy the files back from the newest folder under `~/.local/state/imv-strip-backup/`, or delete the four installed files and reinstall imv's defaults.

## License

MIT. See [LICENSE](LICENSE).
