import os
import re
import bisect
from datetime import datetime

import gi
import numpy

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', "1.0")
from gi.repository import Gtk, GObject, Gio, Pango, Gdk

from matplotlib.backends.backend_gtk3agg import FigureCanvasGTK3Agg as FigureCanvas
from matplotlib.backends.backend_gtk3 import NavigationToolbar2GTK3 as NavigationToolbar
from matplotlib.figure import Figure
from matplotlib.markers import MarkerStyle

from . import version
from .utils import logger, StripData

Y_AXIS_OFFSET = 60
AXIS_SPACE = 0.92

CONVERTERS = {
    'Min': float,
    'Max': float,
    'Scale': int,
    'Precision': int,
    'PlotStatus': bool,
    'Comment': str,
    'Name': str,
    'Timespan': int,
    'NumSamples': int,
    'SampleInterval': float,
    'RefreshInterval': float,
    'GridXOn': bool,
    'GridYOn': bool,
    'AxisYcolorStat': bool,
    'GraphLineWidth': float,
    'Units': str,
}


def stp_to_spec(filename):
    color_pattern = re.compile(r'Strip\.Color\.(?P<key>\w+)\s+(?P<r>\d+)\s+(?P<g>\d+)\s+(?P<b>\d+)')
    curve_pattern = re.compile(r'Strip\.Curve\.(?P<curve>\d+).(?P<key>\w+)\s+(?P<value>[-\w.:_]*?)\n')
    option_pattern = re.compile(r'Strip\.(?:(Time)|(Option))\.(?P<key>\w+)\s+(?P<value>[-\w.:_]*?)\n')
    with open(filename, 'r') as fobj:
        data = fobj.read()

    info = {
        'options': {
            item[0].lower(): tuple(map(lambda v: int(v)*255//65535, item[1:]))
            for item in color_pattern.findall(data)
        },
        'plots': [{'axis': i} for i in range(10)]
    }
    for m in curve_pattern.finditer(data):
        item = m.groupdict()
        info['plots'][int(item['curve'])][item['key'].lower()] = CONVERTERS.get(item['key'], lambda v: v)(item['value'])

    for m in option_pattern.finditer(data):
        item = m.groupdict()
        info['options'][item['key'].lower()] = CONVERTERS.get(item['key'], lambda v: v)(item['value'])

    # Transfer colors
    for i in range(10):
        info['plots'][i]['color'] = info['options'].pop(f'color{i+1}')

    return info


class ChartToolbar(NavigationToolbar):

    toolitems = (
        ('Open', 'Open Chart/Data', 'document-open', 'open_chart'),
        ('Archive', 'Save the Data', 'document-save', 'save_data'),
        ('Save', 'Save the Figure', 'media-floppy', 'save_figure'),
        (None, None, None, None),
        ('Home', 'Reset original view', 'emblem-synchronizing', 'home'),
        ('Back', 'Back to  previous view', 'go-previous', 'back'),
        ('Forward', 'Forward to next view', 'go-next', 'forward'),
        ('Pan', 'Pan axes with left mouse, zoom with right', 'preferences-system-privacy', 'pan'),
        ('Zoom', 'Zoom to rectangle', 'edit-select-all', 'zoom'),
        ('Autoscale', 'Auto Scale Plots', 'object-flip-vertical', 'auto_scale'),
        ('Pause', 'Pause Updates', 'media-playback-pause', 'pause'),
        (None, None, None, None),

        ('Configure', 'Configure The Chart', 'document-properties', 'configure'),
    )

    def __init__(self, canvas, window):
        super().__init__(canvas, window)
        self.chart = window
        for i, toolitem in enumerate(self):
            if isinstance(toolitem, Gtk.ToolButton):
                icon_name = f'{self.toolitems[i][2]}-symbolic'
                image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
                toolitem.set_icon_widget(image)
                image.props.icon_size = Gtk.IconSize.SMALL_TOOLBAR

    def configure(self, btn):
        self.chart.configure()

    def pause(self, btn):
        self.chart.pause()

    def save_data(self, btn):
        self.chart.save_data()

    def open_chart(self, btn):
        self.chart.open_chart()

    def auto_scale(self, btn):
        self.chart.auto_scale()


class LegendItem(Gtk.EventBox):
    def __init__(self, name, color):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.name = name
        self.label = Gtk.Label(xalign=0.0)
        self.value = Gtk.Label(xalign=1.0)
        self.value.get_style_context().add_class('text-monitor')
        box.pack_start(self.label, True, True, 0)
        box.pack_end(self.value, True, True, 0)
        self.color = color
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_markup(f'<span color="{self.color}">{self.name}</span>')
        self.value.set_markup(f'<span color="{self.color}"><small><tt>nan</tt></small></span>')
        self.show_all()

    def set_value(self, value):
        self.value.set_markup(f'<span color="{self.color}"><small><tt>{value:g}</tt></small></span>')


class ChartWindow(Gtk.Window):
    __gtype_name__ = 'ChartWindow'

    paused = GObject.Property(type=bool, default=False, nick='Pause Updates')

    def __init__(self, *args, **kwargs):
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
        btn = Gtk.ModelButton(text='  Configure ...')
        btn.connect("clicked", self.on_edit)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)

        btn = Gtk.ModelButton(text='  Reload')
        btn.connect("clicked", self.on_reload)
        btn.set_size_request(100, -1)
        box.pack_start(btn, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        btn = Gtk.ModelButton(text='  About GtkDM Charting')
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
            self.header.props.title = "GtkDM Charting - {}".format(title)
        else:
            self.header.props.title = "GtkDM Charting"

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.figure = Figure(dpi=80, layout="tight")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_size_request(650, 550)
        self.canvas.mpl_connect('motion_notify_event', self.on_cursor_motion)
        self.canvas.mpl_connect('button_release_event', self.on_cursor_click)

        self.toolbar = ChartToolbar(self.canvas, self)
        vbox.pack_start(self.canvas, True, True, 0)
        vbox.pack_end(self.toolbar, False, False, 0)

        self.legend = Gtk.ListBox()
        self.legend.get_style_context().add_class('chart-legend')
        self.legend.set_selection_mode(Gtk.SelectionMode.NONE)
        self.legend.set_size_request(200, -1)
        self.legend.connect('row-activated', self.on_legend_activated)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.pack_start(vbox, True, True, 0)
        hbox.pack_end(self.legend, False, False, 0)
        self.add(hbox)

        # chart variables
        self.activated = False
        self.info = {
            'specs': {},
            'data': None,
            'plots': [],
            'axes': [],
            'selectors': [],
            'config': [],
            'labels': [],
            'tags': [],
            'cursor': None,
            'markers': [],
            'yaxis': 0,
        }

    def on_mouse_press(self, widget, event):
        if event.button == Gdk.BUTTON_MIDDLE:
            self.clipboard.set_text(widget.name, -1)

    def on_cursor_motion(self, event):
        style = self.legend.get_style_context()
        if event.inaxes and self.info['data']:
            x = max(self.info['data'].data[0, 0], min(event.xdata, 0))
            self.info['cursor'].set_xdata(x)
            self.info['cursor'].set_linestyle('-')
            markers = [bisect.bisect_right(self.info['data'].data[:, 0], x) - 1]
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

    def on_cursor_click(self, event):
        if event.inaxes:
            x, y = event.xdata, event.ydata
            print("CLICK", x, y)

    def on_legend_activated(self, listbox, row):
        self.info['yaxis'] = row.get_index()
        for i, axis in enumerate(self.info['axes']):
            axis.yaxis.set_visible(i == self.info['yaxis'])

    def configure(self):
        print('configure')

    def pause(self):
        print("pause")

    def save_data(self):
        print("save data")

    def open_chart(self):
        print("Open chart")

    def auto_scale(self):
        print("auto scale")

    def setup_chart(self, specs):
        self.info['specs'] = specs
        host = self.figure.add_subplot()
        host.set_xlabel(datetime.now().strftime("%b %d\n%H:%M:%S"), loc='right')
        self.info['cursor'] = host.axvline(1, ls="none", lw=0.25, color="#000", alpha=0.75)
        self.info['axes'] = [host]
        self.info['yaxis'] = 0
        data_names = []
        selectors = [-1]
        artists = []

        marker_style = MarkerStyle(marker='o', fillstyle="full")
        for i, item in enumerate(specs.get('plots', [])):
            if not item.get('name', '').strip(): continue
            data_names.append(item['name'])
            color = '#{:02x}{:02x}{:02x}'.format(*item['color'])
            label = LegendItem(item['name'], color=color)
            label.connect("button-press-event", self.on_mouse_press)
            self.legend.add(label)
            self.info['labels'].append(label)
            if len(self.info['axes']) > item['axis']:
                axis = self.info['axes'][item['axis']]
            else:
                axis = host.twinx()
                axis.yaxis.tick_left()
                axis.yaxis.set_label_position('left')
                axis.yaxis.set_visible(i == self.info['yaxis'])
                axis.set_frame_on(False)
                self.info['axes'].append(axis)

            axis.tick_params(axis='y', colors=color)

            ln, = axis.plot([], [], '-', marker=marker_style, markevery=[], color=color, markerfacecolor="white")
            axis.set_ylim(item['min'], item['max'])
            artists.append(ln)
            selectors.append(item['axis'])
        self.info['plots'] = artists
        self.info['selectors'] = numpy.array(selectors)

        if 'options' in specs:
            s_freq = int(1/specs['options']['sampleinterval'])
            r_freq = int(1/specs['options']['refreshinterval'])
            period = specs['options']['numsamples'] * specs['options']['sampleinterval']
        else:
            s_freq = 10
            r_freq = 10
            period = 60

        data = StripData(
            *data_names,
            period=period,
            sample_freq=s_freq,
            refresh_freq=r_freq,
        )
        data.connect('changed', self.on_data_changed)
        self.info['data'] = data

    def on_data_changed(self, plot):
        for j in range(plot.count-1):
            ln = self.info['plots'][j]
            finder = -1
            if self.info['markers']:
                finder = self.info['markers'][0]
            #self.info['labels'][j].set_value(plot.data[finder, j+1])
            if not self.paused:
                ln.set_data(plot.data[:, 0], plot.data[:, j+1])

            value = ln.get_ydata()[finder]
            self.info['labels'][j].set_value(value)

        if not self.paused:
            # update x-limits if not explicitly set
            if not self.activated:
                vx_min, vx_max = -self.info['specs']['options']['timespan'], 0
                if vx_min != vx_max:
                    self.info['axes'][0].set_xlim(vx_min, vx_max)
                self.activated = True

            # for k in numpy.unique(self.info['selectors'][1:]):
            #     axis = self.info['axes'][k]
            #     sel = (selectors == k)
            #     if not numpy.isnan(plot.data[:, sel]).all():
            #         vy_min, vy_max = numpy.nanmin(plot.data[:, sel]), numpy.nanmax(plot.data[:, sel])
            #         dev = numpy.nanstd(plot.data[:, sel])

            self.info['axes'][0].set_xlabel(self.info['data'].now_time.strftime("%b %d\n%H:%M:%S"), loc='right')
        self.canvas.draw_idle()

    def on_edit(self, btn):
        self.props.paused = not(self.props.paused)
        logger.warn("GtkDM Charting configuration not available")

    def on_reload(self, btn):
        logger.warn("GtkDM Charting configuration not available")

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
        self.search_paths = [os.getcwd()] + os.environ.get('GTKDM_CHART_PATH', '').split(':')

    def find_chart(self, path, root_path=None):
        """
        Search for the chart file and return the full path
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

    def show_chart(self, path=None):
        """
        Show a display file

        :param path: absolute or relative path to display file
        """

        specs = {}
        if path:
            full_path = self.find_chart(path)
            if not full_path:
                logger.error('Chart File {} not found'.format(path))
            specs = stp_to_spec(full_path)
            logger.info(f"Loading: {full_path}...")
        else:
            full_path = None

        window = ChartWindow()
        window.setup_chart(specs)
        window.connect('destroy', lambda x: Gtk.main_quit())
        window.show_all()


Manager = ChartManager()
