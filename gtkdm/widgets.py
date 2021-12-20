import hashlib
import json
import os
import re
import subprocess
import textwrap
import time
import zipfile
from datetime import datetime
from math import atan2, pi, cos, sin, ceil

import gi
import numpy

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', "1.0")
from gi.repository import Gtk, GObject, Gdk, Gio, GdkPixbuf, GLib, PangoCairo

from epics.ca import ChannelAccessGetFailure
import gepics
import xml.etree.ElementTree as ET

from . import utils, colors, version, PLUGIN_DIR
from .utils import logger

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
                    window.connect('destroy', lambda x: self.registry.pop(key))
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
        except ValueError:
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

    def on_active(self, pv, connected):
        self.copy_text = pv.name
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self.on_mouse_press)
        self.set_tooltip_text(self.copy_text)
        if connected:
            try:
                self.ctrlvars = pv.get_with_metadata(with_ctrlvars=True)
            except ChannelAccessGetFailure:
                self.ctrlvars = {}
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
        self.get_style_context().add_class('gtkdm-window')

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


class DisplayFrame(Gtk.Bin):
    __gtype_name__ = 'DisplayFrame'
    label = GObject.Property(type=str, default='', nick='Label')
    shadow_type = GObject.Property(type=Gtk.ShadowType, default=Gtk.ShadowType.NONE, nick='Shadow Type')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    yalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='Y-Alignment')
    xscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='X-Scale')
    yscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='Y-Scale')
    display = GObject.Property(type=str, default='', nick='Default Display')
    macros = GObject.Property(type=str, default='', nick='Default Macros')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.box = Gtk.Frame()
        self.frame = Gtk.Alignment()
        self.box.add(self.frame)
        self.add(self.box)
        for prop in ['xalign', 'yalign', 'xscale', 'yscale']:
            self.bind_property(prop, self.frame, prop, GObject.BindingFlags.DEFAULT)
        self.bind_property('label', self.box, 'label', GObject.BindingFlags.DEFAULT)
        self.bind_property('shadow-type', self.box, 'shadow-type', GObject.BindingFlags.DEFAULT)
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
        self.label = Gtk.Label('...')
        self.add(self.label)
        self.pv = None
        self.connect('realize', self.on_realize)
        self.bind_property('xalign', self.label, 'xalign', GObject.BindingFlags.DEFAULT)
        self.get_style_context().add_class('gtkdm')
        self.palette = ColorSequence(self.colors)

    def on_realize(self, obj):
        style = self.get_style_context()
        self.palette = ColorSequence(self.colors)

        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

        super().on_realize(obj)

    def on_change(self, pv, value):
        if pv.type in ['enum', 'time_enum', 'ctrl_enum']:
            text = pv.enum_strs[value]
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
            text = pv.char_value

        if self.pv.units and self.show_units:
            text = '{} {}'.format(text, pv.units)
        if self.colors:
            text = '<span color="{}">{}</span>'.format(self.palette[value], text)
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
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.desc_label = Gtk.Label('Description', xalign=0.0)
        self.value_label = Gtk.Label('Value')
        self.box.pack_start(self.desc_label, False, False, 0)
        self.box.pack_end(self.value_label, True, True, 0)
        self.add(self.box)
        self.pv = None
        self.label_pv = None
        self.connect('realize', self.on_realize)
        self.bind_property('xalign', self.value_label, 'xalign', GObject.BindingFlags.DEFAULT)
        self.bind_property('label', self.desc_label, 'label', GObject.BindingFlags.DEFAULT)
        self.get_style_context().add_class('gtkdm')
        self.desc_label.get_style_context().add_class('panel-desc')
        self.palette = ColorSequence(self.colors)

    def on_realize(self, obj):
        main_style = self.get_style_context()
        style = self.value_label.get_style_context()
        desc_style = self.desc_label.get_style_context()
        self.palette = ColorSequence(self.colors)
        main_style.add_class('panel')
        style.add_class('panel-value')
        desc_style.add_class('panel-desc')

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
        self.bind_property('text', self.label, 'label', GObject.BindingFlags.DEFAULT)
        self.bind_property('xalign', self.label, 'xalign', GObject.BindingFlags.DEFAULT)
        self.add(self.label)
        self.connect('realize', self.on_realize)
        self.get_style_context().add_class('gtkdm')

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
        self.bind_property('xalign', self.label, 'xalign', GObject.BindingFlags.DEFAULT)
        self.add(self.label)
        self.connect('realize', self.on_realize)
        self.get_style_context().add_class('gtkdm')

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
    count = GObject.Property(type=int, minimum=1, maximum=8, default=8, nick='Bit Count')
    big_endian = GObject.Property(type=bool, default=False, nick='Big-Endian')
    labels = GObject.Property(type=str, default='', nick='Labels')
    colors = GObject.Property(type=str, default="AG", nick='Value Colors')
    columns = GObject.Property(type=int, minimum=1, maximum=8, default=1, nick='Columns')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        stride = ceil(self.count / self.columns)
        col_width = allocation.width / self.columns

        # draw boxes
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())

        cr.set_line_width(0.75)
        margin = 4
        for i in range(self.count):
            x = pix((i // stride) * col_width + margin)
            y = pix(margin + (i % stride) * (self.size + 5))
            cr.rectangle(x, y, self.size, self.size)
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
                cr.move_to(2 * margin + x + self.size, y + self.size / 2 - logical.height / 2)
                PangoCairo.show_layout(cr, layout)

                # xb, yb, w, h = cr.text_extents(label)[:4]
                # cr.move_to(x + self.size + 4.5, y + self.size / 2 - yb - h / 2)
                # cr.show_text(label)
                # cr.stroke()

    def on_realize(self, widget):
        v = (self.size + 5) * int(round(self.count / self.columns))
        # self.set_size_request(100, v)
        self.palette = ColorSequence(self.colors)
        labels = [v.strip() for v in self.labels.split(',')]
        self._view_labels = labels + (self.count - len(labels)) * ['']
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def on_change(self, pv, value):
        bits = bin(value)[2:].zfill(64)
        if self.big_endian:
            self._view_bits = bits[(self.offset * 8):][:self.count]
        else:
            self._view_bits = bits[(-(self.offset + 1) * 8):][:self.count]
        self.queue_draw()


class Indicator(ActiveMixin, AlarmMixin, BlankWidget):
    __gtype_name__ = 'Indicator'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    colors = GObject.Property(type=str, default="AG", nick='Value Colors')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_size_request(20, 20)
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
        cr.set_line_width(0.75)
        margin = 4.5
        self.theme['label'] = style.get_color(style.get_state())
        cr.set_source_rgba(*self.theme['fill'])
        cr.rectangle(margin, margin, self.size, self.size)
        cr.fill_preserve()
        cr.set_source_rgba(*self.theme['border'])
        cr.stroke()

        cr.set_source_rgba(*self.theme['label'])
        layout = self.create_pango_layout(self.label)
        ink, logical = layout.get_pixel_extents()
        cr.move_to(2 * margin + self.size, margin + self.size / 2 - logical.height / 2)
        PangoCairo.show_layout(cr, layout)

    def on_realize(self, widget):
        self.palette = ColorSequence(self.colors)
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


class ScaleControl(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'ScaleControl'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    minimum = GObject.Property(type=float, default=0., nick='Minimum')
    maximum = GObject.Property(type=float, default=100., nick='Maximum')
    increment = GObject.Property(type=float, default=1., nick='Increment')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.HORIZONTAL, nick='Orientation')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    inverted = GObject.Property(type=bool, default=False, nick='Inverted')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.in_progress = False
        self.adjustment = Gtk.Adjustment(50., 0.0, 100.0, 1.0, .0, 0)
        self.scale = Gtk.Scale()
        self.scale.set_adjustment(self.adjustment)
        self.connect('realize', self.on_realize)
        self.add(self.scale)
        self.bind_property('orientation', self.scale, 'orientation', GObject.BindingFlags.DEFAULT)
        self.bind_property('inverted', self.scale, 'inverted', GObject.BindingFlags.DEFAULT)
        self.bind_property('maximum', self.adjustment, 'upper', GObject.BindingFlags.DEFAULT)
        self.bind_property('minimum', self.adjustment, 'lower', GObject.BindingFlags.DEFAULT)
        self.bind_property('increment', self.adjustment, 'step-increment', GObject.BindingFlags.DEFAULT)
        self.get_style_context().add_class('gtkdm')
        self.set_sensitive(False)

    def on_realize(self, obj):
        position = Gtk.PositionType.TOP if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.LEFT
        value_pos = Gtk.PositionType.BOTTOM if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.RIGHT
        self.scale.props.value_pos = value_pos
        self.scale.clear_marks()
        self.scale.add_mark(self.minimum, position, '{}'.format(self.minimum))
        self.scale.add_mark(self.maximum, position, '{}'.format(self.maximum))

        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.adjustment.connect('value-changed', self.on_value_set)

    def on_change(self, pv, value):
        self.in_progress = True
        self.adjustment.set_value(value)
        self.in_progress = False

    def on_value_set(self, obj):
        if not self.in_progress:
            if pv.type in ['double', 'float', 'time_double', 'time_float', 'ctrl_double', 'ctrl_float']:
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
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    use_limits = GObject.Property(type=bool, default=False, nick='Use PV Limits')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.in_progress = False
        self.adjustment = Gtk.Adjustment(50., 0.0, 100.0, 1.0, .0, 0)
        self.tweak = Gtk.SpinButton()
        self.tweak.set_adjustment(self.adjustment)
        self.connect('realize', self.on_realize)
        self.add(self.tweak)
        self.bind_property('maximum', self.adjustment, 'upper', GObject.BindingFlags.DEFAULT)
        self.bind_property('minimum', self.adjustment, 'lower', GObject.BindingFlags.DEFAULT)
        self.bind_property('increment', self.adjustment, 'step-increment', GObject.BindingFlags.DEFAULT)
        self.get_style_context().add_class('gtkdm')

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.adjustment.connect('value-changed', self.on_value_set)

    def on_change(self, pv, value):
        self.in_progress = True
        self.adjustment.set_value(value)
        self.in_progress = False

    def on_value_set(self, obj):
        if not self.in_progress:
            self.pv.put(self.adjustment.props.value)


class TextControl(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'TextControl'
    PV_COPY_BUTTON = 1
    channel = GObject.Property(type=str, default='', nick='PV Name')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    editable = GObject.Property(type=bool, default=True, nick='Editable')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    prec = GObject.Property(type=int, default=-1, minimum=-1, maximum=10, nick='Precision')
    sci = GObject.Property(type=bool, default=False, nick='Sci. Format')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect('realize', self.on_realize)
        self.entry = Gtk.Entry(width_chars=5)
        self.bind_property('xalign', self.entry, 'xalign', GObject.BindingFlags.DEFAULT)
        self.bind_property('editable', self.entry, 'editable', GObject.BindingFlags.DEFAULT)
        self.bind_property('editable', self.entry, 'can-focus', GObject.BindingFlags.DEFAULT)
        self.entry.connect('activate', self.on_activate)
        self.in_progress = False
        self.pv = None
        self.add(self.entry)
        self.get_style_context().add_class('gtkdm')
        self.set_sensitive(False)

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

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
        self.bind_property('label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.add(self.button)
        self.set_sensitive(False)

    def on_clicked(self, button):
        if self.pv:
            self.pv.put(1)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('active', self.on_active)

            if not (self.label or self.icon_name):
                self.label_pv = gepics.PV('{}.DESC'.format(self.channel))
                self.label_pv.connect('changed', self.on_label_change)
            else:
                if self.icon_name:
                    self.button.set_always_show_image(True)
                    self.button.set_image(Gtk.Image.new_from_icon_name(self.icon_name, Gtk.IconSize.MENU))
                if self.label:
                    self.button.set_label(self.label)

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
        self.button = Gtk.Button()
        self.state_pv = None
        self.state = None
        self.registry = {}

        self.connect('realize', self.on_realize)
        self.button.connect('clicked', self.on_clicked)
        self.bind_property('on_label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.add(self.button)
        self.set_sensitive(False)

    def on_clicked(self, button):
        if self.state:
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
        ctx = self.get_style_context()
        ctx.add_class('gtkdm')
        ctx.add_class('tiny')
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
    on_value = GObject.Property(type=int, default=0, nick='On Value')
    off_value = GObject.Property(type=int, default=1, nick='Off Value')
    on_state_value = GObject.Property(type=int, default=0, nick='On State')
    off_state_value = GObject.Property(type=int, default=1, nick='Off State')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = Gtk.Switch()
        self.state_pv = None
        self.registry = {}

        self.connect('realize', self.on_realize)
        self.button.connect('state-set', self.on_change)
        self.add(self.button)
        self.show_all()
        self.set_sensitive(False)

    def on_change(self, button, value):
        for state, spec in self.registry.items():
            if value == spec['active']:
                spec['pv'].put(spec['value'], wait=True)
                break
        return True

    def on_state_change(self, obj, value):
        for state, spec in self.registry.items():
            if value == spec['state']:
                self.button.set_state(spec['active'])
                break

    def on_realize(self, obj):
        ctx = self.get_style_context()
        ctx.add_class('gtkdm')
        ctx.add_class('onoff')
        ctx.add_class('tiny')
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
                print('Invalid Value: {}'.format(e))


class ChoiceButton(ActiveMixin, AlarmMixin, Gtk.EventBox):
    __gtype_name__ = 'ChoiceButton'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.VERTICAL, nick='Orientation')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.label_pv = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.connect('realize', self.on_realize)
        self.in_progress = False
        self.bind_property('orientation', self.box, 'orientation', GObject.BindingFlags.DEFAULT)
        self.buttons = [Gtk.ToggleButton(label='One'), Gtk.ToggleButton(label='Two'), ]
        for i, btn in enumerate(self.buttons):
            self.box.pack_start(btn, False, False, 0)
            btn.connect('toggled', self.on_toggled, i)
        self.add(self.box)
        self.set_sensitive(False)
        self.box.get_style_context().add_class('linked')
        self.get_style_context().add_class('gtkdm')

    def on_toggled(self, button, i):
        if not self.in_progress:
            self.pv.put(i)

    def on_realize(self, obj):
        if self.channel and not EDITOR:
            self.pv = gepics.PV(self.channel)
            self.pv.connect('active', self.on_active)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('changed', self.on_change)

    def on_active(self, pv, connected):
        ActiveMixin.on_active(self, pv, connected)
        if connected:
            for i, label in enumerate(pv.enum_strs):
                if i < len(self.buttons):
                    self.buttons[i].props.label = label
                else:
                    btn = Gtk.ToggleButton(label=label)
                    btn.connect('toggled', self.on_toggled, i)
                    self.buttons.append(btn)
                    self.box.pack_start(btn, False, False, 0)
                    btn.show()

            for btn in self.buttons[i + 1:]:
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
        self.get_style_context().add_class('gtkdm')
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
        self.button = Gtk.Button()
        self.connect('realize', self.on_realize)
        self.button.connect('clicked', self.on_clicked)
        self.add(self.button)
        self.proc = None
        self.bind_property('label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.show_all()

    def on_clicked(self, button):
        if self.command:
            if self.proc:
                self.proc.poll()
            if self.multiple or self.proc is None or self.proc.returncode is not None:
                cmds = self.command.split()
                self.proc = subprocess.Popen(cmds, shell=True, stdout=subprocess.DEVNULL)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')


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
        self.set_size_request(120, 100)
        self.pv = None
        self.label_pv = None
        self.ctrlvars = None
        self.value = 0
        self.units_label = 'mA'
        self.connect('realize', self.on_realize)
        self.palette = ColorSequence(self.colors)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        x = allocation.width / 2
        y = allocation.height / 2
        r = 4 * x / 6

        style = self.get_style_context()
        font_desc = style.get_font(style.get_state())
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
        if self.levels and self.ctrlvars:
            lolo = self.ctrlvars['lower_alarm_limit'] * angle_scale + start_angle
            lo = self.ctrlvars['lower_warning_limit'] * angle_scale + start_angle
            hi = self.ctrlvars['upper_warning_limit'] * angle_scale + start_angle
            hihi = self.ctrlvars['upper_alarm_limit'] * angle_scale + start_angle

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

        # ticks
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

        # label
        if self.label:
            xb, yb, tw, th = cr.text_extents(self.label)[:4]
            lines = textwrap.wrap(self.label, int(len(self.label) * 0.6 * allocation.width / tw))
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
                self.ctrlvars = self.pv.get_ctrlvars()
                self.units_label = self.pv.units
            except ChannelAccessGetFailure as e:
                self.ctrlvars = {}
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
        x = allocation.width / 2
        y = allocation.height / 2
        if self.pixbuf:
            scale = min(allocation.width / self.pixbuf.get_width(), allocation.height / self.pixbuf.get_height())
            cr.save()
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(
                cr, self.pixbuf,
                x - self.pixbuf.get_width() * scale / 2,
                y - self.pixbuf.get_height() * scale / 2
            )
            cr.paint()
            cr.restore()
        else:
            # draw boxes
            style = self.get_style_context()
            color = style.get_color(style.get_state())
            cr.set_source_rgba(*color)
            cr.rectangle(1.5, 1.5, allocation.width - 3, allocation.height - 3)
            cr.stroke()


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
        self.get_style_context().add_class('gtkdm')
        self.btn.connect('toggled', self.on_toggle)
        self.bind_property('label', self.btn, 'label', GObject.BindingFlags.DEFAULT)
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
        self.bind_property('label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.add(self.button)
        self.get_style_context().add_class('gtkdm')

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
            color = self.palette(int(self.value))
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
        self.btn = Gtk.MenuButton(use_popover=True)
        self.icon = Gtk.Image.new_from_icon_name('view-paged-symbolic', Gtk.IconSize.MENU)
        self.text = Gtk.Label(label=self.label)
        child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        child.pack_start(self.icon, False, False, 0)
        child.pack_start(self.text, True, True, 0)
        self.btn.add(child)
        self.add(self.btn)
        self.bind_property('label', self.text, 'label', GObject.BindingFlags.DEFAULT)
        self.bind_property('menu', self.btn, 'popover', GObject.BindingFlags.DEFAULT)
        self.get_style_context().add_class('gtkdm')


class DisplayMenu(Gtk.Popover):
    """
    A Popover menu for DisplayMenuItem entries
    """
    __gtype_name__ = 'DisplayMenu'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_border_width(3)
        self.get_style_context().add_class('gtkdm')


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
        self.bind_property('label', self.entry, 'text', GObject.BindingFlags.DEFAULT)
        self.add(self.entry)
        self.entry.connect('clicked', self.on_clicked)
        self.get_style_context().add_class('gtkdm')
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
        self.bind_property('label', self.entry, 'text', GObject.BindingFlags.DEFAULT)
        self.add(self.entry)
        self.proc = None
        self.entry.connect('clicked', self.on_clicked)
        self.get_style_context().add_class('gtkdm')
        self.show_all()

    def on_clicked(self, button):
        if self.command and not EDITOR:
            if self.proc:
                self.proc.poll()
            if self.multiple or self.proc is None or self.proc.returncode is not None:
                cmds = self.command.split()
                self.proc = subprocess.Popen(cmds, shell=True, stdout=subprocess.DEVNULL)


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
        self.get_style_context().add_class('gtkdm')

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
        _iter = self.buffer.get_end_iter()

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
        self.get_style_context().add_class('gtkdm')
        self.btn.connect('realize', self.on_realize)

    def on_realize(self, obj):
        top_level = self.get_toplevel()
        if isinstance(top_level, DisplayWindow):
            for name in self.widgets.split(','):
                w = top_level.builder.get_object(name.strip())
                if w:
                    self.btn.bind_property('active', w, 'visible', GObject.BindingFlags.DEFAULT)
        GLib.timeout_add(2000, self.btn.set_active, self.default)
        # self.btn.set_active(self.default)


class ChartCoord(object):
    def __init__(self, xlimits=(-1.0, 1.0), ylimits=(-1.0, 1.0), size=(400, 300), margins=(0, 0), xoffset=0.0,
                 yoffset=0.0):
        self.xmin, self.xmax = xlimits
        self.ymin, self.ymax = ylimits
        self.width, self.height = size

        xmargin = margins[0] + 10
        ymargin = margins[1] + 10

        self.width = (size[0] - 2 * xmargin) - xoffset
        self.height = (size[1] - 2 * ymargin) - yoffset

        self.orgx = xmargin + xoffset
        self.orgy = size[1] - (ymargin + yoffset)
        self.xscale = self.width / (self.xmax - self.xmin)
        self.yscale = self.height / (self.ymax - self.ymin)

    def xy(self, points, xoff=0.0, yoff=0.0):
        points = numpy.asarray(points)
        out = numpy.zeros_like(points)
        out[:, 0] = (points[:, 0] - self.xmin) * self.xscale + self.orgx + xoff
        out[:, 1] = self.orgy - (points[:, 1] - self.ymin) * self.yscale + yoff
        return out

    def x(self, points, offset=0.0):
        points = numpy.asarray(points)
        return (points - self.xmin) * self.xscale + self.orgx + offset

    def y(self, points, offset=0.0):
        points = numpy.asarray(points)
        return self.orgy - (points - self.ymin) * self.yscale + offset


class ChartPair(GObject.GObject):
    __gsignals__ = {
        'changed': (GObject.SIGNAL_RUN_FIRST, None, [])
    }

    def __init__(self, xname, yname, size=1, update=0.01):
        super().__init__()
        self.size = size
        self.array_mode = False
        self.data = numpy.empty((self.size, 2))
        self.xpv = gepics.PV(xname)
        self.ypv = gepics.PV(yname)
        self.xpv.connect('changed', self.on_change)
        self.ypv.connect('changed', self.on_change)
        self.xpv.connect('active', self.on_active)
        self.ypv.connect('active', self.on_active)
        self.min_update = update
        self.last_change = time.time()

    def on_change(self, pv, value):
        if time.time() - self.last_change >= self.min_update:
            x = self.xpv.get()
            y = self.ypv.get()
            if self.array_mode:
                cx = self.data.shape[0] if self.xpv.count == 1 else x.shape[0]
                cy = self.data.shape[0] if self.ypv.count == 1 else y.shape[0]
                self.data[:cx, 0] = x
                self.data[:cy, 1] = y
            else:
                self.data[:-1] = self.data[1:]
                self.data[-1] = (x, y)

            self.emit('changed')
            self.last_change = time.time()

    def on_active(self, pv, active):
        # prepare data array according to pv sizes
        if self.xpv.is_active() and self.ypv.is_active():
            sizes = (self.xpv.count, self.ypv.count)
            if sizes == (1, 1):
                self.data = numpy.empty((self.size, 2))
                self.array_mode = False
            else:
                self.data = numpy.empty((max(sizes), 2))
                self.array_mode = True


class XYScatter(Gtk.DrawingArea):
    __gtype_name__ = 'XYScatter'
    buffer = GObject.Property(type=int, default=1, minimum=1, maximum=100, nick='Buffer Size')
    sample = GObject.Property(type=float, default=10, minimum=.1, maximum=50, nick='Update Freq (hz)')
    color_bg = GObject.Property(type=Gdk.RGBA, nick='Background Color')
    color_fg = GObject.Property(type=Gdk.RGBA, nick='Foreground Color')
    colors = GObject.Property(type=str, default='RGYOPB', nick='Plot Colors')
    fade = GObject.Property(type=bool, default=True, nick='Fade Old Values')
    fontsize = GObject.Property(type=int, minimum=5, default=9, maximum=30, nick='Font Size')
    digits = GObject.Property(type=int, minimum=0, default=3, maximum=8, nick='Significant Digits')

    xmin = GObject.Property(type=float, default=-1.0, nick='X min')
    xmax = GObject.Property(type=float, default=1.0, nick='X max')
    ymin = GObject.Property(type=float, default=-1.0, nick='Y min')
    ymax = GObject.Property(type=float, default=1.0, nick='Y max')

    marginx = GObject.Property(type=int, minimum=0, maximum=50, default=0, nick='X margin')
    marginy = GObject.Property(type=int, minimum=0, maximum=50, default=0, nick='Y margin')

    plot0 = GObject.Property(type=str, default='', nick='Plot {i} PVs'.format(i=0))
    plot1 = GObject.Property(type=str, default='', nick='Plot {i} PVs'.format(i=1))
    plot2 = GObject.Property(type=str, default='', nick='Plot {i} PVs'.format(i=2))
    plot3 = GObject.Property(type=str, default='', nick='Plot {i} PVs'.format(i=3))
    plot4 = GObject.Property(type=str, default='', nick='Plot {i} PVs'.format(i=4))

    show_xaxis = GObject.Property(type=bool, default=True, nick='Show X-axis')
    show_yaxis = GObject.Property(type=bool, default=True, nick='Show Y-axis')

    xstep = GObject.Property(type=float, default=.1, nick='X Step Size')
    xticks = GObject.Property(type=int, default=5, nick='X Ticks/Step')
    ystep = GObject.Property(type=float, default=.1, nick='Y Step Size')
    yticks = GObject.Property(type=int, default=5, nick='Y Ticks/Step')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_style_context().add_class('gtkdm')
        self.params = {}
        self.plots = []
        self.palette = None

        self.connect('realize', self.on_realize)

    def calculate_parameters(self):
        xminimum, xmaximum, xmajor, xminor = tick_points(self.xmin, self.xmax, self.xstep, self.xticks)
        yminimum, ymaximum, ymajor, yminor = tick_points(self.ymin, self.ymax, self.ystep, self.yticks)

        xmajor_points = list(zip(xmajor, (yminimum,) * len(xmajor)))
        xminor_points = list(zip(xminor, (yminimum,) * len(xminor)))

        ymajor_points = list(zip((xminimum,) * len(ymajor), ymajor))
        yminor_points = list(zip((xminimum,) * len(yminor), yminor))

        alloc = self.get_allocation()

        yoffset = 0.0 if not self.show_xaxis else self.fontsize * 2
        xoffset = 0.0 if not self.show_yaxis else self.fontsize * 3

        self.params = {
            'alloc': alloc,
            'xmin': xminimum,
            'xmax': xmaximum,
            'xmajor': xmajor_points,
            'xminor': xminor_points,
            'ymin': yminimum,
            'ymax': ymaximum,
            'ymajor': ymajor_points,
            'yminor': yminor_points,
            'converter': ChartCoord(
                xlimits=(xminimum, xmaximum),
                ylimits=(yminimum, ymaximum),
                size=(alloc.width, alloc.height),
                margins=(self.marginx, self.marginy),
                xoffset=xoffset, yoffset=yoffset
            )
        }

    def on_realize(self, widget):
        self.get_style_context().add_class('gtkdm')
        self.palette = ColorSequence(self.colors)

        if not EDITOR:
            # extract pairs of pv names
            for i in range(5):
                m = re.match('^\s*([^\s,|;]+)[\s,|;]*([^\s,|;]+)\s*$', getattr(self, 'plot{}'.format(i), ''))
                if m:
                    xname, yname = m.groups()
                    pair = ChartPair(xname, yname, self.buffer, update=1 / self.sample)
                    pair.connect('changed', lambda x: self.queue_draw())
                    self.plots.append(pair)

    def do_draw(self, cr):
        if self.color_bg:
            cr.set_source_rgba(*self.color_bg)
            cr.paint()

        if self.color_fg:
            cr.set_source_rgba(*self.color_fg)
        else:
            cr.set_source_rgba(0.0, 0.0, 0.0, 1.0)

        # draw axes
        cr.set_line_width(0.75)
        cr.set_font_size(self.fontsize)
        alloc = self.get_allocation()
        if not self.params or (alloc.width, alloc.height) != (self.params['alloc'].width, self.params['alloc'].height):
            self.calculate_parameters()

        if self.show_xaxis:
            xframe = self.params['converter'].xy(
                [
                    (self.params['xmin'], self.params['ymin']),
                    (self.params['xmax'], self.params['ymin']),
                    (self.params['xmin'], self.params['ymax']),
                    (self.params['xmax'], self.params['ymax'])
                ],
                yoff=5
            )
            cr.move_to(*xframe[0])
            cr.line_to(*xframe[1])
            cr.stroke()
            # if self.show_yaxis:
            #     cr.move_to(*xframe[2])
            #     cr.line_to(*xframe[3])
            #     cr.stroke()

            major = self.params['converter'].xy(self.params['xmajor'], yoff=5)
            for i, tick in enumerate(major):
                vtick = self.params['xmajor'][i]
                cr.move_to(tick[0], tick[1])
                cr.line_to(tick[0], tick[1] + 5)
                cr.stroke()
                text = ('{{:0.{}g}}'.format(self.digits)).format(vtick[0])
                xb, yb, w, h = cr.text_extents(text)[:4]
                cr.move_to(tick[0] - xb - w / 2, tick[1] + 7 - yb)
                cr.show_text(text)

            if self.xticks:
                minor = self.params['converter'].xy(self.params['xminor'], yoff=5)
                for tick in minor:
                    cr.move_to(tick[0], tick[1])
                    cr.line_to(tick[0], tick[1] + 3)
                    cr.stroke()

        if self.show_yaxis:
            yframe = self.params['converter'].xy(
                [
                    (self.params['xmin'], self.params['ymin']),
                    (self.params['xmin'], self.params['ymax']),
                    (self.params['xmax'], self.params['ymin']),
                    (self.params['xmax'], self.params['ymax'])
                ],
                xoff=-5
            )

            cr.move_to(*yframe[0])
            cr.line_to(*yframe[1])
            cr.stroke()
            # if self.show_xaxis:
            #     cr.move_to(*yframe[2])
            #     cr.line_to(*yframe[3])
            #     cr.stroke()

            major = self.params['converter'].xy(self.params['ymajor'], xoff=-5)
            for i, tick in enumerate(major):
                vtick = self.params['ymajor'][i]
                cr.move_to(tick[0], tick[1])
                cr.line_to(tick[0] - 5, tick[1])
                cr.stroke()
                text = ('{{:0.{}g}}'.format(self.digits)).format(vtick[1])
                xb, yb, w, h = cr.text_extents(text)[:4]
                cr.move_to(tick[0] - 7 - w - xb, tick[1] - yb - h / 2)
                cr.show_text(text)

            if self.yticks:
                minor = self.params['converter'].xy(self.params['yminor'], xoff=-5)
                for tick in minor:
                    cr.move_to(tick[0], tick[1])
                    cr.line_to(tick[0] - 2, tick[1])
                    cr.stroke()

        if not self.plots:
            return

        for i, plot in enumerate(self.plots):
            pos = self.params['converter'].xy(plot.data)
            if plot.array_mode:
                cr.set_line_width(0.75)
                cr.set_source_rgba(*self.palette(i))
                for j, mark in enumerate(pos):
                    if j == 0:
                        cr.arc(*mark, 2, 0, 2 * pi)
                        cr.fill_preserve()
                        cr.stroke()
                        cr.move_to(*mark)
                        continue
                    else:
                        cr.save()
                        cr.arc(*mark, 2, 0, 2 * pi)
                        cr.fill_preserve()
                        cr.stroke()
                        cr.restore()
                        cr.line_to(*mark)
                cr.stroke()
            else:
                cr.set_line_width(1.0)
                for j, mark in enumerate(pos):
                    cr.set_source_rgba(*alpha(self.palette(i), (j + 1.) / (self.buffer + 1.)))
                    cr.arc(*mark, 2, 0, 2 * pi)
                    cr.fill_preserve()
                    cr.stroke()


class StripData(GObject.GObject):
    __gsignals__ = {
        'changed': (GObject.SIGNAL_RUN_FIRST, None, [])
    }

    def __init__(self, names, period=60.0, sample_freq=1, refresh_freq=1):
        super().__init__()
        self.size = int(period * sample_freq)
        self.count = len(names)
        self.ydata = numpy.empty((self.size, self.count))
        self.xdata = numpy.linspace(-period, 0, self.size)
        self.ydata.fill(numpy.nan)
        self.pvs = [
            gepics.PV(name) for name in names
        ]
        self.sample_time = 1000. / sample_freq
        self.refresh_time = 1000. / refresh_freq
        GLib.timeout_add(self.sample_time, self.sample_data)
        GLib.timeout_add(self.refresh_time, self.refresh)

    def sample_data(self):
        self.ydata[:-1] = self.ydata[1:]
        for i, pv in enumerate(self.pvs):
            self.ydata[-1, i] = numpy.nan if not pv.is_active() else pv.get()
        return True

    def refresh(self):
        self.emit("changed")
        return True


class StripPlot(Gtk.DrawingArea):
    __gtype_name__ = 'StripPlot'
    period = GObject.Property(type=int, default=60, minimum=5, maximum=1440, nick='Time Window (s)')
    refresh = GObject.Property(type=float, default=1, minimum=.1, maximum=10, nick='Redraw Freq (hz)')
    sample = GObject.Property(type=float, default=1, minimum=.1, maximum=10, nick='Sample Freq (hz)')

    color_bg = GObject.Property(type=Gdk.RGBA, nick='Background Color')
    color_fg = GObject.Property(type=Gdk.RGBA, nick='Foreground Color')
    colors = GObject.Property(type=str, default='RGYOPB', nick='Plot Colors')
    fontsize = GObject.Property(type=int, minimum=5, default=9, maximum=30, nick='Font Size')
    digits = GObject.Property(type=int, minimum=0, default=3, maximum=8, nick='Significant Digits')

    ymin = GObject.Property(type=float, default=-1.0, nick='Y min')
    ymax = GObject.Property(type=float, default=1.0, nick='Y max')
    marginx = GObject.Property(type=int, minimum=0, maximum=50, default=0, nick='X margin')
    marginy = GObject.Property(type=int, minimum=0, maximum=50, default=0, nick='Y margin')

    plot0 = GObject.Property(type=str, default='', nick='Plot 1 PV')
    plot1 = GObject.Property(type=str, default='', nick='Plot 2 PV')
    plot2 = GObject.Property(type=str, default='', nick='Plot 3 PV')
    plot3 = GObject.Property(type=str, default='', nick='Plot 4 PV')
    plot4 = GObject.Property(type=str, default='', nick='Plot 5 PV')

    show_xaxis = GObject.Property(type=bool, default=True, nick='Show X-axis')
    show_yaxis = GObject.Property(type=bool, default=True, nick='Show Y-axis')

    xstep = GObject.Property(type=float, default=.1, nick='X Step Size')
    xticks = GObject.Property(type=int, default=5, nick='X Ticks/Step')
    ystep = GObject.Property(type=float, default=.1, nick='Y Step Size')
    yticks = GObject.Property(type=int, default=5, nick='Y Ticks/Step')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_style_context().add_class('gtkdm')
        self.params = {}
        self.plot = None
        self.palette = None
        self.connect('realize', self.on_realize)

    def calculate_parameters(self):
        self.xmin = - self.period
        self.xmax = 0.0

        xminimum, xmaximum, xmajor, xminor = tick_points(self.xmin, self.xmax, self.xstep, self.xticks)
        yminimum, ymaximum, ymajor, yminor = tick_points(self.ymin, self.ymax, self.ystep, self.yticks)

        xmajor_points = list(zip(xmajor, (yminimum,) * len(xmajor)))
        xminor_points = list(zip(xminor, (yminimum,) * len(xminor)))

        ymajor_points = list(zip((xminimum,) * len(ymajor), ymajor))
        yminor_points = list(zip((xminimum,) * len(yminor), yminor))

        alloc = self.get_allocation()

        yoffset = 0.0 if not self.show_xaxis else self.fontsize * 2
        xoffset = 0.0 if not self.show_yaxis else self.fontsize * 3

        self.params = {
            'alloc': alloc,
            'xmin': xminimum,
            'xmax': xmaximum,
            'xmajor': xmajor_points,
            'xminor': xminor_points,
            'ymin': yminimum,
            'ymax': ymaximum,
            'ymajor': ymajor_points,
            'yminor': yminor_points,
            'converter': ChartCoord(
                xlimits=(xminimum, xmaximum),
                ylimits=(yminimum, ymaximum),
                size=(alloc.width, alloc.height),
                margins=(self.marginx, self.marginy),
                xoffset=xoffset, yoffset=yoffset
            )
        }
        if self.plot:
            self.xvalues = self.params['converter'].x(self.plot.xdata)

    def on_realize(self, widget):
        self.get_style_context().add_class('gtkdm')
        self.palette = ColorSequence(self.colors)
        # extract pairs of pv names
        pv_names = filter(None, [getattr(self, 'plot{}'.format(i), '').strip() for i in range(5)])
        xminimum, xmaximum, xmajor, xminor = tick_points(-self.period, 0, self.xstep, self.xticks)

        if not EDITOR:
            self.plot = StripData(list(pv_names), period=-xminimum, sample_freq=self.sample, refresh_freq=self.refresh)
            self.plot.connect('changed', lambda x: self.queue_draw())

    def do_draw(self, cr):
        if self.color_bg:
            cr.set_source_rgba(*self.color_bg)
            cr.paint()

        if self.color_fg:
            cr.set_source_rgba(*self.color_fg)
        else:
            cr.set_source_rgba(0.0, 0.0, 0.0, 1.0)

        # draw axes
        cr.set_line_width(0.75)
        cr.set_font_size(self.fontsize)
        alloc = self.get_allocation()
        if not self.params or (alloc.width, alloc.height) != (self.params['alloc'].width, self.params['alloc'].height):
            self.calculate_parameters()

        if self.show_xaxis:
            xframe = self.params['converter'].xy(
                [
                    (self.params['xmin'], self.params['ymin']),
                    (self.params['xmax'], self.params['ymin']),
                    (self.params['xmin'], self.params['ymax']),
                    (self.params['xmax'], self.params['ymax'])
                ],
                yoff=5
            )
            cr.move_to(*xframe[0])
            cr.line_to(*xframe[1])
            cr.stroke()
            # if self.show_yaxis:
            #     cr.move_to(*xframe[2])
            #     cr.line_to(*xframe[3])
            #     cr.stroke()

            major = self.params['converter'].xy(self.params['xmajor'], yoff=5)
            for i, tick in enumerate(major):
                vtick = self.params['xmajor'][i]
                cr.move_to(tick[0], tick[1])
                cr.line_to(tick[0], tick[1] + 5)
                cr.stroke()
                text = ('{{:0.{}g}}'.format(self.digits)).format(vtick[0])
                xb, yb, w, h = cr.text_extents(text)[:4]
                cr.move_to(tick[0] - xb - w / 2, tick[1] + 7 - yb)
                cr.show_text(text)

            if self.xticks:
                minor = self.params['converter'].xy(self.params['xminor'], yoff=5)
                for tick in minor:
                    cr.move_to(tick[0], tick[1])
                    cr.line_to(tick[0], tick[1] + 3)
                    cr.stroke()

        if self.show_yaxis:
            yframe = self.params['converter'].xy(
                [
                    (self.params['xmin'], self.params['ymin']),
                    (self.params['xmin'], self.params['ymax']),
                    (self.params['xmax'], self.params['ymin']),
                    (self.params['xmax'], self.params['ymax'])
                ],
                xoff=-5
            )

            cr.move_to(*yframe[0])
            cr.line_to(*yframe[1])
            cr.stroke()
            # if self.show_xaxis:
            #     cr.move_to(*yframe[2])
            #     cr.line_to(*yframe[3])
            #     cr.stroke()

            major = self.params['converter'].xy(self.params['ymajor'], xoff=-5)
            for i, tick in enumerate(major):
                vtick = self.params['ymajor'][i]
                cr.move_to(tick[0], tick[1])
                cr.line_to(tick[0] - 5, tick[1])
                cr.stroke()
                text = ('{{:0.{}g}}'.format(self.digits)).format(vtick[1])
                xb, yb, w, h = cr.text_extents(text)[:4]
                cr.move_to(tick[0] - 7 - w - xb, tick[1] - yb - h / 2)
                cr.show_text(text)

            if self.yticks:
                minor = self.params['converter'].xy(self.params['yminor'], xoff=-5)
                for tick in minor:
                    cr.move_to(tick[0], tick[1])
                    cr.line_to(tick[0] - 2, tick[1])
                    cr.stroke()

        if not self.plot:
            return

        cr.set_line_width(0.75)
        for j in range(self.plot.count):
            sel = numpy.logical_not(numpy.isnan(self.plot.ydata[:, j]))
            yvalues = self.params['converter'].y(self.plot.ydata[sel, j])
            cr.set_source_rgba(*self.palette(j))
            for i, (x, y) in enumerate(zip(self.xvalues[sel], yvalues)):
                if i == 0:
                    cr.move_to(x, y)
                    continue
                cr.line_to(x, y)
            cr.stroke()
