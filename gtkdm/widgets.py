import hashlib
import json
import os
import re
import shutil
import time
import shlex
import subprocess
import textwrap
import zipfile
from datetime import datetime
from math import atan2, pi, cos, sin, ceil
from pathlib import Path
from enum import Enum

import cairo
import gi
import numpy
import yaml

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', "1.0")
from gi.repository import Gtk, GObject, Gdk, Gio, GdkPixbuf, GLib, PangoCairo, Pango

from matplotlib.backends.backend_gtk3agg import FigureCanvasGTK3Agg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.style import context as style_context

from epics.ca import ChannelAccessGetFailure
import gepics
import xml.etree.ElementTree as ET

from . import utils, colors, version, PLUGIN_DIR
from .utils import logger, XYData, StripData

EDITOR = True

ENTRY_CONVERTERS = {
    'string': str,
    'int': int,
    'short': int,
    'float': float,
    'enum': int,
    'long': int,
    'double': float,
    'time_string': str,
    'time_int': int,
    'time_short': int,
    'time_float': float,
    'time_enum': int,
    'time_char': str,
    'time_long': int,
    'time_double': float,
    'ctrl_string': str,
    'ctrl_int': int,
    'ctrl_short': int,
    'ctrl_float': float,
    'ctrl_enum': int,
    'ctrl_char': str,
    'ctrl_long': int,
    'ctrl_double': float
}

FONT_SIZES = {
    -3: 'xxs', -2: 'xs', -1: 'sm', 0: 'md', 1: 'lg', 2: 'xl', 3: 'xxl'
}


class DisplayManager(object):
    """Manages all displays"""

    def __init__(self):
        self.macros = {}
        self.registry = {}
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        self.search_paths = [os.getcwd()] + os.environ.get('GTKDM_DISPLAY_PATH', '').split(':')

    def reset(self, macro_spec):
        self.macros = utils.parse_macro_spec(macro_spec)

    def find_display(self, path, root_path=None):
        """
        Search for the display file and return the full path
        :param path: relative or absolute path to find
        :param root_path: top-level path of display frame to search first.
        :return: Full path to display file, or None if not found

        """
        search_locations = self.search_paths if not root_path else [root_path] + self.search_paths

        is_abs = os.path.isabs(path)
        if is_abs and os.path.exists(path):
            full_path = path
        elif not is_abs:
            for display_path in search_locations:
                full_path = os.path.join(display_path, path)
                if os.path.exists(full_path):
                    break
            else:
                full_path = None
        else:
            full_path = None

        return full_path

    def destroy_window(self, key):
        """
        Destroy all objects in the Window

        :param key: registry key
        """
        window = self.registry.pop(key)
        for obj in window.builder.get_objects():
            if hasattr(obj, 'destroy'):
                obj.destroy()

    def show_display(self, path, macros_spec="", main=False, multiple=False):
        """
        Show a display file

        :param path: absolute or relative path to display file
        :param macros_spec: macro specification
        :param main: Whether this is a main window or a related display
        :param multiple: Whether multiple instances are allowed or not
        """
        global EDITOR
        if main:
            EDITOR = False

        full_path = self.find_display(path)
        if not full_path:
            logger.error('Display File {} not found'.format(path))
            return

        logger.info(f"Loading: {full_path}...")

        directory, filename = os.path.split(full_path)
        tree = ET.parse(full_path)
        w = tree.find(".//object[@class='GtkWindow']")
        w.set('class', 'DisplayWindow')  # Switch to full Window
        w.set('id', 'related_display')

        new_macros = {}
        new_macros.update(self.macros)
        new_macros.update(utils.parse_macro_spec(macros_spec))
        new_macro_spec = utils.compress_macro(new_macros)
        unique_text = ('{}{}'.format(filename, new_macro_spec)).encode('utf-8')
        key = hashlib.sha256(unique_text).hexdigest()
        if multiple or key not in self.registry:
            try:
                utils.update_properties(tree, new_macros)
            except KeyError as e:
                logger.warn('Macro {} not specified for display "{}"'.format(e, filename))
            data = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n' +
                    ET.tostring(tree.getroot(), encoding='unicode', method='xml')
            )
            with utils.working_dir(directory):
                builder = Gtk.Builder.new_from_string(data, -1)
                window = builder.get_object('related_display')
                window.builder = builder
                window.macros = new_macro_spec
                window.header.set_subtitle(filename)
                window.props.path = full_path
                if main:
                    window.connect('destroy', lambda x: Gtk.main_quit())
                elif not multiple:
                    self.registry[key] = window
                    window.connect('destroy', lambda x: self.destroy_window(key))
                window.show_all()
        else:
            window = self.registry[key]
            window.present()

    def embed_display(self, frame, path, macros_spec=""):
        """
        Embed a display in a target frame

        :param frame: Target DisplayFrame to embed display in
        :param path: relative or absolute path to the display file to embed
        :param macros_spec: Macro specification
        """

        top_level = frame.get_toplevel()
        root_path = os.path.dirname(top_level.path) if isinstance(top_level, DisplayWindow) else None
        full_path = self.find_display(path, root_path=root_path)
        if not full_path:
            logger.error('Display File {} not found'.format(path))
            return

        directory, filename = os.path.split(full_path)
        tree = ET.parse(full_path)
        w = tree.find(".//object[@class='GtkWindow']/child/object[1]")
        w.set('id', 'embedded_display')

        # get list of non GtkWindow Top levels. These should be loaded.
        top_levels = list(
            {
                element.get('id') for element in tree.findall("./object")
            } - {
                element.get('id') for element in tree.findall("./object[@class='GtkWindow']")
            }
        ) + ['embedded_display']

        new_macros = {}
        new_macros.update(self.macros)
        new_macros.update(utils.parse_macro_spec(macros_spec))
        new_macro_spec = utils.compress_macro(new_macros)
        try:
            utils.update_properties(tree, new_macros)
        except KeyError as e:
            logger.warn('Macro {} not specified for display "{}"'.format(e, filename))
        data = (
                '<?xml version="1.0" encoding="UTF-8"?>\n' +
                ET.tostring(tree.getroot(), encoding='unicode', method='xml')
        )
        with utils.working_dir(directory):
            builder = Gtk.Builder()
            builder.add_objects_from_string(data, top_levels)
            display = builder.get_object('embedded_display')
            child = frame.get_child()
            if child:
                child.destroy()
            frame.add(display)
            # If reloading main window, frame will be a DisplayWindow, keep reference to builder
            if isinstance(frame, DisplayWindow):
                frame.builder = builder
                frame.macros = new_macro_spec
            display.show_all()


Manager = DisplayManager()


class ColorSequence(object):
    def __init__(self, sequence):
        self.specs = [colors.TANGO.get(v, '#000000') for v in sequence]

    def __call__(self, value, alpha=1.0):
        try:
            i = min(value, len(self.specs) - 1)
        except ValueError:
            i = 0
        spec = self.specs[i]
        return self.parse(spec)

    def __getitem__(self, item):
        try:
            i = int(item)
        except:
            i = 0
        return self.specs[i % len(self.specs)]

    @staticmethod
    def parse(spec):
        col = Gdk.RGBA()
        col.parse(spec)
        return col


def alpha(rgba, a):
    col = rgba.copy()
    col.alpha = a
    return col


def pix(v):
    """Round to neareast 0.5 for cairo drawing"""
    x = round(v * 2)
    return x / 2 if x % 2 else (x + 1) / 2


def radians(a):
    return (a * pi / 180)


def ticks(lo, hi, step):
    return [i * step + ceil(float(lo) / step) * step for i in range(1 + int(ceil((float(hi) - lo) / step)))]


def tick_points(vmin, vmax, vstep, vticks):
    minimum = (vmin // vstep) * vstep
    maximum = ceil(vmax // vstep) * vstep
    major = ticks(minimum, maximum, vstep)
    if vticks:
        minor_raw = ticks(minimum, maximum, vstep / (vticks + 1))
        minor = [minor_raw[v] for v in list(range(len(minor_raw))) if v % (vticks + 1) != 0]
    else:
        minor = []
    return minimum, maximum, major, minor


Direction = Gdk.WindowEdge


class BlankWidget(Gtk.Widget):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect('notify', self.do_notify)

    def do_notify(self, *args):
        self.queue_draw()

    def do_realize(self, *args):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        mask = Gdk.WindowAttributesType.X | Gdk.WindowAttributesType.Y | Gdk.WindowAttributesType.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask)
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)

    def get_top_level(self):
        parent = self.get_parent()
        if parent:
            return parent.get_toplevel()


class AlarmMixin(object):
    def on_alarm(self, pv, alarm):
        if self.alarm:
            if alarm == gepics.Alarm.MAJOR:
                self.get_style_context().remove_class('gtkdm-warning')
                self.get_style_context().add_class('gtkdm-critical')
            elif alarm == gepics.Alarm.MINOR:
                self.get_style_context().add_class('gtkdm-warning')
                self.get_style_context().remove_class('gtkdm-critical')
            else:
                self.get_style_context().remove_class('gtkdm-warning')
                self.get_style_context().remove_class('gtkdm-critical')
            self.queue_draw()


class ActiveMixin(object):
    PV_COPY_BUTTON = 2
    ready: bool
    copy_text:  str

    def set_ready(self, state):
        self.ready = state

    def on_active(self, pv, connected):
        self.ready = False
        self.copy_text = pv.name
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self.on_mouse_press)
        self.set_tooltip_text(self.copy_text)
        if connected:
            GLib.timeout_add(1000, self.set_ready, True)
            try:
                pv.ctrlvars = pv.get_with_metadata(with_ctrlvars=True)
            except ChannelAccessGetFailure:
                pv.ctrlvars = {}
            self.get_style_context().remove_class('gtkdm-inactive')
            self.set_sensitive(True)
        else:
            self.get_style_context().add_class('gtkdm-inactive')
            self.set_sensitive(False)
        self.queue_draw()

    def on_mouse_press(self, widget, event):
        if event.button == self.PV_COPY_BUTTON:
            valid = (
                self.PV_COPY_BUTTON == 2,
                self.PV_COPY_BUTTON == 1 and event.type == Gdk.EventType._2BUTTON_PRESS
            )
            if any(valid) and hasattr(self, 'copy_text'):
                Manager.clipboard.set_text(self.copy_text, -1)


