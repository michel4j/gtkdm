import hashlib
import json
import os
import time
from threading import Thread
import re
import shlex
import subprocess
import textwrap
import zipfile
from datetime import datetime
from math import atan2, pi, cos, sin, ceil
from pathlib import Path

import cairo
import gi
import numpy
import yaml

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', "1.0")
from gi.repository import Gtk, GObject, Gdk, Gio, GdkPixbuf, GLib, PangoCairo


from matplotlib.backends.backend_gtk3agg import FigureCanvasGTK3Agg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.style import context as style_context
from matplotlib import pyplot as plt

from epics.ca import ChannelAccessGetFailure
import gepics
import xml.etree.ElementTree as ET

from . import utils, colors, version, PLUGIN_DIR
from .utils import logger, StripData

Y_AXIS_OFFSET = 60
AXIS_SPACE = 0.92


class ChartWindow(Gtk.Window):
    __gtype_name__ = 'ChartWindow'

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

        self.figure = Figure(dpi=72, layout="tight")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_size_request(800, 500)
        self.legend = Gtk.ListBox()
        self.legend.set_selection_mode(Gtk.SelectionMode.NONE)
        self.legend.set_size_request(250, -1)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.pack_start(self.canvas, True, True, 0)
        hbox.pack_end(self.legend, False, False, 0)
        self.add(hbox)

        # chart variables
        self.info = {
            'specs': {},
            'data': [],
            'plots': [],
            'axes': [],
            'selectors': [],
            'config': [],
        }

    def setup_chart(self, specs):
        self.info['specs'] = specs
        host = self.figure.add_subplot()
        host.set_xlabel(datetime.now().strftime("%b %d\n%H:%M:%S"), loc='right')

        data_names = []
        selectors = [-1]
        artists = []
        for i, item in enumerate(specs.get('items', [])):
            data_names.append(item['pv'])
            if len(self.info['axes']) > item['axis']:
                axis = self.info['axes'][item['axis']]
            else:
                axis = host.twinx()
                axis.yaxis.tick_left()
                axis.yaxis.set_label_position('left')
                axis.yaxis.set_visible(False)
                axis.set_frame_on(False)
                self.info['axes'].append(axis)

            ln, = axis.plot([], [], '-', ms=5)
            artists.append(ln)
            selectors.append(item['axis'])
        self.info['plots'] = artists
        self.info['selectors'] = numpy.array(selectors)
        data = StripData(*data_names, period=60, sample_freq=10, refresh_freq=10)
        data.connect('changed', self.on_data_changed)
        self.info['data'] = data

    def on_data_changed(self, plot):
        artists = self.info['plots']
        selectors = self.info['selectors']
        for j in range(plot.count-1):
            ln = artists[j]
            ln.set_data(plot.data[:, 0], plot.data[:, j+1])

        for k in numpy.unique(selectors[1:]):
            axis = self.info['axes'][k]
            sel = (selectors == k)
            if not numpy.isnan(plot.data[:, sel]).all():
                vy_min, vy_max = numpy.nanmin(plot.data[:, sel]), numpy.nanmax(plot.data[:, sel])
                dev = numpy.nanstd(plot.data[:, sel])
                if vy_min != vy_max or dev > 0:
                    axis.set_ylim(vy_min-2*dev, vy_max+2*dev)

        self.info['axes'][0].set_xlabel(datetime.now().strftime("%b %d\n%H:%M:%S"), loc='right')
        self.canvas.draw_idle()

    def on_edit(self, btn):
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
        if path:
            full_path = self.find_chart(path)
            if not full_path:
                logger.error('Chart File {} not found'.format(path))

            logger.info(f"Loading: {full_path}...")
        else:
            full_path = None

        window = ChartWindow()
        window.setup_chart(full_path)
        window.connect('destroy', lambda x: Gtk.main_quit())
        window.show_all()


Manager = ChartManager()
