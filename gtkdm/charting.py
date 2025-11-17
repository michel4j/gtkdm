import os
import re
import bisect

from datetime import datetime
from pathlib import Path
import gi
import yaml
import numpy
import pandas
import cairo

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', "1.0")
from gi.repository import Gtk, GObject, Gio, Pango, Gdk, GLib

from matplotlib.backends.backend_gtk3cairo import FigureCanvasGTK3Cairo as FigureCanvas
from matplotlib.backends.backend_gtk3 import NavigationToolbar2GTK3 as NavigationToolbar
from matplotlib.backend_tools import Cursors
from matplotlib.figure import Figure
from matplotlib import pyplot
from matplotlib.markers import MarkerStyle

from . import version, colors
from .utils import logger, StripData
from .gui import Column, ColumnType, Table, Validator, FormField, Form

STP_CONVERTERS = {
    'Min': ('ymin', float),
    'Max': ('ymax', float),
    'Scale': ('log', lambda v: bool(int(v))),
    'Precision': ('precision', int),
    'PlotStatus': ('show', bool),
    'Comment': ('comments', lambda v: str(v).strip("\"'")),
    'Name': ('name', str),
    'Timespan': ('period', float),
    'NumSamples': ('samples', int),
    'SampleInterval': ('sample_freq', lambda v: int(1/float(v))),
    'RefreshInterval': ('refresh_freq', lambda v: int(1/float(v))),
    'GridXon': ('x_grid', bool),
    'GridYon': ('y_grid', bool),
    'AxisYcolorStat': ('color_axis', bool),
    'GraphLineWidth': ('line_width', lambda v: numpy.linspace(0, 3, 6)[int(v)+1]),
    'Units': ('units', str),
}


def get_relative_path(path):
    """
    Get full path relative to the location of this module
    :param path: relative path
    :return: str
    """
    return str(Path(__file__).parent.joinpath(path))


def stp_to_spec(filename):
    """
    Load a StripTool STP file and return a plot specification

    :param filename:
    :return:
    """
    color_pattern = re.compile(r'^Strip\.Color\.(?P<key>\w+)\s+(?P<r>\d+)\s+(?P<g>\d+)\s+(?P<b>\d+)\s+$', re.MULTILINE)
    curve_pattern = re.compile(r'^Strip\.Curve\.(?P<curve>\d+)\.(?P<key>\w+)(?P<value>[^\n]*?)$', re.MULTILINE)
    option_pattern = re.compile(r'^Strip\.(?:(Time)|(Option))\.(?P<key>\w+)(?P<value>[^\n]*?)$', re.MULTILINE)
    with open(filename, 'r') as fobj:
        data = fobj.read()
    stp_colors = {
        item[0].lower(): colors.str_to_hex(item[1:], bits=16)
        for item in color_pattern.findall(data)
    }

    info = {
        'options': {'dark': False},
        'plots': [{} for i in range(10)]
    }

    if colors.darker(stp_colors['background'], stp_colors['foreground']):
        info['options']['dark']: True

    for m in curve_pattern.finditer(data):
        item = m.groupdict()
        key, clean_func = STP_CONVERTERS.get(item['key'])
        info['plots'][int(item['curve'])][key] = clean_func(item['value'].strip())

    for m in option_pattern.finditer(data):
        item = m.groupdict()
        if not item['key'] in STP_CONVERTERS: continue
        key, clean_func = STP_CONVERTERS.get(item['key'])
        info['options'][key] = clean_func(item['value'])

    # Transfer colors from to curves
    for i in range(10):
        info['plots'][i]['color'] = stp_colors[f'color{i+1}']
    info['plots'] = list(filter(lambda item: item.get('name', '').strip(), info['plots']))
    return info


def plt_to_spec(filename):
    with open(filename, 'r') as fobj:
        info = yaml.safe_load(fobj)
    return info

