# The design — `factory.pen` is the source

**Change the design first, then the implementation.** A screen implemented first has no
specification to be checked against, which is why the web workflow leads with `DESIGN`.

```
factory.pen     the design. 34 frames, 17 themed variables. Open it in Pencil.
tokens.json     its variables, exported. THE COURIER, not the source.
nocturne.css    the design system underneath it
ref/            what the design owes, and the check that every identity is drawn
BRIEF.md        the frames the design owes
```

## Why `tokens.json` exists

A `.pen` is encrypted and only the Pencil MCP tools can open one. `npm run gen:tokens` is a shell
script and cannot call them. So the variables are exported to `tokens.json`, the generator reads
that, and `tests/theme.test.tsx` asserts every token in `src/styles/tokens.css` is byte-equal to
it in BOTH themes.

The chain is therefore: **edit `factory.pen` → export to `tokens.json` → `npm run gen:tokens` →
the test says whether the client followed.** Never hand-edit `tokens.json` or `tokens.css`.

## Two sources, two themes — and the half that had only one

`factory.pen` carries seventeen **themed** variables: every one has a light value and a dark value,
and `gen:tokens` writes each into the theme's own block. `nocturne.css` is not themed that way —
its `:root` is the design system's dark ground, and the generator layers it under the pen's dark
set. So a nocturne token the client paints has a dark value and, until `WEB-FIX-326`, no light one:
the light theme inherited `:root` and drew the dark accent on a light panel at 2.62:1.

`nocturne.css` therefore carries a `[data-theme="light"]` block of its own, and the generator reads
it into the light theme exactly as it reads `:root` into the dark one. It holds **only** the tokens
the product paints in the light theme — the rest of `:root` is nocturne's own dark surface
treatment and is never drawn on a light ground.

    :root                 nocturne's dark ground   →  tokens.css :root, under the pen's dark set
    [data-theme="light"]  what light overrides     →  tokens.css [data-theme="light"], under light

## What was here before

Four `.dc.html` exports were the source until 2026-09-04, and `gen-tokens.mjs` read
`Ticket.dc.html`'s `:root`. They are deleted. Two of the four were read by nothing at all, and the
other two expressed the same colours through `oklch()` and `var()` indirection — so "the design"
lived in a form nobody could open in a design tool. That is the whole reason for the move: one
design, changed in one place, and the diff shows what moved.

**No colour, radius, space or font may be written anywhere else in this client.**