class FontMixin(object):
    # font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    # monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    # bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def on_realize(self, *args):
        # adjust style classes
        style = self.get_style_context()
        for k, v in FONT_SIZES.items():
            if k == self.font_size:
                style.add_class(v)
            else:
                style.remove_class(v)

        if self.monospace:
            style.add_class('mono-font')
        if self.bold:
            style.add_class('bold-font')


class Layout(Gtk.Fixed):
    __gtype_name__ = 'Layout'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DisplayWindow(Gtk.Window):
    __gtype_name__ = 'DisplayWindow'
    path = GObject.Property(type=str, default='')
    builder = GObject.Property(type=Gtk.Builder)
    macros = GObject.Property(type=str, default='')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.header = Gtk.HeaderBar()
        self.header.set_show_close_button(True)
        self.set_titlebar(self.header)
        self.set_icon_name('applications-engineering')
        button = Gtk.MenuButton()
        icon = Gio.ThemedIcon(name="open-menu-symbolic")
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.BUTTON)
        button.add(image)
        self.header.pack_end(button)

        icon = Gio.ThemedIcon(name="applications-engineering")
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.LARGE_TOOLBAR)
        self.header.pack_start(image)
        self.get_style_context().add_class('gtkdm')

        # prepare application menu
        popover = Gtk.Popover()
        popover.set_border_width(3)
        button.set_popover(popover)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        popover.add(box)

        # register menu items
        btn = Gtk.ModelButton(text='  Edit ...')
        btn.connect("clicked", self.on_edit)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)

        btn = Gtk.ModelButton(text='  Reload')
        btn.connect("clicked", self.on_reload)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        btn = Gtk.ModelButton(text='  About GtkDM')
        btn.connect("clicked", self.on_about)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        btn = Gtk.ModelButton(text='  Close')
        btn.connect("clicked", self.on_close)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        popover.show_all()
        title = self.header.get_title()
        if title:
            self.header.props.title = "GtkDM - {}".format(title)
        else:
            self.header.props.title = "GtkDM"

    def on_edit(self, btn):
        try:
            environ = dict(os.environ)
            environ['GLADE_CATALOG_SEARCH_PATH'] = PLUGIN_DIR
            environ['GLADE_MODULE_SEARCH_PATH'] = PLUGIN_DIR

            subprocess.Popen(['glade', self.path], env=environ)
        except FileNotFoundError as e:
            logger.warn("GtkDM Editor not available")

    def on_reload(self, btn):
        Manager.embed_display(self, self.path, self.macros)

    def on_about(self, btn):
        about_dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        about_dialog.set_program_name("GtkDM")
        about_dialog.set_logo_icon_name('applications-engineering')
        about_dialog.set_comments("Python-based Gtk Display Manager for \nEPICS Operator Screens")
        about_dialog.set_version(version.get_version())
        about_dialog.set_copyright("© 2019-{} Canadian Light Source, Inc.".format(datetime.now().year))
        about_dialog.set_license_type(Gtk.License.MIT_X11)
        about_dialog.set_authors(["Michel Fodje <michel.fodje@lightsource.ca>"])
        about_dialog.present()

    def on_close(self, btn):
        self.destroy()


class DisplayFrame(Gtk.EventBox):
    __gtype_name__ = 'DisplayFrame'
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    yalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='Y-Alignment')
    xscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='X-Scale')
    yscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='Y-Scale')
    display = GObject.Property(type=str, default='', nick='Default Display')
    macros = GObject.Property(type=str, default='', nick='Default Macros')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('display-frame')
        self.get_style_context().add_class('display-frame')
        self.frame = Gtk.Alignment()
        self.add(self.frame)
        for prop in ['xalign', 'yalign', 'xscale', 'yscale']:
            self.bind_property(prop, self.frame, prop, GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        top_level = self.get_toplevel()
        if self.display and isinstance(top_level, DisplayWindow):
            try:
                self.display = self.display.format(**utils.parse_macro_spec(self.macros))
            except KeyError as e:

                logger.warn('Macro {} not specified for display "{}": {}'.format(e, self.display, self.macros))
            Manager.embed_display(self, self.display, macros_spec=self.macros)


class TextMonitor(FontMixin, ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'TextMonitor'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    colors = GObject.Property(type=str, default="", nick='Value Colors')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=1.0, nick='X-Alignment')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    prec = GObject.Property(type=int, default=-1, minimum=-1, maximum=10, nick='Precision')
    sci = GObject.Property(type=bool, default=False, nick='Sci. Format')
    show_units = GObject.Property(type=bool, default=True, nick='Show Units')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('text-monitor')
        self.get_style_context().add_class('text-monitor')
        self.label = Gtk.Label('...')
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.add(self.label)
        self.pv = None
        self.connect('realize', self.on_realize)
        self.bind_property('xalign', self.label, 'xalign',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.palette = ColorSequence(self.colors)

    def on_realize(self, obj):
        self.palette = ColorSequence(self.colors)
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

        super().on_realize(obj)

    def on_change(self, pv, value):
        if pv.type in ['enum', 'time_enum', 'ctrl_enum']:
            try:
                text = pv.enum_strs[value]
            except IndexError:
                text = "Invalid"
        elif pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
            precision = self.prec if self.prec >= 0 else pv.precision
            if precision < 0:
                text = f'{value:g}'
            elif self.sci:
                precision += 1
                text = f'{value:.{precision}g}'
            else:
                text = f'{value:.{precision}f}'
        else:
            text = pv.char_value.strip('"').strip("'")

        if self.pv.units and self.show_units:
            text = '{} {}'.format(text, pv.units)
        if self.colors:
            try:
                color = self.palette[value]
            except:
                color = 'black'
            text = '<span color="{}">{}</span>'.format(color, text)
        self.label.set_markup(text)


class ArrayMonitor(TextMonitor):
    __gtype_name__ = 'ArrayMonitor'

    index = GObject.Property(type=int, default=0, nick='Show Index')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_change(self, pv, value):
        if pv.count > 1:
            if self.index < pv.count:
                value = value[self.index]
            else:
                value = value[self.index % pv.count]
        super().on_change(pv, value)


class TextPanel(FontMixin, ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'TextPanel'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    colors = GObject.Property(type=str, default="", nick='Value Colors')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    prec = GObject.Property(type=int, default=-1, minimum=-1, maximum=10, nick='Precision')
    sci = GObject.Property(type=bool, default=False, nick='Sci. Format')
    show_units = GObject.Property(type=bool, default=True, nick='Show Units')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('text-panel')
        self.get_style_context().add_class('text-panel')
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.desc_label = Gtk.Label('<descr>', xalign=0.0)
        self.value_label = Gtk.Label('<value>')
        self.box.pack_start(self.desc_label, False, False, 0)
        self.box.pack_end(self.value_label, True, True, 0)
        self.add(self.box)
        self.pv = None
        self.label_pv = None
        self.connect('realize', self.on_realize)
        self.bind_property('xalign', self.value_label, 'xalign',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('label', self.desc_label, 'label',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.palette = ColorSequence(self.colors)

        self.value_label.get_style_context().add_class('value')
        self.desc_label.get_style_context().add_class('desc')

    def on_realize(self, obj):
        self.palette = ColorSequence(self.colors)
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)
        super().on_realize(obj)

    def on_label_change(self, pv, value):
        self.props.label = value

    def on_change(self, pv, value):
        if pv.type in ['enum', 'time_enum', 'ctrl_enum']:
            text = pv.enum_strs[value]
        elif pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
            precision = self.prec if self.prec >= 0 else pv.precision
            if precision < 0:
                text = f'{pv.value:g}'
            elif self.sci:
                precision += 1
                text = f'{pv.value:.{precision}g}'
            else:
                text = f'{pv.value:.{precision}f}'
        else:
            text = pv.char_value

        if self.pv.units and self.show_units:
            text = '{} {}'.format(text, pv.units)
        if self.colors:
            text = '<span color="{}">{}</span>'.format(self.palette[value], text)
        self.value_label.set_markup(text)


class TextLabel(FontMixin, Gtk.Bin):
    __gtype_name__ = 'TextLabel'

    text = GObject.Property(type=str, default='Label', nick='Label')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.label = Gtk.Label(label='Label')
        self.bind_property('text', self.label, 'label', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('xalign', self.label, 'xalign',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.label)
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        super().on_realize(obj)


class DateLabel(FontMixin, Gtk.Bin):
    __gtype_name__ = 'DateLabel'

    format = GObject.Property(type=str, default='%a %b %d, %X', nick='Date/Time Format')
    refresh = GObject.Property(type=float, default=1, minimum=.1, maximum=10, nick='Redraw Freq (hz)')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.label = Gtk.Label(label='')
        self.bind_property('xalign', self.label, 'xalign',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.label)
        self.connect('realize', self.on_realize)

    def update(self):
        self.label.set_text(datetime.now().strftime(self.format))
        return True

    def on_realize(self, obj):
        self.update()
        GLib.timeout_add(1000. / self.refresh, self.update)
        super().on_realize(obj)


class LineMonitor(ActiveMixin, AlarmMixin, BlankWidget):
    __gtype_name__ = 'LineMonitor'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    line_width = GObject.Property(type=float, minimum=0.1, maximum=100.0, default=1.0, nick='Width')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    colors = GObject.Property(type=str, default="K", nick='Value Colors')
    arrow = GObject.Property(type=bool, default=False, nick='Arrow')
    arrow_size = GObject.Property(type=int, minimum=1, maximum=10, default=2, nick='Arrow Size')
    direction = GObject.Property(type=Direction, default=Direction.EAST, nick='Direction')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_style_context().add_class('line')
        self.set_size_request(40, 40)
        self.pv = None
        self.palette = ColorSequence(self.colors)
        self.connect('realize', self.on_realize)

    def get_coords(self):
        allocation = self.get_allocation()
        x1 = x2 = y1 = y2 = 0
        if self.direction in [Direction.NORTH, Direction.SOUTH]:
            x1 = x2 = allocation.width / 2
        elif self.direction in [Direction.WEST, Direction.NORTH_WEST, Direction.SOUTH_WEST]:
            x1 = allocation.width
        elif self.direction in [Direction.EAST, Direction.NORTH_EAST, Direction.SOUTH_EAST]:
            x2 = allocation.width

        if self.direction in [Direction.NORTH, Direction.NORTH_WEST, Direction.NORTH_EAST]:
            y1 = allocation.height
        elif self.direction in [Direction.SOUTH, Direction.SOUTH_WEST, Direction.SOUTH_EAST]:
            y2 = allocation.height
        elif self.direction in [Direction.EAST, Direction.WEST]:
            y1 = y2 = allocation.height / 2

        return pix(x1), pix(y1), pix(x2), pix(y2)

    def do_draw(self, cr):
        # draw line
        x1, y1, x2, y2 = self.get_coords()

        if not self.color:
            self.props.color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)

        cr.set_source_rgba(*self.color)
        cr.set_line_width(self.line_width)

        cr.move_to(x1, y1)  # top left of the widget
        cr.line_to(x2, y2)
        cr.stroke()

        if self.arrow:
            w = self.arrow_size * 5
            ang = atan2(y2 - y1, x2 - x1) + pi
            a = pi / 12

            ax1 = x2 + w * cos(ang - a)
            ay1 = y2 + w * sin(ang - a)
            ax2 = x2 + w * cos(ang + a)
            ay2 = y2 + w * sin(ang + a)

            cr.move_to(x2, y2)
            cr.line_to(ax1, ay1)
            cr.stroke()
            cr.move_to(x2, y2)
            cr.line_to(ax2, ay2)
            cr.stroke()

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)

        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def on_change(self, pv, value):
        self.color = self.palette(int(value))


class Byte(ActiveMixin, AlarmMixin, BlankWidget):
    __gtype_name__ = 'Byte'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    offset = GObject.Property(type=int, minimum=0, maximum=4, default=0, nick='Byte Offset')
    count = GObject.Property(type=int, minimum=1, maximum=16, default=8, nick='Bit Count')
    shift = GObject.Property(type=int, minimum=0, maximum=16, default=0, nick='Bit Shift')
    inverted = GObject.Property(type=bool, default=False, nick='Inverted')
    big_endian = GObject.Property(type=bool, default=False, nick='Big-Endian')
    labels = GObject.Property(type=str, default='', nick='Labels')
    colors = GObject.Property(type=str, default="AG", nick='Value Colors')
    columns = GObject.Property(type=int, minimum=1, maximum=8, default=1, nick='Columns')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')
    spacing = GObject.Property(type=int, minimum=0, maximum=50, default=4, nick='Spacing')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('byte')
        self.get_style_context().add_class('byte')
        self._view_bits = '0' * self.count
        self._view_labels = [''] * self.count

        self.theme = {
            'border': Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0),
            'fill': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }
        self.connect('realize', self.on_realize)
        self.palette = ColorSequence(self.colors)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        actual_count = len([l for l in self._view_labels if l.strip()])
        stride = ceil(actual_count / self.columns)
        col_width = allocation.width / self.columns

        # draw boxes
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())

        cr.set_line_width(0.75)
        pos = 0
        for i in range(self.count):
            if not self._view_labels[i].strip(): continue
            x = pix((pos // stride) * col_width + self.spacing)
            y = pix(self.spacing + (pos % stride) * (self.size + self.spacing))
            cr.rectangle(x, y, self.size, self.size)
            if i < len(self._view_bits):
                color = self.palette(int(self._view_bits[i]))
                cr.set_source_rgba(*color)
                cr.fill_preserve()
            cr.set_source_rgba(*self.theme['border'])
            cr.stroke()

            if i < len(self._view_labels):
                cr.set_source_rgba(*self.theme['label'])
                label = self._view_labels[i]
                layout = self.create_pango_layout(label)
                ink, logical = layout.get_pixel_extents()
                cr.move_to(self.spacing + x + self.size, y + self.size / 2 - logical.height / 2)
                PangoCairo.show_layout(cr, layout)
            pos += 1

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)
        labels = [v.strip() for v in self.labels.split(',')]
        self._view_labels = labels + (self.count - len(labels)) * ['']
        actual_count = len([l for l in self._view_labels if l.strip()])

        stride = ceil(actual_count / self.columns)
        height = stride * self.size + (stride + 1) * self.spacing
        self.set_size_request(self.get_allocation().width, int(height))
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def on_change(self, pv, value):
        bit_string = f'{int(value):064b}'[::-1]
        byte_strings = [bit_string[i:i + 8] for i in range(0, 64, 8)]
        if self.big_endian:
            bit_string = "".join(byte_strings[::-1])

        start = self.offset * 8 + self.shift
        end = start + self.count
        self._view_bits = bit_string[start:end]

        if self.inverted:
            self._view_bits = self._view_bits[::-1]

        self.queue_draw()


class Indicator(ActiveMixin, AlarmMixin, BlankWidget):
    __gtype_name__ = 'Indicator'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    colors = GObject.Property(type=str, default="AG", nick='Value Colors')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')
    spacing = GObject.Property(type=int, minimum=0, maximum=50, default=4, nick='Spacing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('indicator')
        self.get_style_context().add_class('indicator')
        self.pv = None
        self.label_pv = None
        self.palette = ColorSequence(self.colors)
        self.theme = {
            'border': Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0),
            'fill': self.palette(0),
        }
        self.set_sensitive(False)
        self.connect('realize', self.on_realize)

    def do_draw(self, cr):
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())

        cr.set_line_width(0.75)
        cr.set_source_rgba(*self.theme['fill'])
        x = pix(self.spacing)
        y = pix(self.spacing / 2)

        cr.rectangle(x, y, self.size, self.size)
        cr.fill_preserve()
        cr.set_source_rgba(*self.theme['border'])
        cr.stroke()

        cr.set_source_rgba(*self.theme['label'])
        label = '<label>' if EDITOR and not self.label else self.label
        layout = self.create_pango_layout(label)
        ink, logical = layout.get_pixel_extents()
        cr.move_to(self.spacing + x + self.size, y + self.size / 2 - logical.height / 2)
        PangoCairo.show_layout(cr, layout)

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)
        self.set_size_request(self.get_allocation().width, self.size + self.spacing)
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.theme['fill'] = self.palette(int(value))
        self.queue_draw()


