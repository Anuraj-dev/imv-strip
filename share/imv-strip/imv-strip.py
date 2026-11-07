#!/usr/bin/env python3
"""Image viewer with a PowerPoint-style thumbnail strip.

Usage: imv-strip.py FILE            show FILE with every image in its folder
       imv-strip.py FILE FILE...    show exactly the given files

Left/Right step through the folder (wrapping, like imv). The strip on the left
lists every image in order, keeps the current one highlighted and scrolled into
view, and jumps on click or Enter. The top-left counter mirrors the imv overlay
set in ~/.config/imv/config. Ctrl+p (print), Ctrl+r (rotate) and Ctrl+e (edit in
omasnap) are carried over from that file; trash keys are deliberately omitted.
"""

import collections
import hashlib
import os
import re
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

APP_ID = "imv.strip"
EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
        ".avif", ".heic", ".svg", ".jxl", ".qoi"}
THUMB_W, THUMB_H = 144, 90          # box a thumbnail fits in; 16:10 like most screens
THUMB_DECODE = 2                    # decode at 2x so fractional/HiDPI scales stay sharp
THUMB_CACHE = 400                   # thumbnails kept in memory (~200 KB each)
THUMB_WORKERS = 3
LIST_PAD = 6                        # vertical padding of the strip, matches CSS
ROW_PAD_L, ROW_PAD_R = 4, 10        # row side padding, matches CSS


def natural_key(path):
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def folder_images(path):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = os.listdir(folder)
    except OSError:
        return [os.path.abspath(path)]
    files = [os.path.join(folder, n) for n in names
             if os.path.splitext(n)[1].lower() in EXTS
             and os.path.isfile(os.path.join(folder, n))]
    files.sort(key=natural_key)
    return files


def texture_from_pixbuf(pixbuf):
    # MemoryTexture is immutable and safe to build off the main thread.
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), fmt,
                                 pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


def load_full(path):
    """Decode a full image, honouring EXIF orientation. Returns a Gdk.Texture or None."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        return texture_from_pixbuf(pixbuf.apply_embedded_orientation() or pixbuf)
    except GLib.Error:
        pass
    try:
        return Gdk.Texture.new_from_filename(path)
    except GLib.Error:
        return None


def freedesktop_thumb(path):
    """Reuse a thumbnail another app (Nautilus etc.) already cached, if still valid."""
    try:
        mtime = int(os.stat(path).st_mtime)
    except OSError:
        return None
    digest = hashlib.md5(Gio.File.new_for_path(path).get_uri().encode()).hexdigest()
    base = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
    for size in ("large", "x-large", "normal"):
        cached = os.path.join(base, size, digest + ".png")
        if not os.path.exists(cached):
            continue
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(cached)
        except GLib.Error:
            continue
        if pixbuf.get_option("tEXt::Thumb::MTime") == str(mtime):
            return pixbuf
    return None


def load_thumb(path):
    w, h = THUMB_W * THUMB_DECODE, THUMB_H * THUMB_DECODE
    pixbuf = freedesktop_thumb(path)
    if pixbuf is None:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, True)
            pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        except GLib.Error:
            return load_full(path)  # formats gdk-pixbuf can't scale; GPU scales instead
    pw, ph = pixbuf.get_width(), pixbuf.get_height()
    scale = min(w / pw, h / ph, 1.0)
    if scale < 1.0:
        pixbuf = pixbuf.scale_simple(max(1, int(pw * scale)), max(1, int(ph * scale)),
                                     GdkPixbuf.InterpType.BILINEAR)
    return texture_from_pixbuf(pixbuf)


class ThumbLoader:
    """Loads thumbnails on worker threads, newest request first.

    Rows request thumbnails as they scroll into view; a LIFO stack means a fast
    scroll loads what is on screen now instead of everything it passed over.
    Requests for rows that scrolled away are dropped before decoding.
    """

    def __init__(self, on_ready):
        self.on_ready = on_ready
        self.cache = collections.OrderedDict()
        self.stack = []
        self.wanted = collections.Counter()  # path -> number of rows showing it
        self.cv = threading.Condition()
        for _ in range(THUMB_WORKERS):
            threading.Thread(target=self._work, daemon=True).start()

    def get(self, path):
        tex = self.cache.get(path)
        if tex is not None:
            self.cache.move_to_end(path)
        return tex

    def want(self, path):
        with self.cv:
            self.wanted[path] += 1
            if path in self.stack:
                self.stack.remove(path)
            self.stack.append(path)
            self.cv.notify()

    def unwant(self, path):
        with self.cv:
            self.wanted[path] -= 1
            if self.wanted[path] <= 0:
                del self.wanted[path]

    def forget(self, path):
        self.cache.pop(path, None)

    def store(self, path, tex):
        self.cache[path] = tex
        self.cache.move_to_end(path)
        while len(self.cache) > THUMB_CACHE:
            self.cache.popitem(last=False)
        self.on_ready(path, tex)
        return GLib.SOURCE_REMOVE

    def _work(self):
        while True:
            with self.cv:
                while not self.stack:
                    self.cv.wait()
                path = self.stack.pop()
                if path not in self.wanted:
                    continue
            tex = load_thumb(path)
            if tex is not None:
                GLib.idle_add(self.store, path, tex)


class MainLoader:
    """Loads the current image plus its neighbours on one background thread."""

    def __init__(self, on_ready):
        self.on_ready = on_ready
        self.cache = {}
        self.want = []
        self.cv = threading.Condition()
        threading.Thread(target=self._work, daemon=True).start()

    def request(self, paths):
        with self.cv:
            self.want = list(dict.fromkeys(paths))
            for p in list(self.cache):
                if p not in self.want:
                    del self.cache[p]
            self.cv.notify()

    def forget(self, path):
        with self.cv:
            self.cache.pop(path, None)

    def get(self, path):
        with self.cv:
            return self.cache.get(path, False)  # False = not loaded yet, None = failed

    def _work(self):
        while True:
            with self.cv:
                todo = [p for p in self.want if p not in self.cache]
                while not todo:
                    self.cv.wait()
                    todo = [p for p in self.want if p not in self.cache]
                path = todo[0]
            tex = load_full(path)
            with self.cv:
                if path in self.want:
                    self.cache[path] = tex
            GLib.idle_add(self.on_ready, path)


def accent_color():
    """Use Hyprland's active border colour so the highlight matches the theme."""
    try:
        out = subprocess.run(["hyprctl", "-j", "getoption", "general:col.active_border"],
                             capture_output=True, text=True, timeout=1).stdout
        m = re.search(r'"gradient":\s*"[0-9a-fA-F]{2}([0-9a-fA-F]{6})', out)
        if m:
            return "#" + m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return "#7aa2f7"


