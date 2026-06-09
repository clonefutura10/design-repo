"""
Diagnostic: Inspect how annotations are stored in reference aCRFs.
Dumps text blocks, PDF annotations, and positioning from first few pages.
"""

from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import fitz

_INPUT_DIR = PROJECT_ROOT / "input" / "reference_acrfs"


def inspect_pdf(pdf_path: Path, max_pages: int = 5):
    """Inspect first N pages of a PDF for annotation patterns."""
    doc = fitz.open(str(pdf_path))
    page_width = doc[0].rect.width
    page_height = doc[0].rect.height

    print(f"\n{'='*70}")
    print(f"  FILE: {pdf_path.name}")
    print(f"  Pages: {doc.page_count}, Size: {page_width:.0f} x {page_height:.0f}")
    print(f"{'='*70}")

    for page_idx in range(min(max_pages, doc.page_count)):
        page = doc[page_idx]
        print(f"\n  ── Page {page_idx + 1} ──")

        # ════════════════════════════════════════════════════════
        # CHECK 1: PDF Annotation Objects (FreeText, Stamps, etc.)
        # ════════════════════════════════════════════════════════
        annots = list(page.annots()) if page.annots() else []
        if annots:
            print(f"\n  📎 PDF ANNOTATIONS ({len(annots)} found):")
            for i, annot in enumerate(annots[:10]):
                print(f"     [{i}] Type: {annot.type[1]}, "
                      f"Rect: ({annot.rect.x0:.0f},{annot.rect.y0:.0f})-"
                      f"({annot.rect.x1:.0f},{annot.rect.y1:.0f}), "
                      f"Content: '{annot.info.get('content', '')[:60]}'")
                # Try getting text from FreeText annotations
                if annot.type[0] == 2:  # FreeText
                    print(f"           FreeText: '{annot.get_text()[:80]}'")
        else:
            print(f"\n  📎 PDF ANNOTATIONS: None")

        # ════════════════════════════════════════════════════════
        # CHECK 2: Right-margin text (X > 50% of page width)
        # ════════════════════════════════════════════════════════
        x_threshold = page_width * 0.45  # Lower threshold to catch more
        right_texts = []

        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    x = span["origin"][0]
                    y = span["origin"][1]
                    text = span["text"].strip()
                    size = span["size"]
                    font = span["font"]

                    if x >= x_threshold and text:
                        right_texts.append({
                            "text": text,
                            "x": x,
                            "y": y,
                            "size": size,
                            "font": font,
                        })

        if right_texts:
            print(f"\n  📐 RIGHT-MARGIN TEXT (x>{x_threshold:.0f}, {len(right_texts)} spans):")
            # Show unique texts with their properties
            seen = set()
            for rt in sorted(right_texts, key=lambda r: r["y"])[:20]:
                key = rt["text"][:50]
                if key not in seen:
                    seen.add(key)
                    print(f"     x={rt['x']:6.1f}  y={rt['y']:6.1f}  "
                          f"size={rt['size']:4.1f}  font={rt['font'][:15]:15s}  "
                          f"'{rt['text'][:60]}'")
        else:
            print(f"\n  📐 RIGHT-MARGIN TEXT: None found (x>{x_threshold:.0f})")

        # ════════════════════════════════════════════════════════
        # CHECK 3: All text with DOMAIN.VARIABLE pattern anywhere
        # ════════════════════════════════════════════════════════
        import re
        sdtm_pattern = re.compile(r"(?:SUPP)?[A-Z]{2,6}\.[A-Z][A-Z0-9_]{1,20}")
        pattern_matches = []

        all_text = page.get_text("text")
        for line in all_text.split("\n"):
            line = line.strip()
            if sdtm_pattern.search(line):
                pattern_matches.append(line)

        if pattern_matches:
            print(f"\n  🎯 SDTM PATTERNS FOUND ({len(pattern_matches)}):")
            for pm in pattern_matches[:15]:
                print(f"     '{pm[:80]}'")
        else:
            print(f"\n  🎯 SDTM PATTERNS: None found on this page")

        # ════════════════════════════════════════════════════════
        # CHECK 4: Colored rectangles (annotation boxes)
        # ════════════════════════════════════════════════════════
        drawings = page.get_drawings()
        colored_rects = [d for d in drawings
                        if d.get("fill") and d["fill"] != (1.0, 1.0, 1.0)
                        and d.get("rect")]
        if colored_rects:
            print(f"\n  🎨 COLORED RECTANGLES ({len(colored_rects)} found):")
            for cr in colored_rects[:8]:
                r = cr["rect"]
                print(f"     Rect: ({r.x0:.0f},{r.y0:.0f})-({r.x1:.0f},{r.y1:.0f})  "
                      f"Fill: {cr['fill']}  "
                      f"Width: {r.width:.0f}")
        else:
            print(f"\n  🎨 COLORED RECTANGLES: None")

    doc.close()


def main():
    pdf_files = sorted(_INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        print("No PDFs found!")
        return

    # Inspect first 3 PDFs, 3 pages each
    for pdf_path in pdf_files[:3]:
        inspect_pdf(pdf_path, max_pages=3)

    print(f"\n\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")
    print("  Check the output above to determine:")
    print("  1. Are annotations stored as PDF annotation objects (📎)?")
    print("  2. Are they regular text in the right margin (📐)?")
    print("  3. Do DOMAIN.VARIABLE patterns appear anywhere (🎯)?")
    print("  4. Are there colored boxes indicating annotation regions (🎨)?")
    print()


if __name__ == "__main__":
    main()