class ScaleControl(FontMixin, ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'ScaleControl'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    minimum = GObject.Property(type=float, default=0., nick='Minimum')
    maximum = GObject.Property(type=float, default=100., nick='Maximum')
    increment = GObject.Property(type=float, default=1., nick='Increment')
    digits = GObject.Property(type=int, minimum=0, maximum=5, default=1, nick='Decimals')
    marks = GObject.Property(type=int, minimum=0, maximum=10, default=2, nick='Marks')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.HORIZONTAL, nick='Orientation')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    inverted = GObject.Property(type=bool, default=False, nick='Inverted')
    labels = GObject.Property(type=str, default='', nick='Mark Labels')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pv = None
        self.in_progress = False
        self.adjustment = Gtk.Adjustment(50, 0, 100, 1, 0, 0)
        self.scale = Gtk.Scale()
        self.scale.set_adjustment(self.adjustment)
        self.connect('realize', self.on_realize)
        self.add(self.scale)
        self.bind_property('orientation', self.scale, 'orientation',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('inverted', self.scale, 'inverted',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('maximum', self.adjustment, 'upper',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('minimum', self.adjustment, 'lower',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('increment', self.adjustment, 'step-increment',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('digits', self.scale, 'digits',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        for signal in ['notify::marks', 'notify::digits', 'notify::labels']:
            self.connect(signal, self.update_marks)
        self.set_sensitive(False)

    def update_marks(self, *args):
        position = Gtk.PositionType.TOP if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.LEFT
        self.scale.clear_marks()
        mark_labels = [v.strip() for v in re.split(r'[,|;]', self.labels)] if self.labels else []
        for i, mark_value in enumerate(numpy.linspace(self.minimum, self.maximum, self.marks)):
            if not mark_labels:
                label = f'{{:0.{self.props.digits}f}}'.format(mark_value)
            elif i < len(mark_labels):
                if mark_labels[i] == '#':
                    label = f'{{:0.{self.props.digits}f}}'.format(mark_value)
                elif mark_labels[i]:
                    label = mark_labels[i]
                else:
                    label = None
            else:
                label = None
            self.scale.add_mark(mark_value, position, label)

    def on_realize(self, obj):
        value_pos = Gtk.PositionType.BOTTOM if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.RIGHT
        self.scale.props.value_pos = value_pos
        self.update_marks()
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.adjustment.connect('value-changed', self.on_value_set)
        else:
            self.adjustment.set_value(self.minimum)
        super().on_realize(obj)

    def on_change(self, pv, value):
        self.in_progress = True
        self.adjustment.set_value(value)
        self.in_progress = False

    def on_value_set(self, obj):
        if self.ready and not self.in_progress:
            if self.pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
                self.pv.put(self.adjustment.props.value)
            else:
                self.pv.put(int(round(self.adjustment.props.value)))


class TweakControl(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'TweakControl'
    PV_COPY_BUTTON = 1

    channel = GObject.Property(type=str, default='', nick='PV Name')
    minimum = GObject.Property(type=float, default=0., nick='Minimum')
    maximum = GObject.Property(type=float, default=100., nick='Maximum')
    increment = GObject.Property(type=float, default=1., nick='Increment')
    digits = GObject.Property(type=int, minimum=0, maximum=5, default=1, nick='Decimals')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    use_limits = GObject.Property(type=bool, default=False, nick='Use PV Limits')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pv = None
        self.in_progress = False

        self.tweak = Gtk.SpinButton()
        self.adjustment = self.tweak.get_adjustment()

        self.connect('realize', self.on_realize)
        self.add(self.tweak)
        self.bind_property('maximum', self.adjustment, 'upper',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('minimum', self.adjustment, 'lower',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('increment', self.adjustment, 'step-increment',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('digits', self.tweak, 'digits',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.tweak.connect('value-changed', self.on_value_set)

    def on_change(self, pv, value):
        self.in_progress = True
        self.tweak.set_value(value)
        self.in_progress = False

    def on_value_set(self, obj):
        if self.ready and not self.in_progress:
            self.pv.put(self.tweak.get_value())


class TextControl(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'TextControl'
    PV_COPY_BUTTON = 1
    channel = GObject.Property(type=str, default='', nick='PV Name')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    editable = GObject.Property(type=bool, default=True, nick='Editable')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    prec = GObject.Property(type=int, default=-1, minimum=-1, maximum=10, nick='Precision')
    sci = GObject.Property(type=bool, default=False, nick='Sci. Format')
    restore = GObject.Property(type=bool, default=True, nick='Restore Value')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.connect('realize', self.on_realize)
        self.entry = Gtk.Entry(width_chars=5)
        self.bind_property('xalign', self.entry, 'xalign',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('editable', self.entry, 'editable',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('editable', self.entry, 'sensitive',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('editable', self.entry, 'can-focus',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.entry.connect('activate', self.on_activate)
        self.entry.connect('focus-out-event', self.on_focus_out)
        self.entry.connect('focus-in-event', self.disable_restore)

        self.in_progress = False
        self.restore_src = None
        self.pv = None
        self.add(self.entry)

        self.set_sensitive(False)

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def disable_restore(self, *args, **kwargs):
        if self.restore_src:
            GLib.source_remove(self.restore_src)  #
        self.restore_src = None

    def on_focus_out(self, obj, event):
        # update value 5 seconds after losing focus
        if self.restore:
            self.restore_src = GLib.timeout_add(5000, self.restore_value)

    def restore_value(self):
        if self.pv:
            self.on_change(self.pv, self.pv.value)
        self.disable_restore()

    def on_change(self, pv, value):
        self.in_progress = True
        if pv.type in ['enum', 'time_enum', 'ctrl_enum']:
            text = pv.enum_strs[value]
        elif pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
            precision = self.prec if self.prec >= 0 else pv.precision
            if precision < 0:
                text = f'{pv.value:g}'
            elif self.sci:
                precision += 1
                text = f'{pv.value:.{precision}g}'
            else:
                text = f'{pv.value:.{precision}f}'
        else:
            text = pv.char_value

        self.entry.set_text(text)
        self.in_progress = False

    def on_activate(self, entry):
        text = self.entry.get_text()
        if self.pv.type in ['char', 'time_char', 'ctrl_char'] and self.pv.count > 1:
            converter = str
        else:
            converter = ENTRY_CONVERTERS[self.pv.type]
        try:
            value = converter(text)
            self.pv.put(value)
        except ValueError as e:
            logger.warn("Invalid Value: {}".format(e))


class TextEntryMonitor(ActiveMixin, Gtk.Box):
    __gtype_name__ = 'TextEntryMonitor'
    PV_COPY_BUTTON = 1
    tgt_channel = GObject.Property(type=str, default='', nick='Target PV')
    fbk_channel = GObject.Property(type=str, default='', nick='Feedback PV')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    colors = GObject.Property(type=str, default="", nick='Value Colors')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    prec = GObject.Property(type=int, default=-1, minimum=-1, maximum=10, nick='Precision')
    sci = GObject.Property(type=bool, default=False, nick='Sci. Format')
    restore = GObject.Property(type=bool, default=True, nick='Restore Value')
    show_units = GObject.Property(type=bool, default=True, nick='Show Units')

    def __init__(self, *args, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.connect('realize', self.on_realize)
        target = Gtk.Entry(width_chars=6)
        self.entries = {
            'target': target,
            'feedback': Gtk.Entry(width_chars=6, editable=False, can_focus=False)
        }

        for name, entry in self.entries.items():
            self.pack_start(entry, True, True, 0)
            self.bind_property('xalign', entry, 'xalign',
                               GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)

        self.entries['target'].connect('activate', self.on_activate)
        self.entries['target'].connect('focus-out-event', self.on_focus_out)
        self.entries['target'].connect('focus-in-event', self.disable_restore)
        self.pv = {
            'target': None,
            'feedback': None,
        }
        self.progress = {
            'target': False,
            'feedback': False,
        }
        self.restore_src = None

        ctx = self.get_style_context()
        ctx.add_class('linked')
        self.entries['feedback'].get_style_context().add_class('feedback')
        self.show_all()
        self.set_sensitive(False)

    def on_alarm(self, pv, alarm, name):
        if self.alarm:
            widget = self.entries[name]
            if alarm == gepics.Alarm.MAJOR:
                widget.get_style_context().remove_class('gtkdm-warning')
                widget.get_style_context().add_class('gtkdm-critical')
            elif alarm == gepics.Alarm.MINOR:
                widget.get_style_context().add_class('gtkdm-warning')
                widget.get_style_context().remove_class('gtkdm-critical')
            else:
                widget.get_style_context().remove_class('gtkdm-warning')
                widget.get_style_context().remove_class('gtkdm-critical')
            widget.queue_draw()

    def on_realize(self, obj):
        if not EDITOR:
            if self.tgt_channel and (not self.fbk_channel or self.tgt_channel == self.fbk_channel):
                pv = gepics.PV(self.tgt_channel)
                self.pv['target'] = pv
                self.pv['feedback'] = pv
            elif self.tgt_channel and self.fbk_channel:
                self.pv['target'] = gepics.PV(self.tgt_channel)
                self.pv['feedback'] = gepics.PV(self.fbk_channel)
            else:
                return

            for name, pv in self.pv.items():
                pv.connect('changed', self.on_change, name)
                pv.connect('alarm', self.on_alarm, name)

            self.pv['target'].connect('active', self.on_active)

    def disable_restore(self, *args, **kwargs):
        if self.restore_src:
            GLib.source_remove(self.restore_src)  #
        self.restore_src = None

    def on_focus_out(self, obj, event):
        # update value 5 seconds after losing focus
        if self.restore:
            self.restore_src = GLib.timeout_add(5000, self.restore_value)

    def restore_value(self):
        if self.pv['target']:
            self.on_change(self.pv['target'], self.pv['target'].value, 'target')
        self.disable_restore()

    def on_change(self, pv, value, name):
        self.progress[name] = True
        entry = self.entries[name]
        if pv.type in ['enum', 'time_enum', 'ctrl_enum']:
            text = pv.enum_strs[value]
        elif pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
            precision = self.prec if self.prec >= 0 else pv.precision
            if precision <= 0:
                text = f'{pv.value:0.5g}'
            elif self.sci:
                precision += 1
                text = f'{pv.value:.{precision}g}'
            else:
                text = f'{pv.value:.{precision}f}'
        else:
            text = pv.char_value
        if name == 'feedback' and pv.units and self.show_units:
            text = '{} {}'.format(text, pv.units)

        entry.set_text(text)
        self.progress[name] = False

    def on_activate(self, entry):
        text = self.entries['target'].get_text()
        pv = self.pv['target']
        if pv.type in ['char', 'time_char', 'ctrl_char'] and pv.count > 1:
            converter = str
        else:
            converter = ENTRY_CONVERTERS[pv.type]
        try:
            value = converter(text)
            pv.put(value)
        except ValueError as e:
            logger.warn("Invalid Value: {}".format(e))


class CommandButton(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'CommandButton'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    icon_name = GObject.Property(type=str, default='', nick='Icon Name')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = Gtk.Button()
        self.pv = None
        self.label_pv = None
        self.connect('realize', self.on_realize)
        self.button.connect('clicked', self.on_clicked)
        self.bind_property('label', self.button, 'label',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.button)
        self.set_sensitive(False)
        self.get_style_context().add_class('gtkdm-button')
        self.button.get_style_context().add_class('button')

    def on_clicked(self, button):
        if self.pv:
            self.pv.put(1)

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('active', self.on_active)

            if not (self.label or self.icon_name):
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)

        if self.icon_name:
            self.button.set_always_show_image(True)
            self.button.set_image(Gtk.Image.new_from_icon_name(self.icon_name, Gtk.IconSize.MENU))

        label = '<label>' if EDITOR and not (self.label or self.icon_name) else self.label
        if label:
            self.button.set_label(label)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()


class OnOffButton(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'OnOffButton'
    # channels
    on_channel = GObject.Property(type=str, default='', nick='On PV')
    off_channel = GObject.Property(type=str, default='', nick='Off PV')
    state_channel = GObject.Property(type=str, default='', nick='State PV')
    # values
    on_value = GObject.Property(type=int, default=0, nick='On Value')
    off_value = GObject.Property(type=int, default=1, nick='Off Value')
    on_state_value = GObject.Property(type=int, default=0, nick='On State')
    off_state_value = GObject.Property(type=int, default=1, nick='Off State')
    # labels
    on_label = GObject.Property(type=str, default='↑', nick='On Label')
    off_label = GObject.Property(type=str, default='↓', nick='Off Label')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ctx = self.get_style_context()
        ctx.add_class('tiny')
        self.button = Gtk.Button()
        self.state_pv = None
        self.state = None
        self.registry = {}

        self.connect('realize', self.on_realize)
        self.button.connect('clicked', self.on_clicked)
        self.bind_property('on_label', self.button, 'label',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.button)
        self.set_sensitive(False)
        self.get_style_context().add_class('gtkdm-button')
        self.button.get_style_context().add_class('button')

    def on_clicked(self, button):
        if self.ready and self.state:
            spec = self.registry[self.state]
            spec['pv'].put(spec['value'], wait=True)

    def on_state_change(self, obj, value):
        self.state = None
        self.button.set_sensitive(False)

        ctx = self.get_style_context()
        for cls in ['on-btn', 'off-btn']:
            ctx.remove_class(cls)

        for state, spec in self.registry.items():
            if value == spec['state']:
                self.state = state
                self.button.set_label(spec['label'])
                self.button.set_sensitive(True)
                ctx.add_class(f'{self.state}-btn')

    def on_realize(self, obj):
        self.registry = {
            'on': {
                'channel': self.off_channel,
                'value': self.off_value,
                'state': self.on_state_value,
                'label': self.off_label,
            },
            'off': {
                'channel': self.on_channel,
                'value': self.on_value,
                'state': self.off_state_value,
                'label': self.on_label
            },
        }
        self.button.set_label(self.on_label)
        if not EDITOR:
            self.state_pv = gepics.PV(self.state_channel)
            self.state_pv.connect('changed', self.on_state_change)
            self.state_pv.connect('active', self.on_active)
            for state, spec in self.registry.items():
                spec['pv'] = gepics.PV(spec['channel'])


class OnOffSwitch(ActiveMixin, AlarmMixin, Gtk.Bin):
    __gtype_name__ = 'OnOffSwitch'
    # channels
    on_channel = GObject.Property(type=str, default='', nick='On PV')
    off_channel = GObject.Property(type=str, default='', nick='Off PV')
    state_channel = GObject.Property(type=str, default='', nick='State PV')
    # values
    on_value = GObject.Property(type=int, default=1, nick='On Value')
    off_value = GObject.Property(type=int, default=0, nick='Off Value')
    on_state_value = GObject.Property(type=int, default=1, nick='On State')
    off_state_value = GObject.Property(type=int, default=0, nick='Off State')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('on-off')
        self.get_style_context().add_class('on-off')
        self.button = Gtk.Switch()
        ctx = self.get_style_context()
        ctx.add_class('tiny')
        self.state_pv = None

        self.registry = {}
        self.connect('realize', self.on_realize)
        self.button.connect('state-set', self.on_switch_change)
        self.add(self.button)
        self.set_sensitive(False)
        self.show_all()

    def on_switch_change(self, button, value):
        if self.ready:
            for state, spec in self.registry.items():
                if value == spec['active']:
                    spec['pv'].put(spec['value'])
                    break
        return True

    def on_state_change(self, obj, value):
        for state, spec in self.registry.items():
            if value == spec['state']:
                if self.button.get_state() != spec['active']:
                    self.button.set_state(spec['active'])
                break

    def on_realize(self, obj):
        self.registry = {
            'on': {
                'channel': self.on_channel,
                'value': self.on_value,
                'state': self.on_state_value,
                'active': True,
            },
            'off': {
                'channel': self.off_channel,
                'value': self.off_value,
                'state': self.off_state_value,
                'active': False,
            },
        }
        if not EDITOR:
            self.state_pv = gepics.PV(self.state_channel)
            self.state_pv.connect('changed', self.on_state_change)
            self.state_pv.connect('active', self.on_active)
            for state, spec in self.registry.items():
                spec['pv'] = gepics.PV(spec['channel'])


class MessageButton(CommandButton):
    __gtype_name__ = 'MessageButton'
    value = GObject.Property(type=str, default='', nick='Value')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_clicked(self, button):
        if self.pv and self.value:
            if self.pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
                converter = float
            elif self.pv.type in ['int', 'long', 'time_int', 'time_long', 'ctrl_int', 'ctrl_long']:
                converter = int
            else:
                converter = str
            try:
                value = converter(self.value)
                self.pv.put(value)
            except ValueError as e:
                logger.error('Invalid Value: {}'.format(e))


class ChoiceButton(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'ChoiceButton'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.VERTICAL, nick='Orientation')
    labels = GObject.Property(type=str, default='', nick='Labels')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.label_pv = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.connect('realize', self.on_realize)
        self.in_progress = False
        self.menu_labels = []
        self.bind_property('orientation', self.box, 'orientation',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.buttons = [Gtk.ToggleButton(label='One'), Gtk.ToggleButton(label='Two'), ]
        for i, btn in enumerate(self.buttons):
            self.box.pack_start(btn, False, False, 0)
            btn.connect('toggled', self.on_toggled, i)
        self.add(self.box)
        self.set_sensitive(False)
        self.box.get_style_context().add_class('linked')

    def on_toggled(self, button, i):
        if not self.in_progress:
            self.pv.put(i)

    def on_realize(self, obj):
        if self.labels.strip():
            self.menu_labels = [v.strip() for v in re.split(r'[,|;]', self.labels)]

        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('active', self.on_active)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('changed', self.on_change)

    def on_active(self, pv, connected):
        ActiveMixin.on_active(self, pv, connected)
        if connected:
            if pv.enum_strs and not self.menu_labels:  # if menu labels are provided, ignore enum strings
                labels = pv.enum_strs
            else:
                labels = self.menu_labels

            count = 0
            for i, label in enumerate(labels):
                if label:  # only add entry if label is not blank
                    if count < len(self.buttons):
                        self.buttons[count].props.label = label
                    else:
                        btn = Gtk.ToggleButton(label=label)
                        btn.connect('toggled', self.on_toggled, i)
                        self.buttons.append(btn)
                        self.box.pack_start(btn, False, False, 0)
                        btn.show()
                    count += 1

            for btn in self.buttons[count + 1:]:
                btn.destroy()

    def on_change(self, pv, value):
        self.in_progress = True
        for i, btn in enumerate(self.buttons):
            btn.set_active(i == value)
        self.in_progress = False


class ChoiceMenu(ActiveMixin, Gtk.EventBox):
    __gtype_name__ = 'ChoiceMenu'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    labels = GObject.Property(type=str, default='', nick='Labels')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.box = Gtk.ComboBoxText()
        self.connect('realize', self.on_realize)
        self.box.connect('changed', self.on_toggled)
        self.in_progress = False
        self.menu_labels = []
        self.add(self.box)
        self.box.get_style_context().add_class('linked')

        self.set_sensitive(False)

    def on_toggled(self, box):
        if not self.in_progress:
            active = self.box.get_active()
            if active >= 0:
                self.pv.put(active)

    def on_realize(self, obj):
        if self.labels.strip():
            self.menu_labels = [v.strip() for v in re.split(r'[,|;]', self.labels)]
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect_after('active', self.on_active)
            self.pv.connect('changed', self.on_change)

    def on_active(self, pv, connected):
        super().on_active(pv, connected)
        if connected:
            self.box.remove_all()
            if pv.enum_strs and not self.menu_labels:  # if menu labels are provided, ignore enum strings
                labels = pv.enum_strs
            else:
                labels = self.menu_labels

            for i, label in enumerate(labels):
                if label:  # only add entry if label is not blank
                    self.box.append_text(label)

    def on_change(self, pv, value):
        self.in_progress = True
        self.box.set_active(value)
        self.in_progress = False


class ShellButton(Gtk.Bin):
    __gtype_name__ = 'ShellButton'
    command = GObject.Property(type=str, default='', nick='Shell Command')
    label = GObject.Property(type=str, default='', nick='Label')
    icon_name = GObject.Property(type=str, default='', nick='Icon Name')
    multiple = GObject.Property(type=bool, default=False, nick='Allow Multiple')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ctx = self.get_style_context()
        ctx.add_class('tiny')
        self.button = Gtk.Button()
        self.button.connect('clicked', self.on_clicked)
        self.add(self.button)
        self.proc = None
        self.bind_property('label', self.button, 'label',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.show_all()
        self.get_style_context().add_class('gtkdm-button')
        self.button.get_style_context().add_class('button')

    def on_clicked(self, button):
        if self.command:
            if self.proc:
                self.proc.poll()
            if self.multiple or self.proc is None or self.proc.returncode is not None:
                cmds = shlex.split(self.command)
                if shutil.which(cmds[0]) is not None:
                    self.proc = subprocess.Popen(cmds, stdout=subprocess.DEVNULL)
                else:
                    logger.error(f"{cmds[0]} not found!")


class Gauge(ActiveMixin, BlankWidget):
    __gtype_name__ = 'Gauge'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    angle = GObject.Property(type=int, minimum=90, maximum=335, default=270, nick='Angle')
    step = GObject.Property(type=float, default=10., nick='Step Size')
    ticks = GObject.Property(type=int, default=5, nick='Ticks/Step')
    minimum = GObject.Property(type=float, default=0., nick='Minimum')
    maximum = GObject.Property(type=float, default=100., nick='Maximum')
    label = GObject.Property(type=str, default='', nick='Label')
    units = GObject.Property(type=bool, default=True, nick='Show Units')
    levels = GObject.Property(type=bool, default=False, nick='Show Levels')
    colors = GObject.Property(type=str, default='GOR', nick='Colors')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_css_name('gauge')
        self.get_style_context().add_class('gauge')
        self.set_size_request(120, 100)
        self.pv = None
        self.label_pv = None
        self.value = 0
        self.units_label = 'mA'
        self.connect('realize', self.on_realize)
        self.palette = ColorSequence(self.colors)
        self.ctrl_vars = {}

    def do_draw(self, cr):
        allocation = self.get_allocation()
        x = allocation.width / 2
        y = allocation.height / 2
        r = 4 * x / 6

        style = self.get_style_context()
        color = style.get_color(style.get_state())
        cr.set_source_rgba(*color)
        cr.set_line_width(0.75)

        minimum = (self.minimum // self.step) * self.step
        maximum = ceil(self.maximum // self.step) * self.step

        half_angle = self.angle / 2
        start_angle = radians(270 - half_angle)
        end_angle = radians(270 + half_angle)
        offset = r * sin(90 - radians(half_angle)) / 2
        angle_scale = (end_angle - start_angle) / (maximum - minimum)
        tick_width = 12
        y += offset
        cr.arc(x, y, r, start_angle, end_angle)
        cr.stroke()

        rt = r + tick_width
        r1 = r - tick_width / 2
        r0 = r + tick_width / 2

        major = ticks(minimum, maximum, self.step)
        minor = ticks(minimum, maximum, self.step / (self.ticks + 1))

        # levels
        cr.set_line_width(2)
        rl = 2 * r / 3
        if self.levels and self.ctrl_vars:
            lolo = self.ctrl_vars['lower_alarm_limit'] * angle_scale + start_angle
            lo = self.ctrl_vars['lower_warning_limit'] * angle_scale + start_angle
            hi = self.ctrl_vars['upper_warning_limit'] * angle_scale + start_angle
            hihi = self.ctrl_vars['upper_alarm_limit'] * angle_scale + start_angle

            if lolo > start_angle:
                cr.set_source_rgba(*self.palette(2, alpha=0.6))
                cr.arc(x, y, rl, start_angle, lolo)
                cr.stroke()
            if lo > lolo:
                cr.set_source_rgba(*self.palette(1, alpha=0.6))
                cr.arc(x, y, rl, lolo, lo)
                cr.stroke()
            if hi > lo:
                cr.set_source_rgba(*self.palette(0, alpha=0.6))
                cr.arc(x, y, rl, lo, hi)
                cr.stroke()
            if hihi > hi:
                cr.set_source_rgba(*self.palette(1, alpha=0.6))
                cr.arc(x, y, rl, hi, hihi)
                cr.stroke()
            if end_angle > hihi:
                cr.set_source_rgba(*self.palette(2, alpha=0.6))
                cr.arc(x, y, rl, hihi, end_angle)
                cr.stroke()

        # ticks4
        cr.set_line_width(0.75)
        for tick in set(minor + major):
            is_major = tick in major
            tick_angle = angle_scale * (tick - minimum) + start_angle
            rt2 = r0 if is_major else r
            tx1 = x + r1 * cos(tick_angle)
            ty1 = y + r1 * sin(tick_angle)
            tx2 = x + rt2 * cos(tick_angle)
            ty2 = y + rt2 * sin(tick_angle)

            cr.set_source_rgba(*color)
            if is_major:
                tx3 = x + rt * cos(tick_angle)
                ty3 = y + rt * sin(tick_angle)
                label = '{:g}'.format(tick)
                xb, yb, tw, th = cr.text_extents(label)[:4]
                cr.move_to(tx3 - xb - tw / 2, ty3 - yb - th / 2)
                cr.show_text(label)
            cr.move_to(tx2, ty2)
            cr.line_to(tx1, ty1)
            cr.stroke()

        # Units
        if self.units:
            units_angle = (end_angle + start_angle) / 2
            ur = r / 3
            ux2 = x + ur * cos(units_angle)
            uy2 = y + ur * sin(units_angle)
            xb, yb, tw, th = cr.text_extents(self.units_label)[:4]
            cr.set_source_rgba(*color)
            cr.move_to(ux2 - xb - tw / 2, uy2 - yb - th / 2)
            cr.show_text(self.units_label)

        # needle
        cr.save()
        cr.set_operator(cairo.OPERATOR_DIFFERENCE)
        cr.set_line_width(0.75)
        value_angle = angle_scale * (self.value - minimum) + start_angle
        vr = 5 * r / 6
        vx2 = x + vr * cos(value_angle)
        vy2 = y + vr * sin(value_angle)
        nx = 2 * sin(value_angle)
        ny = -2 * cos(value_angle)
        cr.set_source_rgba(*alpha(color, 0.5))
        cr.move_to(x - nx, y - ny)
        cr.line_to(vx2, vy2)
        cr.line_to(x + nx, y + ny)
        cr.fill_preserve()
        cr.stroke()
        cr.restore()

        # label
        if self.label:
            xb, yb, tw, th = cr.text_extents(self.label)[:4]
            w = int(len(self.label) * 0.6 * allocation.width / tw)
            if w > 0:
                lines = textwrap.wrap(self.label, w)
            else:
                lines = [self.label]
            cr.set_source_rgba(*color)
            yl = max(y, y + rt * sin(start_angle))
            for i, line in enumerate(lines):
                xb, yb, tw, th = cr.text_extents(line)[:4]
                cr.move_to(x - xb - tw / 2, yl + (i + 1.2) * th)
                cr.show_text(line)

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.value = value
        self.queue_draw()

    def on_active(self, pv, connected):
        if connected:
            try:
                self.ctrl_vars = pv.get_ctrlvars()
                self.units_label = self.pv.units
            except ChannelAccessGetFailure as e:
                self.units_label = ''
        super().on_active(pv, connected)


class SymbolFrames(object):
    registry = {}

    def __init__(self, path=None):
        self.frames = []
        self.width = 0
        self.height = 0
        if path:
            self.load_symbol_file(path)

    def load_symbol_file(self, path):
        with zipfile.ZipFile(path, 'r') as sym:
            index = json.loads(sym.read('symbol.json'))
            for frame in index:
                data = sym.read(frame)
                if frame.endswith('.sym'):  # nested symbols for animation
                    self.frames.append(SymbolFrames.new_from_data(data, os.path.join(path, frame)))
                else:
                    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
                    pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
                    self.width = max(self.width, pixbuf.get_width())
                    self.height = max(self.height, pixbuf.get_height())
                    self.frames.append(pixbuf)

    @classmethod
    def new_from_file(cls, path):
        full_path = os.path.abspath(path)
        if full_path in cls.registry:
            return cls.registry[full_path]
        else:
            sf = SymbolFrames(full_path)
            cls.registry[full_path] = sf
            return sf

    @classmethod
    def new_from_data(cls, data, path):
        full_path = os.path.abspath(path)
        if full_path in cls.registry:
            return cls.registry[full_path]
        else:
            sf = SymbolFrames(full_path)
            cls.registry[full_path] = sf
            return sf

    def __call__(self, value):
        if 0 <= abs(int(value)) < len(self.frames):
            return self.frames[int(value)]


class Symbol(ActiveMixin, BlankWidget):
    __gtype_name__ = 'Symbol'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    file = GObject.Property(type=str, nick='Symbol File')
    angle = GObject.Property(type=float, default=0, nick='Angle')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frames = None
        self.image = None
        self.connect('realize', self.on_realize)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        x = allocation.width / 2
        y = allocation.height / 2
        if self.image:
            scale = min(allocation.width / self.frames.width, allocation.height / self.frames.height)
            w = self.image.get_width() * scale
            h = self.image.get_height() * scale
            pixbuf = self.image.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
            if self.angle != 0:
                cr.translate(x, y)
                cr.rotate(self.angle * pi / 180.0)
                cr.translate(-x, -y)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, x - w / 2, y - h / 2)
            cr.paint()
        else:
            # draw boxes
            style = self.get_style_context()
            color = style.get_color(style.get_state())
            cr.set_source_rgba(*color)
            cr.set_line_width(.25)
            cr.rectangle(1.5, 1.5, allocation.width - 3, allocation.height - 3)
            cr.stroke()

    def on_realize(self, widget):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('active', self.on_active)

        if self.file:
            symbol_path = Manager.find_display(self.file)
            self.frames = SymbolFrames.new_from_file(symbol_path)
            self.image = self.frames(-1)

    def on_change(self, pv, value):
        self.image = self.frames(value)
        self.queue_draw()


class Diagram(BlankWidget):
    __gtype_name__ = 'Diagram'
    pixbuf = GObject.Property(type=GdkPixbuf.Pixbuf, nick='Image File')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        vw = allocation.width
        vh = allocation.height

        if self.pixbuf:
            iw = self.pixbuf.get_width()
            ih = self.pixbuf.get_height()
            scale = min(vw / iw, vh / ih)
            xoff = (vw - iw * scale) / 2
            yoff = (vh - ih * scale) / 2
            cr.save()
            cr.translate(xoff, yoff)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
            cr.paint()
            cr.restore()

        else:
            # draw boxes
            style = self.get_style_context()
            color = style.get_color(style.get_state())
            cr.set_source_rgba(*color)
            cr.rectangle(pix(1), pix(1), allocation.width - 2, allocation.height - 2)
            cr.stroke()


class Vessel(ActiveMixin, BlankWidget):
    __gtype_name__ = 'Vessel'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    kind = GObject.Property(type=str, nick='Type')
    scale = GObject.Property(type=float, default=1, nick='Value Scale')
    offset = GObject.Property(type=float, default=0, nick='Value Offset')
    animate = GObject.Property(type=bool, default=False, nick='Animate Surface')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    yalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='Y-Alignment')
    margin = GObject.Property(type=float, minimum=0.0, maximum=0.5, default=0.05, nick='Margin Fraction')
    body = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.8, nick='Body Fraction')
    shelves = GObject.Property(type=int, minimum=0, maximum=4, default=0, nick='# Shelves')
    ripples = GObject.Property(type=int, minimum=0, maximum=6, default=6, nick='# Ripples')

    class Type(Enum):
        RECTANGLE = 0
        COLUMN = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ready = False
        self.ripple_height = 0.025
        self.value = 0.0
        self.gap = 2
        self.pv = None
        self.colors = {
            'vessel': (0.0, 0.0, 0.0, 0.5),
            'liquid': (1.0, 1.0, 1.0, 0.5)
        }
        self.config = {}
        self.connect('size-allocate', self.setup)

    def do_realize(self, *args):
        super().do_realize(*args)
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('active', self.on_active)
        style = self.get_style_context()
        self.colors['vessel'] = self.get_style_context().get_color(style.get_state())

    def do_notify(self, *args):
        self.setup(self, self.get_allocation())
        super().do_notify(*args)

    def on_active(self, pv, connected):
        super().on_active(pv, connected)
        if connected:
            self.colors['liquid'] = (0.2, 0.75, 0.85, .75)
            if self.props.animate:
                GLib.timeout_add(100, self.animate_surface)
        else:
            self.colors['liquid'] = (1.0, 1.0, 1.0, 0.5)

    def on_change(self, pv, value):
        self.value = (value - self.offset) * self.scale
        self.queue_draw()

    def setup(self, widget, alloc):
        x_margin = pix(self.props.margin * alloc.width)
        y_margin = pix(self.props.margin * alloc.height)
        width = alloc.width - 2 * x_margin
        height = alloc.height - 2 * y_margin
        full = self.props.body * height

        self.config = {
            'x_margin': x_margin,
            'y_margin': y_margin,
            'width': width,
            'height': height,
            'full': full,
            'base': (height - full)/2,
            'label_pos': (x_margin + width * self.props.xalign, y_margin + height * self.props.yalign),
            'matrix': cairo.Matrix(1.0, 0.0, 0.0, -1.0, 0.0, alloc.height),
        }
        self.ready = True

    def animate_surface(self):
        self.ripple_height = 0.025 * numpy.sin(time.time() * 5)
        self.queue_draw()
        return self.pv is not None and self.pv.is_active()

    def do_draw(self, cr):
        if not self.ready:
            return
        elif self.kind == 'rectangle':
            self.draw_rect(cr)
        elif self.kind == 'column':
            self.draw_column(cr)

    def draw_liquid_surface(self, cr):
        # liquid surface
        width = self.config['width'] - 2*self.gap
        if self.props.ripples:
            height = self.config['height']
            w = width / (self.props.ripples * 3)
            h = self.ripple_height * height
            for i in range(self.props.ripples):
                cr.rel_curve_to(w, -h, 2 * w, h, 3 * w, 0)
        else:
            cr.rel_line_to(width, 0)

    def draw_level(self, cr):
        cr.save()
        cr.set_source_rgba(1, 1, 1, 1)
        cx, cy = self.config['label_pos']
        label = f'{self.value:0.2g} %'
        layout = self.create_pango_layout(label)
        ink, logical = layout.get_pixel_extents()
        cr.move_to(cx - logical.width / 2, cy - logical.height / 2)
        cr.set_operator(cairo.OPERATOR_DIFFERENCE)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def draw_shelves(self, cr):
        if self.props.shelves:
            # setup variables
            x_margin = self.config['x_margin']
            y_margin = self.config['y_margin']
            width = self.config['width']
            height = self.config['height']
            full = self.config['full']
            base = self.config['base']

            cr.set_line_width(0.5)
            if self.kind in ['column', 'rectangle', 'tank', 'cylinder']:
                separation = (full - base)
                shelf = separation / 4
                bottom = (height - separation)/2
                for i in range(self.props.shelves - 1):
                    cr.move_to(x_margin, y_margin + bottom + i*shelf)
                    cr.rel_line_to(width, 0)
                    if i < 3:
                        cr.move_to(x_margin, y_margin + bottom + separation - i*shelf)
                        cr.rel_line_to(width, 0)
                cr.stroke()

    def draw_rect(self, cr):
        cr.save()
        cr.transform(self.config['matrix']) # flip Y axis

        # setup variables
        x_margin = self.config['x_margin']
        y_margin = self.config['y_margin']
        width = self.config['width']
        height = self.config['height']
        full = self.config['full']
        base = self.config['base']
        cx, cy = self.config['label_pos']
        contents = int(self.value * full / 100)

        # vessel body
        cr.set_source_rgba(*self.colors['vessel'])  # vessel walls
        cr.set_line_width(1.5)
        cr.rectangle(x_margin, y_margin, width, height)
        cr.stroke()
        self.draw_shelves(cr)

        # contents
        width = width - 2 * self.gap
        cr.set_source_rgba(*self.colors['liquid'])
        cr.move_to(x_margin + self.gap, y_margin + self.gap)
        cr.rel_line_to(0, contents + base)

        self.draw_liquid_surface(cr) # liquid surface

        cr.rel_line_to(0, -(contents + base))
        cr.rel_line_to(-width, 0)
        cr.close_path()
        cr.fill()

        cr.restore()
        cr.save()

        self.draw_level(cr) # show level

    def draw_column(self, cr):
        cr.save()
        cr.transform(self.config['matrix']) # flip Y axis

        # setup variables
        x_margin = self.config['x_margin']
        y_margin = self.config['y_margin']
        width = self.config['width']
        height = self.config['height']
        full = self.config['full']
        base = self.config['base']
        cx, cy = self.config['label_pos']
        contents = int(self.value * full / 100)

        # vessel
        cr.set_line_width(1.5)
        cr.set_source_rgba(*self.colors['vessel'])  # vessel walls
        cr.move_to(x_margin, y_margin)
        cr.rel_move_to(0, base)
        cr.rel_line_to(0, full)
        cr.rel_curve_to(0, base, width, base, width, 0)
        cr.rel_line_to(0, -full)
        cr.rel_curve_to(0, -base, -width, -base, -width, 0)
        cr.stroke()
        self.draw_shelves(cr)

        # contents
        width -= 2 * self.gap
        base -= self.gap / 2
        cr.set_source_rgba(*self.colors['liquid'])
        cr.move_to(x_margin + self.gap, y_margin + self.gap)
        cr.rel_move_to(0, base)
        cr.rel_line_to(0, contents)

        self.draw_liquid_surface(cr)

        cr.rel_line_to(0, -contents)
        cr.rel_curve_to(0, -base, -width, -base, -width, 0)
        cr.close_path()
        cr.fill()
        cr.restore()

        self.draw_level(cr)


class CheckControl(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'CheckControl'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.btn = Gtk.CheckButton(label=self.label)
        self.in_progress = False
        self.add(self.btn)
        self.pv = None

        self.btn.connect('toggled', self.on_toggle)
        self.bind_property('label', self.btn, 'label', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.connect('realize', self.on_realize)

    def on_toggle(self, obj):
        if not self.in_progress:
            self.pv.put(int(obj.get_active()))

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.in_progress = True
        self.btn.set_active(bool(value))
        self.in_progress = False


class DisplayButton(Gtk.Bin):
    """
    A button for launching single related displays
    """
    __gtype_name__ = 'DisplayButton'
    label = GObject.Property(type=str, default='', nick='Label')
    display = GObject.Property(type=str, default='', nick='Display File')
    macros = GObject.Property(type=str, default='', nick='Macros')
    frame = GObject.Property(type=DisplayFrame, nick='Target Frame')
    multiple = GObject.Property(type=bool, default=False, nick='Allow Multiple')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = Gtk.Button(label=self.label)
        self.button.connect('clicked', self.on_clicked)
        self.bind_property('label', self.button, 'label',
                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.button)
        ctx = self.get_style_context()
        ctx.add_class('tiny')
        self.get_style_context().add_class('gtkdm-button')
        self.button.get_style_context().add_class('button')

    def on_clicked(self, button):
        if self.display and not EDITOR:
            if self.frame:
                Manager.embed_display(self.frame, self.display, macros_spec=self.macros)
            else:
                Manager.show_display(self.display, macros_spec=self.macros, multiple=self.multiple)


class Shape(ActiveMixin, AlarmMixin, BlankWidget):
    """
    A drawing of a rectangle or oval with fill color determined by a process variable and optional label.
    """
    __gtype_name__ = 'Shape'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    labelled = GObject.Property(type=bool, default=False, nick='Show Label')
    filled = GObject.Property(type=bool, default=False, nick='Fill Shape')
    colors = GObject.Property(type=str, default='RGB', nick='Fill Colors')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    oval = GObject.Property(type=bool, default=False, nick='Oval')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style = self.get_style_context()
        self.theme = {
            'border': style.get_color(style.get_state())
        }
        self.value = 0
        self.connect('realize', self.on_realize)
        self.palette = ColorSequence(self.colors)

    def do_draw(self, cr):
        # draw boxes
        allocation = self.get_allocation()

        cr.set_line_width(0.75)
        width = min(allocation.width - 2, allocation.height - 2)
        cr.set_font_size(min(2 * width // 5, 12))
        x = pix(allocation.width / 2)
        y = pix(allocation.height / 2)

        if self.oval:
            cr.arc(x, y, width / 2, 0, 2 * pi)
        else:
            cr.rectangle(x - width // 2, y - width // 2, width, width)
        if self.filled:

            try:
                color = self.palette(int(self.value))
            except ValueError:
                color = self.palette(0)
            cr.set_source_rgba(*color)
            cr.fill_preserve()
        cr.set_source_rgba(*self.theme['border'])
        cr.stroke()
        if self.labelled:
            xb, yb, w, h = cr.text_extents(self.label)[:4]
            cr.move_to(x - xb - w / 2, y - yb - h / 2)
            cr.show_text(self.label)
            cr.stroke()

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)
        style = self.get_style_context()
        self.theme = {
            'border': style.get_color(style.get_state())
        }
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.value = value
        self.queue_draw()


class MenuButton(Gtk.Bin):
    """
    A Menu Button for launching DisplayMenu popovers
    """
    __gtype_name__ = 'MenuButton'
    label = GObject.Property(type=str, default='', nick='Label')
    menu = GObject.Property(type=Gtk.Popover, nick='Display Menu')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ctx = self.get_style_context()
        self.btn = Gtk.MenuButton(use_popover=True)
        self.icon = Gtk.Image.new_from_icon_name('pan-down-symbolic', Gtk.IconSize.MENU)
        self.text = Gtk.Label(label=self.label)
        child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        child.pack_start(self.icon, False, False, 0)
        child.pack_start(self.text, True, True, 0)
        self.btn.add(child)
        self.add(self.btn)
        self.bind_property('label', self.text, 'label', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property('menu', self.btn, 'popover', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)


class DisplayMenu(Gtk.Popover):
    """
    A Popover menu for DisplayMenuItem entries
    """
    __gtype_name__ = 'DisplayMenu'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_border_width(3)


class DisplayMenuItem(Gtk.Bin):
    """
    A menu item for a display menu linking to a related display.
    """
    __gtype_name__ = 'DisplayMenuItem'
    file = GObject.Property(type=str, default='', nick='Display')
    label = GObject.Property(type=str, default='', nick='Label')
    macros = GObject.Property(type=str, default='', nick='Macros')
    multiple = GObject.Property(type=bool, default=False, nick='Allow Multiple')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry = Gtk.ModelButton(text=self.label)
        self.entry.set_size_request(100, -1)
        self.bind_property('label', self.entry, 'text', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.entry)
        self.entry.connect('clicked', self.on_clicked)

        self.show_all()

    def on_clicked(self, obj):
        if self.file and not EDITOR:
            Manager.show_display(self.file, macros_spec=self.macros, multiple=self.multiple)


class ShellMenuItem(Gtk.Bin):
    """
    A menu item for a display menu linking to a shell command.
    """
    __gtype_name__ = 'ShellMenuItem'
    label = GObject.Property(type=str, default='', nick='Label')
    command = GObject.Property(type=str, default='', nick='Command')
    multiple = GObject.Property(type=bool, default=False, nick='Allow Multiple')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry = Gtk.ModelButton(text=self.label)
        self.entry.set_size_request(100, -1)
        self.bind_property('label', self.entry, 'text', GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        self.add(self.entry)
        self.proc = None
        self.entry.connect('clicked', self.on_clicked)
        self.show_all()

    def on_clicked(self, button):
        if self.command and not EDITOR:
            if self.proc:
                self.proc.poll()
            if self.multiple or self.proc is None or self.proc.returncode is not None:
                print(shutil.which('gtkdm-charting'))
                self.proc = subprocess.Popen(self.command, shell=True, stdout=subprocess.DEVNULL)


class MessageLog(FontMixin, ActiveMixin, Gtk.EventBox):
    """
    A rolling log viewer displaying values from the process variable with optional time prefix and alarm colors.
    """
    __gtype_name__ = 'MessageLog'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    buffer_size = GObject.Property(type=int, default=5000, nick='Buffer Size')
    show_time = GObject.Property(type=bool, default=True, nick='Show Time')

    font_size = GObject.Property(type=int, minimum=-3, maximum=3, default=0, nick='Font Size')
    monospace = GObject.Property(type=bool, default=False, nick='Monospace Font')
    bold = GObject.Property(type=bool, default=False, nick='Bold Font')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view = Gtk.TextView()
        self.buffer = Gtk.TextBuffer()
        self.sw = Gtk.ScrolledWindow()
        self.sw.set_shadow_type(Gtk.ShadowType.IN)
        self.sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        self.view.set_buffer(self.buffer)
        self.view.set_editable(False)
        self.view.set_border_width(3)
        self.wrap_mode = Gtk.WrapMode.WORD
        self.sw.add(self.view)
        self.add(self.sw)
        self.adj = self.sw.get_vadjustment()
        self.tags = {
            gepics.Alarm.MAJOR: self.buffer.create_tag(foreground='Red', wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.MINOR: self.buffer.create_tag(foreground='Orange', wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.NORMAL: self.buffer.create_tag(wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.INVALID: self.buffer.create_tag(foreground='Gray', wrap_mode=Gtk.WrapMode.WORD),
        }
        self.active_tag = self.tags[gepics.Alarm.NORMAL]
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
        super().on_realize(obj)

    def on_change(self, pv, value):
        lines = self.buffer.get_line_count()
        if lines > self.buffer_size:
            start_iter = self.buffer.get_start_iter()
            end_iter = self.buffer.get_start_iter()
            end_iter.forward_lines(10)
            self.buffer.delete(start_iter, end_iter)

        _iter = self.buffer.get_end_iter()
        if self.show_time:
            text = "{} - {}\n".format(datetime.now().strftime("%m/%d %H:%M:%S"), value)
        else:
            text = "{}\n".format(value)
        self.buffer.insert_with_tags(_iter, text, self.active_tag)
        self.adj.set_value(self.adj.get_upper() - self.adj.get_page_size())

    def on_alarm(self, pv, alarm):
        if self.alarm:
            self.active_tag = self.tags[alarm]


class HideSwitch(Gtk.Bin):
    """
    A Switch to which widgets are attached. The visibility of attached widgets follows the active state of the switch.
    """
    __gtype_name__ = 'HideSwitch'
    widgets = GObject.Property(type=str, nick='Widgets')
    default = GObject.Property(type=bool, default=False, nick='Show by default')

    def __init__(self):
        super().__init__()
        self.btn = Gtk.Switch(active=True)
        self.add(self.btn)
        self.btn.connect('realize', self.on_realize)

    def on_realize(self, obj):
        top_level = self.get_toplevel()
        if isinstance(top_level, DisplayWindow):
            for name in self.widgets.split(','):
                w = top_level.builder.get_object(name.strip())
                if w:
                    self.btn.bind_property('active', w, 'visible',
                                           GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE)
        GLib.timeout_add(2000, self.btn.set_active, self.default)
        # self.btn.set_active(self.default)


Y_AXIS_OFFSET = 60
AXIS_SPACE = 0.92


class Plot(Gtk.Bin):
    __gtype_name__ = 'Plot'

    scheme = GObject.Property(type=str, default="default", nick='Plot Style')
    dpi = GObject.Property(type=int, default=72, minimum=50, maximum=500, nick='DPI')
    legend = GObject.Property(type=bool, default=False, nick='Show Legend')
    marker_size = GObject.Property(type=float, default=5, minimum=0, maximum=50, nick='Marker Size')
    strip_plot = GObject.Property(type=bool, default=False, nick='Strip Plot')
    specs = GObject.Property(type=str, nick='Specification File')
    macros = GObject.Property(type=str, default='', nick='Macros')

    refresh = GObject.Property(type=float, default=1, minimum=0, maximum=10, nick='Redraw Freq (hz)')
    sample = GObject.Property(type=float, default=1, minimum=0, maximum=10, nick='Sample Freq (hz)')
    period = GObject.Property(type=int, default=60, minimum=1, nick='Display Period (s)')
    buffer = GObject.Property(type=int, default=1, minimum=1, nick='Buffer Size')
    y_margin = GObject.Property(type=float, default=2, minimum=0, maximum=5, nick='Y-margin (std)')

    def __init__(self):
        super().__init__()
        self.connect('realize', self.on_realize)
        self.figure = None
        self.canvas = None
        self.info = {
            'specs': {},
            'data': [],
            'plots': [],
            'axes': {},
            'selectors': []
        }
        self.show_all()

    def destroy(self):
        for data in self.info['data']:
            data.destroy()
        super().destroy()

    def load_specs(self):
        if not self.specs.strip():
            return {}

        full_spec_path = Manager.find_display(self.specs)
        spec_file = Path(full_spec_path)
        if spec_file.exists():
            with open(spec_file, 'r') as fobj:
                try:
                    specs = yaml.safe_load(fobj)
                except yaml.YAMLError as err:
                    logger.error('Invalid File Format')
                    specs = {}
        else:
            specs = {}

        macros = utils.parse_macro_spec(self.macros)
        if macros:

            for field in ['x-label', 'y-label', 'y1-label', 'y2-label']:
                if field in specs:
                    specs[field] = specs[field].format(**macros)
            for group in specs.get('series', []):
                if 'x-data' in group:
                    group['x-data'] = group['x-data'].format(**macros)

                for item in group['y-data']:
                    for field in ['y', 'style', 'label', 'y1', 'y2']:
                        if field in item:
                            item[field] = item[field].format(**macros)
        return specs

    def setup_axes(self, count):
        specs = self.info['specs']

        host = self.figure.add_subplot()
        host.set_xlim(*specs.get('x-limits', (None, None)))
        self.info['axes']['y'] = host

        for i in range(1, min(count, 3)):
            axis = host.twinx()
            axis.spines["right"].set_position(("outward", Y_AXIS_OFFSET * (i - 1)))
            axis.xaxis.set_ticks([])
            self.info['axes'][f'y{i}'] = axis

        for key in ["y", "y1", "y2"]:
            axis = self.info['axes'].get(key)
            if not axis: continue
            self.info['axes'][key].set_ylabel(specs.get(f'{key}-label', None))
            axis.set_ylim(*specs.get(f'{key}-limits', (None, None)))

        if self.strip_plot:
            host.set_xlabel(datetime.now().strftime("%b %d, %H:%M:%S"), loc='right')
        elif self.info['specs'].get('x-label'):
            host.set_xlabel(self.info['specs'].get('x-label'), loc='center')

    def on_realize(self, obj):
        self.info['specs'] = self.load_specs()
        specs = self.info['specs']
        y_axes = {item.get('axis', 'y') for group in specs['series'] for item in group['y-data']}

        # with plt.xkcd():
        with style_context(self.scheme):
            self.figure = Figure(dpi=self.dpi)
            self.figure.set_tight_layout(True)
            self.canvas = FigureCanvas(self.figure)
            self.setup_axes(len(y_axes))

        self.add(self.canvas)
        self.show_all()

        if specs:
            if self.strip_plot:
                data_class = StripData
                data_args = {'period': self.period}
            else:
                data_class = XYData
                data_args = {'buffer': self.buffer}

            handles = []
            for i, group in enumerate(specs['series']):
                data_names = [] if self.strip_plot else [group.get('x-data', "#")]
                artists = []
                selectors = []
                for item in group['y-data']:
                    data_names.append(item['y'])
                    axis = self.info['axes'][item.get('axis', 'y')]
                    item.get("style", "-")

                    ln, = axis.plot(
                        [], [], item.get("style", "-"), ms=self.marker_size, label=item.get('label', item["y"])
                    )
                    artists.append(ln)
                    selectors.append({'y': 0, 'y1': 1, 'y2': 2}[item.get('axis', 'y')])
                self.info['plots'].append(artists)
                self.info['selectors'].append(numpy.array(selectors))
                handles.extend(artists)
                if not EDITOR:
                    data = data_class(*data_names, **data_args, sample_freq=self.sample, refresh_freq=self.refresh)
                    self.info['data'].append(data)
                    data.connect('changed', self.on_data_changed, i)

            if self.legend:
                self.info['axes']["y"].legend(
                    handles=handles, frameon=False, loc="center",
                    bbox_to_anchor=(0., -0.4, 1., .2), ncol=3, mode="expand", borderaxespad=0,
                )

    def on_data_changed(self, plot, i):
        artists = self.info['plots'][i]
        selectors = self.info['selectors'][i]
        x_data = plot.x_data()
        y_data = plot.y_data()
        if x_data is None or y_data is None:
            return

        for j in range(plot.count - 1):
            ln = artists[j]
            ln.set_data(x_data, y_data[:, j])

        # update x-limits if not explicitly set
        if self.strip_plot:
            # update x-limits if not explicitly set
            xmin, xmax = -plot.period, 0
            if xmin != xmax:
                self.info['axes']['y'].set_xlim(xmin, xmax)
        elif not self.info['specs'].get('x-limits') and not numpy.isnan(x_data).all():
            vx_min, vx_max = numpy.nanmin(x_data), numpy.nanmax(x_data)
            if vx_min != vx_max:
                self.info['axes']['y'].set_xlim(vx_min, vx_max)

        # y-limits are special, update them based on all children
        axis_names = ["y", "y1", "y2"]
        for k in numpy.unique(selectors):
            axis = self.info['axes'][axis_names[k]]
            sel = (selectors == k)
            if not self.info['specs'].get('y-limits') and not numpy.isnan(y_data[:, sel]).all():
                vy_min, vy_max = numpy.nanmin(y_data[:, sel]), numpy.nanmax(y_data[:, sel])
                dev = numpy.nanstd(y_data[:, sel])
                if vy_min != vy_max or dev > 0:
                    axis.set_ylim(vy_min - self.y_margin * dev, vy_max + self.y_margin * dev)

        if self.strip_plot:
            self.info['axes']["y"].set_xlabel(datetime.now().strftime("%b %d, %H:%M:%S"), loc='right')

        self.canvas.draw_idle()
