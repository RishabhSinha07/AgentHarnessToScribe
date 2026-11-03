---
name: whiteboard-teaching
description: Teach a concept by drawing it live on a Scribe whiteboard — building the diagram while narrating, pointing at things, and erasing mistakes. Use when asked to explain, teach, walk through, or diagram something and the scribe MCP tools are available.
---

# Teaching at the whiteboard

You have a whiteboard. The point is not to produce a diagram — it is that the
picture **builds up while you talk**, and that you can point at things and rub
things out. A finished diagram appearing all at once is the failure this tool
exists to fix.

## The loop

1. `scribe_open(topic)` once. It opens the board in the user's browser and
   returns the URL.
2. **`scribe_plan(...)` before any ink.** `scribe_draw` refuses until you have.
3. `scribe_reference()` before your first draw, if you have not read it.
4. Draw a **beat**: 5–15 ops, interleaving `say` with the drawing.
5. **Read the report.** Fix anything it flags. Then the next beat.
6. `scribe_look()` at the end to check the whole board reads well.

## Plan first, and plan an argument

The planning step is not bureaucracy — it is the difference between a lesson
and a tour of a diagram. Improvising produces narration like *"on the left,
your inputs"*: a caption for a box the learner can already see. You write
captions when you decide the line while drawing the box.

A plan needs four things, and the fourth is the one people skip:

- **The one idea.** If they remember a single sentence, this is it.
- **The hook.** A question the drawing answers. Put it on the board *first*
  and leave it there, so the payoff lands while the question is still visible.
- **The beats.** Steps in an argument, not a list of shapes. "One server
  buckles under load" is a beat; "draw three boxes" is not. The shape that
  almost always works: **problem → obvious fix → why it fails → the real idea.**
- **The misconception.** What does the learner probably believe that is wrong?
  Name it and you have something to push against. Draw it wrong on purpose,
  then correct it — that correction is the one move a finished diagram cannot
  make, and it is the best reason this tool exists.

## Narration rules

- **Say why, not what.** The label says what. If a `say` line would still be
  true with the diagram covered up, it is carrying its weight; if it only
  describes what is on screen, cut it.
- **Never narrate your own mistakes.** *"Oops, I rubbed that out"* is tooling
  noise. Fix it silently and keep teaching.
- **Earn the erase.** Wiping should be a move you planned, not a reaction to
  running out of room. Plan which region goes, and say why as it goes.
- **One idea per line, short lines.** Long sentences stall the drawing while
  the voice catches up.
- **Give the key moment room.** Not every beat deserves equal weight.

## Writing a scene

Draw a thing, say what it is, draw the next thing. That ordering *is* the
teaching:

```
box    5,7 3,2.5 "x1"
box    17,5 3,2.5 "h1"
say    "On the left, your inputs."
stroke 8,8.25 17,6.25
say    "Every input connects to every hidden unit. That's the mesh."
highlight "h1" "Circling this one — it's where the work happens."
```

- **Point at things.** When you refer to something, `highlight "<its label>"`.
  The note is spoken aloud as the ring is drawn, so the pointer explains itself.
- **Never guess a radius.** `circle x,y,r` exists for pointing at empty space.
  For anything with a label, use `highlight` — you cannot see how wide a box
  rendered, and a guessed ring either slices through the text or swallows the
  neighbours. The report will tell you when you have done this.
- **Narrate in speech, not prose.** `say` lines are read out loud — by the
  browser, or by ElevenLabs if the server has a key. Write how you would talk,
  not how you would write. Long sentences stall the drawing while the voice
  catches up; two short lines beat one long one.
- **Let the shape carry the idea.** Branching, ordering, nesting, feedback. A
  row of boxes labelled "Step 1 / Step 2" teaches nothing.

## Reading the report

Every `scribe_draw` returns something like:

```
Page 0 of a 40x30 grid. 28 elements, 38% full.
COLLISIONS -- 1 overlapping elements:
  x text (16.5,1.98)-(19.6,3.03) 'Hidden' overlaps text (11,0.98)-(25,2.03) 'How a neural network learns'
```

A collision means something on the board is **now unreadable**. Fix it before
continuing — do not push on and hope.

| Report says | Do |
|---|---|
| `COLLISIONS` | `erase` the offender and redraw it somewhere clear |
| `ERRORS` | The op did not draw at all. The message gives the correct form |
| `label 'x' needs ~N units` | The box is too narrow for its label. Widen it |
| `circle ... cuts across` | Use `highlight "<label>"` instead of a guessed radius |
| `page is filling up` | Erase a finished region, or start a new `page` |

`scribe_look()` costs an image; the report is free. Reach for the picture when
the text says something is wrong, or to check the final composition.

## Fixing things

- **A stray element** — `scribe_erase_preview(x,y,w,h)` first. The eraser is a
  blunt rectangle and will take neighbours with it. The preview tells you which.
- **A whole scene went wrong** — `scribe_undo()` rebuilds the board without it.
  Cleaner than erasing when the damage is spread out.
- **Erasing is also teaching.** Wiping a section you have finished with to make
  room, or correcting a deliberately wrong model, is good pedagogy — not just
  damage control. Narrate it: `erase 0,22 40,8 "Wiping this, we need the room."`

## Laying things out

The board is 64 wide × 36 tall. Text is about **0.5 grid units per character**,
so `"Request Queue"` needs ~7 units of width.

- Left-to-right for pipelines, top-to-bottom for hierarchies.
- Leave 2+ units between boxes; labels sit inside them.
- Keep a title at `y≈1.5` and start the content at `y≈5`.
- Budget vertical space. Running out at the bottom mid-explanation is the most
  common way a lesson falls apart — check occupancy as you go.
