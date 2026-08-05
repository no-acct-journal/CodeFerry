from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    from textual.drivers.windows_driver import WindowsDriver as _BaseDriver
else:
    from textual.drivers.linux_driver import LinuxDriver as _BaseDriver


class NoAltScreenDriver(_BaseDriver):
    """Driver that skips the alternate screen and keeps output in terminal scrollback.

    This matches Claude Code rendering behavior and automatically chooses LinuxDriver
    or WindowsDriver as the base class by platform.

    Mechanism: remove alt-screen switch codes and print enough blank lines when
    entering application mode to push existing terminal content into scrollback,
    letting Textual render on a "new page".
    """

    def start_application_mode(self):
        try:
            rows = os.get_terminal_size().lines
        except OSError:
            rows = 24
        # Push existing content into scrollback with newlines before Textual takes over the terminal.
        sys.stdout.write("\n" * rows)
        sys.stdout.flush()
        super().start_application_mode()

    def write(self, data: str) -> None:
        if "\x1b[?1049h" in data:
            data = data.replace("\x1b[?1049h", "")
        if "\x1b[?1049l" in data:
            data = data.replace("\x1b[?1049l", "")
        if data:
            super().write(data)
