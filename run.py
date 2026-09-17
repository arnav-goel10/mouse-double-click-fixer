import sys

from app.main import main


if __name__ == "__main__":
    # The startup shortcut can request a background launch; the tray remains available.
    if "--minimized" in sys.argv:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        from app.main import DoubleClickApp

        DoubleClickApp(root)
        root.mainloop()
    else:
        main()