def get_margins(n, sig=1):
    """
    Calculate the margins for the given number of plots
    :param n: Number of plots
    :param sig: single number of standard deviations, plots will be offset to avoid overlap.
    :return: 2D array consisting of pairs of values which are the numbers of standard deviations on each side.
    """
    y = numpy.arange(n)
    step = (sig*2)/(n+1)
    margins = step*numpy.column_stack((-(y+1), n-y))
    return margins


def stack_margins(x: numpy.ndarray, y_data: numpy.ndarray, padding: float = 0.05) -> list[tuple[float, float]]:
    """
    Calculate the stack margins for the given x data and y_data for waterfall plot
    :param x: shared x-axis array
    :param y_data: y-axis array
    :param padding: Vertical buffer between plots as a fraction of range
    :return: list of tuples of  ymin, ymax margins
    """
    valid = ~numpy.isnan(x)
    norm_ydata = y_data[valid,:]
    num_values, num_plots = norm_ydata.shape
    offsets = numpy.zeros((num_plots, ))
    scales = numpy.ones((num_plots, ))
    skyline = numpy.zeros((num_values,))

    for i in range(num_plots):
        y = norm_ydata[:, i]
        # normalize this plot
        y_min, y_max = numpy.nanmin(y), numpy.nanmax(y)
        offsets[i] = y_min
        if y_min != y_max:
            scales[i] = 1 / (y_max - y_min)
        y = (y - offsets[i]) * scales[i]

        # place over skyline
        shift = numpy.nanmax((skyline - y)) + padding
        offsets[i] -= shift / scales[i]    # adjust and transform offset to unscaled units

        # update skyline
        skyline[:] = y + shift

    # pad skyline to get top margin
    top_margin = numpy.nanmax(skyline) + padding

    return [
        (offsets[i],  (top_margin / scales[i]) + offsets[i])
        for i in range(num_plots)
    ]


def standard_margins(x: numpy.ndarray, y_data: numpy.ndarray, deviations: float = 1.0) -> list[tuple[float, float]]:
    """
    Calculate the margins for the given x data and y_data based on the number of standard deviations
    :param x: shared x-axis array
    :param y_data: y-axis array
    :param deviations: Number of standard deviations for each plot
    :return: list of tuples of  ymin, ymax margins
    """

    valid = ~numpy.isnan(x)
    y_values = y_data[valid,:]
    num_values, num_plots = y_values.shape
    means = means[:] = numpy.mean(y_values, axis=0)
    stds = numpy.std(y_values, axis=0) * deviations

    return [
        (means[i] - stds[i],  means[i] + stds[i])
        for i in range(num_plots)
    ]


PAUSE_ICONS = {
    False:  "media-playback-pause",
    True:   "media-playback-start",
}


