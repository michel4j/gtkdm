from math import atan2, pi, cos, sin, ceil
import textwrap
import os
import json
import gi
import zipfile
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject, Gdk, Gio, GdkPixbuf, Pango, GLib, PangoCairo
import gepics


_SYMBOL_REGISTRY = {

}

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
    return (a*pi/180)


def ticks(lo, hi, step):
    return [i*step+ceil(float(lo)/step)*step for i in range(1+int(ceil((float(hi)-lo)/step)))]



Direction = Gdk.WindowEdge


class Display(Gtk.Fixed):
    __gtype_name__ = 'Display'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TextMonitor(Gtk.EventBox):
    __gtype_name__ = 'TextMonitor'

    channel =  GObject.Property(type=str, default='', nick='PV Name')
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
    big_endian = GObject.Property(type=bool, default=False, nick='Big-Endian')
    labels = GObject.Property(type=str, default='', nick='Labels')
    colors = GObject.Property(type=str, default='AG', nick='Colors')
    columns = GObject.Property(type=int, minimum=1, maximum=8, default=1, nick='Columns')
    size = GObject.Property(type=int, minimum=5, maximum=50, default=10, nick='LED Size')
    alarm = GObject.Property(type=bool, default=False, nick='Alarm Sensitive')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._view_bits = ['0'] * 8
        self._view_labels = [''] * 8
        self.set_size_request(196, 40)
        self.palette = ColorSequence(self.colors)
        self.theme = {
            'border': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }

    def do_draw(self, cr):
        allocation = self.get_allocation()
        stride = ceil(8 / self.columns)
        col_width = allocation.width / self.columns

        # draw boxes
        style = self.get_style_context()
        self.theme['label'] = style.get_color(style.get_state())

        cr.set_line_width(0.75)

        for i in range(8):
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
            cr.move_to(x + self.size + 4.5, y + self.size/2 - yb - h/2)
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

        pv_name = self.channel
        if pv_name:
            self.pv = gepics.PV(pv_name)
            self.pv.connect('changed', self.on_change)
            self.pv.connect('alarm', self.on_alarm)
            self.pv.connect('active', self.on_active)

        labels = [v.strip() for v in self.labels.split(',')]
        self._view_labels = labels + (8 - len(labels)) * ['']

    def on_change(self, pv, value):
        bits = list(bin(value)[2:].zfill(8))
        if self.big_endian:
            self._view_bits = bits
        else:
            self._view_bits = bits[::-1]
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
        self.palette = ColorSequence(self.colors)
        self.theme = {
            'border': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
            'fill': Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0),
        }

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
        cr.move_to(x + self.size + 4, y + self.size/2 - yb - h/2)
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


class ScaleControl(Gtk.EventBox):
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

    def on_realize(self, obj):
        self.get_style_context().add_class('gtkdm')
        self.adjustment.configure(0.0, self.minimum, self.maximum, self.increment, 0, 0)
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
        self.buttons = [Gtk.ToggleButton(label='Choice 1'), Gtk.ToggleButton(label='Choice 2'), Gtk.ToggleButton(label='Choice 3')]
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

            for btn in self.buttons[i+1:]:
                btn.destroy()

            self.set_sensitive(True)
        else:
            self.set_sensitive(False)

    def on_change(self, pv, value):
        self.in_progress = True
        for i, btn in enumerate(self.buttons):
            btn.set_active(i==value)
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
        self.palette = ColorSequence(self.colors)
        self.ctrlvars = None
        self.value = 0
        self.units_label = 'mA'

    def do_draw(self, cr):
        allocation = self.get_allocation()
        x = allocation.width/2
        y = allocation.height/2
        r = 4*x/6

        style = self.get_style_context()
        font_desc = style.get_font(style.get_state())
        color = style.get_color(style.get_state())
        cr.set_source_rgba(*color)
        cr.set_line_width(0.75)

        minimum = (self.minimum//self.step)*self.step
        maximum = ceil(self.maximum//self.step)*self.step

        half_angle = self.angle/2
        start_angle = radians(270 - half_angle)
        end_angle = radians(270 + half_angle)
        offset = r*sin(90-radians(half_angle))/2
        angle_scale = (end_angle - start_angle)/(maximum - minimum)
        tick_width = 12
        y += offset
        cr.arc(x, y, r, start_angle, end_angle)
        cr.stroke()

        rt = r + tick_width
        r1 = r - tick_width/2
        r0 = r + tick_width/2

        major = ticks(minimum, maximum, self.step)
        minor = ticks(minimum, maximum, self.step/(self.ticks+1))

        # levels
        cr.set_line_width(2)
        rl = 2*r/3
        if self.levels and self.ctrlvars:
            lolo = self.ctrlvars['lower_alarm_limit']*angle_scale + start_angle
            lo = self.ctrlvars['lower_warning_limit']*angle_scale + start_angle
            hi = self.ctrlvars['upper_warning_limit']*angle_scale + start_angle
            hihi = self.ctrlvars['upper_alarm_limit']*angle_scale + start_angle

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
        for tick in set(minor+major):
            is_major = tick in major
            tick_angle = angle_scale*(tick-minimum) + start_angle
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
                cr.move_to(tx3 - xb - tw/2, ty3 - yb - th / 2)
                cr.show_text(label)
            cr.move_to(tx2, ty2)
            cr.line_to(tx1, ty1)
            cr.stroke()

        # Units
        if self.units:
            units_angle = (end_angle + start_angle)/2
            ur = r/3
            ux2 = x + ur * cos(units_angle)
            uy2 = y + ur * sin(units_angle)
            xb, yb, tw, th = cr.text_extents(self.units_label)[:4]
            cr.set_source_rgba(*color)
            cr.move_to(ux2-xb - tw/2, uy2-yb-th/2)
            cr.show_text(self.units_label)

        # needle
        cr.set_line_width(0.5)
        value_angle = angle_scale*(self.value - minimum) + start_angle
        vr = 5*r/6
        vx2 = x + vr * cos(value_angle)
        vy2 = y + vr * sin(value_angle)
        cr.set_source_rgba(*alpha(color, 0.5))
        cr.move_to(x - 2, y)
        cr.line_to(vx2, vy2)
        cr.line_to(x + 2, y)
        cr.fill_preserve()
        cr.stroke()

        #label
        if self.label:
            xb, yb, tw, th = cr.text_extents(self.label)[:4]
            lines = textwrap.wrap(self.label, int(len(self.label)*0.6*allocation.width/tw))
            cr.set_source_rgba(*color)
            yl = max(y, y + rt * sin(start_angle))
            for i, line in enumerate(lines):
                xb, yb, tw, th = cr.text_extents(line)[:4]
                cr.move_to(x - xb - tw/2, yl + (i + 1.2)*th)
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
        x = allocation.width/2
        y = allocation.height/2
        if self.image:
            scale = min(allocation.width/self.frames.width, allocation.height/self.frames.height)
            cr.save()
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(
                cr, self.image,
                x - self.image.get_width()*scale/2,
                y - self.image.get_height()*scale/2
            )
            cr.paint()
            cr.restore()
        else:
            # draw boxes
            style = self.get_style_context()
            color = style.get_color(style.get_state())
            cr.set_source_rgba(*color)
            xb, yb, w, h = cr.text_extents(')(')[:4]
            cr.move_to(x - xb - w/2, y - yb - h/2)
            cr.show_text(')(')

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

#TODO
# Toggle Button
# Spin Button
# Combo Box


__all__ = [
    'LineMonitor', 'TextMonitor', 'Display', 'ScaleControl', 'TextControl',
    'TextLabel', 'CommandButton', 'ChoiceButton'
]
