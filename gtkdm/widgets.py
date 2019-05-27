import json
import os
import textwrap
import zipfile
import hashlib
from datetime import datetime
from math import atan2, pi, cos, sin, ceil

import gi

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject, Gdk, Gio, GdkPixbuf, GLib, Pango
import gepics
import xml.etree.ElementTree as ET

from . import utils

COLORS = {
    'R': '#ef2929',
    'G': '#73d216',
    'Y': '#fce94f',
    'O': '#fcaf3e',
    'P': '#ad7fa8',
    'B': '#729fcf',
    'K': '#000000',
    'A': '#888a85',
    'C': '#17becf',
    'W': '#ffffff',
    'M': '#88419d'
}


class DisplayManager(object):
    """Manages all displays"""

    def __init__(self):
        self.macros = {}
        self.registry = {}

    def reset(self, macro_spec):
        self.macros = utils.parse_macro_spec(macro_spec)

    def show_display(self, path, macros_spec="", main=False, multiple=False):
        directory, filename = os.path.split(os.path.abspath(path))
        try:
            tree = ET.parse(path)
        except FileNotFoundError as e:
            print('Display File {} not found'.format(path))
            return

        w = tree.find(".//object[@class='GtkWindow']")
        w.set('id', 'related_display')
        new_macros = {}
        new_macros.update(self.macros)
        new_macros.update(utils.parse_macro_spec(macros_spec))
        unique_text = ('{}{}'.format(filename, utils.compress_macro(new_macros))).encode('utf-8')
        key = hashlib.sha256(unique_text).hexdigest()
        if multiple or key not in self.registry:
            try:
                utils.update_properties(tree, new_macros)
            except KeyError as e:
                print('Macro {} not specified for display "{}"'.format(e, filename))
            data = (
                '<?xml version="1.0" encoding="UTF-8"?>\n' +
                ET.tostring(tree.getroot(), encoding='unicode',method='xml')
            )
            with utils.working_dir(directory):
                builder = Gtk.Builder()
                builder.add_from_string(data)
                window = builder.get_object('related_display')
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
        directory, filename = os.path.split(os.path.abspath(path))
        try:
            tree = ET.parse(path)
        except FileNotFoundError as e:
            print('Display File {} not found'.format(path))
            return

        w = tree.find(".//object[@class='GtkWindow']/child/object[1]")
        w.set('id', 'embedded_display')
        new_macros = {}
        new_macros.update(self.macros)
        new_macros.update(utils.parse_macro_spec(macros_spec))
        try:
            utils.update_properties(tree, new_macros)
        except KeyError as e:
            print('Macro {} not specified for display "{}"'.format(e, filename))
        data = (
                '<?xml version="1.0" encoding="UTF-8"?>\n' +
                ET.tostring(tree.getroot(), encoding='unicode', method='xml')
        )
        with utils.working_dir(directory):
            builder = Gtk.Builder()
            builder.add_objects_from_string(data, ['embedded_display'])
            display = builder.get_object('embedded_display')
            child = frame.get_child()
            if child:
                child.destroy()
            frame.add(display)


Manager = DisplayManager()


class ColorSequence(object):
    def __init__(self, sequence):
        self.values = [self.parse(COLORS.get(v, '#000000')) for v in sequence]

    def __call__(self, value, alpha=1.0):
        if value < len(self.values):
            col = self.values[value].copy()
            col.alpha = alpha
        else:
            col = self.values[-1].copy()
            col.alpha = alpha
        return col

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


Direction = Gdk.WindowEdge


class Layout(Gtk.Fixed):
    __gtype_name__ = 'Layout'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DisplayFrame(Gtk.EventBox):
    __gtype_name__ = 'DisplayFrame'
    label = GObject.Property(type=str, default='', nick='Label')
    shadow_type = GObject.Property(type=Gtk.ShadowType, default=Gtk.ShadowType.NONE, nick='Shadow Type')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    yalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='Y-Alignment')
    xscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='X-Scale')
    yscale = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0, nick='Y-Scale')

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


