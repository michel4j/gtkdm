import contextlib
import os
import re
import math
import logging
import time
from threading import Thread

import gepics
import numpy
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GObject, GLib

import colors


def parse_macro_spec(macro_spec):
    """
    Parse a macro specification and return a dictionary of key-value pairs
    :param macro_spec: Macro string in the format "key=value,key=value,..."
    """
    if macro_spec:
        return dict(re.findall("(\w+)=([^,]*)", macro_spec))
    else:
        return {}


def update_properties(tree, macros):
    """
    Replace macro parameters in properties of xml widget element tree
    :param tree: xml widget element tree
    :param macros: Dictionary containing macro information
    """
    for prop in tree.findall(".//object/property"):
        if not prop.text: continue
        prop.text = prop.text.format(**macros)


def compress_macro(macros):
    """
    Convert a macros dictionary into a macro specification
    :param macros: dictionary
    :return: Macro specification in the format "key=value,key=value,..."
    """

    return ",".join(["{}={}".format(key, value) for key, value in sorted(macros.items())])


@contextlib.contextmanager
def working_dir(newdir):
    """
    Context Manager for Temporarily switch current working directory
    :param newdir:  New Working directory
    """
    curdir = os.getcwd()
    try:
        os.chdir(newdir)
        yield
    finally:
        os.chdir(curdir)


SUPERSCRIPTS_TRANS = str.maketrans('0123456789+-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻')


def sci_fmt_unicode(number, digits=3, sign=False):
    exp = 0 if number == 0 else math.floor(math.log10(abs(number)))
    value = number*(10**-exp)
    exp_text = f'{exp}'.translate(SUPERSCRIPTS_TRANS)
    val_fmt = f'{{:0.{digits}f}}' if sign else f'{{:0.{digits}f}}'
    val_text = val_fmt.format(value)
    return f"{val_text}" if exp == 0 else f"{val_text}×10{exp_text}"


def sci_fmt(number, digits=3, sign=False):
    val_fmt = f'{{:0.{digits}E}}'
    return val_fmt.format(number)


def fix_fmt(number, digits=3, sign=False):
    return f'{{:0.{digits}G}}'.format(number)


class NullHandler(logging.Handler):
    """
    A do-nothing log handler.
    """

    def emit(self, record):
        pass


class ColoredConsoleHandler(logging.StreamHandler):
    """
    A colored console log handler
    """
    def format(self, record):
        msg = super(ColoredConsoleHandler, self).format(record)
        if record.levelno == logging.WARNING:
            msg = colors.color(msg, fg=202)
        elif record.levelno > logging.WARNING:
            msg = colors.color(msg, fg=196)
        elif record.levelno == logging.DEBUG:
            msg = colors.color(msg, fg=57)
        return msg


