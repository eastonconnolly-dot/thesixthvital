# Brand fonts

Both files are free, open-source Google Fonts under the SIL Open Font
License 1.1 (full text: https://openfontlicense.org).

- `PlayfairDisplay-Bold.ttf` — static Bold (700) instance of the
  [Playfair Display](https://fonts.google.com/specimen/Playfair+Display)
  variable font.
- `SourceSans3-Regular.ttf` / `SourceSans3-SemiBold.ttf` — static
  Regular (400) / SemiBold (600) instances of the
  [Source Sans 3](https://fonts.google.com/specimen/Source+Sans+3)
  variable font.

Google's `google/fonts` repo ships these families as variable fonts
only (a single `[wght].ttf` per family), and ReportLab's `TTFont`
registration needs a static instance per weight. Each file here was
generated from the upstream variable font with `fonttools`:

```bash
pip install fonttools
fonttools varLib.instancer -o PlayfairDisplay-Bold.ttf PlayfairDisplay[wght].ttf wght=700
fonttools varLib.instancer -o SourceSans3-Regular.ttf SourceSans3[wght].ttf wght=400
fonttools varLib.instancer -o SourceSans3-SemiBold.ttf SourceSans3[wght].ttf wght=600
```

Loaded by `api/services/pdf/base.py::register_brand_fonts()`, which
falls back to ReportLab's built-in Times-Roman/Helvetica if these files
are ever missing.
