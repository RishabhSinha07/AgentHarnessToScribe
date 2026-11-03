# Build notes

Scribe began as a feasibility spike answering one question: **can an LLM drive a
pen?** The findings below are from that spike. Every defect it names has since
been fixed; they are kept because they explain why the design is shaped as it is.

## What the spike found

- **Coordinate drawing is viable.** 100+ ops, zero syntax errors, legible
  first-pass layout on a coarse grid. Pixels would not have survived it.
- **Text must be text.** Drawing letterforms stroke by stroke is illegible. The
  split between `text` and the pen is the one non-negotiable design decision.
- **Erasure is the headline capability.** Used for repair, and for pedagogy —
  wiping a section that has served its purpose. No diagram format can do either.
- **Pointing is the other one.** `highlight` makes deixis a first-class token.
- **Nothing spatial is caught without feedback.** Every defect in the spike was
  a layout error, and every one was found by *looking*. This is why
  `scribe_draw` returns collisions as text on every call.

## Caveat on the method

The spike was drawn by the same session that wrote the renderer, so it knew the
coordinate system intimately — an advantage a fresh agent does not have. Treat
it as an upper bound on how well coordinate drawing goes, not a typical case.