class ChartToolbar(NavigationToolbar):

    toolitems = (
        ('Open', 'Open Chart/Data', 'document-open', 'open_chart'),
        ('Save', 'Save the Chart', 'media-floppy', 'save_plot'),
        ('Archive', 'Save the Data', 'insert-object', 'save_data'),
        ('Image', 'Save an Image', 'insert-image', 'save_image'),
        (None, None, None, None),
        ('Home', 'Reset original view', 'view-refresh', 'reset_plot'),
        ('Back', 'Back to  previous view', 'go-previous', 'back'),
        ('Forward', 'Forward to next view', 'go-next', 'forward'),
        ('Pan', 'Pan axes with left mouse, zoom with right', 'preferences-system-privacy', 'pan'),
        ('Zoom', 'Zoom to rectangle', 'zoom-fit-best', 'zoom'),
        ('Stack', 'Stack Plots', 'open-menu-symbolic', 'stack'),
        ('Diverge', 'Zoom Out and Expand plots', 'zoom-in', 'scale_diverge'),
        ('Converge', 'Compress plots', 'zoom-out', 'scale_converge'),
        ('Pause', 'Pause Updates', 'media-playback-pause', 'pause'),
        (None, None, None, None),
        ('Configure', 'Configure The Chart', 'document-properties', 'configure'),
    )

    def __init__(self, canvas, window):
        super().__init__(canvas)
        self.chart = window
        self.paused = False
        self.panning = False
        self.widgets = {}
        self.scale = 1
        self.max_scale = 10

        for i, toolitem in enumerate(self):
            if isinstance(toolitem, Gtk.ToolButton):
                icon_name = f'{self.toolitems[i][2]}-symbolic'
                name = self.toolitems[i][0]
                toolitem.get_icon_widget().set_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
                self.widgets[name] = toolitem

    def configure(self, btn):
        self.chart.configure()

    def pause(self, btn):
        self.paused = not(self.paused)
        icon_name = f'{PAUSE_ICONS[self.paused]}-symbolic'
        btn.get_icon_widget().set_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
        self.chart.pause(self.paused)

    def pan(self, *args):
        self.panning = not(self.panning)
        self.chart.set_cursor(Cursors.HAND if self.panning else None)
        super().pan(*args)

    def save_data(self, btn):
        self.chart.save_data()

    def save_plot(self, btn):
        self.chart.save_plot()

    def save_image(self, btn):
        self.chart.save_image()

    def reset_plot(self, btn):
        self.chart.reset()

    def open_chart(self, btn):
        self.chart.open_chart()

    def scale_diverge(self, btn):
        self.set_scale(min(self.scale + 1, self.max_scale))

    def scale_converge(self, btn):
        self.set_scale(max(self.scale - 1, 1))

    def stack(self, btn):
        self.chart.stack()

    def set_scale(self, scale=1):
        self.scale = scale
        self.widgets['Diverge'].set_sensitive(self.scale < self.max_scale)
        self.widgets['Converge'].set_sensitive(self.scale > 1)
        self.chart.auto_scale(self.scale)


class LegendItem(Gtk.EventBox):
    def __init__(self, name, color, units=None, comments=''):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.add(box)

        self.label = Gtk.Label(xalign=0.0)
        self.value = Gtk.Label(xalign=1.0)
        self.value.get_style_context().add_class('text-monitor')
        box.pack_start(self.label, True, True, 0)
        box.pack_end(self.value, True, True, 0)

        self.info = {}
        self.set_info(name=name, color=color, units=units, comments=comments)
        self.selected = False
        self.show_all()

    def set_info(self, **kwargs):
        self.info.update(kwargs)
        color = self.info.get('color', '#000')
        name = self.info.get('name', '').strip()
        units = self.info.get('units')
        comments = self.info.get('comments', '').strip()
        text = name if not comments else comments
        suffix = '' if not units else f' {units}'
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_markup(f'<span color="{color}">{text}</span>')
        self.value.set_markup(f'<span color="{color}"><small><tt>nan</tt></small></span>')
        self.set_tooltip_markup(f'<small>{name}</small>')
        self.name = name

    def set_value(self, value):
        units = self.info.get('units')
        color = self.info.get('color', '#000')
        suffix = '' if not units else f' {units}'
        self.value.set_markup(f'<span color="{color}"><small><tt>{value:g}{suffix}</tt></small></span>')

    def select(self, state):
        self.selected = state
        if self.selected:
            self.label.get_style_context().add_class('selected')
        else:
            self.label.get_style_context().remove_class('selected')


class ChartConfigTable(Table):

    Columns = {
        'name': Column(title='PV Name', type=ColumnType.TEXT, text='{}', expand=True, editable=True, min_width=200),
        'show': Column(title='👁', type=ColumnType.TOGGLE, text='', expand=False, editable=False),
        'color': Column(title='', type=ColumnType.COLOR, text='{}', expand=False, editable=True),
        'ymin': Column(title='Y-min', type=ColumnType.FLOAT, text='{:g}', expand=False, editable=True, min_width=75),
        'ymax': Column(title='Y-max', type=ColumnType.FLOAT, text='{:g}', expand=False, editable=True, min_width=75),
        'log': Column(title='Log', type=ColumnType.TOGGLE, text='', expand=False, editable=False),
        'units': Column(title='Units', type=ColumnType.TEXT, text='{}', expand=False, editable=True, min_width=75),
        'comments': Column(title='Comments', type=ColumnType.TEXT, text='{}', expand=True, editable=True, min_width=75),
    }
    parent = 'name'
    sortable = False
    flat = True
    single_click = True


