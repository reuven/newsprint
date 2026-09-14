# Logo

The mark is what the tool hands you: an open folded sheet, two A5 leaves
creased down the middle, a masthead rule over lines of type on each leaf.
The wordmark is set the way `render.py` sets a packet title — heavy sans,
uppercase, letterspaced, under one heavy rule — so the logo and the paper
share an identity.

| file | use |
| --- | --- |
| `logo.svg` / `logo.png` | the lockup: mark, wordmark, tagline |
| `mark.svg` / `mark.png` | the mark alone, at size |
| `icon.svg`, `icon-*.png` | the mark simplified for small sizes |
| `*-bare.svg` | no paper-coloured ground, for other backgrounds |

`icon` is a different drawing rather than a scaled one: five lines a leaf
fill in to grey below about 48px, so it drops to three heavier lines and a
thicker stroke.

Everything is generated from shapes, and the wordmark is converted to
outlines so nothing depends on a font being installed:

```
uv run --with cairosvg python assets/build_logo.py
```

The two fonts it outlines from (Arial Black, Georgia Italic) are macOS
system fonts, and are only needed to *rebuild* the logo, never to use it.
