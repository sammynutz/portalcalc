"""PortalCalc desktop launcher: `python main.py` (also the PyInstaller entry script)."""

if __name__ == "__main__":
    # The frame + bracing optimiser runs 3D checks in worker processes
    # (portalcalc.bracing.parallel). In the frozen app a worker re-launches this
    # EXE; freeze_support() makes it run the worker instead of the application.
    import multiprocessing

    multiprocessing.freeze_support()

    from portalcalc.desktop.app import main

    main()