class TextMonitor(Gtk.EventBox):
    __gtype_name__ = 'TextMonitor'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=1.0, nick='X-Alignment')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = Gtk.Label('<N/A>')
        self.add(self.label)
        self.pv = None
        self.connect('realize', self.on_realize)
        self.bind_property('xalign', self.label, 'xalign', GObject.BindingFlags.DEFAULT)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def on_change(self, pv, value):
        text = '{} {}'.format(pv.char_value, pv.units) if pv.units else pv.char_value
        self.label.set_markup(text)

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

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.get_style_context().remove_class('gtkdm-inactive')
            self.set_sensitive(True)
        else:
            self.get_style_context().add_class('gtkdm-inactive')
            self.set_sensitive(False)


class TextLabel(Gtk.EventBox):
    __gtype_name__ = 'TextLabel'

    text = GObject.Property(type=str, default='Label', nick='Label')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = Gtk.Label(label='Label')
        self.bind_property('text', self.label, 'label', GObject.BindingFlags.DEFAULT)
        self.bind_property('xalign', self.label, 'xalign', GObject.BindingFlags.DEFAULT)
        self.add(self.label)
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')


class LineMonitor(Gtk.Widget):
    __gtype_name__ = 'LineMonitor'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    line_width = GObject.Property(type=float, minimum=0.1, maximum=10.0, default=1.0, nick='Width')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    arrow = GObject.Property(type=bool, default=False, nick='Arrow')
    arrow_size = GObject.Property(type=int, minimum=1, maximum=10, default=2, nick='Arrow Size')
    direction = cheme = GObject.Property(type=Direction, default=Direction.EAST, nick='Direction')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_size_request(40, 40)

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

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)


class Byte(Gtk.Widget):
    __gtype_name__ = 'Byte'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    offset = GObject.Property(type=int, minimum=0, maximum=4, default=0, nick='Byte Offset')
    count = GObject.Property(type=int, minimum=1, maximum=8, default=8, nick='Byte Count')
    big_endian = GObject.Property(type=bool, default=False, nick='Big-Endian')
    labels = GObject.Property(type=str, default='', nick='Labels')
    colors = GObject.Property(type=str, default='AG', nick='Colors')
    columns = GObject.Property(type=int, minimum=1, maximum=8, default=1, nick='Columns')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._view_bits = ['0'] * self.count
        self._view_labels = [''] * self.count
        self.set_size_request(196, 40)
        self.theme = {
            'border': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }
        self.set_sensitive(False)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        stride = ceil(self.count / self.columns)
        col_width = allocation.width / self.columns

        # draw boxes
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())

        cr.set_line_width(0.75)

        for i in range(self.count):
            x = pix((i // stride) * col_width + 4)
            y = pix(4 + (i % stride) * (self.size + 5))
            cr.rectangle(x, y, self.size, self.size)
            color = self.palette(int(self._view_bits[i]))
            cr.set_source_rgba(*color)
            cr.fill_preserve()
            cr.set_source_rgba(*self.theme['border'])
            cr.stroke()

            cr.set_source_rgba(*self.theme['label'])
            label = self._view_labels[i]
            xb, yb, w, h = cr.text_extents(label)[:4]
            cr.move_to(x + self.size + 4.5, y + self.size / 2 - yb - h / 2)
            cr.show_text(label)
            cr.stroke()

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)
        self.palette = ColorSequence(self.colors)

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

        labels = [v.strip() for v in self.labels.split(',')]
        self._view_labels = labels + (self.count - len(labels)) * ['']

    def on_change(self, pv, value):
        bits = list(bin(value)[2:].zfill(64))
        if self.big_endian:
            self._view_bits = bits[self.offset:][:self.count]
        else:
            self._view_bits = bits[::-1][self.offset:][:self.count]
        self.queue_draw()

    def on_alarm(self, pv, alarm):
        if self.alarm:
            style = self.get_style_context()
            if alarm == gepics.Alarm.MAJOR:
                style.remove_class('gtkdm-warning')
                style.add_class('gtkdm-critical')
            elif alarm == gepics.Alarm.MINOR:
                style.add_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')
            else:
                style.remove_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
            self.theme['border'] = Gdk.RGBA(0.0, 0.0, 0.0, 1.0)
        else:
            self.set_sensitive(False)
            self.theme['border'] = Gdk.RGBA(1.0, 1.0, 1.0, 1.0)
        self.queue_draw()


class Indicator(Gtk.Widget):
    __gtype_name__ = 'Indicator'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    colors = GObject.Property(type=str, default='72', nick='Colors')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_size_request(196, 40)
        self.pv = None
        self.label_pv = None
        self.theme = {
            'border': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
            'fill': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }
        self.set_sensitive(False)

    def do_draw(self, cr):
        cr.set_line_width(0.75)
        x = 4.5
        y = 4.5
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())
        cr.set_source_rgba(*self.theme['fill'])
        cr.rectangle(x, y, self.size, self.size)
        cr.fill_preserve()
        cr.set_source_rgba(*self.theme['border'])
        cr.stroke()

        xb, yb, w, h = cr.text_extents(self.label)[:4]
        cr.move_to(x + self.size + 4, y + self.size / 2 - yb - h / 2)
        cr.set_source_rgba(*self.theme['label'])
        cr.show_text(self.label)

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)
        self.palette = ColorSequence(self.colors)

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(pv_name))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.theme['fill'] = self.palette(value)
        self.queue_draw()

    def on_alarm(self, pv, alarm):
        if self.alarm:
            style = self.get_style_context()
            if alarm == gepics.Alarm.MAJOR:
                style.remove_class('gtkdm-warning')
                style.add_class('gtkdm-critical')
            elif alarm == gepics.Alarm.MINOR:
                style.add_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')
            else:
                style.remove_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
            self.theme['border'] = Gdk.RGBA(0.0, 0.0, 0.0, 1.0)
        else:
            self.set_sensitive(False)
            self.theme['border'] = Gdk.RGBA(1.0, 1.0, 1.0, 1.0)
        self.queue_draw()


