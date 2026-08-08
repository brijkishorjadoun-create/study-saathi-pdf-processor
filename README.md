# Study Saathi - PDF Processor Microservice

Rebuilds a translated PDF page on top of the original page's visual layout
(background, images, diagrams preserved; only text is replaced).

## Endpoints

### `GET /health`
Health check. Returns `{"status": "ok"}`.

### `POST /extract-text-blocks`
Call this FIRST on the original PDF page to get text + coordinates.
Send that text to your existing translation engine, then map the
translated text back onto the same coordinate boxes.

Query params: `page_number` (int, default 0)
Body: `{"pdf_base64": "<base64 of original PDF>"}`

Returns a list of blocks: `{text, x0, y0, x1, y1, font_size, is_bold, font_name}`

### `POST /process-page`
Call this AFTER you have translated text mapped to blocks.
Rebuilds the page: original background/images intact, translated
text drawn into the same boxes (auto-shrinks font if text is longer).

Body:
```json
{
  "pdf_base64": "<base64 of original PDF>",
  "page_number": 0,
  "text_blocks": [
    {
      "text": "Translated Hinglish text here",
      "x0": 72.0, "y0": 100.0, "x1": 300.0, "y1": 120.0,
      "font_size": 12.0,
      "is_bold": false,
      "color": [0, 0, 0]
    }
  ],
  "background_fill": [1, 1, 1]
}
```

Returns:
```json
{
  "page_pdf_base64": "<base64 of rebuilt single-page PDF>",
  "preview_png_base64": "<base64 PNG preview>",
  "page_width": 612.0,
  "page_height": 792.0
}
```

## Auth
If you set the `PDF_PROCESSOR_API_KEY` environment variable, all requests
must include header: `Authorization: Bearer <your-key>`.
If unset, auth is disabled (fine for early testing, NOT for production).

## Local testing
```
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Then visit http://localhost:8000/docs for interactive API testing.

## Deploy to Render
1. Push this folder to a GitHub repo
2. On Render.com: New -> Web Service -> connect your repo
3. Render auto-detects `render.yaml` (or set manually):
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variable `PDF_PROCESSOR_API_KEY` (any secret string you choose)
5. Deploy. Your live URL will look like `https://study-saathi-pdf-processor.onrender.com`
6. Use that URL + your API key as `PDF_PROCESSOR_API_URL` / `PDF_PROCESSOR_API_KEY`
   in your Lovable app's secrets.

## Notes / limitations
- Works best on digital (text-based) PDFs like NCERT textbooks.
- Scanned/image-only PDFs need OCR first (not included in this MVP) — the
  `/extract-text-blocks` endpoint will return empty results for those.
- Auto font-shrink handles moderately longer translated text; extremely
  long translations may still slightly overflow their original box.
- Diagram-internal labels are NOT auto-detected/translated in this MVP —
  only page-level text blocks. Diagrams are preserved as-is (untouched).
