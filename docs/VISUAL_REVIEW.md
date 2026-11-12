# SA-117 — Bounded visual diagnostics

The report exports visual-review.json after rendering. SVG text is inspected using approximate glyph boxes for simple axis-aligned text with numeric coordinates/font size and ordinary anchoring. Candidate overlap requires30% of the smaller glyph box; max200 texts/25 finding examples. Transforms/CSS/tspan/unsupported text geometry and unknown font fallback explicitly reduce coverage. The heuristic does not certify raster or semantic correctness and never edits generated SVG. It can miss defects outside the checked subset and can flag intentional overlaps.

PDF inspection extracts the last page text, removes the known page number at either edge and flags1–2 remaining source lines after the code-appendix heading. This is a tail check, not clipping detection, image verification or general page-layout certification. Source appendix paragraphs use explicit10.5pt leading and keep headers/trailing nonempty lines together. Source bytes remain unchanged.

Report assets remain needs_review; visual diagnostics do not mark a laboratory accepted. A warning accompanies findings or incomplete SVG coverage. Program-generated figure repair is not yet implemented; it must retain approved data/algorithm constraints and avoid replaying unrelated successful outputs.

Offline real G02 evidence found3 center-label overlaps and the orphan source line in the old23-page PDF.45 targeted tests cover genuine overlaps, separated labels, unsupported typography, caps, XML guard and page footer order. Actual re-export/visual QA is recorded after source freeze.