@Gtk.Template.from_file(get_relative_path('glade/chart-config.ui'))
class ChartConfig(Gtk.Window):
    __gtype_name__ = 'ChartConfig'

    cancel_button = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()
    add_entry = Gtk.Template.Child()
    add_button = Gtk.Template.Child()
    editor_view = Gtk.Template.Child()
    period_entry = Gtk.Template.Child()
    samples_entry = Gtk.Template.Child()
    sample_freq_spin = Gtk.Template.Child()
    refresh_freq_spin = Gtk.Template.Child()
    line_width_spin = Gtk.Template.Child()
    xgrid_toggle = Gtk.Template.Child()
    ygrid_toggle = Gtk.Template.Child()
    dark_toggle = Gtk.Template.Child()


class ConfigForm(Form):
    def __init__(self, form):
        super().__init__(
            fields=(
                FormField('period', form.period_entry, Validator.Float(default=2, fmt='{:0.0f}')),
                FormField('samples', form.samples_entry, Validator.Int(lo=1, hi=65535, default=7200)),
                FormField('refresh_freq', form.refresh_freq_spin, Validator.Float(lo=0.1, hi=10, default=1.0)),
                FormField('sample_freq', form.sample_freq_spin, Validator.Float(lo=0.1, hi=10, default=1.0)),
                FormField('line_width', form.line_width_spin, Validator.Float(lo=0.25, hi=4, default=0.5)),
                FormField('x_grid', form.xgrid_toggle, Validator.Bool(default=True)),
                FormField('y_grid', form.ygrid_toggle, Validator.Bool(default=True)),
                FormField('dark', form.dark_toggle, Validator.Bool(default=False)),
            )
        )

    def monitor_changes(self, field, name, value):
        super().monitor_changes(field, name, value)


