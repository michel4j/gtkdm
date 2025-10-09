import contextlib
import logging
import re
import time
from datetime import datetime
from enum import Enum
from threading import Thread

import os
import gi
import math
import numpy
import epics
from numpy.lib import recfunctions
from epics.ca import current_context, attach_context, ChannelAccessGetFailure

gi.require_version('Gtk', '3.0')
from gi.repository import GObject, GLib, GObject
from . import colors


CA_CONTEXT = current_context()
REUSE = False
PACKAGE_DIR = os.path.dirname(__file__)


def get_version(prefix='v', package=PACKAGE_DIR):
    from subprocess import CalledProcessError, check_output

    # Return the version if it has been injected into the file by git-archive
    tag_re = re.compile(rf'\btag: {prefix}([0-9][^,]*)\b')
    version = tag_re.search('$Format:%D$')
    name = __name__.split('.')[0]

    if version:
        return version.group(1)

    package_dir = package
    if os.path.isdir(os.path.join(package_dir, '.git')):
        # Get the version using "git describe".
        version_cmd = 'git describe --tags --abbrev=0'
        release_cmd = 'git rev-list HEAD ^$(git describe --abbrev=0) | wc -l'
        try:
            version = check_output(version_cmd, shell=True).decode().strip()
            release = check_output(release_cmd, shell=True).decode().strip()
            return f'{version}.{release}'.strip(prefix)
        except CalledProcessError:
            version = '0.0'
            release = 'dev'
            return f'{version}.{release}'.strip(prefix)
    else:
        try:
            from importlib import metadata
        except ImportError:
            # Running on pre-3.8 Python; use importlib-metadata package
            import importlib_metadata as metadata

        version = metadata.version(name)

    return version


class Alarm(Enum):
    NORMAL, MINOR, MAJOR, INVALID = range(4)


class BasePV(GObject.GObject):
    """
    Process Variable Base Class
    """
    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'active': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'alarm': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'time': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, name, monitor=True):
        """

        :param name: Process variable name
        :param monitor: Whether to enable monitoring
        """
        GObject.GObject.__init__(self)
        self._state = {}

    def set_state(self, **kwargs):
        """
        Set and emit signals for the specified states. Re-emits signals even if values are the same
        :param kwargs: keywords correspond to signal names, values are signal values to emit
        """

        for state, value in kwargs.items():
            self._state[state] = value
            GLib.idle_add(self.emit, state, value)

    def get_state(self, item):
        """
        Get the current state value for a given signal name
        :param item: signal name
        :return: value emitted with the last signal event
        """
        return self._state.get(item)

    def get_states(self):
        """
        Get the full state dictionary for all signals
        """
        return self._state

    def is_active(self):
        """
        Returns True if the process variable is active and connected.
        """
        return self._state.get('active', False)

    def is_connected(self):
        """An alias for is_active()"""
        return self.is_active()


PV_REPR = (
    "<PV: {name}\n"
    "    Data type:  {type}\n"
    "    Elements:   {count}\n"
    "    Server:     {server}\n"
    "    Access:     {access}\n"
    "    Alarm:      {alarm}\n"
    "    Time-stamp: {time}\n"
    "    Connected:  {connected}\n"
    ">"
)


