---
name: whiteboard-teaching
description: Teach a concept by drawing it live on a Scribe whiteboard — building the diagram while narrating, pointing at things, and erasing mistakes. Use when asked to explain, teach, walk through, or diagram something and the scribe MCP tools are available.
---

# Teaching at the whiteboard

The scribe server states the loop, the planning rules and the narration rules;
`scribe_reference` lists the commands. This skill covers what those cannot: the
judgement calls that decide whether the result is a lesson or a wireframe with
a voiceover.

Read it alongside the tool output, not instead of it. Where they agree, the
tool output is the authority — it is generated from the code.

## Use the right primitive

A board of labelled rectangles looks like a wireframe. These exist so it does
not have to:

| Instead of | Use | Why |
|---|---|---|
| `box ... "Database"` | `icon db x,y 5 "Database"` | A cylinder *is* a database. Biggest single visual win. |
| `arrow` between two boxes | `connect "a" "b" "label"` | Elbow-routed from the real boxes. You cannot see how wide they rendered. |
| A box around related things | `group x,y,w,h "label"` | Dashed container with a label tab; never collides with its contents. |
| An aside squeezed into the diagram | `note x,y,w "text"` | Sticky note, wraps its own text, stays out of the way. |
| Erasing a wrong model | `strike "label"` | Keeps the evidence it was there. Pairs with the misconception beat. |
| Erasing to make room | `pan <y>` | The board extends downward. Everything drawn is kept and exported. |

Icons: db server user cloud queue file clock lock globe gear warning check cross box

`fill "label" blue|green|red|yellow|grey` tints a shape — use it to mark one
path through a diagram, or the half of a system you are discussing. `dash on`
makes everything after it dashed, for optional or async paths. `brace x,y,h
"label"` groups a vertical range.

## Pace the lesson against the voice

Narration and drawing share one clock but not one queue: a `say` line keeps
playing over the ops that follow it. So the shape of a scene is

```
say    "An index is a second copy of that column, kept in sorted order."
icon   file 32,27.5 5 "sorted copy"
connect "CREATE INDEX ..." "sorted copy" "builds"
highlight "sorted copy" "This is the part you are really paying for."
```

— the claim, then the ink that proves it while the words are still in the air.
Two `say` lines back to back freeze the board for both; the draw report says
`NOTHING BEING DRAWN` when you have written one. A line that arrives *after*
its shape is a caption, and the picture did not need it.

The report also flags `REPEATED NARRATION`. Hearing the same sentence twice
reads as a stutter — usually a `highlight` note restating the `say` above it.
Point with the note, do not re-explain: *"this is the write you did not know
you were buying"*, not the sentence you just said again.

## Writing a scene

Draw a thing, say what it is for, draw the next thing — and point at whatever
you are talking about:

- **`highlight "<label>"`, never `circle`, for anything with a label.** You
  cannot see how wide a box rendered; a guessed radius either slices through
  the text or swallows the neighbours. `circle x,y,r` is for empty space only.
- **Narrate in speech, not prose.** `say` lines are read aloud. Write how you
  would talk — two short lines beat one long one.
- **Let the shape carry the idea.** Branching, ordering, nesting, feedback. A
  row of boxes labelled "Step 1 / Step 2" teaches nothing.

## Reading the report

| Report says | Do |
|---|---|
| `COLLISIONS` | Something is now unreadable. `erase` the offender and redraw it clear |
| `ERRORS` | The op did not draw at all. The message gives the correct form |
| `label 'x' needs ~N units` | The box is too narrow for its label. Widen it |
| `circle ... cuts across` | Use `highlight "<label>"` instead of a guessed radius |
| `this screen is filling up` | `pan` to fresh board below |
| `NOTHING BEING DRAWN` | Move a `say` so ink lands under it, or cut the line |
| `REPEATED NARRATION` | Rewrite the second line, or drop it |

The report includes a **layout map** — a coarse text picture of the screen. It
shows crammed bands, empty regions, and rings sitting in open space, none of
which the collision check can see:

```
  |      ~~~~~~~~~~~~~~~~~~~                    |
  |            >>>   OO OOO                     |
  |>>>>>>>>>>>>>>>>>>OOOO>O>>>>>>>>>>>>>>>>>>>> |
```

`scribe_look()` costs an image; the report is free. Reach for the picture
after each beat anyway — it is the only thing that can tell you the board
means something.

## Fixing things

- **A stray element** — `scribe_erase_preview(x,y,w,h)` first. The eraser is a
  blunt rectangle and will take neighbours with it; the preview says which.
- **A whole scene went wrong** — `scribe_undo()` rebuilds the board without
  it. Cleaner than erasing when the damage is spread out.
- **Out of room** — `pan`, never `erase`. Erasing throws away part of the
  lesson that would otherwise be exported. Erase only to correct something
  that is wrong, and when you do, say why you are rubbing it out.