class ScaleControl(Gtk.Bin):
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
        self.set_sensitive(False)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        position = Gtk.PositionType.TOP if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.LEFT
        value_pos = Gtk.PositionType.BOTTOM if self.orientation == Gtk.Orientation.HORIZONTAL else Gtk.PositionType.RIGHT
        self.scale.props.value_pos = value_pos
        self.scale.clear_marks()
        self.scale.add_mark(self.minimum, position, '{}'.format(self.minimum))
        self.scale.add_mark(self.maximum, position, '{}'.format(self.maximum))

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.adjustment.connect('value-changed', self.on_value_set)

    def on_change(self, pv, value):
        self.in_progress = True
        self.adjustment.set_value(value)
        self.in_progress = False

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

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)

    def on_value_set(self, obj):
        if not self.in_progress:
            self.pv.put(self.adjustment.props.value)


class TweakControl(Gtk.Bin):
    __gtype_name__ = 'TweakControl'
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

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)
            self.adjustment.connect('value-changed', self.on_value_set)

    def on_change(self, pv, value):
        self.in_progress = True
        self.adjustment.set_value(value)
        self.in_progress = False

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

    def on_active(self, pv, connected):
        if connected:
            if self.use_limits:
                meta = self.pv.get_ctrlvars()
                self.props.minimum = meta['lower_ctrl_limit']
                self.props.maximum = meta['upper_ctrl_limit']
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)

    def on_value_set(self, obj):
        if not self.in_progress:
            self.pv.put(self.adjustment.props.value)


class TextControl(Gtk.EventBox):
    __gtype_name__ = 'TextControl'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    xalign = GObject.Property(type=float, minimum=0.0, maximum=1.0, default=0.5, nick='X-Alignment')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect('realize', self.on_realize)
        self.entry = Gtk.Entry(width_chars=5)
        self.bind_property('xalign', self.entry, 'xalign', GObject.BindingFlags.DEFAULT)
        self.in_progress = False
        self.pv = None
        self.add(self.entry)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

    def on_change(self, pv, value):
        self.in_progress = True
        self.entry.set_text(pv.char_value)
        self.in_progress = False

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

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)