class PV(BasePV):
    """A Process Variable

    A PV encapsulates an EPICS Process Variable with additional GObject features

    The primary interface methods for a pv are to get() and put() its
    value:

      >>> p = PV(pv_name)    # create a pv object given a pv name
      >>> p.get()            # get pv value
      >>> p.put(value)         # set pv to specified value.

    Additional important attributes include:

      >>> p.name             # name of pv
      >>> p.count            # number of elements in array pvs
      >>> p.type             # EPICS data type

    Note that GObject, derived features are available only when a GObject
    or compatible main-loop is running.

    """

    __REGISTRY = {}  # registry for re-using PVs

    def __init__(self, name, monitor=True):
        """
        Process Variable Object
        :param name: PV name
        :param monitor: boolean, whether to enable monitoring of changes and emitting of change signals
        """
        super().__init__(name, monitor=monitor)
        self.name = name
        self.monitor = monitor
        self.string = False

        # re-use existing instances
        if REUSE and name in self.__REGISTRY:
            self.raw = self.__REGISTRY[name]
        else:
            self.raw = epics.PV(name, auto_monitor=True)
            self.__REGISTRY[name] = self.raw
        self.raw.add_callback(self.on_change)
        self.raw.connection_callbacks.append(self.on_connect)

    def __del__(self):
        """Clean up the PV instance"""
        if self.raw:
            self.raw.remove_callback(self.on_change)
            self.raw.connection_callbacks.remove(self.on_connect)
            self.raw = None
        super(PV, self).__del__()

    def on_connect(self, **kwargs):
        self.set_state(active=kwargs['conn'])

    def on_change(self, **kwargs):
        self.string = (
            kwargs.get('type') in ['time_string'] or
            (kwargs.get('type') in ['time_char'] and kwargs.get('count', 1) > 1)
        )
        value = kwargs['char_value'] if self.string else kwargs['value']
        alarm = Alarm(kwargs.get('severity', 0))
        self.set_state(changed=value, time=datetime.fromtimestamp(kwargs['timestamp']), alarm=alarm)

    def get(self, *args, **kwargs):
        kwargs['as_string'] = kwargs.get('as_string', False) | self.string
        return self.raw.get(*args, **kwargs)

    def put(self, *args, **kwargs):
        return self.raw.put(*args, **kwargs)

    def toggle(self, value1, value2):
        self.raw.put(value1, wait=True)
        return self.raw.put(value2)

    def __getattr__(self, item):
        try:
            return getattr(self.raw, item)
        except AttributeError:
            raise AttributeError('%r object has no attribute %r' % (self.__class__.__name__, item))
        except ChannelAccessGetFailure:
            return None

    def __repr__(self):
        return PV_REPR.format(
            name=self.raw.pvname, connected=self.is_active(), alarm=Alarm(self.raw.severity).name, time=self.raw.timestamp,
            access=self.raw.access, count=self.raw.count, type=self.raw.type, server=self.raw.host,
        )


def threads_init():
    if current_context() != CA_CONTEXT:
        attach_context(CA_CONTEXT)


class epics_context(contextlib.ContextDecorator):
    def __enter__(self):
        if current_context() != CA_CONTEXT:
            attach_context(CA_CONTEXT)
        return self

    def __exit__(self, *exc):
        return False



