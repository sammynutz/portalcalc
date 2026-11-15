"""Small custom input widgets: compass and wall-enclosure selectors."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


# ==========================================================
# COMPASS SELECTOR
# ==========================================================

class CompassSelector(QWidget):
    """Compass or custom north angle, read as viewed from the shed elevation."""

    DIRECTIONS = [
        ("NW", "↖", 0, 0), ("N", "↑", 0, 2), ("NE", "↗", 0, 4),
        ("W",  "←", 2, 0),                         ("E",  "→", 2, 4),
        ("SW", "↙", 4, 0), ("S", "↓", 4, 2), ("SE", "↘", 4, 4),
    ]

    def __init__(self, default="E"):
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        note = QLabel("Select where NORTH is when looking at the shed elevation")
        note.setWordWrap(True)
        layout.addWidget(note)

        any_button = QRadioButton("Any orientation")
        any_button.setProperty("direction", "Any")
        self.button_group = QButtonGroup(self)
        self.button_group.addButton(any_button)
        layout.addWidget(any_button)

        grid = QGridLayout()
        grid.setSpacing(4)

        centre = QLabel("NORTH")
        centre.setAlignment(Qt.AlignCenter)
        centre.setStyleSheet("font-weight: bold; padding: 8px; border: 1px solid #999; border-radius: 6px;")
        grid.addWidget(centre, 2, 2)

        for direction, arrow, row, col in self.DIRECTIONS:
            button = QRadioButton(arrow)
            button.setProperty("direction", direction)
            self.button_group.addButton(button)
            grid.addWidget(button, row, col)
            if direction == default:
                button.setChecked(True)

        # Light arrow guides pointing away from the NORTH reference label.
        guide_labels = [
            ("↖", 1, 1), ("↑", 1, 2), ("↗", 1, 3),
            ("←", 2, 1),             ("→", 2, 3),
            ("↙", 3, 1), ("↓", 3, 2), ("↘", 3, 3),
        ]
        for text, row, col in guide_labels:
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, row, col)

        layout.addLayout(grid)
        self.custom_button = QRadioButton("Custom north angle in section view")
        self.custom_button.setProperty("direction", "Custom")
        self.button_group.addButton(self.custom_button)
        self.angle_input = QDoubleSpinBox()
        self.angle_input.setRange(0, 359.999)
        self.angle_input.setDecimals(3)
        self.angle_input.setSingleStep(0.5)
        self.angle_input.setSuffix("°")
        self.angle_input.setToolTip("Clockwise from up in the section view: up 0°, right 90°. For an open right wall facing true bearing 63°, use 27° here.")
        self.angle_input.setEnabled(False)
        self.custom_button.toggled.connect(self.angle_input.setEnabled)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self.custom_button)
        custom_row.addWidget(self.angle_input)
        layout.addLayout(custom_row)
        self.setCurrentText(default)

    def currentText(self):
        checked = self.button_group.checkedButton()
        if checked is None:
            return "E"
        if checked is self.custom_button:
            return f"{self.angle_input.value():.3f}".rstrip("0").rstrip(".") + "°"
        return checked.property("direction") or "E"

    def setCurrentText(self, value):
        for button in self.button_group.buttons():
            if button.property("direction") == value:
                button.setChecked(True)
                return
        from portalcalc.wind import direction_bearing_degrees
        self.angle_input.setValue(direction_bearing_degrees(value))
        self.custom_button.setChecked(True)


class EnvelopePlanSelector(QWidget):
    WALLS = [
        ("back", "Back"),
        ("left", "Left"),
        ("right", "Right"),
        ("front", "Front"),
    ]

    def __init__(self, labels=None, default_states=None, centre_text="PLAN\nVIEW"):
        super().__init__()
        layout = QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.setLayout(layout)
        self.wall_buttons = {}
        self.side_keys = {"left", "right"}
        self.labels = labels or dict(self.WALLS)
        self.default_states = default_states or {"left": True, "right": True, "front": True, "back": True}

        def add_wall_button(key, label, row, col):
            button = RotatedButton() if key in {"left", "right"} else QPushButton()
            button.setCheckable(True)
            if key in {"left", "right"}:
                button.setFixedWidth(34)
                button.setMinimumHeight(92)
            else:
                button.setFixedWidth(88)
                button.setMinimumHeight(30)
            button.clicked.connect(self.update_button_styles)
            self.wall_buttons[key] = button
            layout.addWidget(button, row, col)

        add_wall_button("back", "Back", 0, 1)
        add_wall_button("left", "Left", 1, 0)

        footprint = QLabel(centre_text)
        footprint.setAlignment(Qt.AlignCenter)
        footprint.setFixedSize(74, 56)
        footprint.setStyleSheet("border: 1px solid #777; background: #f7f7f7; color: #1f2933; font-weight: bold;")
        layout.addWidget(footprint, 1, 1)

        add_wall_button("right", "Right", 1, 2)
        add_wall_button("front", "Front", 2, 1)
        self.set_wall_states(self.default_states)

    def wall_states(self):
        return {key: button.isChecked() for key, button in self.wall_buttons.items()}

    def set_wall_states(self, states):
        for key, button in self.wall_buttons.items():
            button.setChecked(bool(states.get(key, self.default_states.get(key, True))))
        self.update_button_styles()

    def update_button_styles(self):
        for key, button in self.wall_buttons.items():
            label = self.labels.get(key, key.title())
            state = "Clad" if button.isChecked() else "Open"
            if key in self.side_keys:
                label_text = f"{label} {state}"
                metrics = QFontMetrics(button.font())
                button.setText(metrics.elidedText(label_text, Qt.ElideRight, max(button.height() - 12, 40)))
            else:
                button.setText(f"{label}\n{state}")
            if button.isChecked():
                button.setStyleSheet("QPushButton { background: #dfeee4; border: 2px solid #2f7d44; color: #17351f; font-weight: bold; }")
            else:
                button.setStyleSheet("QPushButton { background: #f7e1df; border: 2px dashed #a43d34; color: #6d1f1b; font-weight: bold; }")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_button_styles()


class RotatedButton(QPushButton):
    def __init__(self):
        super().__init__()
        self._display_text = ""

    def setText(self, text):
        self._display_text = text
        super().setText("")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        rect = QRectF(-self.height() / 2, -self.width() / 2, self.height(), self.width())
        painter.setPen(QColor("#17351f") if self.isChecked() else QColor("#6d1f1b"))
        painter.drawText(rect, Qt.AlignCenter, self._display_text)
        painter.end()


class LeanToEnvelopeProxy:
    def __init__(self, selector, side):
        self.selector = selector
        self.side = side

    def wall_states(self):
        return self.selector.lean_wall_states(self.side)

    def set_wall_states(self, states):
        self.selector.set_lean_wall_states(self.side, states)


class BuildingEnvelopeSelector(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.setLayout(layout)

        self.left_selector = EnvelopePlanSelector(
            labels={"left": "Outer", "right": "Inner", "front": "Front", "back": "Back"},
            default_states={"left": False, "right": False, "front": False, "back": False},
            centre_text="LEFT\nLEAN-TO",
        )
        self.main_selector = EnvelopePlanSelector(centre_text="MAIN\nGABLE")
        self.right_selector = EnvelopePlanSelector(
            labels={"left": "Inner", "right": "Outer", "front": "Front", "back": "Back"},
            default_states={"left": False, "right": False, "front": False, "back": False},
            centre_text="RIGHT\nLEAN-TO",
        )

        for selector in [self.left_selector, self.main_selector, self.right_selector]:
            layout.addWidget(selector)

        self.left_selector.wall_buttons["right"].setVisible(False)
        self.right_selector.wall_buttons["left"].setVisible(False)
        self.left_lean_proxy = LeanToEnvelopeProxy(self, "left")
        self.right_lean_proxy = LeanToEnvelopeProxy(self, "right")
        self.wall_buttons = self.main_selector.wall_buttons
        self.set_lean_to_visibility(False, False)

    def all_wall_buttons(self):
        buttons = list(self.main_selector.wall_buttons.values())
        buttons.extend(button for key, button in self.left_selector.wall_buttons.items() if key != "right")
        buttons.extend(button for key, button in self.right_selector.wall_buttons.items() if key != "left")
        return buttons

    def set_lean_to_visibility(self, left_visible, right_visible):
        self.left_selector.setVisible(bool(left_visible))
        self.right_selector.setVisible(bool(right_visible))
        self.sync_inner_walls()

    def sync_inner_walls(self):
        main_states = self.wall_states()
        self.left_selector.wall_buttons["right"].setChecked(main_states.get("left", False))
        self.right_selector.wall_buttons["left"].setChecked(main_states.get("right", False))
        self.left_selector.update_button_styles()
        self.right_selector.update_button_styles()

    def wall_states(self):
        return self.main_selector.wall_states()

    def set_wall_states(self, states):
        self.main_selector.set_wall_states(states)
        self.sync_inner_walls()

    def lean_wall_states(self, side):
        self.sync_inner_walls()
        if side == "left":
            return self.left_selector.wall_states()
        return self.right_selector.wall_states()

    def set_lean_wall_states(self, side, states):
        states = states or {}
        if side == "left" and states.get("right"):
            main_states = self.wall_states()
            main_states["left"] = True
            self.main_selector.set_wall_states(main_states)
        if side == "right" and states.get("left"):
            main_states = self.wall_states()
            main_states["right"] = True
            self.main_selector.set_wall_states(main_states)
        selector = self.left_selector if side == "left" else self.right_selector
        selector.set_wall_states(states)
        self.sync_inner_walls()