CSS = """
window.imv-strip { background: #000; }
.strip-pane { background: #0e0e10; border-right: 1px solid alpha(white, 0.06); }
listview.strip { background: transparent; padding: LIST_PADpx 0; }
listview.strip > row { background: none; padding: 5px ROW_PAD_Rpx 5px ROW_PAD_Lpx; border-radius: 0; }
listview.strip > row:hover .thumb-frame { box-shadow: 0 0 0 1px alpha(white, 0.25); }
listview.strip > row:focus-visible { outline: none; }
listview.strip > row:focus-visible .thumb-frame { box-shadow: 0 0 0 1px alpha(white, 0.55); }
listview.strip > row:selected .thumb-frame { box-shadow: 0 0 0 2px ACCENT; }
listview.strip > row:selected .thumb-num { color: ACCENT; }
.thumb-frame { background: alpha(white, 0.04); border-radius: 3px; }
.thumb-num { font-family: "JetBrainsMono Nerd Font", monospace; font-size: 8pt;
             color: alpha(white, 0.45); padding-top: 2px; }
.counter { font-family: "JetBrainsMono Nerd Font", monospace; font-size: 10pt;
           color: #e6e6e6; background: rgba(0, 0, 0, 0.6); padding: 1px 7px; }
.load-error { color: alpha(white, 0.5); }
"""


