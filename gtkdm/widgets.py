from math import atan2, pi, cos, sin, ceil
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject, Gdk, Gio
import gepics


def pix(v):
    """Round to neareast 0.5 for cairo drawing"""
    x = round(v*2)
    return x/2 if x%2 else (x+1)/2


class Display(Gtk.Fixed):
    __gtype_name__ = 'Display'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

Direction = Gdk.WindowEdge


class TextMonitor(Gtk.EventBox):
    __gtype_name__ = 'TextMonitor'

    channel = channel = GObject.Property(type=str, default='', nick='PV Name')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = Gtk.Label('<N/A>')
        self.add(self.label)
        self.pv = None
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
        text = '<tt>{:>8s}</tt> {}'.format(pv.char_value, pv.units) if pv.units else pv.char_value
        self.label.set_markup(text)

    def on_alarm(self, pv, alarm):
        if alarm == gepics.Alarm.CRITICAL:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().add_class('gtkdm-critical')
        elif alarm == gepics.Alarm.WARNING:
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
    color = GObject.Property(type=Gdk.RGBA, nick='Color')
    columns = GObject.Property(type=int, minimum=1, maximum=8, default=1, nick='Columns')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._view_bits = ['0'] * 8
        self._view_labels = [''] * 8
        self.set_size_request(196, 40)

    def do_draw(self, cr):
        allocation = self.get_allocation()
        stride = ceil(8 / self.columns)
        col_width = allocation.width / self.columns

        # draw boxes
        if not self.color:
            self.props.color = Gdk.RGBA(red=0.0, green=1.0, blue=0.0, alpha=1.0)

        if self.pv.is_active():
            border_color = Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0)
        else:
            border_color = Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0)
        label_color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)

        cr.set_line_width(1)
        #cr.set_font_size(9)

        for i in range(8):
            x = pix((i // stride) * col_width + 4)
            y = pix(4 + (i % stride) * 14)
            cr.rectangle(x, y, 10, 10)
            if self._view_bits[i] == '1':
                cr.set_source_rgba(*self.color)
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 1.0)
            cr.fill_preserve()
            cr.set_source_rgba(*border_color)
            cr.stroke()

            cr.set_source_rgba(*label_color)
            label = self._view_labels[i]
            xb, yb, w, h = cr.text_extents(label)[:4]
            cr.move_to(x + 14, y + h - 1)
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
        if alarm == gepics.Alarm.CRITICAL:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().add_class('gtkdm-critical')
        elif alarm == gepics.Alarm.WARNING:
            self.get_style_context().add_class('gtkdm-warning')
            self.get_style_context().remove_class('gtkdm-critical')
        else:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().remove_class('gtkdm-critical')

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.get_style_context().remove_class('gtkdm-inactive')
        else:
            self.get_style_context().add_class('gtkdm-inactive')

        self.queue_draw()


class Indicator(Gtk.Widget):
    __gtype_name__ = 'Indicator'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    label = GObject.Property(type=str, default='', nick='Label')
    color = GObject.Property(type=Gdk.RGBA, nick='Color')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_size_request(196, 40)
        self.box_color = self.color
        self.pv = None
        self.label_pv = None

    def do_draw(self, cr):
        cr.set_line_width(0.5)
        x = 4.5
        y = 4.5
        label_color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        if self.box_color:
            cr.set_source_rgba(*self.box_color)

        cr.rectangle(x, y, 10, 10)
        cr.fill_preserve()
        cr.set_source_rgba(*label_color)
        cr.stroke()

        xb, yb, w, h = cr.text_extents(self.label)[:4]
        cr.move_to(x + 14, y + h - 1.5)
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
        if value:
            self.box_color = Gdk.RGBA(red=0.0, green=1.0, blue=0.0, alpha=1.0)
        else:
            self.box_color = Gdk.RGBA(red=0.5, green=0.5, blue=0.5, alpha=1.0)
        self.queue_draw()

    def on_alarm(self, pv, alarm):
        if alarm == gepics.Alarm.CRITICAL:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().add_class('gtkdm-critical')
        elif alarm == gepics.Alarm.WARNING:
            self.get_style_context().add_class('gtkdm-warning')
            self.get_style_context().remove_class('gtkdm-critical')
        else:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().remove_class('gtkdm-critical')

    def on_active(self, pv, connected):
        if connected:
            self.pv.get_with_metadata()
            self.get_style_context().remove_class('gtkdm-inactive')
            self.box_color = Gdk.RGBA(red=0.0, green=1.0, blue=0.0, alpha=0.0)
        else:
            self.get_style_context().add_class('gtkdm-inactive')
            self.box_color = Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=1.0)

        self.queue_draw()


class ScaleControl(Gtk.EventBox):
    __gtype_name__ = 'ScaleControl'
    channel = GObject.Property(type=str, default='', nick='PV Name')
    minimum = GObject.Property(type=float, default=0., nick='Minimum')
    maximum = GObject.Property(type=float, default=100., nick='Maximum')
    increment = GObject.Property(type=float, default=1., nick='Increment')
    orientation = GObject.Property(type=Gtk.Orientation, default=Gtk.Orientation.HORIZONTAL, nick='Orientation')
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
        if alarm == gepics.Alarm.CRITICAL:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().add_class('gtkdm-critical')
        elif alarm == gepics.Alarm.WARNING:
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
        if alarm == gepics.Alarm.CRITICAL:
            self.get_style_context().remove_class('gtkdm-warning')
            self.get_style_context().add_class('gtkdm-critical')
        elif alarm == gepics.Alarm.WARNING:
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


TextControl.set_css_name('textcontrol')



__all__ = ['LineMonitor', 'TextMonitor', 'Display', 'ScaleControl', 'TextControl', 'TextLabel']
