"""
Study Saathi - PDF Processor Microservice
------------------------------------------
Takes an original PDF page + translated text blocks (with coordinates)
and rebuilds the page: original background/images/diagrams preserved,
original text blanked out, translated text drawn back at ~same position.

Endpoints:
  GET  /health              -> simple health check
  POST /process-page        -> rebuild a single page (see schema below)
"""

import io
import os
import base64
from typing import List, Optional

import fitz  # PyMuPDF
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="Study Saathi PDF Processor")

# Optional simple auth via bearer token (matches PDF_PROCESSOR_API_KEY in Lovable secrets)
API_KEY = os.environ.get("PDF_PROCESSOR_API_KEY", "")


def check_auth(authorization: Optional[str]):
    if not API_KEY:
        return  # no key configured -> auth disabled (fine for early testing)
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------- Request/response schemas ----------

class TextBlock(BaseModel):
    text: str                # translated text to draw
    x0: float                # bounding box (PDF points, top-left origin)
    y0: float
    x1: float
    y1: float
    font_size: float = 11.0
    is_bold: bool = False
    is_heading: bool = False
    color: List[float] = [0, 0, 0]   # RGB 0-1 range, default black


class ProcessPageRequest(BaseModel):
    pdf_base64: str              # original PDF file, base64 encoded
    page_number: int             # 0-indexed page to process
    text_blocks: List[TextBlock] # translated blocks with original coordinates
    background_fill: List[float] = [1, 1, 1]  # RGB 0-1, fallback blank color


class ProcessPageResponse(BaseModel):
    page_pdf_base64: str    # single-page PDF, base64
    preview_png_base64: str # PNG preview of the rebuilt page, base64
    page_width: float
    page_height: float


# ---------- Core logic ----------

def rebuild_page(pdf_bytes: bytes, page_number: int, text_blocks: List[TextBlock],
                  background_fill: List[float]):
    src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    if page_number < 0 or page_number >= len(src_doc):
        raise HTTPException(status_code=400, detail=f"page_number {page_number} out of range (doc has {len(src_doc)} pages)")

    src_page = src_doc[page_number]
    rect = src_page.rect

    # New single-page doc, same dimensions as original
    out_doc = fitz.open()
    out_page = out_doc.new_page(width=rect.width, height=rect.height)

    # Step 1: copy the ENTIRE original page as a background (preserves images,
    # diagrams, colors, vector art, everything) by rendering it to a pixmap
    # and placing it as a full-page image. This guarantees nothing visual is lost.
    zoom = 2.0  # render at 2x for crisper text-blank quality later
    mat = fitz.Matrix(zoom, zoom)
    pix = src_page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")
    out_page.insert_image(rect, stream=img_bytes)

    # Step 2: blank out each original text region so we can draw translated
    # text cleanly on top (covers the underlying English text)
    for block in text_blocks:
        blank_rect = fitz.Rect(block.x0, block.y0, block.x1, block.y1)
        # Slightly pad to fully cover original glyph edges/antialiasing
        blank_rect = blank_rect + (-1, -1, 1, 1)
        out_page.draw_rect(blank_rect, color=None, fill=tuple(background_fill), overlay=True)

    # Step 3: draw translated text into each blanked region, auto-shrinking
    # font size if the translated text would overflow the box.
    for block in text_blocks:
        target_rect = fitz.Rect(block.x0, block.y0, block.x1, block.y1)
        font_size = block.font_size
        fontname = "helv"
        if block.is_bold:
            fontname = "hebo"

        # Try progressively smaller font sizes until text fits the box height/width,
        # down to a readable minimum.
        min_size = 6.0
        chosen_size = font_size
        while chosen_size > min_size:
            inserted = out_page.insert_textbox(
                target_rect,
                block.text,
                fontsize=chosen_size,
                fontname=fontname,
                color=tuple(block.color),
                align=0,
                render_mode=3,  # invisible test-pass: just measure via return value
            )
            # insert_textbox returns negative value if text didn't fit
            if inserted >= 0:
                break
            chosen_size -= 0.5

        # Final real draw at the chosen size (render_mode default = fill)
        out_page.insert_textbox(
            target_rect,
            block.text,
            fontsize=chosen_size,
            fontname=fontname,
            color=tuple(block.color),
            align=0,
        )

    return out_doc, rect.width, rect.height


# ---------- API routes ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process-page", response_model=ProcessPageResponse)
def process_page(req: ProcessPageRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)

    try:
        pdf_bytes = base64.b64decode(req.pdf_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="pdf_base64 is not valid base64")

    out_doc, width, height = rebuild_page(
        pdf_bytes, req.page_number, req.text_blocks, req.background_fill
    )

    # Export rebuilt single page as PDF bytes
    page_pdf_bytes = out_doc.tobytes()

    # Also export a PNG preview for fast frontend display
    out_page = out_doc[0]
    pix = out_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    preview_png_bytes = pix.tobytes("png")

    out_doc.close()

    return ProcessPageResponse(
        page_pdf_base64=base64.b64encode(page_pdf_bytes).decode("utf-8"),
        preview_png_base64=base64.b64encode(preview_png_bytes).decode("utf-8"),
        page_width=width,
        page_height=height,
    )


@app.post("/extract-text-blocks")
def extract_text_blocks(pdf_base64: str, page_number: int = 0, authorization: Optional[str] = Header(None)):
    """
    Helper endpoint: given an original PDF, extract text blocks with their
    coordinates so you know WHERE to place translated text. Call this first,
    send the text off to your existing translation engine, then call
    /process-page with the translated text mapped back onto these same boxes.
    """
    check_auth(authorization)

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="pdf_base64 is not valid base64")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_number < 0 or page_number >= len(doc):
        raise HTTPException(status_code=400, detail=f"page_number {page_number} out of range")

    page = doc[page_number]
    raw = page.get_text("dict")

    blocks_out = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block, 1 = image block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox = span.get("bbox", [0, 0, 0, 0])
                blocks_out.append({
                    "text": text,
                    "x0": bbox[0],
                    "y0": bbox[1],
                    "x1": bbox[2],
                    "y1": bbox[3],
                    "font_size": span.get("size", 11.0),
                    "is_bold": bool(span.get("flags", 0) & 2**4),
                    "font_name": span.get("font", ""),
                })

    doc.close()
    return {"page_number": page_number, "blocks": blocks_out}
