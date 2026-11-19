import sys

if sys.version_info < (3, 11):
    sys.exit(f"The clipper needs Python 3.11 or newer; this is Python {sys.version_info.major}.{sys.version_info.minor}.")

if len(sys.argv) > 1 and sys.argv[1] == "setup":
    # Runs before the other modules are imported: they need packages that setup may still have to install.
    from .bootstrap import main as setup

    sys.exit(setup(sys.argv[2:]))

from .cli import main

sys.exit(main())
