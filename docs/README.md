# The published demo

`index.html` is the demo served at
<https://rishabhsinha07.github.io/AgentHarnessToScribe/>, via
*Settings -> Pages -> Deploy from a branch -> `main` / `/docs`*.

It is **self-contained**: one recorded op-stream baked into the page, no
server, no network beyond Google Fonts. Open it from disk and it plays.

## Rebuilding it

It is a baked copy of the viewer, so it goes stale silently every time the
viewer is fixed. Do not hand-edit it:

```bash
uv run python -m scribe.build_demo
```

That re-renders `index.html`, `preview.png` and `lesson.json` from
`examples/first_load.ops` using the current code. Edit the lesson, not the
output.

`gallery.png` shows the drawing primitives and is referenced by the top-level
README.

## Video

The published replay uses the browser's built-in voice: it has no server to
proxy ElevenLabs through. To produce a shareable video with the better voice,
run the lesson on the live board (`uv run scribe-serve ...`) and use **Record**
there.