class CommandButton(Gtk.EventBox):
    __gtype_name__ = 'CommandButton'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = Gtk.Button(label=self.label)
        self.pv = None
        self.label_pv = None
        self.bind_property('label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.connect('realize', self.on_realize)
        self.button.connect('clicked', self.on_clicked)
        self.add(self.button)

    def on_clicked(self, button):
        self.pv.put(1)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(pv_name))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)


class ChoiceButton(Gtk.EventBox):
    __gtype_name__ = 'ChoiceButton'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.VERTICAL, nick='Orientation')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.label_pv = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.connect('realize', self.on_realize)
        self.in_progress = False
        self.bind_property('orientation', self.box, 'orientation', GObject.BindingFlags.DEFAULT)
        self.buttons = [Gtk.ToggleButton(label='Choice 1'), Gtk.ToggleButton(label='Choice 2'),
                        Gtk.ToggleButton(label='Choice 3')]
        for i, btn in enumerate(self.buttons):
            self.box.pack_start(btn, False, False, 0)
            btn.connect('toggled', self.on_toggled, i)
        self.add(self.box)

    def on_toggled(self, button, i):
        if not self.in_progress:
            self.pv.put(i)

    def on_realize(self, obj):
        self.box.get_style_context().add_class('linked')
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('active', self.on_active)
            self.pv.connect('changed', self.on_change)

    def on_active(self, pv, connected):
        if connected:
            for i, label in enumerate(pv.enum_strs):
                if i < len(self.buttons):
                    self.buttons[i].props.label = label
                else:
                    btn = Gtk.ToggleButton(label=label)
                    btn.connect('toggled', self.on_toggled, i)
                    self.buttons.append(btn)
                    self.box.pack_start(btn, False, False, 0)

            for btn in self.buttons[i + 1:]:
                btn.destroy()

            self.set_sensitive(True)
        else:
            self.set_sensitive(False)

    def on_change(self, pv, value):
        self.in_progress = True
        for i, btn in enumerate(self.buttons):
            btn.set_active(i == value)
        self.in_progress = False


class ChoiceMenu(Gtk.Bin):
    __gtype_name__ = 'ChoiceMenu'
    channel = GObject.Property(type=str, default='', nick='PV Name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pv = None
        self.box = Gtk.ComboBoxText()
        self.connect('realize', self.on_realize)
        self.box.connect('changed', self.on_toggled)
        self.in_progress = False
        self.add(self.box)

    def on_toggled(self, box):
        if not self.in_progress:
            active = self.box.get_active()
            if active >= 0:
                self.pv.put(active)

    def on_realize(self, obj):
        self.box.get_style_context().add_class('linked')
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('active', self.on_active)
            self.pv.connect('changed', self.on_change)

    def on_active(self, pv, connected):
        if connected:
            self.box.remove_all()
            for i, label in enumerate(pv.enum_strs):
                self.box.append_text(label)
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)

    def on_change(self, pv, value):
        self.in_progress = True
        self.box.set_active(value)
        self.in_progress = False


class Gauge(Gtk.Widget):
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
        self.ctrlvars = None
        self.value = 0
        self.units_label = 'mA'

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
        cr.set_line_width(0.5)
        value_angle = angle_scale * (self.value - minimum) + start_angle
        vr = 5 * r / 6
        vx2 = x + vr * cos(value_angle)
        vy2 = y + vr * sin(value_angle)
        cr.set_source_rgba(*alpha(color, 0.5))
        cr.move_to(x - 2, y)
        cr.line_to(vx2, vy2)
        cr.line_to(x + 2, y)
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

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)
        self.palette = ColorSequence(self.colors)

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(pv_name))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.value = value
        self.queue_draw()

    def on_active(self, pv, connected):
        if connected:
            self.ctrlvars = self.pv.get_ctrlvars()
            self.units_label = self.pv.units
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)
        self.queue_draw()


class SymbolFrames(object):
    registry = {}

    def __init__(self, path):
        self.frames = []
        self.width = 0
        self.height = 0
        with zipfile.ZipFile(path, 'r') as sym:
            index = json.loads(sym.read('symbol.json'))
            for frame in index:
                data = sym.read(frame)
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

    def __call__(self, value):
        if 0 <= value < len(self.frames):
            return self.frames[int(value)]


