import locale
import os
import subprocess
import sys

if sys.version_info < (3, 11):
    sys.exit(f"The clipper needs Python 3.11 or newer; this is Python {sys.version_info.major}.{sys.version_info.minor}.")

if not sys.flags.utf8_mode and locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
    # Windows (and odd locales) default to a legacy text encoding, which breaks on the symbols
    # and emoji in titles and captions. Start again in UTF-8 mode; child processes inherit it.
    env = {**os.environ, "PYTHONUTF8": "1"}
    sys.exit(subprocess.call([sys.executable, "-m", "clipper", *sys.argv[1:]], env=env))

if len(sys.argv) > 1 and sys.argv[1] == "setup":
    # Runs before the other modules are imported: they need packages that setup may still have to install.
    from .bootstrap import main as setup

    sys.exit(setup(sys.argv[2:]))

from .cli import main

sys.exit(main())