class ChartWindow(Gtk.Window):
    __gtype_name__ = 'ChartWindow'

    paused = GObject.Property(type=bool, default=False, nick='Pause Updates')
    line_width = GObject.Property(type=float, default=0.5, nick='Line Width')
    x_grid = GObject.Property(type=bool, default=False, nick='Show X-Grid Lines')
    y_grid = GObject.Property(type=bool, default=False, nick='Show Y-Grid Lines')
    dark = GObject.Property(type=bool, default=False, nick='Dark Mode')

    def __init__(self, title=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
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
        btn = Gtk.ModelButton(text='  About GtkDM Chart')
        btn.connect("clicked", self.on_about)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        btn = Gtk.ModelButton(text='  Quit')
        btn.connect("clicked", self.on_close)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        popover.show_all()
        if title:
            self.header.props.title = "GtkDM Chart - {}".format(title)
        else:
            self.header.props.title = "GtkDM Chart"

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.figure = Figure(dpi=80)
        self.figure.set_tight_layout(True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_size_request(650, 550)
        self.canvas.mpl_connect('motion_notify_event', self.on_cursor_motion)
        #self.canvas.mpl_connect('button_release_event', self.on_cursor_click)
        self.toolbar = ChartToolbar(self.canvas, self)

        self.legend = Gtk.ListBox()
        self.legend.get_style_context().add_class('chart-legend')
        self.legend.set_selection_mode(Gtk.SelectionMode.NONE)
        self.legend.set_size_request(200, -1)
        self.legend.connect('row-activated', self.on_legend_activated)

        vbox.pack_start(hbox, True, True, 0)
        vbox.pack_end(self.toolbar, False, False, 0)
        hbox.pack_start(self.canvas, True, True, 0)
        hbox.pack_end(self.legend, False, False, 0)
        self.add(vbox)
        self.hbox = hbox
        self.color_cycle = pyplot.rcParams['axes.prop_cycle'].by_key()['color']
        # chart variables
        self.activated = False
        self.info = {
            'specs': {},
            'data': None,
            'plots': [],
            'axes': [],
            'config': [],
            'labels': [],
            'tags': [],
            'cursor': None,
            'markers': [],
            'yaxis': None,         # Which y-axis to show, defaults to the first curve
            'colors': [],
        }
        self.marker_size = 5
        self.data_src = None
        self.settings = Gtk.Settings.get_default()
        self.bind_property(
            'dark', self.settings, 'gtk_application_prefer_dark_theme',
            GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE
        )

    def choose_file(self, action: Gtk.FileChooserAction = Gtk.FileChooserAction.OPEN, filters=()):
        overwrite = False
        if action == Gtk.FileChooserAction.CREATE_FOLDER:
            title = 'Create Folder'
            btn = Gtk.STOCK_ADD
            overwrite = True
        elif action == Gtk.FileChooserAction.SELECT_FOLDER:
            title = "Select Folder"
            btn = "Select"
        elif action == Gtk.FileChooserAction.SAVE:
            title = "Select File to Save"
            btn = Gtk.STOCK_SAVE
            overwrite = True
        else:
            title = 'Select File to open'
            btn = Gtk.STOCK_OPEN
        dialog = Gtk.FileChooserDialog(
            title=title, parent=self, action=action
        )
        dialog.set_do_overwrite_confirmation(overwrite)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            btn,
            Gtk.ResponseType.OK,
        )

        for file_filter in filters:
            flt = Gtk.FileFilter()
            flt.set_name(file_filter['name'])
            flt.add_mime_type(file_filter['mime-type'])
            if 'pattern' in file_filter:
                flt.add_pattern(file_filter.get('pattern', '*.*'))
            elif 'patterns' in file_filter:
                [flt.add_pattern(pattern) for pattern in file_filter['patterns']]
            dialog.add_filter(flt)

        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            format = dialog.get_filter()
        else:
            filename = None
            format = None
        dialog.destroy()

        return filename, format

    def on_mouse_press(self, widget, event):
        if event.button == Gdk.BUTTON_MIDDLE:
            self.clipboard.set_text(widget.name, -1)

    def on_cursor_motion(self, event):
        style = self.legend.get_style_context()
        if event.inaxes and self.info['data'] and self.activated:
            x_data = self.info['data'].x_data()
            x = max(numpy.nanmin(x_data), min(event.xdata, numpy.nanmax(x_data)))
            self.info['cursor'].set_linestyle('-')
            finder = len(x_data) - bisect.bisect_right(x_data[::-1], x) - 1
            self.info['cursor'].set_xdata([x_data[finder]])
            markers = [finder]
            style.add_class('finding')
        else:
            self.info['cursor'].set_linestyle('none')
            style.remove_class('finding')
            markers = []

        if markers != self.info['markers']:
            self.info['markers'] = markers
            for i, ln in enumerate(self.info['plots']):
                ln.set_markevery(markers)
        self.canvas.draw_idle()

    # def on_cursor_click(self, event):
    #     if event.inaxes:
    #         x, y = event.xdata, event.ydata

    def on_legend_activated(self, listbox, row):
        index = row.get_index()

        self.info['yaxis'] = None if self.info['yaxis'] == index else index
        for i, axis in enumerate(self.info['axes']):
            is_visible = i == index
            axis.yaxis.set_visible(is_visible)
            axis.yaxis.grid(is_visible and self.y_grid)

            self.info['labels'][i].select(is_visible)
            width = 3*self.line_width if i == self.info['yaxis'] else self.line_width
            self.info['plots'][i].set_linewidth(width)

    def set_cursor(self, cursor: Cursors = None):
        if cursor is None:
            self.canvas.set_cursor(Cursors.POINTER)
        else:
            self.canvas.set_cursor(cursor)

    def pause(self, state):
        self.props.paused = state

    def save_data(self):
        # supported formats
        filters = (
            {'name': 'HDF5', 'mime-type': 'application/x-hdf5', 'pattern': '*.h5'},
            {'name': 'CSV', 'mime-type': 'text/csv', 'pattern': '*.csv'},
            {'name': 'Excel', 'mime-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'pattern': '*.xlsx'},
        )

        filename, format = self.choose_file(Gtk.FileChooserAction.SAVE, filters=filters)
        if filename is not None:
            if isinstance(format, Gtk.FileFilter):
                df = pandas.DataFrame.from_records(self.info['data'].get_structured()).dropna(subset='time')
                if format.get_name() == 'HDF5':
                    basename, ext = os.path.splitext(filename)
                    filename = f'{basename}.h5'
                    df.to_hdf(filename, 'data', complevel=9, complib='blosc:zstd')
                    print(f'Data written to {format.get_name()} file {filename}')
                elif format.get_name() == 'CSV':
                    basename, ext = os.path.splitext(filename)
                    filename = f'{basename}.csv'
                    df.to_csv(filename)
                    print(f'Data written to {format.get_name()} file {filename}')
                elif format.get_name() == 'Excel':
                    basename, ext = os.path.splitext(filename)
                    filename = f'{basename}.xlsx'
                    df.to_excel(filename)
                    print(f'Data written to {format.get_name()} file {filename}')
            else:
                print(f'File format "{format}" not supported.')

    def save_image(self):
        filename, format = self.choose_file(
            action=Gtk.FileChooserAction.SAVE,
            filters=(
                {'name': 'Portable Network Graphics', 'mime-type': 'image/png', 'pattern': '*.png'},
            )
        )
        if filename is not None:
            window = self.hbox.get_window()
            alloc = self.hbox.get_allocation()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
            ctx = cairo.Context(surface)
            Gdk.cairo_set_source_window(ctx, window, -alloc.x, -alloc.y)
            ctx.paint()
            surface.write_to_png(filename)

    def save_plot(self):
        filename, format = self.choose_file(
            action=Gtk.FileChooserAction.SAVE,
            filters=(
                {'name': 'GtkDM Chart', 'mime-type': 'application/chart', 'pattern': '*.plt'},
            )
        )

        if filename is not None:
            root, ext = os.path.splitext(filename)
            filename = f'{root}.plt'
            with open(filename, 'w') as fobj:
                yaml.dump(self.info['specs'], fobj, default_flow_style=False)

    def open_chart(self):
        filename, format = self.choose_file(
            action=Gtk.FileChooserAction.OPEN,
            filters=(
                {'name': 'GtkDM Chart', 'mime-type': 'application/json', 'patterns': ('*.plt', '*.stp')},
            )
        )
        if filename is not None:
            Manager.load_chart(filename)

    def reset(self):
        plot = self.info['data']
        self.toolbar.set_scale(1)
        if plot is None:
            return

        specs = self.info['specs']

        xmax = numpy.nanmax(plot.x_data())
        xmin = xmax - specs['options']['period']
        self.info['axes'][0].set_xlim(xmin, xmax)
        margins = [
            (item.get('orig_ymin', item['ymin']), item.get('orig_ymax', item['ymax']))
            for item in specs.get('plots', [])
        ]
        self.set_margins(margins)

    def auto_scale(self, scale):
        plot = self.info['data']
        if plot is None:
            return

        margins = standard_margins(plot.x_data(), plot.y_data(), deviations=scale)
        self.set_margins(margins)

    def set_margins(self, margins):
        for i, axis in enumerate(self.info['axes']):
            ymin, ymax = margins[i]
            if ymax > ymin:
                axis.set_ylim(ymin, ymax)

    def stack(self):
        plot = self.info['data']
        if plot is None:
            return

        margins = stack_margins(plot.x_data(), plot.y_data())
        self.set_margins(margins)

    def setup_chart(self, specs):
        if specs:
            self.props.x_grid = specs['options'].get('x_grid', False)
            self.props.y_grid = specs['options'].get('y_grid', False)
            self.props.line_width = specs['options'].get('line_width', 0.5)
            self.props.dark = specs['options'].get('dark', False)

        if self.dark:
            style = get_relative_path('glade/dark.mplstyle')
            fg_color = '#9c9c9c'
            bg_color = '#303030'
            self.figure.patch.set_facecolor(bg_color)
        else:
            style = 'default'
            fg_color = '#000'
            bg_color = '#fff'
            self.figure.patch.set_facecolor(bg_color)

        pyplot.style.use(style)

        # clear chart if items exist
        self.figure.clear()
        del self.info['axes']
        del self.info['plots']
        del self.info['labels']
        del self.info['cursor']

        self.info.update({
            'specs': specs, 'plots': [], 'axes': [], 'config': [], 'labels': [], 'tags': [],
            'cursor': None, 'markers': [], 'yaxis': None, 'colors': []
        })

        # clear legend
        for label in self.legend.get_children():
            self.legend.remove(label)

        # clear figure
        self.figure.clear()

        host = self.figure.add_subplot()
        host.set_xlabel(datetime.now().strftime("%b %d\n%H:%M:%S"), loc='right')
        host.xaxis.grid(linewidth=.5, linestyle=':', color=fg_color)
        host.xaxis.grid(self.x_grid)
        self.info['cursor'] = host.axvline(1, ls="none", lw=1, color=fg_color, alpha=0.5)
        self.info['axes'] = [host]
        data_names = []
        artists = []

        marker_style = MarkerStyle(marker='o', fillstyle="full")
        for i, item in enumerate(specs.get('plots', [])):
            data_names.append(item['name'])
            color = item['color']
            self.info['colors'].append(color)
            label = LegendItem(item['name'], color=color, units=item.get('units'), comments=item.get('comments'))
            label.select(i == 0)
            label.connect("button-press-event", self.on_mouse_press)
            self.legend.add(label)
            self.info['labels'].append(label)
            if i == 0:
                axis = self.info['axes'][i]
            else:
                axis = host.twinx()
                axis.yaxis.tick_left()
                axis.yaxis.set_label_position('left')
                axis.set_frame_on(False)
                self.info['axes'].append(axis)

            if item.get('log'):
                axis.set_yscale('symlog')

            axis.yaxis.set_visible(i == 0)
            axis.yaxis.grid(linewidth=.5, linestyle=':', color=fg_color)
            axis.yaxis.grid(self.y_grid and i == 0)

            axis.tick_params(axis='y', colors=color)

            ln, = axis.plot(
                [], [], '-', marker=marker_style, markevery=[], color=color,
                markerfacecolor=bg_color, markersize=self.marker_size, lw=self.line_width
            )
            axis.set_ylim(item['ymin'], item['ymax'])
            artists.append(ln)

        self.info['plots'] = artists

        # transfer and destroy current data
        existing = None
        if self.info['data']:
            old_data = self.info['data']
            existing = old_data.get_structured()
            old_data.destroy()
            self.activated = False
            self.info['data'] = None
            del old_data

        if specs:
            data = StripData(
                *data_names,
                period=specs['options']['period'],
                samples=specs['options']['samples'],
                sample_freq=specs['options']['sample_freq'],
                refresh_freq=specs['options']['refresh_freq'],
                data=existing
            )

            self.data_src = data.connect('changed', self.on_data_changed)
            self.info['data'] = data

    def on_data_changed(self, plot):
        try:
            x_data = plot.x_data()
            y_data = plot.y_data()

            if x_data is None :
                return

            for j in range(plot.count-1):
                ln = self.info['plots'][j]
                if not self.paused:
                    ln.set_data(x_data, y_data[:, j])

                finder = 0
                if self.info['markers']:
                    finder = self.info['markers'][0]
                value = ln.get_ydata()[finder]
                self.info['labels'][j].set_value(value)

            if not self.activated:
                xmin, xmax = -self.info['specs']['options']['period'], 0
                if xmin != xmax:
                    self.info['axes'][0].set_xlim(xmin, xmax)
                self.activated = True

            self.info['axes'][0].set_xlabel(self.info['data'].end_time().strftime("%b %d\n%H:%M:%S"), loc='right')
            self.canvas.draw_idle()

        except (IndexError, AttributeError) as e:
            pass

    def configure(self):
        specs = self.info['specs']
        window = ChartConfig()
        window.set_transient_for(self)
        table = ChartConfigTable(window.editor_view)
        form = ConfigForm(window)

        table.view.connect('key-press-event', self.on_delete_plot)

        window.cancel_button.connect('clicked', lambda b: window.destroy())
        window.apply_button.connect('clicked', self.apply_config, window, table, form)
        window.add_button.connect('clicked', self.add_plot, window, table)

        form.set_values(**specs.get("options", {}))

        table.add_items(specs.get('plots', []))
        window.show_all()

    def on_delete_plot(self, view, event):
        key = Gdk.keyval_name(event.keyval).upper()
        if key == 'DELETE':
            selection = view.get_selection()
            model, selected = selection.get_selected()
            if selected:
                del model[selected]

    def add_plot(self, btn, window, table):
        if table.size() < 10:
            pv_name = window.add_entry.get_text().strip()
            index = table.size() % len(self.color_cycle)
            if pv_name:
                table.add_item({
                    'name': pv_name,
                    'show': True,
                    'ymin': 0,
                    'ymax': 1,
                    'log': False,
                    'units': '',
                    'comments': '',
                    'color': self.color_cycle[index]
                })
        else:
            btn.set_sensitive(False)

    def apply_config(self, btn, window, table, form):
        specs = self.info['specs']
        specs['plots'] = table.get_items()
        specs['options'] = form.get_values()
        self.setup_chart(specs)
        window.destroy()

    def on_about(self, btn):
        about_dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        about_dialog.set_program_name("GtkDM Charting")
        about_dialog.set_logo_icon_name('applications-engineering')
        about_dialog.set_comments("Python-based Gtk Display Manager Strip Charting for \nEPICS Process Variables")
        about_dialog.set_version(version.get_version())
        about_dialog.set_copyright("© 2019-{} Canadian Light Source, Inc.".format(datetime.now().year))
        about_dialog.set_license_type(Gtk.License.MIT_X11)
        about_dialog.set_authors(["Michel Fodje <michel.fodje@lightsource.ca>"])
        about_dialog.present()

    def on_close(self, btn):
        self.destroy()


class ChartManager(object):
    """Manages all displays"""

    def __init__(self):
        self.macros = {}
        self.registry = {}
        self.search_paths = [os.getcwd()] + os.environ.get('GTKDM_DISPLAY_PATH', '').split(':')
        self.windows = []

    def find_chart(self, path, root_path=None):
        """
        Search for the chart file and return the full path
        :param path: relative or absolute path to find
        :param root_path: top-level path of display frame to search first.
        :return: Full path to display file, or None if not found

        """

        if path is None:
            return

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

    def load_chart(self, path=None):
        """
        Show a display file

        :param path: absolute or relative path to display file
        """

        full_path = self.find_chart(path)
        if not full_path:
            logger.warning('Chart File {} not found'.format(path))
            specs = {}
            name = None
        else:
            logger.info(f"Loading: {full_path}...")
            name = Path(path).name
            if full_path.endswith('.stp'):
                specs = stp_to_spec(full_path)
            elif full_path.endswith('.plt'):
                specs = plt_to_spec(full_path)
            else:
                logger.warning('Unsupported chart format!')
                specs = {}
                name = None
        self.create_chart(specs, name)

    def create_chart(self, specs, name):
        """
        Create and display a new chart window

        :param specs: specs dictionary
        :param name:  chart title
        """

        window = ChartWindow(title=name)
        window.setup_chart(specs)
        window.connect('destroy', self.close_window)
        self.windows.append(window)
        window.show_all()

    def close_window(self, window):
        """
        Check and exit application when the last window is closed.

        :param window:
        :return:
        """
        self.windows.remove(window)
        if not len(self.windows):
            Gtk.main_quit()


Manager = ChartManager()