class Symbol(Gtk.Widget):
    __gtype_name__ = 'Symbol'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    file = GObject.Property(type=str, nick='Symbol File')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frames = None
        self.image = None

    def do_draw(self, cr):
        allocation = self.get_allocation()
        x = allocation.width / 2
        y = allocation.height / 2
        if self.image:
            scale = min(allocation.width / self.frames.width, allocation.height / self.frames.height)
            cr.save()
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(
                cr, self.image,
                x - self.image.get_width() * scale / 2,
                y - self.image.get_height() * scale / 2
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

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('active', self.on_active)

        if self.file:
            self.frames = SymbolFrames.new_from_file(self.file)
            self.image = self.frames(0)

    def on_change(self, pv, value):
        self.image = self.frames(value)
        self.queue_draw()

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
        else:
            self.set_sensitive(False)
        self.queue_draw()


class Diagram(Gtk.Widget):
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
                x - self.pixbuf.get_width() * scale/2,
                y - self.pixbuf.get_height() * scale/2
            )
            cr.paint()
            cr.restore()
        else:
            # draw boxes
            style = self.get_style_context()
            color = style.get_color(style.get_state())
            cr.set_source_rgba(*color)
            cr.rectangle(1.5, 1.5, allocation.width-3, allocation.height-3)
            cr.stroke()

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)


class CheckControl(Gtk.EventBox):
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
        self.connect('realize', self.on_realize)
        self.btn.connect('toggled', self.on_toggle)
        self.bind_property('label', self.btn, 'label', GObject.BindingFlags.DEFAULT)

    def on_toggle(self, obj):
        if not self.in_progress:
            self.pv.put(int(obj.get_active()))

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(pv_name))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.in_progress = True
        self.btn.set_active(bool(value))
        self.in_progress = False

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

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.get_style_context().remove_class('gtkdm-inactive')
            self.set_sensitive(True)
        else:
            self.get_style_context().add_class('gtkdm-inactive')
            self.set_sensitive(False)


class DisplayButton(Gtk.EventBox):
    __gtype_name__ = 'DisplayButton'
    label = GObject.Property(type=str, default='', nick='Label')
    display = GObject.Property(type=str, default='', nick='Display File')
    macros = GObject.Property(type=str, default='', nick='Macro')
    frame = GObject.Property(type=DisplayFrame, nick='Target Frame')
    multiple = GObject.Property(type=bool, default=False, nick='Allow Multiple')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = Gtk.Button(label=self.label)
        self.button.connect('clicked', self.on_clicked)
        self.connect('realize', self.on_realize)
        self.bind_property('label', self.button, 'label', GObject.BindingFlags.DEFAULT)
        self.add(self.button)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')

    def on_clicked(self, button):
        print(self.get_toplevel().xid)
        if self.frame:
            Manager.embed_display(self.frame, self.display, macros_spec=self.macros)
        else:
            Manager.show_display(self.display, macros_spec=self.macros, multiple=self.multiple)


