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
    # Highlights and freehand are meant to cross other things; they never
    # count as collisions and nothing collides with them.
    transparent: bool = False
    # Partially erased. The pixels are damaged and the agent is expected to
    # redraw over them, so a clipped element must not report that redraw as
    # a collision with itself.
    clipped: bool = False

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
    grid_w: int = GRID_W
    grid_h: int = GRID_H
    _img: Image.Image = field(init=False)
    _draw: ImageDraw.ImageDraw = field(init=False)
    _rng: random.Random = field(init=False)
    narration: list[str] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # advisory, per scene
    wiped: list[str] = field(default_factory=list)  # what each erase took out
    page: int = 0
    pen: tuple[int, int, int] = INK

    def __post_init__(self) -> None:
        self._img = Image.new("RGB", (self.grid_w * CELL, self.grid_h * CELL), PAPER)
        self._draw = ImageDraw.Draw(self._img)
        self._rng = random.Random(self.seed)

    # --- coordinate helpers -------------------------------------------------
    def _px(self, x: float, y: float) -> tuple[float, float]:
        return x * CELL, y * CELL

    def _jitter(self, p: tuple[float, float], amt: float = 1.6) -> tuple[float, float]:
        return (p[0] + self._rng.uniform(-amt, amt), p[1] + self._rng.uniform(-amt, amt))

    def _rough_line(self, a, b, width=3, color=INK) -> None:
        """Draw a line as two slightly-off passes, the way a pen doubles back."""
        for _ in range(2):
            self._draw.line([self._jitter(a), self._jitter(b)], fill=color, width=width)

    # --- bookkeeping --------------------------------------------------------
    def _add(self, kind, label, x0, y0, x1, y1, transparent=False) -> Element:
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
        )

        # Drawing past the edge used to be silent -- the op "succeeded", the ink
        # landed outside the image, and the report said all was well. An agent
        # reasoning about a 64x36 board will write y=40 sooner or later.
        if el.x1 < 0 or el.y1 < 0 or el.x0 > self.grid_w or el.y0 > self.grid_h:
            self.errors.append(
                f"{kind} at ({el.x0:g},{el.y0:g})-({el.x1:g},{el.y1:g}) is entirely "
                f"off the {self.grid_w}x{self.grid_h} board -- nothing was drawn"
            )
        elif kind != "erase" and (
            el.x0 < 0 or el.y0 < 0 or el.x1 > self.grid_w or el.y1 > self.grid_h
        ):
            self.notes.append(
                f"{el.describe()} runs off the {self.grid_w}x{self.grid_h} board "
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
        self._add("stroke", "", min(xs), min(ys), max(xs), max(ys), transparent=True)

    def box(self, x, y, w, h, label: str = "") -> None:
        c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        px = [self._px(*p) for p in c]
        for a, b in itertools.pairwise(px):
            self._rough_line(a, b, color=self.pen)
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
        self._add("arrow", label, x1, y1, x2, y2, transparent=True)
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
            self._rough_line(a, b, width=3, color=self.pen)
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
            known = [e.label for e in self.live if e.label]
            self.errors.append(
                f"highlight: nothing labelled {label!r} on the board. "
                f"Labelled elements: {known or 'none'}"
            )
            return

        cx = (target.x0 + target.x1) / 2
        cy = (target.y0 + target.y1) / 2
        rx = (target.x1 - target.x0) / 2 + pad
        ry = (target.y1 - target.y0) / 2 + pad
        self._ring(cx, cy, rx, ry)
        self._add("highlight", label, cx - rx, cy - ry, cx + rx, cy + ry, transparent=True)
        if note:
            self.narration.append(note)

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

        def fill(x0, y0, x1, y1):
            for gx in range(max(0, int(x0)), min(self.grid_w, int(x1) + 1)):
                for gy in range(max(0, int(y0)), min(self.grid_h, int(y1) + 1)):
                    cells.add((gx, gy))

        for e in self.live:
            if e.kind in ("erase", "circle", "highlight"):
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

        def take(n: int) -> tuple[list[float], int]:
            """Pull the first n numbers off args, accepting "3 4" or "3,4" equally.

            Returns the numbers plus how many arg tokens they consumed, so any
            trailing quoted label can still be recovered.
            """
            vals: list[float] = []
            used = 0
            for tok in args:
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
            elif cmd == "pen":
                name = (args[0] if args else "ink").lower()
                if name not in PENS:
                    return OpResult(False, f"unknown pen {name!r}; try {sorted(PENS)}")
                self.pen = PENS[name]
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
        """Apply a whole script, collecting errors rather than stopping."""
        for i, line in enumerate(script.splitlines(), 1):
            res = self.apply(line)
            if not res.ok:
                self.errors.append(f"line {i}: {res.detail}  <- {line.strip()!r}")
        return self.errors

    def render(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._img.save(path)
        return path

    def state_summary(self, verbose: bool = True) -> str:
        """What the agent reads back after a scene.

        Leads with the problems. An agent that has to scan a list of 60
        elements to notice an overlap will not notice the overlap.
        """
        live = self.live
        out = [
            (
                f"Page {self.page} of a {self.grid_w}x{self.grid_h} grid. "
                f"{len(live)} elements, {self.occupancy:.0%} full."
            ),
        ]
        if self.occupancy > 0.55:
            out.append("  NOTE: page is filling up. Consider `page` or erasing a finished region.")

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
