"""Minimal whiteboard: parse line-based drawing ops, render to PNG.

Grid coordinates (40 x 30) rather than pixels, so the model works in a space
coarse enough to reason about. Everything renders with a slight jitter so the
result reads as hand-drawn rather than CAD.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Default board. 16:9 so it fills a browser window instead of letterboxing,
# and roughly twice the area of the old 40x30 -- a lesson should be able to
# run to the end without wiping to make room.
GRID_W, GRID_H = 64, 36
CELL = 24  # pixels per grid cell -> 1536 x 864
INK = (28, 28, 32)
PAPER = (252, 251, 247)
ACCENT = (198, 60, 48)

# Named pens. The DSL says `pen red`, never an RGB triple -- a model picks a
# word far more reliably than three numbers.
PENS: dict[str, tuple[int, int, int]] = {
    "ink": INK,
    "red": ACCENT,
    "blue": (42, 86, 168),
    "green": (46, 125, 91),
    "grey": (128, 136, 149),
    "gray": (128, 136, 149),
}

# Soft tints for `fill`. Light enough that ink on top stays readable -- a
# filled shape has to remain a shape, not become a block of colour.
TINTS: dict[str, tuple[int, int, int]] = {
    "ink": (232, 232, 236),
    "red": (250, 230, 228),
    "blue": (228, 237, 252),
    "green": (228, 244, 236),
    "grey": (238, 240, 243),
    "gray": (238, 240, 243),
    "yellow": (252, 245, 216),
    "none": PAPER,
}

SHADOW = (224, 222, 215)  # a single soft offset, not a gradient

# Minimap glyphs. MAP_RANK orders them so a box wins over a stroke crossing it.
MAP_GLYPH = {
    "stroke": "~",
    "arrow": ">",
    "connect": ">",
    "pan": " ",
    "text": "-",
    "group": ":",
    "brace": "}",
    "note": "N",
    "circle": "O",
    "highlight": "O",
    "strike": "X",
    "box": "#",
    "ellipse": "#",
    "icon": "@",
    "erase": " ",
}
MAP_RANK = ["~", ":", "}", ">", "-", "O", "X", "N", "#", "@"]

# A box labelled "Database" is a box. A cylinder is a database. Pictograms are
# the cheapest way for a board to stop looking like a wireframe.
ICONS = [
    "db",
    "server",
    "user",
    "cloud",
    "queue",
    "file",
    "clock",
    "lock",
    "globe",
    "gear",
    "warning",
    "check",
    "cross",
    "box",
]

CHAR_W = 0.52  # grid units per character, for estimating text extents
LINE_H = 1.05

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",  # handwriting, if present
    "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


# Matches the viewer's tokenizer exactly. shlex.split() was wrong here: it
# treats a bare apostrophe as an opening quote, so "say It's fine" failed to
# parse in Python while the browser accepted it -- the student heard a line the
# canvas never recorded.
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def _isnum(tok: str) -> bool:
    try:
        float(tok)
    except ValueError:
        return False
    return True


def tokenize(line: str) -> list[str]:
    # finditer, not findall: findall yields "" for an unmatched group, so the
    # quoted-vs-bare test has to be made against the match object.
    return [m.group(1) if m.group(1) is not None else m.group(2) for m in _TOKEN.finditer(line)]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


# Shown back to the agent when an op fails to parse. A model that is told the
# right shape fixes itself; one told only "bad args" guesses again.
USAGE: dict[str, str] = {
    "text": 'text x,y <words>            e.g.  text 12,1.5 "Binary search"',
    "box": 'box x,y,w,h <label>         e.g.  box 5,7 3,2.5 "Client"',
    "ellipse": 'ellipse x,y,rx,ry <label>   e.g.  ellipse 18,11 2,1.5 "h2"',
    "arrow": 'arrow x1,y1 x2,y2 <label>   e.g.  arrow 11,7 15,7 "GET /x"',
    "highlight": (
        'highlight "<label>" <spoken note>   e.g.  highlight "LOAD BALANCER" "this one here"'
    ),
    "circle": 'circle x,y,r <spoken note>  e.g.  circle 18.5,11.25 3 "this one"',
    "stroke": "stroke x1,y1 x2,y2 ...      e.g.  stroke 2,20 38,20",
    "erase": "erase x,y,w,h <note>  OR  erase x,y,r <note>",
    "pen": "pen <ink|red|blue|green|grey>",
    "board": "board W H                   e.g.  board 40 30 (first line only)",
    "pan": "pan Y                        e.g.  pan 36 (scroll to fresh board)",
    "page": "page <title>",
    "say": 'say <text>                  e.g.  say "Now the hard part."',
}


@dataclass
class OpResult:
    ok: bool
    detail: str


@dataclass
class Element:
    """One drawn thing, with the box it occupies, so collisions are detectable."""

    kind: str
    label: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0
    panels: int = field(init=False, default=1)
    view_y: float = field(init=False, default=0.0)
    # Highlights and freehand are meant to cross other things; they never
    # count as collisions and nothing collides with them.
    transparent: bool = False
    # Partially erased. The pixels are damaged and the agent is expected to
    # redraw over them, so a clipped element must not report that redraw as
    # a collision with itself.
    clipped: bool = False
    # For line-like elements: the actual route, so a diagonal arrow is not
    # reported as a filled rectangle the size of its bounding box.
    path: list[tuple[float, float]] | None = None

    @property
    def solid(self) -> bool:
        return not (self.transparent or self.clipped)

    def overlaps(self, other: Element, pad: float = 0.0) -> bool:
        return not (
            self.x1 + pad <= other.x0
            or other.x1 + pad <= self.x0
            or self.y1 + pad <= other.y0
            or other.y1 + pad <= self.y0
        )

    def describe(self) -> str:
        box = f"({self.x0:g},{self.y0:g})-({self.x1:g},{self.y1:g})"
        return f"{self.kind} {box}" + (f" {self.label!r}" if self.label else "")


@dataclass
class Canvas:
    """Stateful whiteboard. Ops mutate it; `render` snapshots it."""

    seed: int = 7
    grid_w: int = GRID_W  # the viewport: what a watcher sees at once
    grid_h: int = GRID_H
    max_panels: int = 6  # how far the board may extend downward
    _img: Image.Image = field(init=False)
    _draw: ImageDraw.ImageDraw = field(init=False)
    _rng: random.Random = field(init=False)
    narration: list[str] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # advisory, per scene
    wiped: list[str] = field(default_factory=list)  # what each erase took out
    page: int = 0
    panels: int = field(init=False, default=1)
    view_y: float = field(init=False, default=0.0)
    pen: tuple[int, int, int] = INK
    pen_name: str = "ink"
    dashed: bool = False

    @property
    def board_h(self) -> int:
        """Full height of the drawing surface, which grows as the lesson does."""
        return self.grid_h * self.panels

    def __post_init__(self) -> None:
        self.panels = 1
        self.view_y = 0.0  # top row of the visible window
        self._img = Image.new("RGB", (self.grid_w * CELL, self.grid_h * CELL), PAPER)
        self._draw = ImageDraw.Draw(self._img)
        self._rng = random.Random(self.seed)

    # --- coordinate helpers -------------------------------------------------
    def _px(self, x: float, y: float) -> tuple[float, float]:
        return x * CELL, y * CELL

    def _jitter(self, p: tuple[float, float], amt: float = 1.6) -> tuple[float, float]:
        return (p[0] + self._rng.uniform(-amt, amt), p[1] + self._rng.uniform(-amt, amt))

    def _dashed_line(self, a, b, width=3, color=None, on=9.0, off=7.0) -> None:
        """Dashes follow the line, so a dashed box still reads as hand-drawn."""
        color = color or self.pen
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            end = min(length, pos + on)
            self._draw.line(
                [(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)],
                fill=color,
                width=width,
            )
            pos = end + off

    def _line(self, a, b, width=3, color=None) -> None:
        """Honour the current dash state. Use this for anything structural."""
        if self.dashed:
            self._dashed_line(a, b, width, color)
        else:
            self._rough_line(a, b, width, color or self.pen)

    def _rough_line(self, a, b, width=3, color=INK) -> None:
        """Draw a line as two slightly-off passes, the way a pen doubles back."""
        for _ in range(2):
            self._draw.line([self._jitter(a), self._jitter(b)], fill=color, width=width)

    # --- bookkeeping --------------------------------------------------------
    def _add(self, kind, label, x0, y0, x1, y1, transparent=False, path=None) -> Element:
        """Record an element, flagging it if it falls outside the board."""
        el = Element(
            kind,
            label,
            min(x0, x1),
            min(y0, y1),
            max(x0, x1),
            max(y0, y1),
            page=self.page,
            transparent=transparent,
            path=path,
        )

        # Drawing past the edge used to be silent -- the op "succeeded", the ink
        # landed outside the image, and the report said all was well. An agent
        # reasoning about a 64x36 board will write y=40 sooner or later.
        if kind not in ("pan",) and self._grow_to(el.y1):
            pass
        if el.x1 < 0 or el.y1 < 0 or el.x0 > self.grid_w or el.y0 > self.board_h:
            self.errors.append(
                f"{kind} at ({el.x0:g},{el.y0:g})-({el.x1:g},{el.y1:g}) is entirely "
                f"off the {self.grid_w}x{self.board_h} board -- nothing was drawn"
            )
        elif kind not in ("erase", "pan") and (
            el.x0 < 0 or el.y0 < 0 or el.x1 > self.grid_w or el.y1 > self.board_h
        ):
            self.notes.append(
                f"{el.describe()} runs off the {self.grid_w}x{self.board_h} board "
                f"-- part of it is not visible"
            )
        self.elements.append(el)
        return el

    def _text_box(self, x, y, s, size, center) -> tuple[float, float, float, float]:
        w = len(s) * CHAR_W * (size / 20)
        h = LINE_H * (size / 20)
        x0 = x - w / 2 if center else x
        return x0, y - h / 2, x0 + w, y + h / 2

    @property
    def live(self) -> list[Element]:
        """Elements on the current page that have not been erased away."""
        return [e for e in self.elements if e.page == self.page]

    # --- primitives ---------------------------------------------------------
    def stroke(self, pts: list[tuple[float, float]], color=None) -> None:
        px = [self._px(*p) for p in pts]
        for a, b in itertools.pairwise(px):
            self._rough_line(a, b, color=color or self.pen)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self._add(
            "stroke", "", min(xs), min(ys), max(xs), max(ys), transparent=True, path=list(pts)
        )

    def box(self, x, y, w, h, label: str = "") -> None:
        c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        px = [self._px(*p) for p in c]
        for a, b in itertools.pairwise(px):
            self._line(a, b)
        self._add("box", label, x, y, x + w, y + h)
        if label:
            # Warn when a label will not fit inside the box that holds it.
            need = len(label) * CHAR_W
            if need > w:
                self.notes.append(
                    f"label {label!r} needs ~{need:.1f} units but its box is {w:g} wide"
                )
            self.text(x + w / 2, y + h / 2, label, center=True, _log=False)

    def arrow(self, x1, y1, x2, y2, label: str = "") -> None:
        a, b = self._px(x1, y1), self._px(x2, y2)
        self._rough_line(a, b, color=self.pen)
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        for sign in (+1, -1):
            tip = (b[0] - 14 * math.cos(ang + sign * 0.45), b[1] - 14 * math.sin(ang + sign * 0.45))
            self._rough_line(b, tip, width=2, color=self.pen)
        self._add("arrow", label, x1, y1, x2, y2, transparent=True, path=[(x1, y1), (x2, y2)])
        if label:
            lx, ly = self._dodge_label((x1 + x2) / 2, (y1 + y2) / 2 - 0.7, label)
            self.text(lx, ly, label, center=True, _log=False)

    def _dodge_label(self, x, y, label, size: int = 18) -> tuple[float, float]:
        """Nudge a label off any solid element already sitting where it would land.

        Converging arrows share a midpoint, so midpoint placement stacks their
        labels. Step perpendicular until the slot is clear, then give up and
        use the original spot rather than wander far from the arrow.
        """
        for step in (0.0, -1.2, 1.2, -2.4, 2.4, -3.6, 3.6):
            cand = Element("label", label, *self._text_box(x, y + step, label, size, True))
            clash = any(cand.overlaps(o, pad=0.1) for o in self.live if o.solid)
            if not clash:
                return x, y + step
        return x, y

    def text(self, x, y, s: str, size: int = 20, center: bool = False, _log: bool = True) -> None:
        font = _load_font(size)
        anchor = "mm" if center else "lm"
        self._draw.text(self._px(x, y), s, font=font, fill=self.pen, anchor=anchor)
        if _log:
            self._add("text", s, *self._text_box(x, y, s, size, center))

    def ellipse(self, x, y, rx, ry, label: str = "") -> None:
        """Shape in the current pen -- the neutral counterpart to `circle`."""
        pts = [
            (x + rx * math.cos(t * math.pi / 8), y + ry * math.sin(t * math.pi / 8))
            for t in range(17)
        ]
        px = [self._px(*p) for p in pts]
        for a, b in itertools.pairwise(px):
            self._line(a, b, width=3)
        self._add("ellipse", label, x - rx, y - ry, x + rx, y + ry)
        if label:
            self.text(x, y, label, center=True, _log=False)

    def find(self, label: str) -> Element | None:
        """The most recent live element with this label. Exact match wins."""
        live = self.live
        for e in reversed(live):
            if e.label == label:
                return e
        needle = label.casefold()
        for e in reversed(live):
            if e.label and needle in e.label.casefold():
                return e
        return None

    def highlight(self, label: str, note: str = "", pad: float = 0.9) -> None:
        """Ring the element with this label, sized from its own bounding box.

        This is how pointing should work. `circle` makes the caller guess a
        radius for a box whose size it cannot see, and the guess is usually
        wrong -- too small and it cuts the label, too big and it swallows the
        neighbours.
        """
        target = self.find(label)
        if target is None:
            self._no_such(label, "highlight")
            return

        cx = (target.x0 + target.x1) / 2
        cy = (target.y0 + target.y1) / 2
        rx = (target.x1 - target.x0) / 2 + pad
        ry = (target.y1 - target.y0) / 2 + pad
        self._ring(cx, cy, rx, ry)
        self._add("highlight", label, cx - rx, cy - ry, cx + rx, cy + ry, transparent=True)
        if note:
            self.narration.append(note)

    def fill(self, label: str, tint: str = "yellow") -> None:
        """Tint a shape, addressed by its label. Redraws the outline on top."""
        target = self.find(label)
        if target is None:
            self._no_such(label, "fill")
            return
        if tint not in TINTS:
            self.errors.append(f"fill: unknown tint {tint!r}; try {sorted(TINTS)}")
            return

        a, b = self._px(target.x0, target.y0), self._px(target.x1, target.y1)
        self._draw.rectangle([a, b], fill=TINTS[tint])
        # The fill covers the outline, so put the shape back over it.
        corners = [
            (target.x0, target.y0),
            (target.x1, target.y0),
            (target.x1, target.y1),
            (target.x0, target.y1),
            (target.x0, target.y0),
        ]
        for p0, p1 in itertools.pairwise([self._px(*c) for c in corners]):
            self._rough_line(p0, p1, color=self.pen)
        if target.label:
            self.text(
                (target.x0 + target.x1) / 2,
                (target.y0 + target.y1) / 2,
                target.label,
                center=True,
                _log=False,
            )

    def strike(self, label: str, note: str = "") -> None:
        """Cross something out.

        The counterpart to drawing a model wrong on purpose: this is how the
        wrong model gets retired without erasing the evidence it was there.
        """
        target = self.find(label)
        if target is None:
            self._no_such(label, "strike")
            return
        pad = 0.3
        self._rough_line(
            self._px(target.x0 - pad, target.y0 - pad),
            self._px(target.x1 + pad, target.y1 + pad),
            width=4,
            color=ACCENT,
        )
        self._rough_line(
            self._px(target.x0 - pad, target.y1 + pad),
            self._px(target.x1 + pad, target.y0 - pad),
            width=4,
            color=ACCENT,
        )
        self._add("strike", label, target.x0, target.y0, target.x1, target.y1, transparent=True)
        if note:
            self.narration.append(note)

    def connect(self, src: str, dst: str, label: str = "") -> None:
        """Arrow between two labelled elements, routed as an elbow.

        The agent never computes these coordinates. It cannot see how wide a
        box rendered, and a straight diagonal between two boxes is what makes
        a diagram look sketched rather than drawn.
        """
        a, b = self.find(src), self.find(dst)
        if a is None:
            self._no_such(src, "connect")
            return
        if b is None:
            self._no_such(dst, "connect")
            return

        ax, ay = (a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2
        bx, by = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
        horizontal = abs(bx - ax) >= abs(by - ay)

        if horizontal:
            start = (a.x1 if bx > ax else a.x0, ay)
            end = (b.x0 if bx > ax else b.x1, by)
            mid = (start[0] + end[0]) / 2
            pts = [start, (mid, ay), (mid, by), end]
        else:
            start = (ax, a.y1 if by > ay else a.y0)
            end = (bx, b.y0 if by > ay else b.y1)
            mid = (start[1] + end[1]) / 2
            pts = [start, (ax, mid), (bx, mid), end]

        # Drop the dog-leg when the two are already aligned.
        pts = [
            p
            for i, p in enumerate(pts)
            if i == 0 or abs(p[0] - pts[i - 1][0]) > 0.05 or abs(p[1] - pts[i - 1][1]) > 0.05
        ]

        px = [self._px(*p) for p in pts]
        for p0, p1 in itertools.pairwise(px):
            self._line(p0, p1, color=self.pen)
        self._arrow_head(px[-2], px[-1])

        # A connector that ploughs through a third element is unreadable, and
        # the agent cannot see it happen. Name the obstacle.
        for other in self.live:
            if other is a or other is b or not other.solid:
                continue
            if any(self._segment_hits(p0, p1, other) for p0, p1 in itertools.pairwise(pts)):
                self.notes.append(
                    f"connect {src!r} -> {dst!r} passes through {other.describe()}. "
                    f"Move one of them, or route around it with `arrow`."
                )

        self._add(
            "connect",
            label,
            min(p[0] for p in pts),
            min(p[1] for p in pts),
            max(p[0] for p in pts),
            max(p[1] for p in pts),
            transparent=True,
            path=list(pts),
        )
        if label:
            lx, ly = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
            self.text(
                lx,
                self._dodge_label(lx, ly - 0.7, label)[1],
                label,
                size=18,
                center=True,
                _log=False,
            )

    @staticmethod
    def _segment_hits(p0, p1, el: Element) -> bool:
        """Does the segment p0-p1 cross this element's box? Sampled, not exact."""
        steps = max(2, int(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) * 2))
        for i in range(steps + 1):
            t = i / steps
            x = p0[0] + (p1[0] - p0[0]) * t
            y = p0[1] + (p1[1] - p0[1]) * t
            if el.x0 < x < el.x1 and el.y0 < y < el.y1:
                return True
        return False

    def _arrow_head(self, a, b) -> None:
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        for sign in (+1, -1):
            self._rough_line(
                b,
                (b[0] - 14 * math.cos(ang + sign * 0.45), b[1] - 14 * math.sin(ang + sign * 0.45)),
                width=2,
                color=self.pen,
            )

    def _no_such(self, label: str, op: str) -> None:
        known = [e.label for e in self.live if e.label]
        self.errors.append(
            f"{op}: nothing labelled {label!r} on the board. Labelled elements: {known or 'none'}"
        )

    def _ring(self, cx, cy, rx, ry, color=ACCENT) -> None:
        pts = [
            (cx + rx * math.cos(t * math.pi / 8), cy + ry * math.sin(t * math.pi / 8))
            for t in range(17)
        ]
        px = [self._px(*p) for p in pts]
        for a, b in itertools.pairwise(px):
            self._rough_line(a, b, width=3, color=color)

    def circle(self, x, y, r, color=ACCENT, note: str = "") -> None:
        pts = [
            (x + r * math.cos(t * math.pi / 8) * 1.12, y + r * math.sin(t * math.pi / 8) * 0.9)
            for t in range(17)
        ]
        px = [self._px(*p) for p in pts]
        for a, b in itertools.pairwise(px):
            self._rough_line(a, b, width=3, color=color)
        ring = Element("circle", note, x - r * 1.12, y - r * 0.9, x + r * 1.12, y + r * 0.9)
        for other in self.live:
            if not other.solid or not ring.overlaps(other):
                continue
            encloses = (
                ring.x0 <= other.x0
                and ring.x1 >= other.x1
                and ring.y0 <= other.y0
                and ring.y1 >= other.y1
            )
            if not encloses:
                self.notes.append(
                    f"circle ({x:g},{y:g}) r={r:g} cuts across {other.describe()} "
                    f"instead of enclosing it -- use `highlight {other.label!r}` "
                    f"and the ring will be sized for you"
                )
        self._add(
            "circle", note, x - r * 1.12, y - r * 0.9, x + r * 1.12, y + r * 0.9, transparent=True
        )
        if note:
            self.narration.append(note)

    def icon(self, name: str, x: float, y: float, size: float = 4.0, label: str = "") -> None:
        """Draw a pictogram centred on (x, y), `size` grid units wide."""
        name = name.lower()
        if name not in ICONS:
            self.errors.append(f"icon: unknown {name!r}; try {' '.join(ICONS)}")
            return

        r = size / 2
        h = r * 1.15  # icons are slightly taller than wide
        getattr(self, f"_icon_{name}")(x, y, r, h)
        self._add("icon", label or name, x - r, y - h, x + r, y + h + (1.2 if label else 0))
        if label:
            self.text(x, y + h + 0.7, label, size=18, center=True, _log=False)

    def _ellipse_pts(self, cx, cy, rx, ry, start=0, end=16):
        return [
            (cx + rx * math.cos(t * math.pi / 8), cy + ry * math.sin(t * math.pi / 8))
            for t in range(start, end + 1)
        ]

    def _poly(self, pts, close=False, width=3) -> None:
        path = [*pts, pts[0]] if close else pts
        for a, b in itertools.pairwise([self._px(*p) for p in path]):
            self._rough_line(a, b, width=width, color=self.pen)

    def _icon_db(self, x, y, r, h) -> None:
        lid = h * 0.28
        self._poly(self._ellipse_pts(x, y - h + lid, r, lid), close=True)
        self._poly([(x - r, y - h + lid), (x - r, y + h - lid)])
        self._poly([(x + r, y - h + lid), (x + r, y + h - lid)])
        self._poly(self._ellipse_pts(x, y + h - lid, r, lid, 0, 8))  # front lip only
        self._poly(self._ellipse_pts(x, y, r, lid, 0, 8))  # a band

    def _icon_server(self, x, y, r, h) -> None:
        for i in range(3):
            top = y - h + i * (2 * h / 3)
            self._poly(
                [
                    (x - r, top),
                    (x + r, top),
                    (x + r, top + 2 * h / 3 - 0.15),
                    (x - r, top + 2 * h / 3 - 0.15),
                ],
                close=True,
            )
            self._poly([(x - r * 0.6, top + h / 3), (x + r * 0.1, top + h / 3)], width=2)

    def _icon_user(self, x, y, r, h) -> None:
        head = r * 0.46
        self._poly(self._ellipse_pts(x, y - h + head * 1.3, head, head), close=True)
        self._poly(self._ellipse_pts(x, y + h, r, h * 0.8, 8, 16))  # shoulders

    def _icon_cloud(self, x, y, r, h) -> None:
        """The union outline of three overlapping bumps.

        Drawing each bump whole leaves the hidden interior arcs visible and the
        icon reads as a row of arches. Keeping only the points that lie outside
        every other bump traces the silhouette instead.
        """
        base = y + h * 0.5
        bumps = [
            (x - r * 0.48, r * 0.54, h * 0.6),
            (x + r * 0.06, r * 0.56, h * 0.92),
            (x + r * 0.58, r * 0.46, h * 0.58),
        ]

        def inside(px_, py_, skip):
            for j, (cx, rx, ry) in enumerate(bumps):
                if j == skip:
                    continue
                if ((px_ - cx) / rx) ** 2 + ((py_ - base) / ry) ** 2 < 0.97:
                    return True
            return False

        for i, (cx, rx, ry) in enumerate(bumps):
            run: list[tuple[float, float]] = []
            for k in range(41):
                ang = math.pi + math.pi * k / 40
                pt = (cx + rx * math.cos(ang), base + ry * math.sin(ang))
                if inside(pt[0], pt[1], i):
                    if len(run) > 1:
                        self._poly(run)
                    run = []
                else:
                    run.append(pt)
            if len(run) > 1:
                self._poly(run)
        self._poly([(x - r, base), (x + r, base)])

    def _icon_queue(self, x, y, r, h) -> None:
        self._poly([(x - r, y - h * 0.55), (x + r, y - h * 0.55)])
        self._poly([(x - r, y + h * 0.55), (x + r, y + h * 0.55)])
        box_w = r * 0.42
        for i in range(3):
            left = x - r * 0.85 + i * (box_w + r * 0.26)
            self._poly(
                [
                    (left, y - h * 0.3),
                    (left + box_w, y - h * 0.3),
                    (left + box_w, y + h * 0.3),
                    (left, y + h * 0.3),
                ],
                close=True,
                width=2,
            )

    def _icon_file(self, x, y, r, h) -> None:
        fold = r * 0.5
        self._poly(
            [
                (x - r, y - h),
                (x + r - fold, y - h),
                (x + r, y - h + fold),
                (x + r, y + h),
                (x - r, y + h),
            ],
            close=True,
        )
        self._poly([(x + r - fold, y - h), (x + r - fold, y - h + fold), (x + r, y - h + fold)])

    def _icon_clock(self, x, y, r, h) -> None:
        self._poly(self._ellipse_pts(x, y, r, h * 0.88), close=True)
        self._poly([(x, y), (x, y - h * 0.5)], width=2)
        self._poly([(x, y), (x + r * 0.6, y + h * 0.18)], width=2)

    def _icon_lock(self, x, y, r, h) -> None:
        body_top = y - h * 0.05
        self._poly(
            [(x - r, body_top), (x + r, body_top), (x + r, y + h), (x - r, y + h)], close=True
        )
        sr = r * 0.56
        shackle = [
            (x - sr, body_top),
            (x - sr, y - h * 0.45),
            *[
                (
                    x + sr * math.cos(math.pi + math.pi * i / 10),
                    y - h * 0.45 + sr * 0.85 * math.sin(math.pi + math.pi * i / 10),
                )
                for i in range(11)
            ],
            (x + sr, y - h * 0.45),
            (x + sr, body_top),
        ]
        self._poly(shackle, width=3)
        self._poly([(x, y + h * 0.3), (x, y + h * 0.62)], width=3)

    def _icon_globe(self, x, y, r, h) -> None:
        self._poly(self._ellipse_pts(x, y, r, h * 0.88), close=True)
        self._poly(self._ellipse_pts(x, y, r * 0.42, h * 0.88), close=True)
        self._poly([(x - r, y), (x + r, y)], width=2)

    def _icon_gear(self, x, y, r, h) -> None:
        self._poly(self._ellipse_pts(x, y, r * 0.72, h * 0.66), close=True)
        self._poly(self._ellipse_pts(x, y, r * 0.3, h * 0.28), close=True)
        for i in range(8):
            ang = i * math.pi / 4
            self._poly(
                [
                    (x + r * 0.72 * math.cos(ang), y + h * 0.66 * math.sin(ang)),
                    (x + r * 1.0 * math.cos(ang), y + h * 0.92 * math.sin(ang)),
                ],
                width=2,
            )

    def _icon_warning(self, x, y, r, h) -> None:
        self._poly([(x, y - h), (x + r, y + h * 0.8), (x - r, y + h * 0.8)], close=True)
        self._poly([(x, y - h * 0.35), (x, y + h * 0.25)], width=3)
        self._poly([(x, y + h * 0.5), (x, y + h * 0.58)], width=3)

    def _icon_check(self, x, y, r, h) -> None:
        self._poly(
            [(x - r * 0.8, y), (x - r * 0.15, y + h * 0.6), (x + r * 0.9, y - h * 0.7)], width=4
        )

    def _icon_cross(self, x, y, r, h) -> None:
        self._poly([(x - r * 0.75, y - h * 0.7), (x + r * 0.75, y + h * 0.7)], width=4)
        self._poly([(x - r * 0.75, y + h * 0.7), (x + r * 0.75, y - h * 0.7)], width=4)

    def _icon_box(self, x, y, r, h) -> None:
        self._poly([(x - r, y - h), (x + r, y - h), (x + r, y + h), (x - r, y + h)], close=True)

    def group(self, x, y, w, h, label: str = "") -> None:
        """A dashed container with a label tab. Structure without a hard edge."""
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        for a, b in itertools.pairwise([self._px(*c) for c in corners]):
            self._dashed_line(a, b, width=2, color=PENS["grey"])
        if label:
            # A tab sitting on the top edge, so the label is clearly the group's.
            tw = len(label) * CHAR_W + 0.8
            self._draw.rectangle(
                [self._px(x + 0.6, y - 0.55), self._px(x + 0.6 + tw, y + 0.55)], fill=PAPER
            )
            self.text(x + 1.0, y, label, size=17, _log=False)
        # Transparent: a group is meant to contain things, not collide with them.
        self._add("group", label, x, y, x + w, y + h, transparent=True)

    def note(self, x, y, w, text: str) -> None:
        """A sticky note. For an aside that is not part of the diagram."""
        lines = self._wrap(text, max(6, int(w / CHAR_W)))
        h = len(lines) * LINE_H + 0.9
        a, b = self._px(x, y), self._px(x + w, y + h)
        self._draw.rectangle([(a[0] + 5, a[1] + 5), (b[0] + 5, b[1] + 5)], fill=SHADOW)
        self._draw.rectangle([a, b], fill=TINTS["yellow"])
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        for p0, p1 in itertools.pairwise([self._px(*c) for c in corners]):
            self._rough_line(p0, p1, width=2, color=PENS["grey"])
        for i, line in enumerate(lines):
            self.text(x + 0.5, y + 0.7 + i * LINE_H, line, size=17, _log=False)
        self._add("note", text[:24], x, y, x + w, y + h)

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if len(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def brace(self, x, y, h, label: str = "") -> None:
        """A vertical brace grouping a range, with the label outside it."""
        mid, d = y + h / 2, 0.55
        pts = [
            (x, y),
            (x + d, y + 0.35),
            (x + d, mid - 0.6),
            (x + d * 1.8, mid),
            (x + d, mid + 0.6),
            (x + d, y + h - 0.35),
            (x, y + h),
        ]
        for a, b in itertools.pairwise([self._px(*p) for p in pts]):
            self._rough_line(a, b, width=3, color=self.pen)
        if label:
            self.text(x + d * 2.4, mid, label, size=18, _log=False)
        self._add("brace", label, x, y, x + d * 2, y + h, transparent=True)

    def erase_preview(self, x0, y0, x1, y1) -> list[Element]:
        """Which live elements a given erase rectangle would touch. No side effects."""
        region = Element("region", "", x0, y0, x1, y1)
        return [e for e in self.live if region.overlaps(e)]

    def erase(self, x, y, r=None, w=None, h=None, note: str = "") -> None:
        """Square form `erase x,y,r`, or rectangle form `erase x,y,w,h`.

        Records every element the rectangle clipped, so an agent can see the
        collateral damage instead of discovering it in the next screenshot.
        """
        if w is not None and h is not None:
            x0, y0, x1, y1 = x, y, x + w, y + h
        else:
            x0, y0, x1, y1 = x - r, y - r, x + r, y + r

        hit = self.erase_preview(x0, y0, x1, y1)
        self._draw.rectangle([self._px(x0, y0), self._px(x1, y1)], fill=PAPER)

        for e in hit:
            if e.clipped:
                continue  # already reported damaged; re-erasing it says nothing new
            fully = x0 <= e.x0 and x1 >= e.x1 and y0 <= e.y0 and y1 >= e.y1
            self.wiped.append(f"{'removed' if fully else 'CLIPPED'} {e.describe()}")
            if fully:
                self.elements.remove(e)
            else:
                e.clipped = True  # damaged; expects a redraw, so stop guarding it

        self._add("erase", "", x0, y0, x1, y1, transparent=True)
        if note:
            self.narration.append(note)

    def _grow_to(self, bottom: float) -> bool:
        """Extend the surface downward so `bottom` fits. False if capped."""
        needed = math.ceil(bottom / self.grid_h)
        if needed <= self.panels:
            return True
        if needed > self.max_panels:
            return False
        taller = Image.new("RGB", (self.grid_w * CELL, needed * self.grid_h * CELL), PAPER)
        taller.paste(self._img, (0, 0))
        self._img = taller
        self._draw = ImageDraw.Draw(self._img)
        self.panels = needed
        return True

    def pan(self, y: float) -> None:
        """Move the visible window so drawing can continue on fresh board.

        The alternative is erasing to make room, which destroys the lesson as
        it goes and leaves nothing to export.
        """
        if y < 0:
            self.errors.append("pan: y cannot be negative")
            return
        if not self._grow_to(y + self.grid_h):
            self.errors.append(
                f"pan {y:g}: the board is capped at {self.max_panels} screens "
                f"({self.max_panels * self.grid_h} rows). Finish the lesson here."
            )
            return
        self.view_y = y
        self._add("pan", "", 0, y, 0.1, y + 0.1, transparent=True)

    def set_board(self, w: float, h: float) -> None:
        """Resize the board. Only valid before anything is drawn.

        Lets a lesson declare the stage it was written for, instead of the
        reader having to know a command-line flag.
        """
        if self.elements:
            raise ValueError("board must come before anything is drawn")
        if not (8 <= w <= 200 and 6 <= h <= 200):
            raise ValueError(f"board {w:g}x{h:g} is out of range (8..200 by 6..200)")
        self.grid_w, self.grid_h = int(w), int(h)
        self.panels = 1
        self.view_y = 0.0  # top row of the visible window
        self._img = Image.new("RGB", (self.grid_w * CELL, self.grid_h * CELL), PAPER)
        self._draw = ImageDraw.Draw(self._img)

    def new_page(self, title: str = "") -> None:
        """Wipe to a clean page. Elements stay in history, tagged to the old page."""
        self._img.paste(PAPER, (0, 0, self._img.size[0], self._img.size[1]))
        self.page += 1
        if title:
            self.text(1.5, 1.5, title)

    @property
    def collisions(self) -> list[str]:
        """Solid elements currently overlapping, plus any advisory notes.

        Derived rather than accumulated. A collision the agent has since
        repaired -- by erasing the offender and redrawing it -- disappears from
        the report, instead of standing forever and sending the agent in
        circles trying to fix something that is already fixed.
        """
        live = [e for e in self.live if e.solid]
        found = [
            f"{a.describe()} overlaps {b.describe()}"
            for i, a in enumerate(live)
            for b in live[i + 1 :]
            if a.overlaps(b)
        ]
        return found + self.notes

    @property
    def occupancy(self) -> float:
        """Roughly how much of the page carries ink, 0..1 -- a fullness signal.

        Counting bounding boxes was useless: one diagonal `stroke 0,0 64,36`
        has a box covering the whole board and reported 100% full, and the
        example lesson's input mesh pushed it to 67% on a mostly-white page.
        Solid shapes occupy their box; freehand and arrows occupy the cells
        their path actually passes through.
        """
        cells: set[tuple[int, int]] = set()
        top, bottom = self.view_y, self.view_y + self.grid_h

        def fill(x0, y0, x1, y1):
            for gx in range(max(0, int(x0)), min(self.grid_w, int(x1) + 1)):
                for gy in range(max(int(top), int(y0)), min(int(bottom), int(y1) + 1)):
                    cells.add((gx, gy))

        for e in self.live:
            if e.y1 < top or e.y0 > bottom:
                continue
            if e.kind in ("erase", "circle", "highlight", "pan"):
                continue  # erasures remove ink; rings sit over what is there
            if e.kind in ("stroke", "arrow"):
                # Walk the diagonal of the box rather than filling it.
                steps = max(1, int(max(e.x1 - e.x0, e.y1 - e.y0) * 2))
                for i in range(steps + 1):
                    t = i / steps
                    fill(
                        e.x0 + (e.x1 - e.x0) * t,
                        e.y0 + (e.y1 - e.y0) * t,
                        e.x0 + (e.x1 - e.x0) * t,
                        e.y0 + (e.y1 - e.y0) * t,
                    )
            else:
                fill(e.x0, e.y0, e.x1, e.y1)
        return len(cells) / (self.grid_w * self.grid_h)

    def say(self, s: str) -> None:
        self.narration.append(s)

    # --- op dispatch --------------------------------------------------------
    def apply(self, line: str) -> OpResult:
        line = line.strip()
        if not line or line.startswith("#"):
            return OpResult(True, "skip")
        parts = tokenize(line)
        if not parts:
            return OpResult(True, "skip")

        cmd, args = parts[0].lower(), parts[1:]

        def take(n: int, start: int = 0) -> tuple[list[float], int]:
            """Pull the first n numbers off args, accepting "3 4" or "3,4" equally.

            Returns the numbers plus how many arg tokens they consumed, so any
            trailing quoted label can still be recovered.
            """
            vals: list[float] = []
            used = 0
            for tok in args[start:]:
                if len(vals) >= n:
                    break
                used += 1
                for piece in tok.split(","):
                    piece = piece.strip()
                    if piece:
                        vals.append(float(piece))
            if len(vals) < n:
                raise ValueError(f"expected {n} numbers, got {len(vals)}")
            return vals[:n], used

        def label_after(used: int) -> str:
            return " ".join(args[used:]).strip()

        try:
            if cmd == "stroke":
                pts = [tuple(float(v) for v in p.split(",")) for p in args if "," in p]
                if len(pts) < 2:
                    return OpResult(False, f"stroke needs >=2 points. {USAGE['stroke']}")
                self.stroke(pts)
            elif cmd == "box":
                (x, y, w, h), used = take(4)
                self.box(x, y, w, h, label_after(used))
            elif cmd == "arrow":
                (x1, y1, x2, y2), used = take(4)
                self.arrow(x1, y1, x2, y2, label_after(used))
            elif cmd == "text":
                (x, y), used = take(2)
                self.text(x, y, label_after(used))
            elif cmd == "highlight":
                self.highlight(args[0] if args else "", label_after(1))
            elif cmd == "ellipse":
                (x, y, rx, ry), used = take(4)
                self.ellipse(x, y, rx, ry, label_after(used))
            elif cmd == "circle":
                (x, y, r), used = take(3)
                self.circle(x, y, r, note=label_after(used))
            elif cmd == "erase":
                # Three numbers is a square radius; four is a rectangle.
                try:
                    (x, y, w, h), used = take(4)
                    self.erase(x, y, w=w, h=h, note=label_after(used))
                except ValueError:
                    (x, y, r), used = take(3)
                    self.erase(x, y, r, note=label_after(used))
            elif cmd == "icon":
                name = args[0] if args else ""
                (x, y), used = take(2, start=1)
                rest = args[used + 1 :]
                size = 4.0
                if rest and _isnum(rest[0]):
                    size = float(rest[0])
                    rest = rest[1:]
                self.icon(name, x, y, size, " ".join(rest).strip())
            elif cmd == "connect":
                self.connect(
                    args[0] if args else "",
                    args[1] if len(args) > 1 else "",
                    " ".join(args[2:]).strip(),
                )
            elif cmd == "fill":
                self.fill(args[0] if args else "", (args[1] if len(args) > 1 else "yellow").lower())
            elif cmd == "strike":
                self.strike(args[0] if args else "", " ".join(args[1:]).strip())
            elif cmd == "group":
                (x, y, w, h), used = take(4)
                self.group(x, y, w, h, label_after(used))
            elif cmd == "note":
                (x, y, w), used = take(3)
                self.note(x, y, w, label_after(used))
            elif cmd == "brace":
                (x, y, h), used = take(3)
                self.brace(x, y, h, label_after(used))
            elif cmd == "pan":
                (y,), _ = take(1)
                self.pan(y)
            elif cmd == "dash":
                self.dashed = (args[0].lower() if args else "on") not in ("off", "false", "0")
            elif cmd == "pen":
                name = (args[0] if args else "ink").lower()
                if name not in PENS:
                    return OpResult(False, f"unknown pen {name!r}; try {sorted(PENS)}")
                self.pen, self.pen_name = PENS[name], name
            elif cmd == "board":
                (w, h), _ = take(2)
                self.set_board(w, h)
            elif cmd == "page":
                self.new_page(label_after(0))
            elif cmd == "say":
                self.say(label_after(0))
            else:
                return OpResult(
                    False,
                    f"unknown command {cmd!r}. Known commands: {', '.join(sorted(USAGE))}",
                )
        except (ValueError, IndexError) as exc:
            hint = USAGE.get(cmd)
            detail = f"bad args for {cmd!r}: {exc}"
            return OpResult(False, f"{detail}. Correct form: {hint}" if hint else detail)
        return OpResult(True, cmd)

    def run(self, script: str) -> list[str]:
        """Apply a whole script, collecting errors rather than stopping.

        `accepted` is the authoritative list of ops that actually drew. The
        caller must not try to work that out from the error text: `apply`
        numbers every source line while a caller counting "real" ops skips
        blanks and comments, and the two disagree the moment a lesson has
        either -- which silently drops a good op and broadcasts a bad one.
        """
        self.accepted = []
        for i, line in enumerate(script.splitlines(), 1):
            res = self.apply(line)
            if not res.ok:
                self.errors.append(f"line {i}: {res.detail}  <- {line.strip()!r}")
            elif res.detail != "skip":
                self.accepted.append(line)
        return self.errors

    def render(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._img.save(path)
        return path

    def minimap(self, cols: int = 56, rows: int = 14) -> str:
        """A coarse picture of the current screen, in text.

        The report could say "no collisions" about a board where everything is
        crammed into one band and two thirds is blank, because nothing it
        measured was wrong. This is the cheap channel for "does the layout
        actually look like anything" -- the agent cannot see the canvas, and
        an image every turn is the expensive answer to the same question.
        """
        top, bottom = self.view_y, self.view_y + self.grid_h
        grid = [[" "] * cols for _ in range(rows)]

        def put(cx: int, cy: int, glyph: str) -> None:
            if not (0 <= cx < cols and 0 <= cy < rows):
                return
            cur = grid[cy][cx]
            if cur == " " or MAP_RANK.index(glyph) >= MAP_RANK.index(cur):
                grid[cy][cx] = glyph

        for e in self.live:
            glyph = MAP_GLYPH.get(e.kind, "#")
            if glyph == " " or e.y1 < top or e.y0 > bottom:
                continue
            if e.path:
                for a, b in itertools.pairwise(e.path):
                    steps = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) * 2))
                    for i in range(steps + 1):
                        f = i / steps
                        put(
                            int((a[0] + (b[0] - a[0]) * f) / self.grid_w * cols),
                            int((a[1] + (b[1] - a[1]) * f - top) / self.grid_h * rows),
                            glyph,
                        )
                continue

            x0 = int(e.x0 / self.grid_w * cols)
            x1 = max(x0, int(e.x1 / self.grid_w * cols) - 1)
            y0 = int((e.y0 - top) / self.grid_h * rows)
            y1 = max(y0, int((e.y1 - top) / self.grid_h * rows) - 1)
            for cy in range(y0, y1 + 1):
                for cx in range(x0, x1 + 1):
                    # Outline only for big shapes, so they do not become blocks.
                    edge = cy in (y0, y1) or cx in (x0, x1)
                    if glyph in "#@" and not edge and (x1 - x0) > 1 and (y1 - y0) > 1:
                        continue
                    put(cx, cy, glyph)

        legend = "  # box  @ icon  - text  > arrow  ~ stroke  O ring  : group  N note"
        body = "\n".join("  |" + "".join(r) + "|" for r in grid)
        return (
            f"Layout of rows {top:g}-{bottom:g} "
            f"(each cell is ~{self.grid_w / cols:.1f}x{self.grid_h / rows:.1f} units):\n"
            + body
            + "\n"
            + legend
        )

    def state_summary(self, verbose: bool = True) -> str:
        """What the agent reads back after a scene.

        Leads with the problems. An agent that has to scan a list of 60
        elements to notice an overlap will not notice the overlap.
        """
        live = self.live
        out = [
            (
                f"Screen rows {self.view_y:g}-{self.view_y + self.grid_h:g} "
                f"of a {self.grid_w}x{self.board_h} board. "
                f"{len(live)} elements, this screen {self.occupancy:.0%} full."
            ),
        ]
        if self.occupancy > 0.55:
            out.append(
                "  NOTE: this screen is filling up. `pan "
                f"{self.view_y + self.grid_h:g}` moves to fresh board below -- "
                "prefer that to erasing, which destroys the lesson as it goes."
            )

        if self.errors:
            out.append(f"ERRORS -- {len(self.errors)} ops did not draw:")
            out += [f"  ! {e}" for e in self.errors]
        if self.collisions:
            out.append(f"COLLISIONS -- {len(self.collisions)} overlapping elements:")
            out += [f"  x {c}" for c in self.collisions]
        if self.wiped:
            out.append("ERASED:")
            out += [f"  - {w}" for w in self.wiped]
        if not (self.errors or self.collisions):
            out.append("No errors, no collisions.")

        out.append("")
        out.append(self.minimap())

        if verbose:
            out.append("Elements:")
            out += [f"  - {e.describe()}" for e in live]
        return "\n".join(out)

    def clear_feedback(self) -> None:
        """Reset the per-scene problem lists, keeping the drawing itself.

        Collisions are derived from the board, so there is nothing to clear --
        they resolve themselves when the overlap is actually fixed.
        """
        self.errors.clear()
        self.notes.clear()
        self.wiped.clear()