class Shape(Gtk.Widget):
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
        self.theme = {
            'border': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }
        self.value = 0

    def do_draw(self, cr):
        allocation = self.get_allocation()

        # draw boxes
        style = self.get_style_context()
        self.theme['border'] = style.get_color(style.get_state())

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

    def do_realize(self):
        allocation = self.get_allocation()
        attr = Gdk.WindowAttr()
        attr.window_type = Gdk.WindowType.CHILD
        attr.x = allocation.x
        attr.y = allocation.y
        attr.width = allocation.width
        attr.height = allocation.height
        attr.visual = self.get_visual()
        attr.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        WAT = Gdk.WindowAttributesType
        mask = WAT.X | WAT.Y | WAT.VISUAL
        window = Gdk.Window(self.get_parent_window(), attr, mask);
        self.set_window(window)
        self.register_window(window)
        self.set_realized(True)
        window.set_background_pattern(None)
        self.palette = ColorSequence(self.colors)

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

            if not self.label:
                self.label_pv = gepics.PV('{}.DESC'.format(pv_name))
                self.label_pv.connect('changed', self.on_label_change)

    def on_label_change(self, pv, value):
        self.props.label = value
        self.queue_draw()

    def on_change(self, pv, value):
        self.value = value
        self.queue_draw()

    def on_alarm(self, pv, alarm):
        if self.alarm:
            style = self.get_style_context()
            if alarm == gepics.Alarm.MAJOR:
                style.remove_class('gtkdm-warning')
                style.add_class('gtkdm-critical')
            elif alarm == gepics.Alarm.MINOR:
                style.add_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')
            else:
                style.remove_class('gtkdm-warning')
                style.remove_class('gtkdm-critical')

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.set_sensitive(True)
            self.theme['border'] = Gdk.RGBA(0.0, 0.0, 0.0, 1.0)
        else:
            self.set_sensitive(False)
            self.theme['border'] = Gdk.RGBA(1.0, 1.0, 1.0, 1.0)
        self.queue_draw()


class MenuButton(Gtk.Bin):
    __gtype_name__ = 'MenuButton'
    label = GObject.Property(type=str, default='', nick='Label')
    menu = GObject.Property(type=Gtk.Popover, nick='Display Menu')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.btn = Gtk.MenuButton(use_popover=True)
        self.icon = Gtk.Image.new_from_icon_name('open-menu-symbolic', Gtk.IconSize.MENU)
        self.text = Gtk.Label(label=self.label)
        child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        child.pack_start(self.icon, False, False, 0)
        child.pack_start(self.text, True, True, 0)
        self.btn.add(child)
        self.add(self.btn)
        self.bind_property('label', self.text, 'label', GObject.BindingFlags.DEFAULT)
        self.bind_property('menu', self.btn, 'popover', GObject.BindingFlags.DEFAULT)
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')


class DisplayMenu(Gtk.Popover):
    __gtype_name__ = 'DisplayMenu'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_border_width(3)
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')


class DisplayMenuItem(Gtk.Bin):
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
        self.show_all()
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')

    def on_clicked(self, obj):
        Manager.show_display(self.file, macros_spec=self.macros, multiple=self.multiple)


class MessageLog(Gtk.Bin):
    __gtype_name__ = 'MessageLog'

    channel = GObject.Property(type=str, default='', nick='PV Name')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')
    buffer_size = GObject.Property(type=int, default=5000, nick='Buffer Size')
    font_size = GObject.Property(type=float, minimum=5.0, default=9.0, maximum=20.0, nick='Font Size')
    show_time = GObject.Property(type=bool, default=True, nick='Show Time')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view = Gtk.TextView()
        self.buffer = Gtk.TextBuffer()
        self.sw = Gtk.ScrolledWindow()
        self.sw.set_shadow_type(Gtk.ShadowType.IN)
        self.sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        self.view.set_buffer(self.buffer)
        self.view.set_editable = False
        self.view.set_border_width(3)
        font = Pango.FontDescription('monospace {}'.format(self.font_size))
        self.view.modify_font(font)
        self.wrap_mode = Gtk.WrapMode.WORD
        self.sw.add(self.view)
        self.add(self.sw)
        self.tags = {
            gepics.Alarm.MAJOR : self.buffer.create_tag(foreground='Red', wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.MINOR: self.buffer.create_tag(foreground='Orange', wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.NORMAL: self.buffer.create_tag(foreground='Black', wrap_mode=Gtk.WrapMode.WORD),
            gepics.Alarm.INVALID: self.buffer.create_tag(foreground='Gray', wrap_mode=Gtk.WrapMode.WORD),
        }
        self.active_tag = self.tags[gepics.Alarm.NORMAL]
        self.connect('realize', self.on_realize)

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

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

        adj = self.sw.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def on_alarm(self, pv, alarm):
        if self.alarm:
            self.active_tag = self.tags[alarm]

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.get_style_context().remove_class('gtkdm-inactive')
            self.set_sensitive(True)
        else:
            self.get_style_context().add_class('gtkdm-inactive')
            self.set_sensitive(False)
