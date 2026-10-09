"""The History, Apps and Devices panes."""

from __future__ import annotations

import os
import platform
import time
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QMenu, QVBoxLayout, QWidget

from .. import app_keys
from ..core import Button
from ..inputs import TOUCH_KINDS, as_button, button_name
from ..wear import SHOWN_DAYS
from .base import Page, card_icon, label, make_button
from .charts import DailyRateChart, GapHistogram
from .theme import IS_MAC
from .widgets import Row, Switch, ValueLabel, wheel_needs_focus

#: How often the History pane reads the counts again while it is on screen:
#: the clicks someone makes to watch it are counted as they go.
HISTORY_TICK_MS = 5000
#: How often the Devices pane brings "seen 5 min ago" up to date while it is
#: on screen; the times are in whole minutes.
DEVICES_TICK_MS = 30000


class LivePage(Page):
    """A pane that reads its numbers again every so often while it is on
    screen, and not at all while it is hidden."""

    def __init__(self, title: str, interval_ms: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(title, parent)
        self.ticker = QTimer(self)
        self.ticker.setInterval(interval_ms)
        self.ticker.timeout.connect(self._tick)

    def refresh(self) -> None:
        raise NotImplementedError

    def _tick(self) -> None:
        if not self.window().isMinimized():
            self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.ticker.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.ticker.stop()


# -- History -----------------------------------------------------------------------

TREND_NOTES = {
    "worse": "More of your clicks bounce than two weeks ago: the switch is wearing.",
    "better": "Fewer of your clicks bounce than two weeks ago.",
    "same": "The share of clicks that bounce has held steady.",
    "unknown": "A trend needs two weeks of clicks.",
}


class HistoryPage(LivePage):
    """How often each button has bounced over the last 30 days."""

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__("History", HISTORY_TICK_MS, parent)
        self.controller = controller
        self.button: Button = Button.LEFT
        self._choices_shown: list[Button] = []

        picker = self.section()
        self.button_picker = QComboBox()
        self.button_picker.setAccessibleName("Button to show")
        wheel_needs_focus(self.button_picker)  # the wheel scrolls the pane
        self.button_picker.activated.connect(self._on_pick)
        picker.add(Row("Button", "", self.button_picker, card_icon("mouse")))

        self.header(f"Bounces per 100 clicks, last {SHOWN_DAYS} days")
        chart = self.section()
        self.rate_chart = DailyRateChart()
        chart.add(self._holder(self.rate_chart))
        self.trend_note = self.footnote("")

        self.header("Summary")
        summary = self.section()
        self.trend_value = ValueLabel()
        self.trend_value.setAccessibleName("Trend")
        self.clicks_value = ValueLabel()
        self.bounces_value = ValueLabel()
        self.dropouts_value = ValueLabel()
        self.trend_row = summary.add(Row("Trend", "", self.trend_value, card_icon("history")))
        summary.add(Row("Clicks", "", self.clicks_value, card_icon("mouse")))
        summary.add(Row("Bounces blocked", "", self.bounces_value, card_icon("chart")))
        summary.add(
            Row("Dropouts repaired", "A drag whose contact broke and came back.", self.dropouts_value, card_icon("chart"))
        )

        self.header("Bounce lengths")
        gaps = self.section()
        self.histogram = GapHistogram()
        gaps.add(self._holder(self.histogram))
        self.histogram_note = self.footnote(
            "How long after a release each blocked bounce came. Bounces close to the window "
            "mean it is only just long enough."
        )

        self.wheel_header = self.header("Scroll wheel")
        self.wheel_section = self.section()
        self.wheel_value = ValueLabel()
        self.wheel_section.add(Row("Reversals dropped", "", self.wheel_value, card_icon("wheel")))
        self.body.addStretch(1)

    @staticmethod
    def _holder(chart: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(14, 12, 14, 8)
        layout.addWidget(chart)
        return holder

    def _choices(self) -> list[Button]:
        """The buttons worth a look: those filtered now or with any history."""
        shown = set(self.controller.buttons) | set(self.controller.wear.buttons_with_data())
        return [button for button in Button if button in shown]

    def _on_pick(self, index: int) -> None:
        self.button = as_button(self.button_picker.itemData(index))
        self.refresh()

    def refresh(self) -> None:
        choices = self._choices()
        if self.button not in choices:
            self.button = choices[0]
        self.button_picker.blockSignals(True)
        if choices != self._choices_shown:
            # Built again only when the buttons change: a refresh every few
            # seconds must not close the list while someone has it open.
            self._choices_shown = choices
            self.button_picker.clear()
            for button in choices:
                self.button_picker.addItem(button_name(button), button.value)
        self.button_picker.setCurrentIndex(choices.index(self.button))
        self.button_picker.blockSignals(False)

        wear = self.controller.wear
        days = wear.daily(self.button)
        self.rate_chart.set_days(days)
        totals = wear.totals(self.button)
        trend = wear.trend(self.button)
        self.trend_value.setText(trend.words)
        note = TREND_NOTES[trend.key]
        if trend.window_changed and trend.key in ("worse", "better"):
            note += " The window changed in this time, which moves the count too."
        self.trend_note.setText(note)
        self.clicks_value.setText(f"{totals.clicks:,}")
        self.bounces_value.setText(f"{totals.bounces:,}")
        self.dropouts_value.setText(f"{totals.dropouts:,}")
        self.histogram.set_histogram(wear.histogram(self.button, window_ms=self.controller.threshold_for(self.button)))
        ticks, dropped = wear.wheel_totals()
        show_wheel = bool(ticks) or self.controller.wheel_fix
        self.wheel_header.setVisible(show_wheel)
        self.wheel_section.setVisible(show_wheel)
        self.wheel_value.setText(f"{dropped:,} of {ticks:,} ticks")


# -- Apps --------------------------------------------------------------------------

APPS_NOTE = (
    "Nothing is filtered while one of these apps is in front: the app you are using, "
    "not the one under the pointer. Useful for games whose anti-cheat objects to "
    "filtered input."
)


def _choose_title() -> str:
    return label("Choose App…") if IS_MAC else label("Choose Program…")


class AppsPage(Page):
    """Apps in which nothing is filtered."""

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__("Apps", parent)
        self.controller = controller
        self._shown: Optional[list] = None

        self.header("Never filtered in")
        self.list_section = self.section()
        self.remove_buttons: dict[str, QWidget] = {}
        self.footnote(APPS_NOTE)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 14, 0, 0)
        actions.addStretch(1)
        self.add_button = make_button("Add App…")
        self.add_button.setAccessibleName("Add an app")
        self.add_menu = QMenu(self.add_button)
        self.add_menu.aboutToShow.connect(self._fill_menu)
        self.add_button.setMenu(self.add_menu)
        actions.addWidget(self.add_button)
        self.body.addLayout(actions)
        self.body.addStretch(1)

    def refresh(self) -> None:
        apps = self.controller.excluded_apps
        if apps == self._shown:
            return  # unchanged: keep the rows, and keyboard focus with them
        self._shown = apps
        self.list_section.clear()
        self.remove_buttons = {}
        if not apps:
            self.list_section.add(Row("No apps", "Every app is filtered.", None, card_icon("apps")))
            return
        for entry in apps:
            remove = make_button("Remove")
            remove.setAccessibleName(f"Remove {entry['name']}")
            remove.clicked.connect(lambda _checked=False, key=entry["key"]: self.controller.remove_excluded_app(key))
            detail = entry["key"] if entry["key"] != entry["name"] else ""
            self.list_section.add(Row(entry["name"], detail, remove, card_icon("apps")))
            self.remove_buttons[entry["key"]] = remove

    def _fill_menu(self) -> None:
        """Running apps first, then the file dialog, built as the menu opens."""
        self.add_menu.clear()
        excluded = {entry["key"] for entry in self.controller.excluded_apps}
        running = [app for app in app_keys.running_apps() if app.key not in excluded]
        if running:
            heading = self.add_menu.addAction("Running Apps" if IS_MAC else "Running apps")
            heading.setEnabled(False)
            for app in running:
                action = self.add_menu.addAction(app.name)
                action.setToolTip(app.key)
                action.triggered.connect(lambda _checked=False, choice=app: self.add(choice))
            self.add_menu.addSeparator()
        self.add_menu.addAction(_choose_title(), self.choose_file)

    def add(self, choice: app_keys.AppChoice) -> None:
        self.controller.add_excluded_app(choice.key, choice.name)

    def choose_file(self) -> None:
        if platform.system() == "Windows":
            # Where this Windows keeps programs, which needn't be drive C.
            start, kinds = os.environ.get("ProgramFiles") or "C:\\Program Files", "Programs (*.exe)"
        else:
            start, kinds = "/Applications", "Applications (*.app)"
        path, _filter = QFileDialog.getOpenFileName(self, _choose_title().rstrip("…"), start, kinds)
        choice = app_keys.from_path(path)
        if choice is not None:
            self.add(choice)


# -- Devices -----------------------------------------------------------------------

KIND_NAMES = {"trackpad": "Trackpad", "touchscreen": "Touchscreen", "pen": "Pen", "mouse": "Mouse", "unknown": "Pointing device"}
DEVICES_NOTE = (
    "Trackpads, touchscreens and pens are never filtered: taps have no switch to wear out. "
    "A mouse you stop filtering stays that way whenever it is connected."
)


def _transport(key: str) -> str:
    prefix = key.split(":", 1)[0].lower()
    return {"usb": "USB", "bt": "Bluetooth"}.get(prefix, "")


def _seen(when: float) -> str:
    if not when:
        return ""
    minutes = int(max(0.0, time.time() - when) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def _mouse_detail(key: str, kind: str, last_seen: float) -> str:
    parts = [KIND_NAMES.get(kind, "Pointing device"), _transport(key)]
    parts.append(f"seen {_seen(last_seen)}" if last_seen else "not seen since launch")
    return " · ".join(part for part in parts if part)


class DevicesPage(LivePage):
    """The pointing devices the filter has seen, and which it filters."""

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__("Devices", DEVICES_TICK_MS, parent)
        self.controller = controller
        self._shown: Optional[tuple] = None

        self.header("Mice")
        self.mice = self.section()
        self.header_touch = self.header("Trackpads, touchscreens and pens")
        self.touch = self.section()
        self.footnote(DEVICES_NOTE)
        self.body.addStretch(1)
        self.switches: dict[str, Switch] = {}
        self.mouse_rows: dict[str, Row] = {}

    def _rows(self) -> tuple[list, list]:
        """(mice, touch devices): what the filter saw this run, plus any
        ignored device that hasn't turned up yet, so it can be turned back on."""
        devices = self.controller.seen_devices()
        mice = [(device.key, device.name, device.kind, device.last_seen) for device in devices if device.kind not in TOUCH_KINDS]
        touch = [(device.key, device.name, device.kind, device.last_seen) for device in devices if device.kind in TOUCH_KINDS]
        seen = {device.key for device in devices}
        mice += [(entry["key"], entry["name"], "mouse", 0.0) for entry in self.controller.ignored_devices if entry["key"] not in seen]
        return mice, touch

    def refresh(self) -> None:
        mice, touch = self._rows()
        ignored = {entry["key"] for entry in self.controller.ignored_devices}
        layout = (tuple(key for key, *_rest in mice), tuple(key for key, *_rest in touch))
        if layout == self._shown:
            # The same devices: only their switches and times may have moved.
            # The rows stay, and keyboard focus with them.
            for key, _name, kind, last_seen in mice:
                self.switches[key].setChecked(key not in ignored, animate=False)
                self.mouse_rows[key].set_detail(_mouse_detail(key, kind, last_seen))
            return
        self._shown = layout
        self.mice.clear()
        self.touch.clear()
        self.switches = {}
        self.mouse_rows = {}
        if not mice:
            self.mice.add(
                Row(
                    "No mouse seen yet",
                    "Each mouse shows up here once you click with it while the filter is on.",
                    None,
                    card_icon("mouse"),
                )
            )
        for key, name, kind, last_seen in mice:
            switch = Switch(accessible_name=f"Filter {name}")
            switch.setChecked(key not in ignored, animate=False)
            switch.clicked.connect(
                lambda checked, device=key, title=name: self.controller.set_device_ignored(device, title, not checked)
            )
            self.mouse_rows[key] = self.mice.add(Row(name, _mouse_detail(key, kind, last_seen), switch, card_icon("mouse")))
            self.switches[key] = switch
        for key, name, kind, _last_seen in touch:
            value = ValueLabel("Never filtered")
            self.touch.add(Row(name, KIND_NAMES.get(kind, ""), value, card_icon("devices")))
        self.header_touch.setVisible(bool(touch))
        self.touch.setVisible(bool(touch))
