"""
Script: Remove Non-Unique Variable Mapping Pages from CRF PDF
Purpose: Extract only the blank CRF pages (unique forms) for SDTM annotation
         by removing pages that contain the variable specification tables.

Requirements:
    pip install PyMuPDF
    (or: pip install pymupdf)
"""

import fitz  # PyMuPDF
import os
import sys
from pathlib import Path


def is_variable_mapping_page(page):
    """
    Determine if a page is a non-unique variable mapping page.
    
    These pages contain column headers like:
    'Field Name', 'Data Type', 'SAS Format', 'SAS Label', 'Values'
    
    Parameters:
        page: A PyMuPDF page object
    
    Returns:
        bool: True if the page is a variable mapping page, False otherwise
    """
    text = page.get_text("text")
    
    # Define the key identifiers that appear on variable mapping pages
    # These column headers are characteristic of the non-unique specification pages
    mapping_indicators = [
        "Field Name",
        "Data Type",
        "SAS Format",
        "SAS Label",
    ]
    
    # Count how many indicators are present on the page
    indicator_count = sum(1 for indicator in mapping_indicators if indicator in text)
    
    # If 3 or more of the 4 indicators are found, it's a mapping page
    # Using threshold of 3 to handle cases where text extraction may miss one
    if indicator_count >= 3:
        return True
    
    return False


def remove_non_unique_pages(input_pdf_path, output_pdf_path=None, verbose=True):
    """
    Remove non-unique variable mapping pages from a CRF PDF,
    keeping only the blank CRF pages suitable for SDTM annotations.
    
    Parameters:
        input_pdf_path (str): Path to the input PDF file
        output_pdf_path (str): Path for the output PDF file (optional)
        verbose (bool): Print progress information
    
    Returns:
        tuple: (pages_kept, pages_removed, total_pages)
    """
    input_path = Path(input_pdf_path)
    
    if not input_path.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")
    
    # Default output path if not specified
    if output_pdf_path is None:
        output_pdf_path = str(input_path.parent / f"{input_path.stem}_unique_CRF{input_path.suffix}")
    
    # Open the source PDF
    source_doc = fitz.open(input_pdf_path)
    total_pages = len(source_doc)
    
    if verbose:
        print(f"{'='*60}")
        print(f"  CRF Non-Unique Page Remover for SDTM Annotations")
        print(f"{'='*60}")
        print(f"  Input:  {input_pdf_path}")
        print(f"  Output: {output_pdf_path}")
        print(f"  Total pages in source: {total_pages}")
        print(f"{'='*60}\n")
    
    # Identify pages to keep (unique/blank CRF pages)
    pages_to_keep = []
    pages_to_remove = []
    
    for page_num in range(total_pages):
        page = source_doc[page_num]
        
        if is_variable_mapping_page(page):
            pages_to_remove.append(page_num + 1)  # 1-based for display
            if verbose:
                # Extract form name for context
                text = page.get_text("text")
                form_info = _extract_form_name(text)
                print(f"  [REMOVING] Page {page_num + 1:>4} - Variable mapping: {form_info}")
        else:
            pages_to_keep.append(page_num)  # 0-based for PyMuPDF
            if verbose:
                text = page.get_text("text")
                form_info = _extract_form_name(text)
                print(f"  [KEEPING]  Page {page_num + 1:>4} - Blank CRF: {form_info}")
    
    # Create new PDF with only the unique pages
    output_doc = fitz.open()  # New empty document
    
    for page_num in pages_to_keep:
        output_doc.insert_pdf(source_doc, from_page=page_num, to_page=page_num)
    
    # Save the output PDF
    output_doc.save(output_pdf_path)
    output_doc.close()
    source_doc.close()
    
    pages_kept = len(pages_to_keep)
    pages_removed = len(pages_to_remove)
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"  SUMMARY")
        print(f"{'='*60}")
        print(f"  Total pages processed:  {total_pages}")
        print(f"  Pages kept (Unique):    {pages_kept}")
        print(f"  Pages removed (Non-Unique): {pages_removed}")
        print(f"  Output saved to: {output_pdf_path}")
        print(f"{'='*60}")
    
    return pages_kept, pages_removed, total_pages


