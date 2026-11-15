"""Plain input fields for running the frame engine without a user interface.

The engine reads its inputs and writes its results through a small part of the
Qt widget interface: ``text()``, ``currentText()``, ``isChecked()``, ``value()``
and their setters. These classes provide exactly that part, holding plain
values. The desktop gives the engine real widgets; ``HeadlessFrameEngine``
gives it these, built from ``form_schema.json``.

Each class follows the behaviour of the widget it stands in for where that
behaviour affects values (a choice only changes to an option it has; a number
is clamped to its range). Presentation calls (style, tooltip, enabled) are
accepted and ignored.
"""

from __future__ import annotations

from portalcalc.wind import direction_bearing_degrees


class Signal:
    """A change notification: connected callables run, with no arguments, when it fires."""

    def __init__(self, owner):
        self._owner = owner
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self):
        if not self._owner.signals_blocked:
            for slot in list(self._slots):
                slot()


class Field:
    """Common base: signal blocking, and presentation calls that have no effect without a screen."""

    signals_blocked = False

    def blockSignals(self, block):  # noqa: N802 (Qt naming)
        previous = self.signals_blocked
        self.signals_blocked = bool(block)
        return previous

    def setStyleSheet(self, style):  # noqa: N802
        pass

    def setToolTip(self, text):  # noqa: N802
        pass

    def setToolTipDuration(self, milliseconds):  # noqa: N802
        pass

    def setEnabled(self, enabled):  # noqa: N802
        pass

    def setVisible(self, visible):  # noqa: N802
        pass


class TextField(Field):
    """A line edit, label, button caption or multi-line text box."""

    def __init__(self, text=""):
        self._text = str(text)

    def text(self):
        return self._text

    def setText(self, text):  # noqa: N802
        self._text = "" if text is None else str(text)

    def toPlainText(self):  # noqa: N802
        return self._text

    def setPlainText(self, text):  # noqa: N802
        self.setText(text)

    def append(self, text):
        self._text = f"{self._text}\n{text}" if self._text else str(text)

    def clear(self):
        self._text = ""

    def setTextFormat(self, text_format):  # noqa: N802
        pass


class ChoiceField(Field):
    """A drop-down list: ordered (text, data) options and the selected index."""

    def __init__(self, items=(), index=-1):
        self._items = [(str(text), data) for text, data in items]
        self._index = index if -1 <= index < len(self._items) else -1
        self.currentIndexChanged = Signal(self)  # noqa: N815
        self.currentTextChanged = Signal(self)  # noqa: N815

    def _select(self, index):
        if index != self._index:
            self._index = index
            self.currentIndexChanged.emit()
            self.currentTextChanged.emit()

    def count(self):
        return len(self._items)

    def currentIndex(self):  # noqa: N802
        return self._index

    def setCurrentIndex(self, index):  # noqa: N802
        self._select(index if 0 <= index < len(self._items) else -1)

    def currentText(self):  # noqa: N802
        return self._items[self._index][0] if 0 <= self._index < len(self._items) else ""

    def setCurrentText(self, text):  # noqa: N802
        index = self.findText(text)
        if index >= 0:
            self._select(index)

    def currentData(self):  # noqa: N802
        return self._items[self._index][1] if 0 <= self._index < len(self._items) else None

    def itemText(self, index):  # noqa: N802
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def itemData(self, index):  # noqa: N802
        return self._items[index][1] if 0 <= index < len(self._items) else None

    def setItemText(self, index, text):  # noqa: N802
        if 0 <= index < len(self._items):
            self._items[index] = (str(text), self._items[index][1])

    def findText(self, text):  # noqa: N802
        for index, (item_text, _) in enumerate(self._items):
            if item_text == text:
                return index
        return -1

    def findData(self, data):  # noqa: N802
        for index, (_, item_data) in enumerate(self._items):
            if item_data == data:
                return index
        return -1

    def addItem(self, text, data=None):  # noqa: N802
        self._items.append((str(text), data))
        if self._index < 0:
            self._select(0)

    def addItems(self, texts):  # noqa: N802
        for text in texts:
            self.addItem(text)

    def clear(self):
        self._items = []
        self._select(-1)


class CheckField(Field):
    """A tick box."""

    def __init__(self, checked=False):
        self._checked = bool(checked)
        self.toggled = Signal(self)

    def isChecked(self):  # noqa: N802
        return self._checked

    def setChecked(self, checked):  # noqa: N802
        if bool(checked) != self._checked:
            self._checked = bool(checked)
            self.toggled.emit()


