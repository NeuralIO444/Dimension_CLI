import sys
import os

# Ensure the app paths are correctly resolved when frozen by PyInstaller
if getattr(sys, 'frozen', False):
    application_path = sys._MEIPASS
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, application_path)

import survey
import orchestrator

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "survey":
        sys.argv.pop(1)
        survey.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "dump-data":
        sys.argv.pop(1)
        import data_dumper
        data_dumper.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "garden":
        sys.argv.pop(1)
        import comment_garden_cli
        comment_garden_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "prefs":
        sys.argv.pop(1)
        import prefs_cli
        prefs_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "target":
        sys.argv.pop(1)
        import target_cli
        target_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "profile":
        sys.argv.pop(1)
        import profile_cli
        profile_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "duplication":
        sys.argv.pop(1)
        import duplication_cli
        duplication_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "units":
        sys.argv.pop(1)
        import units_cli
        units_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "unit-override":
        sys.argv.pop(1)
        import unit_override_cli
        unit_override_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "lut":
        sys.argv.pop(1)
        import lut_cli
        lut_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "mask":
        sys.argv.pop(1)
        import mask_cli
        mask_cli.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "serve":
        sys.argv.pop(1)
        import dimension_server
        dimension_server.main()
    elif len(sys.argv) > 1 and sys.argv[1] == "debug-bundle":
        sys.argv.pop(1)
        import tools.debug_bundle as debug_bundle
        sys.exit(debug_bundle.main())
    else:
        if len(sys.argv) > 1 and sys.argv[1] == "execute":
            sys.argv.pop(1)

        orchestrator.main()

if __name__ == "__main__":
    main()