def create_logger(name='gtkdm'):
    """
    Create a logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(NullHandler())
    return logger


def log_to_console(level=logging.DEBUG):
    """
    Add a log handler which logs to the console.
    """

    console = ColoredConsoleHandler()
    console.setLevel(level)
    if level == logging.DEBUG:
        formatter = logging.Formatter('%(asctime)s [%(name)s] %(message)s', '%b/%d %H:%M:%S')
    else:
        formatter = logging.Formatter('%(asctime)s %(message)s', '%b/%d %H:%M:%S')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)


logger = create_logger()


class PlotData(GObject.GObject):
    __gsignals__ = {
        'changed': (GObject.SIGNAL_RUN_FIRST, None, [])
    }

    def __init__(self, count, size:int = 1, sample_freq: float = 1, refresh_freq: float = 1):
        super().__init__()
        self.count = count
        self.size = size
        self.data = None
        self.arrays = False
        self.sample_freq = sample_freq
        self.refresh_freq = refresh_freq
        self.refresh_pending = False
        self.alive = True

        Thread(target=self.monitor, daemon=True).start()

    def monitor(self):
        gepics.threads_init()
        last_sample = time.time()
        last_refresh = time.time()
        if self.sample_freq:
            sample_every = 1/self.sample_freq
            refresh_every = 1/self.refresh_freq
            sleep_for = min(sample_every, refresh_every)/2
            src_id = GLib.timeout_add(int(sleep_for*1000), self.refresh)
            while self.alive:
                if time.time() - last_sample > sample_every:
                    self.sample_data()
                    last_sample = time.time()
                if time.time() - last_refresh > refresh_every:
                    self.refresh_pending = True
                    last_refresh = time.time()
                time.sleep(sleep_for)
            GLib.source_remove(src_id)

    def destroy(self):
        self.alive = False

    def sample_data(self):
        raise NotImplementedError()

    def refresh(self):
        if self.refresh_pending:
            self.refresh_pending = False
            if self.data is not None:
                self.emit("changed")
        return True


class XYData(PlotData):
    def __init__(self, *names, buffer: int = 1, sample_freq: float = 1, refresh_freq: float = 1):
        count = len(names)
        super().__init__(count, size=buffer, sample_freq=sample_freq, refresh_freq=refresh_freq)
        self.names = names
        self.updating = False
        self.offset = 0
        if names[0] == '#':
            self.offset = 1

        self.pvs = [
            gepics.PV(name) for name in names[self.offset:]
        ]
        for i, pv in enumerate(self.pvs):
            pv.connect('active', self.activate)
            if sample_freq == 0:
                pv.connect('changed', self.update, i)

    def update(self, pv, data, index):
        if not self.updating:
            self.updating = True
            self.update_column(index, data)
            self.refresh()
            self.updating = False

    def activate(self, obj, state):
        if state and self.data is None:
            if obj.count == 1:
                self.arrays = False
            else:
                self.size = obj.count
                self.arrays = True
            self.data = numpy.empty((self.size, self.count))
            self.data.fill(numpy.nan)
            if self.names[0] == '#':
                self.data[:, 0] = numpy.arange(self.size)

    def update_column(self, index, vals):
        if isinstance(vals, (float, int)):
            vals = [vals]
        n = min(len(vals), self.size)
        self.data[:n, index + self.offset] = vals[:n]

    def sample_data(self):
        if self.data is not None:
            if self.arrays:
                for i, pv in enumerate(self.pvs):
                    if pv.is_active():
                        vals = pv.get()
                        if pv.count == 1:
                            vals = [vals]
                    else:
                        vals = [numpy.nan] * self.size
                    n = min(len(vals), self.size)
                    self.update_column(i, vals)
            else:
                if self.size > 1:
                    self.data[:-1, self.offset:] = self.data[1:, self.offset:]
                for i, pv in enumerate(self.pvs):
                    self.data[-1, i+self.offset] = numpy.nan if not pv.is_active() else pv.get()


class StripData(PlotData):
    def __init__(self, *names, period=60.0, sample_freq=1.0, refresh_freq=1.0):
        sample_freq = max(0.1, sample_freq)     # Strip charts can't use auto-update.
        refresh_freq = max(0.1, refresh_freq)
        size = int(period * sample_freq)
        count = len(names) + 1
        super().__init__(count, size=size, sample_freq=sample_freq, refresh_freq=refresh_freq)
        self.period = period
        self.names = ('time',) + names
        self.pvs = [
            gepics.PV(name) for name in names
        ]
        self.now_time = datetime.now()
        for pv in self.pvs:
            pv.connect('active', self.activate)

    def activate(self, obj, state):
        if state and self.data is None:
            self.data = numpy.empty((self.size, self.count), dtype=numpy.float32)
            self.data.fill(numpy.nan)
            self.data[:, 0] = numpy.linspace(-self.period, 0, self.size)

    def sample_data(self):
        self.now_time = datetime.now()
        if self.data is not None:
            self.data[:-1, 1:] = self.data[1:, 1:]  # preserve time axis
            for i, pv in enumerate(self.pvs):
                value = numpy.nan
                if pv.is_active():
                    value = pv.get()
                    if isinstance(value, numpy.ndarray):
                        value = value[-1]
                self.data[-1, i+1] = value