class NumberField(Field):
    """A spin box: a number held within a range (rounded to ``decimals`` when not whole)."""

    def __init__(self, value=0, minimum=0, maximum=99, decimals=0):
        self._minimum, self._maximum, self._decimals = minimum, maximum, decimals
        self._value = minimum
        self.valueChanged = Signal(self)  # noqa: N815
        self.setValue(value)

    def value(self):
        return self._value

    def setValue(self, value):  # noqa: N802
        value = min(max(value, self._minimum), self._maximum)
        value = round(float(value), self._decimals) if self._decimals else int(value)
        if value != self._value:
            self._value = value
            self.valueChanged.emit()

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum


class OrientationField(Field):
    """Where north is in the section view: a compass point, "Any", or a custom angle."""

    DIRECTIONS = ("Any", "NW", "N", "NE", "W", "E", "SW", "S", "SE")

    def __init__(self, text="E"):
        self._direction = "E"
        self._angle = NumberField(0.0, 0, 359.999, 3)
        self.setCurrentText(text)

    def currentText(self):  # noqa: N802
        if self._direction == "Custom":
            return f"{self._angle.value():.3f}".rstrip("0").rstrip(".") + "\N{DEGREE SIGN}"
        return self._direction

    def setCurrentText(self, value):  # noqa: N802
        if value in self.DIRECTIONS:
            self._direction = value
            return
        self._angle.setValue(direction_bearing_degrees(value))
        self._direction = "Custom"


class _WallStates:
    """Clad/open state of the four walls of one footprint."""

    WALLS = ("back", "left", "right", "front")

    def __init__(self, defaults):
        self.defaults = dict(defaults)
        self.states = {wall: bool(self.defaults.get(wall, True)) for wall in self.WALLS}

    def wall_states(self):
        return dict(self.states)

    def set_wall_states(self, states):
        for wall in self.WALLS:
            self.states[wall] = bool(states.get(wall, self.defaults.get(wall, True)))


class EnvelopeField(Field):
    """Wall enclosure of the main building and its lean-tos.

    A lean-to's inner wall is the main building's wall on that side, so it always
    follows it; setting a lean-to's inner wall clad also clads the main wall.
    """

    def __init__(self, main=None, left=None, right=None):
        self.main_selector = _WallStates({"left": True, "right": True, "front": True, "back": True})
        self.left_selector = _WallStates({"left": False, "right": False, "front": False, "back": False})
        self.right_selector = _WallStates({"left": False, "right": False, "front": False, "back": False})
        if main:
            self.main_selector.set_wall_states(main)
        if left:
            self.left_selector.set_wall_states(left)
        if right:
            self.right_selector.set_wall_states(right)
        self.left_lean_proxy = LeanToEnvelope(self, "left")
        self.right_lean_proxy = LeanToEnvelope(self, "right")
        self.sync_inner_walls()

    def set_lean_to_visibility(self, left_visible, right_visible):
        self.sync_inner_walls()

    def sync_inner_walls(self):
        main_states = self.wall_states()
        self.left_selector.states["right"] = bool(main_states.get("left", False))
        self.right_selector.states["left"] = bool(main_states.get("right", False))

    def wall_states(self):
        return self.main_selector.wall_states()

    def set_wall_states(self, states):
        self.main_selector.set_wall_states(states)
        self.sync_inner_walls()

    def lean_wall_states(self, side):
        self.sync_inner_walls()
        return (self.left_selector if side == "left" else self.right_selector).wall_states()

    def set_lean_wall_states(self, side, states):
        states = states or {}
        inner, main_wall = ("right", "left") if side == "left" else ("left", "right")
        if states.get(inner):
            main_states = self.wall_states()
            main_states[main_wall] = True
            self.main_selector.set_wall_states(main_states)
        (self.left_selector if side == "left" else self.right_selector).set_wall_states(states)
        self.sync_inner_walls()


class LeanToEnvelope:
    """One lean-to's walls, read and written through the building's envelope."""

    def __init__(self, envelope, side):
        self.envelope = envelope
        self.side = side

    def wall_states(self):
        return self.envelope.lean_wall_states(self.side)

    def set_wall_states(self, states):
        self.envelope.set_lean_wall_states(self.side, states)


def field_from_schema(spec, fields):
    """Build one field from its ``form_schema.json`` entry (``fields`` resolves references)."""
    kind = spec["kind"]
    if kind == "text":
        return TextField(spec.get("text", ""))
    if kind == "choice":
        return ChoiceField(spec.get("items", ()), spec.get("index", -1))
    if kind == "check":
        return CheckField(spec.get("checked", False))
    if kind == "number":
        return NumberField(spec["value"], spec["minimum"], spec["maximum"], spec.get("decimals", 0))
    if kind == "orientation":
        return OrientationField(spec.get("text", "E"))
    if kind == "envelope":
        return EnvelopeField(spec.get("main"), spec.get("left"), spec.get("right"))
    if kind == "lean_to_envelope":
        return getattr(fields[spec["of"]], f"{spec['side']}_lean_proxy")
    raise ValueError(f"Unknown field kind {kind!r}")
