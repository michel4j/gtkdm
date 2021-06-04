import contextlib
import os
import re
import math
import logging
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
    return f'{{:0.{digits}f}}'.format(number)


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