
# Lower case letters represet lighter version of same color

COLOR_NAMES = {
    'R': 'red',
    'G': 'green',
    'Y': 'yellow',
    'O': 'orange',
    'P': 'purple',
    'B': 'blue',
    'K': 'black',
    'A': 'grey',
    'C': 'cyan',
    'W': 'white',
    'M': 'magenta',
}

DEFAULT = {
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
    'M': '#88419d',

    'r': '#ef2929',
    'g': '#73d216',
    'y': '#fce94f',
    'o': '#fcaf3e',
    'p': '#ad7fa8',
    'b': '#729fcf',
    'k': '#000000',
    'a': '#888a85',
    'c': '#17becf',
    'w': '#ffffff',
    'm': '#88419d',

}

TANGO = {
    'R': '#EF2929',
    'G': '#8AE234',
    'Y': '#FCE94F',
    'O': '#C4A000',
    'P': '#AD7FA8',
    'B': '#729FCF',
    'K': '#2E3436',
    'A': '#555753',
    'C': '#34E2E2',
    'W': '#EEEEEC',
    'M': '#75507B',

    'r': '#ef2929',
    'g': '#73d216',
    'y': '#fce94f',
    'o': '#fcaf3e',
    'p': '#ad7fa8',
    'b': '#729fcf',
    'k': '#000000',
    'a': '#888a85',
    'c': '#17becf',
    'w': '#ffffff',
    'm': '#88419d',
}

SOLAR = {
    'R': '#DC322F',
    'G': '#859900',
    'Y': '#B58900',
    'O': '#CB4B16',
    'P': '#D33682',
    'B': '#268BD2',
    'K': '#000000',
    'A': '#586E75',
    'C': '#2AA198',
    'W': '#FFFFFF',
    'M': '#75507B',

    'r': '#ef2929',
    'g': '#73d216',
    'y': '#fce94f',
    'o': '#fcaf3e',
    'p': '#ad7fa8',
    'b': '#729fcf',
    'k': '#000000',
    'a': '#888a85',
    'c': '#17becf',
    'w': '#ffffff',
    'm': '#88419d',
}


def is_string(obj):
    """
    Is the given object a string?
    """
    return isinstance(obj, str)

def _join(*values):
    """
    Join a series of values with semicolons. The values
    are either integers or strings, so stringify each for
    good measure. Worth breaking out as its own function
    because semicolon-joined lists are core to ANSI coding.
    """
    return ';'.join(str(v) for v in values)


def color_code(spec, base):
    """
    Encode a color.
    :param str|int spec: Color specification
    :param int base: Either 30 or 40, signifying the base value
        for color encoding (foreground and background respectively).
        Low values are added directly to the base. Higher values use `
        base + 8` (i.e. 38 or 48) then extended codes.
    :returns: ANSI color encoding.
    :rtype: str
    """

    if isinstance(spec, int) and 0 <= spec <= 255:
        return ';'.join(str(v) for v in [base + 8, 5, spec])
    else:
        return ';'.join(str(v) for v in [base + 9])


def color(s, fg=None, bg=None):
    """
    Add ANSI colors and styles to a string.
    :param str s: String to format.
    :param str|int|tuple fg: Foreground color specification.
    :param str|int|tuple bg: Background color specification.
    :param str: Style names, separated by '+'
    :returns: Formatted string.
    :rtype: str (or unicode in Python 2, if s is unicode)
    """
    codes = []

    if fg:
        codes.append(color_code(fg, 30))
    if bg:
        codes.append(color_code(bg, 40))

    if codes:
        template = '\x1b[{0}m{1}\x1b[0m'
        return template.format(';'.join(codes), s)
    else:
        return s
