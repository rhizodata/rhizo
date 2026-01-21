# Diagram Sources

TikZ source files for Rhizo architecture diagrams. These compile to publication-quality vector graphics.

## Testing Without Local LaTeX

1. Go to [Overleaf](https://www.overleaf.com/)
2. Create a new blank project
3. Copy the contents of `src/architecture.tex` into the editor
4. Click "Recompile" to see the diagram

## Local Compilation

If you have LaTeX installed:

```bash
cd docs/diagrams/src
pdflatex architecture.tex
```

To convert PDF to SVG (requires pdf2svg or Inkscape):

```bash
pdf2svg architecture.pdf ../architecture.svg
# or
inkscape architecture.pdf --export-filename=../architecture.svg
```

## Files

| File | Description |
|------|-------------|
| `src/architecture.tex` | 4-layer system architecture |

## Why TikZ?

- Version-controllable (plain text)
- Publication-quality vector output
- Required for academic papers (arXiv, VLDB, SIGMOD)
- Consistent styling across all diagrams
