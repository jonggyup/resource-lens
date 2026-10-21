"""Shared text presentation; no telemetry, actions or terminal input handling."""
import curses
import textwrap


def wrap_lines(lines, width):
    """Preserve paragraphs and indentation when explanations wrap."""
    width = max(10, width)
    result = []
    for raw in lines:
        line = str(raw)
        indent = len(line) - len(line.lstrip())
        continuation = ' ' * min(indent, width // 3)
        result.extend(textwrap.wrap(line, width, subsequent_indent=continuation,
                                    replace_whitespace=False) or [''])
    return result


def draw_text(screen, y, x, text, attr=0, limit=None, accent=0):
    """Emphasize headings, field labels and actions without changing row geometry."""
    from .ui import put
    stripped = text.strip()
    heading = (stripped.startswith(('-- ', 'HELP /', 'INVESTIGATE /')) or
               (stripped.endswith(':') and len(stripped) < 60) or
               (len(stripped) > 3 and stripped.isupper()))
    action = stripped.startswith(('[ Apply ]', '[ Cancel ]', '[ Enable controls ]', '[ Disable controls ]'))
    put(screen, y, x, text, attr | ((curses.A_BOLD | accent) if heading or action else 0), limit)
    # Field labels remain distinct from values, including in monochrome terminals.
    label, separator, value = text.partition(': ')
    if not (attr & curses.A_BOLD) and separator and value and 0 < len(label.strip()) <= 28 and not stripped.startswith(('/', '|')):
        put(screen, y, x, label + ':', attr | curses.A_BOLD | accent,
            min(len(label) + 1, limit) if limit is not None else len(label) + 1)
