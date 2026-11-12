# SA-115 — Report asset rendering

SVG artifacts are screened as XML: no DTD/entities, script/foreignObject/unsupported active elements, event attributes, external href/url, xml:base or CSS viewport bypass. A finite viewBox bounds the viewport; source bytes remain unchanged. Sanitized XML preserves the default SVG namespace and is imported with the installed LibreOffice svg_Import filter, exported using draw_png_Export, and cached by input+renderer contract hash. Unsupported SVG fails explicitly. Raster dimensions are checked. This conservative SVG subset may reject valid unsupported SVG features; it is not a claim of a complete hostile-document sandbox or a hard memory limit for LibreOffice.

The first developer smoke produced a PNG of XML text due to namespace/autodetection; this failed visual evidence is retained. Default namespace plus explicit installed filter corrected the rendering. The corrected fixture raster is visually inspected; source G02 SVG is rendered without editing its content. Its central relation labels overlap in the original model-produced layout; embedding does not repair that laboratory artifact.

Code appendix selection uses current source artifacts owned by done code steps, a matching written checkpoint and exact successful snapshot bytes. If explicit own source deliverables exist, they are preferred over drivers/test scaffolding. Imported starter snapshots are excluded. Full relative source paths label entries. Missing required source metadata blocks rendering; it does not silently fall back to unrelated code.

Long raw CSV cells are explicitly abbreviated with the original CSV reference; short numeric values and all displayed outcome rows remain unchanged. The complete raw artifact is retained.

527 full tests/Ruff passed04.11.2026. Security/path/geometry cases and source-selection/declared-source-missing/cell-text tests passed; real fixture conversion inspected. Actual report export and visual QA are recorded separately after freeze.