def _extract_form_name(text):
    """
    Extract form name from page text for logging purposes.
    
    Parameters:
        text (str): Page text content
    
    Returns:
        str: Form name or 'Unknown'
    """
    lines = text.strip().split('\n')
    
    # Look for the "Form:" line which identifies the CRF form
    for line in lines:
        if line.strip().startswith("Form:"):
            return line.strip()
    
    # Fallback: return first meaningful line
    for line in lines[:5]:
        stripped = line.strip()
        if stripped and len(stripped) > 5:
            return stripped[:80]
    
    return "Unknown"


def remove_non_unique_pages_advanced(input_pdf_path, output_pdf_path=None, 
                                      additional_keywords=None,
                                      keep_first_page=True,
                                      verbose=True):
    """
    Advanced version with additional configuration options.
    
    Parameters:
        input_pdf_path (str): Path to the input PDF file
        output_pdf_path (str): Path for the output PDF file
        additional_keywords (list): Extra keywords to identify mapping pages
        keep_first_page (bool): Always keep the first page (often a cover page)
        verbose (bool): Print progress information
    
    Returns:
        tuple: (pages_kept, pages_removed, total_pages)
    """
    input_path = Path(input_pdf_path)
    
    if not input_path.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")
    
    if output_pdf_path is None:
        output_pdf_path = str(input_path.parent / f"{input_path.stem}_unique_CRF{input_path.suffix}")
    
    # Default mapping page indicators
    mapping_indicators = [
        "Field Name",
        "Data Type",
        "SAS Format",
        "SAS Label",
        "Values",
    ]
    
    # Add any additional keywords
    if additional_keywords:
        mapping_indicators.extend(additional_keywords)
    
    source_doc = fitz.open(input_pdf_path)
    total_pages = len(source_doc)
    
    if verbose:
        print(f"\n  Processing {total_pages} pages...")
        print(f"  Detection keywords: {mapping_indicators}\n")
    
    pages_to_keep = []
    
    for page_num in range(total_pages):
        # Option to always keep first page
        if keep_first_page and page_num == 0:
            pages_to_keep.append(page_num)
            continue
        
        page = source_doc[page_num]
        text = page.get_text("text")
        
        # Count matching indicators
        indicator_count = sum(1 for ind in mapping_indicators if ind in text)
        
        # Threshold: at least 3 core indicators must be present
        core_indicators = ["Field Name", "Data Type", "SAS Format", "SAS Label"]
        core_count = sum(1 for ind in core_indicators if ind in text)
        
        if core_count >= 3:
            if verbose:
                print(f"    Removing page {page_num + 1} (matched {core_count}/4 core indicators)")
        else:
            pages_to_keep.append(page_num)
    
    # Build output PDF
    output_doc = fitz.open()
    for page_num in pages_to_keep:
        output_doc.insert_pdf(source_doc, from_page=page_num, to_page=page_num)
    
    output_doc.save(output_pdf_path)
    output_doc.close()
    source_doc.close()
    
    pages_kept = len(pages_to_keep)
    pages_removed = total_pages - pages_kept
    
    if verbose:
        print(f"\n  Result: Kept {pages_kept} pages, Removed {pages_removed} pages")
        print(f"  Saved to: {output_pdf_path}")
    
    return pages_kept, pages_removed, total_pages


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    
    # -------------------------------------------------------------------------
    # CONFIGURATION - Update these paths as needed
    # -------------------------------------------------------------------------
    
    INPUT_PDF = "D7984C00002 non unique.pdf"    # Your input file
    OUTPUT_PDF = "D7984C00002_unique_CRF.pdf"   # Output file name
    
    # -------------------------------------------------------------------------
    # Option 1: Command-line arguments
    # -------------------------------------------------------------------------
    if len(sys.argv) >= 2:
        INPUT_PDF = sys.argv[1]
    if len(sys.argv) >= 3:
        OUTPUT_PDF = sys.argv[2]
    
    # -------------------------------------------------------------------------
    # Run the script
    # -------------------------------------------------------------------------
    try:
        pages_kept, pages_removed, total_pages = remove_non_unique_pages(
            input_pdf_path=INPUT_PDF,
            output_pdf_path=OUTPUT_PDF,
            verbose=True
        )
        
        print(f"\n  ✓ Successfully created unique blank CRF for SDTM annotations!")
        print(f"    {pages_removed} variable mapping pages removed.")
        print(f"    {pages_kept} blank CRF pages retained.\n")
        
    except FileNotFoundError as e:
        print(f"\n  ✗ Error: {e}")
        print(f"    Please check the input file path and try again.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ✗ Unexpected error: {e}")
        sys.exit(1)