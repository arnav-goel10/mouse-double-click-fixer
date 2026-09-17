"""Tkinter user interface for DoubleClick Fixer."""

from __future__ import annotations

import tkinter as tk
import queue
from time import monotonic
from tkinter import messagebox, ttk
from typing import Callable, Optional

from .core import DoubleClickEngine, ThresholdCalibrator
from .platform import GlobalMouseMonitor
from . import settings, startup
from .tray import TrayController


class DoubleClickApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DoubleClick Fixer")
        self.root.geometry("620x620")
        self.root.minsize(520, 440)
        saved = settings.load()
        self.diagnostic_engine = DoubleClickEngine(threshold_ms=500)
        self.monitor: Optional[GlobalMouseMonitor] = None
        self.tray: Optional[TrayController] = None
        self._ui_actions: queue.Queue[Callable[[], None]] = queue.Queue()
        self._global_events: queue.Queue[tuple[bool, Optional[float]]] = queue.Queue()
        self._monitor_errors: queue.Queue[str] = queue.Queue()
        self._event_poll_job: Optional[str] = None
        self.calibrator = ThresholdCalibrator()
        self.calibration_active = not bool(saved["calibration_complete"])
        self.calibration_phase = "single"
        self.calibration_single_clicks = 0
        self.calibration_pair_pending = False
        self._last_calibration_action_at = 0.0
        self._calibration_min_single_gap_ms = 350
        self.calibration_job: Optional[str] = None
        self.click_count = 0
        self.double_click_count = 0
        self.global_event_count = 0
        self.last_interval = "No click measured yet"
        self.threshold = tk.IntVar(value=int(saved["bounce_threshold_ms"]))
        self.fix_enabled = tk.BooleanVar(value=bool(saved["fix_enabled"]) and not self.calibration_active)
        self.start_at_login = tk.BooleanVar(value=bool(saved["start_at_login"]))
        self._build_ui()
        self._build_tray()
        self._event_poll_job = self.root.after(100, self._drain_background_events)
        if self.calibration_active:
            self._prepare_calibration()
        elif self.fix_enabled.get():
            self._start_fix()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        self.root.geometry("700x650")
        self.root.minsize(600, 560)
        bg = "#f3f7f6"
        ink = "#153f46"
        muted = "#607679"
        accent = "#0f9f82"
        style = ttk.Style(self.root)
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(16, 9))
        style.configure("Secondary.TButton", font=("Segoe UI", 9), padding=(10, 6))
        self.root.configure(background=bg)
        outer = tk.Frame(self.root, bg=bg, padx=30, pady=26)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=bg)
        header.pack(fill="x", pady=(0, 20))
        tk.Label(header, text="DoubleClick Fixer", bg=bg, fg=ink, font=("Segoe UI", 25, "bold")).pack(side="left")
        self.status_label = tk.Label(header, text="FIX OFF", bg="#f8e5d7", fg="#8b5e3c", font=("Segoe UI", 9, "bold"), padx=12, pady=6)
        self.status_label.pack(side="right", pady=4)
        setup = tk.Frame(outer, bg="#ffffff", padx=18, pady=14, highlightbackground="#dce8e5", highlightthickness=1)
        setup.pack(fill="x", pady=(0, 14))
        setup_head = tk.Frame(setup, bg="#ffffff")
        setup_head.pack(fill="x")
        tk.Label(setup_head, text="AUTOMATIC SETUP", bg="#ffffff", fg=accent, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.calibration_button = ttk.Button(setup_head, text="Next: double-clicks", command=self.calibration_action, style="Secondary.TButton")
        self.calibration_button.pack(side="right")
        self.calibration_status = tk.Label(setup, text="1/2 Single clicks: click once, wait, repeat.", bg="#ffffff", fg=ink, anchor="w", justify="left", wraplength=520, font=("Segoe UI", 10))
        self.calibration_status.pack(fill="x", pady=(8, 0))

        self.test_area = tk.Canvas(outer, height=155, background="#dcefeb", highlightthickness=0)
        self.test_area.pack(fill="x")
        self.test_area.bind("<Button-1>", self.handle_test_click)
        self.test_title_id = self.test_area.create_text(350, 58, text="CLICK HERE TO TEST", fill=ink, font=("Segoe UI", 17, "bold"))
        self.test_hint = self.test_area.create_text(350, 96, text="Click naturally to test", fill="#467277", font=("Segoe UI", 10))
        self.test_area.bind("<Configure>", self._resize_test_area)

        stats = tk.Frame(outer, bg=bg)
        stats.pack(fill="x", pady=14)
        for column in range(3):
            stats.grid_columnconfigure(column, weight=1)
        click_card = tk.Frame(stats, bg="#ffffff", padx=14, pady=10, highlightbackground="#dce8e5", highlightthickness=1)
        click_card.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        double_card = tk.Frame(stats, bg="#ffffff", padx=14, pady=10, highlightbackground="#dce8e5", highlightthickness=1)
        double_card.grid(row=0, column=1, sticky="ew", padx=6)
        interval_card = tk.Frame(stats, bg="#ffffff", padx=14, pady=10, highlightbackground="#dce8e5", highlightthickness=1)
        interval_card.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        tk.Label(click_card, text="CLICKS", bg="#ffffff", fg=muted, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.click_label = tk.Label(click_card, text="0", bg="#ffffff", fg=ink, font=("Segoe UI", 18, "bold"))
        self.click_label.pack(anchor="w")
        tk.Label(double_card, text="DOUBLE-CLICKS", bg="#ffffff", fg=muted, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.double_label = tk.Label(double_card, text="0", bg="#ffffff", fg=ink, font=("Segoe UI", 18, "bold"))
        self.double_label.pack(anchor="w")
        tk.Label(interval_card, text="LAST INTERVAL", bg="#ffffff", fg=muted, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.interval_label = tk.Label(interval_card, text="--", bg="#ffffff", fg=ink, font=("Segoe UI", 12, "bold"))
        self.interval_label.pack(anchor="w", pady=(5, 3))

        controls = tk.Frame(outer, bg=bg)
        controls.pack(fill="x", pady=(2, 0))
        settings = tk.Frame(controls, bg=bg)
        settings.pack(side="left")
        tk.Label(settings, text="Bounce filter", bg=bg, fg=ink, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Spinbox(settings, from_=20, to=2000, increment=10, textvariable=self.threshold, width=6).pack(side="left", padx=(10, 5))
        ttk.Button(settings, text="Apply", command=self.apply_threshold, style="Secondary.TButton").pack(side="left")
        self.fix_button = ttk.Button(controls, text="Enable fix", command=self.toggle_fix, style="Primary.TButton")
        self.fix_button.pack(side="right")
        ttk.Checkbutton(outer, text="Start automatically at login", variable=self.start_at_login, command=self.toggle_startup).pack(anchor="w", pady=(14, 0))

        if self.calibration_active:
            self.fix_button.configure(state="disabled")
        else:
            self.calibration_button.configure(text="Recalibrate")
            self.calibration_status.configure(text=f"Calibrated: {self.threshold.get()} ms")

    def _resize_test_area(self, event: tk.Event) -> None:
        center = event.width // 2
        self.test_area.coords(self.test_title_id, center, 58)
        self.test_area.coords(self.test_hint, center, 96)

    def _prepare_calibration(self) -> None:
        self.fix_enabled.set(False)
        self._stop_fix()
        self.calibration_job = self.root.after(120000, self.finish_calibration)
        self._update_calibration_status()

    def apply_threshold(self) -> None:
        self.threshold.set(max(20, self.threshold.get()))
        settings.save({"bounce_threshold_ms": self.threshold.get()})
        if self.monitor is not None:
            self._stop_fix()
            self._start_fix()
        self.reset()

    def _build_tray(self) -> None:
        try:
            self.tray = TrayController(
                lambda: self._queue_ui(self.show),
                lambda: self._queue_ui(self.toggle_fix),
                lambda: self._queue_ui(self.remove_startup),
                lambda: self._queue_ui(self.quit),
            )
            self.tray.start()
        except RuntimeError:
            self.tray = None

    def hide_to_tray(self) -> None:
        self._save_settings()
        self.root.withdraw()

    def _save_settings(self) -> None:
        settings.save(
            {
                "bounce_threshold_ms": self.threshold.get(),
                "fix_enabled": self.fix_enabled.get(),
                "start_at_login": self.start_at_login.get(),
                "calibration_complete": not self.calibration_active,
            }
        )

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def close(self) -> None:
        self._save_settings()
        if self.tray is not None and self.tray.started:
            self.hide_to_tray()
            return
        self.quit()

    def quit(self) -> None:
        self._save_settings()
        if self.calibration_job is not None:
            self.root.after_cancel(self.calibration_job)
        if self._event_poll_job is not None:
            self.root.after_cancel(self._event_poll_job)
        self._stop_fix()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def _queue_ui(self, action: Callable[[], None]) -> None:
        self._ui_actions.put(action)

    def _drain_background_events(self) -> None:
        while True:
            try:
                action = self._ui_actions.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except tk.TclError:
                return
        while True:
            try:
                is_double_click, interval_ms = self._global_events.get_nowait()
            except queue.Empty:
                break
            self._show_global_result(is_double_click, interval_ms)
        while True:
            try:
                message = self._monitor_errors.get_nowait()
            except queue.Empty:
                break
            self._show_monitor_error(message)
        try:
            if self.root.winfo_exists():
                self._event_poll_job = self.root.after(100, self._drain_background_events)
        except tk.TclError:
            return

    def toggle_fix(self) -> None:
        if self.calibration_active:
            return
        self.fix_enabled.set(not self.fix_enabled.get())
        if self.fix_enabled.get():
            self._start_fix()
        else:
            self._stop_fix()
        settings.save({"fix_enabled": self.fix_enabled.get()})

    def _start_fix(self) -> None:
        try:
            self.monitor = GlobalMouseMonitor(self.handle_global_click, self.threshold.get(), self.handle_monitor_error)
            self.monitor.start()
            self.status_label.configure(text="FIX ON", bg="#d9f1e5", fg="#146b4f")
            self.fix_button.configure(text="Disable fix")
        except RuntimeError as error:
            self.fix_enabled.set(False)
            self.status_label.configure(text="FIX OFF", bg="#f8e5d7", fg="#8b5e3c")
            self.fix_button.configure(text="Enable fix")
            messagebox.showerror("Could not enable fix", str(error))

    def _stop_fix(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        self.status_label.configure(text="FIX OFF", bg="#f8e5d7", fg="#8b5e3c")
        self.fix_button.configure(text="Enable fix")

    def toggle_startup(self) -> None:
        try:
            startup.set_enabled(self.start_at_login.get())
            settings.save({"start_at_login": self.start_at_login.get()})
        except RuntimeError as error:
            self.start_at_login.set(False)
            messagebox.showerror("Startup unavailable", str(error))

    def remove_startup(self) -> None:
        startup.remove()
        self.start_at_login.set(False)
        settings.save({"start_at_login": False})

    def handle_test_click(self, _event: tk.Event) -> None:
        result = self.diagnostic_engine.process_click()
        if self.calibration_active:
            if self.calibration_phase == "single":
                if result.interval_ms is not None and result.interval_ms < self._calibration_min_single_gap_ms:
                    self.calibrator.add_single_click(result.interval_ms)
                    self.diagnostic_engine.reset()
                    self.calibration_status.configure(text="Possible duplicate ignored. Wait, then click once.")
                    return
                self.calibration_single_clicks += 1
                self.calibrator.record_isolated_single_click()
                self.diagnostic_engine.reset()
            elif self.calibration_pair_pending:
                if result.interval_ms is not None and result.interval_ms <= 2000:
                    self.calibrator.add_intentional_double(result.interval_ms)
                    self.calibration_pair_pending = False
                    self.diagnostic_engine.reset()
                else:
                    self.calibration_status.configure(text="Pair timed out. Start a new double-click pair.")
                    self.diagnostic_engine.reset()
                    return
            else:
                self.calibration_pair_pending = True
            self._update_calibration_status()
        self.click_count += 1
        if result.is_double_click:
            self.double_click_count += 1
            self.test_area.configure(background="#f9dfc7")
        else:
            self.test_area.configure(background="#e7f1f2")
        self.click_label.configure(text=str(self.click_count))
        self.double_label.configure(text=str(self.double_click_count))
        interval = "--" if result.interval_ms is None else f"{result.interval_ms:.0f} ms"
        self.interval_label.configure(text=interval)

    def _update_calibration_status(self) -> None:
        if self.calibration_phase == "single":
            self.calibration_status.configure(text=f"1/2 Isolated clicks: {self.calibrator.isolated_single_clicks}/10. Click once, wait, repeat.")
            self.calibration_button.configure(state="normal" if self.calibration_single_clicks >= 10 else "disabled")
        elif self.calibration_pair_pending:
            self.calibration_status.configure(text=f"2/2 Double clicks: pair {self.calibrator.double_click_count + 1}/10")
        else:
            self.calibration_status.configure(text=f"2/2 Double clicks: {self.calibrator.double_click_count}/10")
        if self.calibration_phase == "double":
            self.calibration_button.configure(state="normal" if self.calibrator.double_click_count >= 3 else "disabled")

    def finish_calibration(self) -> None:
        if self.calibration_job is not None:
            self.root.after_cancel(self.calibration_job)
            self.calibration_job = None
        self.calibration_active = False
        suggestion = self.calibrator.suggested_threshold()
        if suggestion is None:
            self.calibration_status.configure(text="Need 10 isolated clicks and 3 double-click pairs.")
            self.calibration_button.configure(text="Calibrate again", state="normal")
            return
        self.threshold.set(suggestion)
        self.apply_threshold()
        settings.save({"calibration_complete": True, "fix_enabled": False})
        self.fix_enabled.set(False)
        self.fix_button.configure(state="normal")
        self.calibration_status.configure(text=f"Calibrated: {self.threshold.get()} ms")
        self.calibration_button.configure(text="Recalibrate", state="normal")

    def calibration_action(self) -> None:
        now = monotonic()
        if now - self._last_calibration_action_at < 0.5:
            return
        self._last_calibration_action_at = now
        if self.calibration_active:
            if self.calibration_phase == "single":
                self.calibration_phase = "double"
                self.calibration_pair_pending = False
                self.diagnostic_engine.reset()
                self.calibration_button.configure(text="Apply calibration")
                self._update_calibration_status()
            else:
                self.finish_calibration()
        else:
            self.restart_calibration()

    def restart_calibration(self) -> None:
        self.calibrator = ThresholdCalibrator()
        self.calibration_active = True
        self.fix_enabled.set(False)
        self._stop_fix()
        settings.save({"calibration_complete": False, "fix_enabled": False})
        self.calibration_phase = "single"
        self.calibration_single_clicks = 0
        self.calibration_pair_pending = False
        self.calibration_button.configure(text="Next: double-clicks")
        self.fix_button.configure(state="disabled")
        self.calibration_status.configure(text="1/2 Isolated clicks: click once, wait, repeat.")
        self.calibration_job = self.root.after(120000, self.finish_calibration)
    def reset(self) -> None:
        self.diagnostic_engine.reset()
        self.click_count = 0
        self.double_click_count = 0
        self.test_area.configure(background="#e7f1f2")
        self.click_label.configure(text="0")
        self.double_label.configure(text="0")
        self.interval_label.configure(text="--")

    def handle_global_click(self, is_double_click: bool, interval_ms: Optional[float]) -> None:
        self.global_event_count += 1
        self._global_events.put((is_double_click, interval_ms))

    def handle_monitor_error(self, message: str) -> None:
        self._monitor_errors.put(message)

    def _show_monitor_error(self, message: str) -> None:
        self.fix_enabled.set(False)
        self._stop_fix()
        settings.save({"fix_enabled": False})
        messagebox.showerror("Fix stopped", f"The global mouse hook stopped:\n\n{message}")

    def _show_global_result(self, is_double_click: bool, interval_ms: Optional[float]) -> None:
        self.status_label.configure(text="FIX ON · FILTERED" if is_double_click else "FIX ON · ACTIVE")
        if is_double_click and interval_ms is not None:
            self.interval_label.configure(text=f"{interval_ms:.0f} ms")

    def close(self) -> None:
        if self.calibration_job is not None:
            self.root.after_cancel(self.calibration_job)
        if self._event_poll_job is not None:
            self.root.after_cancel(self._event_poll_job)
        self._stop_fix()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DoubleClickApp(root)
    root.mainloop()
