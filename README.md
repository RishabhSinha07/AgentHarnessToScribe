# Scribe

A whiteboard an agent can draw on **while it teaches**.

Ask an LLM for a diagram today and you get a finished picture — or, if the
syntax breaks halfway, nothing at all. Mermaid and SVG describe a final state.
They have no way to express *"this box exists now, that arrow comes later"*,
and no way to point at something while explaining it.

Scribe is the missing piece: a stateful canvas, a small drawing language, and an
MCP server so any agent can draw on it live, narrate as it goes, point at what
it means, and erase what it got wrong.

![A hand-drawn neural network diagram with input, hidden and output layers, a red circle highlighting one unit, and a learning loop along the bottom](docs/preview.png)

*Drawn by Claude, one command at a time, while explaining backpropagation.*

**[▶ Watch it draw itself](https://claude.ai/code/artifact/612cf43f-a1f2-4040-9fa8-aa0daec5a375)** — the same lesson played back stroke
by stroke, narrated aloud.

---

## Why a canvas instead of a diagram format

Two things a whiteboard does that no static format can:

**Erasing.** Half of teaching is correcting — showing a wrong model and fixing
it, revealing in stages, clearing space to go deeper. `erase` is a first-class
op, and the canvas reports exactly what it destroyed.

**Pointing.** Teaching from a diagram is mostly deixis: "*this* queue feeds
*that* worker." `circle` draws a red loop around something and says what it is
while drawing it. Nothing in Mermaid can gesture.

---

## Quickstart

```bash
uv sync
uv run scribe-serve examples/neural_network.ops
```

Open the URL it prints. The lesson draws itself.

### As an MCP server

```bash
claude mcp add scribe -- uv run --directory /path/to/scribe scribe-mcp
```

Then ask Claude to teach you something at the whiteboard. It calls
`scribe_open`, you open the URL, and it draws while it talks.

---

## The drawing language

One command per line. Coordinates are a 40 × 30 grid — coarse on purpose, since
models reason about grid cells far better than pixels.

```
text    x,y <words>            write words
box     x,y,w,h <label>        rectangle, label centred
ellipse x,y,rx,ry <label>      oval in the current pen
arrow   x1,y1 x2,y2 <label>    arrow, label auto-dodges collisions
circle  x,y,r <spoken note>    RED highlight — the pointer
stroke  x1,y1 x2,y2 ...        freehand polyline
erase   x,y,w,h <spoken note>  erase a rectangle
pen     ink|red|blue|green|grey
page    <title>                wipe to a clean page
say     <text>                 narration, spoken at this moment
```

A lesson reads as draw, speak, point, speak — and that ordering *is* the
teaching:

```
box    5,7 3,2.5 "x1"
box    17,5 3,2.5 "h1"
stroke 8,8.25 17,6.25
say    "Every input connects to every hidden unit. That's the mesh."
circle 18.5,11.25 3 "Circling h2 here — this is the one we'll open up."
```

The note on `circle` and `erase` is spoken aloud as the gesture happens, so the
pointer explains itself.

---

## MCP tools

| Tool | Does |
|---|---|
| `scribe_reference` | The command reference. Read it first. |
| `scribe_open(topic)` | Opens a lesson, starts the viewer, returns a URL |
| `scribe_draw(ops)` | Draws. Returns errors, collisions, occupancy |
| `scribe_look()` | A PNG of the board, for the agent's own eyes |
| `scribe_erase_preview(x,y,w,h)` | What an erase would destroy, before committing |
| `scribe_status()` | Topic, ops drawn, viewers connected |

`scribe_draw` returns collisions **as text on every call**. That is the central
design choice: the agent is told about an overlap cheaply, in words, and only
spends an image on `scribe_look` when the text says something is wrong.

---

## How it fits together

```
agent ──MCP──> scribe.mcp ──> Session ──> Canvas      (state, collisions, PNG)
                                 │
                                 └──WebSocket──> viewer   (draws as ops arrive)
```

One process owns everything. `scribe.live` runs the web server on a background
thread so MCP keeps stdio to itself.

| Module | Role |
|---|---|
| `canvas.py` | Drawing surface, op parser, collision detection, PIL renderer |
| `session.py` | One lesson: canvas + op log + connected viewers |
| `live.py` | Starlette server; streams ops over WebSocket |
| `mcp.py` | MCP adapter |
| `viewer/index.html` | The board: progressive drawing, voice, scrubbing |
| `cli.py` | `scribe-serve`, for running without an MCP client |

---

## What the canvas tells the agent

Every `scribe_draw` returns a report that leads with the problems:

```
Page 0 of a 40x30 grid. 28 elements, 38% full.
COLLISIONS -- 1 overlapping elements:
  x text (16.5,1.98)-(19.6,3.03) 'Hidden' overlaps text (11,0.98)-(25,2.03) 'How a neural network learns'
```

That is a real defect from the first run of the example lesson, caught
automatically on a re-run. Highlights and freehand are exempt from collision
checks — they are *meant* to cross things.

## Status

Early. The canvas, the language, the viewer and the MCP server work. The
original goal — training a small local model to draw and teach this way — is
next; the op-streams this produces are the training data.

## License

MIT
