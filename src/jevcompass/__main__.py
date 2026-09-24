import sys

if len(sys.argv) > 1 and sys.argv[1] == "hook":
    from .advisor import hook_main

    raise SystemExit(hook_main())

from .cli import main

raise SystemExit(main())