def parse_macro_spec(macro_spec):
    """
    Parse a macro specification and return a dictionary of key-value pairs
    :param macro_spec: Macro string in the format "key=value,key=value,..."
    """
    if macro_spec:
        return dict(re.findall(r"(\w+)=([^,]*)", macro_spec))
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
    value = number * (10 ** -exp)
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

    def __init__(self, count, size: int = 1, sample_freq: float = 1, refresh_freq: float = 1):
        super().__init__()
        self.count = count
        self.size = size
        self.sample_freq = 1
        self.refresh_freq = 1
        self.sample_every = 1.0
        self.refresh_every = 1.0
        self.sleep_for = 0.5
        self.data = None
        self.setup(count, size, sample_freq, refresh_freq)
        self.arrays = False
        self.alive = True

        Thread(target=self.monitor, daemon=True).start()

    def setup(self, count, size: int = 1, sample_freq: float = 1, refresh_freq: float = 1):
        self.count = count
        self.size = size
        self.sample_freq = sample_freq
        self.refresh_freq = refresh_freq
        if sample_freq > 0 and refresh_freq > 0:
            self.sample_every = 1 / self.sample_freq
            self.refresh_every = 1 / self.refresh_freq
            self.sleep_for = min(self.sample_every, self.refresh_every) / 2
        self.data = numpy.empty((self.size, self.count))
        self.data.fill(numpy.nan)

    def monitor(self):
        threads_init()
        last_sample = time.time()
        last_refresh = time.time()
        if self.sample_freq:
            while self.alive:
                if time.time() - last_sample > self.sample_every:
                    self.sample_data()
                    last_sample = time.time()
                if time.time() - last_refresh > self.refresh_every:
                    GLib.idle_add(self.refresh)
                time.sleep(self.sleep_for)

    def x_data(self):
        if self.data is not None:
            sel = ~numpy.isnan(self.data[:, 0])
            if sel.sum():
                return self.data[:, 0]

    def y_data(self):
        if self.data is not None:
            sel = ~numpy.isnan(self.data[:, 0])
            if sel.sum():
                return self.data[:, 1:]

    def destroy(self):
        self.alive = False

    def get_structured(self):
        raise NotImplementedError()

    def refresh(self):
        if self.data is not None:
            self.emit("changed")
        return False


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
            PV(name) for name in names[self.offset:]
        ]
        for i, pv in enumerate(self.pvs):
            pv.connect('active', self.activate)
            if numpy.isclose(sample_freq, 0.0):
                pv.connect('changed', self.update, i)

    def get_structured(self):
        dtype = [(name, float) for name in self.names]
        return recfunctions.unstructured_to_structured(self.data, numpy.dtype(dtype))

    def update(self, pv, data, index):
        #if not self.updating:
        self.updating = True
        self.update_column(index, data)
        self.refresh()
        self.updating = False

    def activate(self, obj, state):
        if all(pv.is_active() for pv in self.pvs):
            size = max(pv.count for pv in self.pvs)
            if size == 1:
                self.arrays = False
            else:
                self.size = size
                self.arrays = True
                self.setup(len(self.names), size, self.sample_freq, self.refresh_freq)
            if self.names[0] == '#':
                self.data[:, 0] = numpy.arange(self.size)
            # for i, pv in enumerate(self.pvs):
            #     self.update(pv, pv.get(), i)

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
                    self.data[-1, i + self.offset] = numpy.nan if not pv.is_active() else pv.get()


class StripData(PlotData):
    def __init__(self, *names, period=60.0, samples=7200, sample_freq=1.0, refresh_freq=1.0, data=None):
        sample_freq = max(0.1, sample_freq)  # Strip charts can't use auto-update.
        refresh_freq = max(0.1, refresh_freq)
        size = samples
        count = len(names) + 1
        super().__init__(count, size=size, sample_freq=sample_freq, refresh_freq=refresh_freq)
        self.period = period
        self.pvs = [
            PV(name) for name in names
        ]
        self.names = ('time',) + names
        if data is not None:
            keys = data.dtype.names
            n = min(data.shape[0], self.data.shape[0])
            for i, name in enumerate(self.names):
                if name in keys:
                    self.data[:n, i] = data[name][:n]

    def get_structured(self):
        dtype = [(name, float) for name in self.names]
        return recfunctions.unstructured_to_structured(self.data, numpy.dtype(dtype))

    def x_data(self):
        if self.data is not None:
            sel = ~numpy.isnan(self.data[:, 0])
            if sel.sum():
                return self.data[:, 0] - self.data[0, 0]

    def end_time(self):
        t = datetime.now()
        if self.data is not None:
            sel = ~numpy.isnan(self.data[:, 0])
            if sel.sum():
                t = datetime.fromtimestamp(float(self.data[0, 0]))
        return t

    def sample_data(self):
        if self.data is not None:
            self.data[1:, :] = self.data[:-1, :]
            for i, pv in enumerate(self.pvs):
                if pv.is_active():
                    value = pv.get()
                    if isinstance(value, numpy.ndarray):
                        value = value[-1]
                    self.data[0, i + 1] = value
            self.data[0, 0] = time.time()



