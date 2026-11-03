# Showcase

`index.html` is a **self-contained** replay of a lesson Claude drew — no server,
no build step, no network beyond Google Fonts. Open it in a browser, or serve
this directory with GitHub Pages for a public demo link:

> Settings → Pages → Source: *Deploy from a branch* → `main` / `/docs`

It bakes one recorded op-stream into the page. The live version
(`uv run scribe-serve`) streams ops over a WebSocket instead, but uses the same
drawing engine — progressive strokes, voice, scrubbing, the lot.

`preview.png` is the finished board, used in the top-level README.