class Viewer(Gtk.ApplicationWindow):
    def __init__(self, app, paths, start):
        super().__init__(application=app, title="Image Viewer")
        self.add_css_class("imv-strip")
        self.set_default_size(1200, 780)

        self.store = Gtk.StringList.new(paths)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=True,
                                             can_unselect=False)
        self.digits = len(str(len(paths)))
        self.bound = collections.defaultdict(set)  # path -> pictures showing it
        self.thumbs = ThumbLoader(self._thumb_ready)
        self.main = MainLoader(self._main_ready)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)

        self.list = Gtk.ListView(model=self.selection, factory=factory,
                                 single_click_activate=False)
        self.list.add_css_class("strip")
        self.list.connect("activate", lambda _l, pos: self.go(pos))

        strip = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER)
        strip.add_css_class("strip-pane")
        # Size the strip to a row's minimum width. Its natural width would follow the
        # thumbnails' decoded pixel size, which is 2x the box they are shown in.
        row_min = self._make_row()[0].measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        strip.set_size_request(row_min + ROW_PAD_L + ROW_PAD_R, -1)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   hexpand=True, vexpand=True)
        self.error = Gtk.Label(visible=False)
        self.error.add_css_class("load-error")
        self.counter = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.counter.add_css_class("counter")
        stage = Gtk.Overlay(child=self.picture)
        stage.add_overlay(self.error)
        stage.add_overlay(self.counter)

        box = Gtk.Box()
        box.append(strip)
        box.append(stage)
        self.set_child(box)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        self.selection.connect("notify::selected", lambda *_: self._show())
        self.selection.set_selected(start)
        self._show()
        self.connect("map", lambda *_: GLib.idle_add(self._reveal, True))

    # Rows -------------------------------------------------------------------

    def _make_row(self):
        num = Gtk.Label(xalign=1, valign=Gtk.Align.START, width_chars=self.digits)
        num.add_css_class("thumb-num")
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                          hexpand=True, vexpand=True)
        frame = Gtk.Box(overflow=Gtk.Overflow.HIDDEN, hexpand=False)
        frame.add_css_class("thumb-frame")
        frame.set_size_request(THUMB_W, THUMB_H)
        frame.append(pic)
        row = Gtk.Box(spacing=8)
        row.append(num)
        row.append(frame)
        return row, num, pic

    def _setup_row(self, _factory, item):
        row, item.num, item.pic = self._make_row()
        item.path = None
        item.set_child(row)

    def _bind_row(self, _factory, item):
        path = item.get_item().get_string()
        item.path = path
        item.num.set_label(str(item.get_position() + 1))
        item.pic.set_paintable(self.thumbs.get(path))
        item.pic.set_alternative_text(os.path.basename(path))
        self.bound[path].add(item.pic)
        if item.pic.get_paintable() is None:
            self.thumbs.want(path)

    def _unbind_row(self, _factory, item):
        path = item.path
        if path is None:
            return
        self.bound[path].discard(item.pic)
        if not self.bound[path]:
            del self.bound[path]
        if item.pic.get_paintable() is None:
            self.thumbs.unwant(path)
        item.pic.set_paintable(None)
        item.path = None

    def _thumb_ready(self, path, tex):
        for pic in self.bound.get(path, ()):
            if pic.get_paintable() is None:
                pic.set_paintable(tex)
                self.thumbs.unwant(path)

    # Navigation -------------------------------------------------------------

    def current_path(self):
        item = self.selection.get_selected_item()
        return item.get_string() if item else None

    def go(self, pos):
        n = self.store.get_n_items()
        if n == 0:
            return
        self.selection.set_selected(pos % n)
        self._reveal()

    def step(self, delta):
        self.go(self.selection.get_selected() + delta)

    def _reveal(self, center=False):
        """Keep the current thumbnail in view with a neighbour showing on each side.

        Scrolls only when needed, so stepping through the folder doesn't make the
        strip jump on every key press. Rows share one height, which lets the
        target offset come straight from the list's scroll range.
        """
        pos = self.selection.get_selected()
        n = self.store.get_n_items()
        if pos == Gtk.INVALID_LIST_POSITION or n == 0:
            return GLib.SOURCE_REMOVE
        # Moves keyboard focus to the row, so Enter and Up/Down start from it.
        self.list.scroll_to(pos, Gtk.ListScrollFlags.FOCUS, None)
        adj = self.list.get_vadjustment()
        pad = LIST_PAD * 2
        row = (adj.get_upper() - pad) / n
        page = adj.get_page_size()
        if row <= 0 or page <= 0:
            if center:  # not laid out yet; try again once it is
                GLib.timeout_add(30, self._reveal, True)
            return GLib.SOURCE_REMOVE
        top = LIST_PAD + pos * row
        if center:
            value = top - (page - row) / 2
        else:
            margin = min(row * 0.75, max(0, (page - row) / 2))
            value = adj.get_value()
            if top - margin < value:
                value = top - margin
            elif top + row + margin > value + page:
                value = top + row + margin - page
        adj.set_value(max(0, min(value, adj.get_upper() - page)))
        return GLib.SOURCE_REMOVE

    def _show(self):
        n = self.store.get_n_items()
        pos = self.selection.get_selected()
        path = self.current_path()
        if path is None:
            return
        self.counter.set_label(f"{pos + 1} / {n}")
        self.set_title(f"{os.path.basename(path)} — Image Viewer")
        neighbours = [self.store.get_string((pos + d) % n) for d in (0, 1, -1, 2)]
        self.main.request(neighbours)
        self._main_ready(path)

    def _main_ready(self, path):
        if path != self.current_path():
            return GLib.SOURCE_REMOVE
        tex = self.main.get(path)
        if tex is False:
            return GLib.SOURCE_REMOVE  # still loading; keep the previous image up
        self.picture.set_paintable(tex)
        self.error.set_visible(tex is None)
        if tex is None:
            self.error.set_label(f"Can't open {os.path.basename(path)}")
        return GLib.SOURCE_REMOVE

    # Actions (carried over from ~/.config/imv/config [binds]) ----------------

    def _run(self, argv, then=None):
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        if then:
            proc.wait_async(None, lambda p, res: (p.wait_finish(res), then(p.get_successful())))

    def _remove_current(self):
        pos = self.selection.get_selected()
        path = self.current_path()
        self.thumbs.forget(path)
        self.main.forget(path)
        self.store.remove(pos)
        if self.store.get_n_items() == 0:
            self.close()
            return
        self.digits = len(str(self.store.get_n_items()))
        self.go(min(pos, self.store.get_n_items() - 1))
        self._show()
        self._renumber()

    def _renumber(self):
        # Positions after a removal shift; rebinding refreshes the numbers.
        self.list.set_model(None)
        self.list.set_model(self.selection)
        GLib.idle_add(self._reveal, True)

    def _rotated(self, path, ok):
        if not ok:
            return
        self.thumbs.forget(path)
        self.main.forget(path)
        for pic in self.bound.get(path, ()):
            pic.set_paintable(None)
            self.thumbs.want(path)
        if path == self.current_path():
            self._show()

    def _key(self, _ctrl, keyval, _code, state):
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                        | Gdk.ModifierType.ALT_MASK)
        ctrl = Gdk.ModifierType.CONTROL_MASK
        key = Gdk.keyval_to_lower(keyval)
        path = self.current_path()

        if mods == 0:
            if keyval in (Gdk.KEY_Right, Gdk.KEY_l, Gdk.KEY_Page_Down, Gdk.KEY_space):
                self.step(1)
            elif keyval in (Gdk.KEY_Left, Gdk.KEY_h, Gdk.KEY_Page_Up, Gdk.KEY_BackSpace):
                self.step(-1)
            elif keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
                # Own Up/Down too, so the strip keeps a neighbour in view; no wrap.
                n = self.store.get_n_items()
                delta = 1 if keyval == Gdk.KEY_Down else -1
                self.go(max(0, min(n - 1, self.selection.get_selected() + delta)))
            elif keyval == Gdk.KEY_Home:
                self.go(0)
            elif keyval == Gdk.KEY_End:
                self.go(-1)
            elif keyval == Gdk.KEY_f:
                self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
            elif keyval == Gdk.KEY_Escape and self.is_fullscreen():
                self.unfullscreen()
            elif keyval in (Gdk.KEY_q, Gdk.KEY_Escape):
                self.close()
            elif keyval == Gdk.KEY_o and path:
                # Hand off to imv for zoom and pan, which this viewer doesn't do.
                self._run(["imv", path])
            else:
                return False
            return True

        if not path:
            return False
        if mods == ctrl and key == Gdk.KEY_p:
            self._run(["lp", path])
        elif mods == ctrl and key == Gdk.KEY_r:
            self._run(["mogrify", "-rotate", "90", path], lambda ok: self._rotated(path, ok))
        elif mods == ctrl and key == Gdk.KEY_e:
            self._run(["omasnap", path])
            self.close()
        else:
            return False
        return True


class App(Gtk.Application):
    def __init__(self, args):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.args = args

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS.replace("ACCENT", accent_color()).replace("LIST_PAD", str(LIST_PAD))
                             .replace("ROW_PAD_R", str(ROW_PAD_R)).replace("ROW_PAD_L", str(ROW_PAD_L)))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        first = os.path.abspath(self.args[0])
        paths = ([os.path.abspath(p) for p in self.args] if len(self.args) > 1
                 else folder_images(first))
        if first not in paths:
            paths.insert(0, first)
        Viewer(self, paths, paths.index(first)).present()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return App(args).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
