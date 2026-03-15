# Receipt Extraction Fixes - March 7, 2026

## Problems Identified

1. **Wrong Gemini Model**: The app was using `"gpt-4o"` (OpenAI model) instead of a valid Gemini model. This would cause API failures since the Gemini API doesn't support gpt-4o.

2. **Poor Tesseract OCR**: Basic Tesseract configuration doesn't work well with receipt images. Receipts are notoriously difficult because they:
   - Have small text
   - Often have low contrast
   - Use monospace font
   - Have complex layouts with items, prices, totals

3. **Generic Gemini Prompt**: The receipt parsing prompt was too vague and didn't give Gemini specific instructions on:
   - What constitutes a "receipt item"
   - How to parse item lines (name + quantity + price)
   - Which field is which (subtotal vs total, etc.)

## Solutions Implemented

### 1. Fixed Gemini Model Configuration
**File**: `app.py` (Line 34)
```python
# BEFORE (WRONG - not a valid Gemini model):
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")

# AFTER (CORRECT - valid Gemini model):
RECEIPT_MODEL = os.getenv("RECEIPT_MODEL", "gemini-2.5-flash")
```

### 2. Enhanced Tesseract OCR for Receipts
**File**: `app.py` - `extract_text_from_image()` function

**Improvements**:
- **Upscaling**: Images smaller than 800x600 are upscaled for better OCR accuracy
- **Grayscale Conversion**: Converts to grayscale (standard for Tesseract)
- **Thresholding**: Applies binary thresholding to improve contrast and text clarity
- **Tesseract Config**: Uses optimized settings:
  - `--psm 6`: Assumes uniform text blocks (good for receipts with columns)
  - `--oem 3`: Uses both legacy and LSTM OCR engines for better accuracy
  - `tessedit_char_whitelist`: Restricts to receipt-relevant characters (numbers, letters, $, punctuation)

**Result**: Much better text extraction from receipt images

### 3. Improved Gemini Prompt for Receipt Parsing
**File**: `app.py` - `process_receipt_with_gemini()` function

**Key Improvements**:
- Explicitly lists all fields to extract with descriptions
- Provides specific rules for parsing receipt items
- Explains common receipt formats (e.g., "PRODUCT_NAME QUANTITY PRICE")
- Handles edge cases (missing fields, tax calculation, discounts)
- Better error handling and JSON validation
- Fallback to regex extraction if Gemini fails

**Example Prompt Guidance**:
```
Rules for parsing:
1. Look for line items with product names and prices
2. Items typically have format: "PRODUCT_NAME QUANTITY PRICE"
3. Parse all quantified items (skip header lines, dividers, store info)
4. Tax rate is usually shown as "TAX X,XXX %" format
5. Final total is the largest amount or marked as "TOTAL" "AMOUNT DUE" etc
6. If no subtotal shown: calculate it as (total - tax - discount)
```

## Testing the Fix

To test with your Walmart receipt:

1. Start the Flask app:
```bash
python app.py
```

2. Navigate to `http://127.0.0.1:5000/`

3. Login or register, then upload your Walmart receipt image

4. The app should now:
   - Extract text accurately using improved Tesseract settings
   - Parse items correctly using the enhanced Gemini prompt
   - Show proper merchant name, items with prices, and totals

## Expected Results

For the Walmart receipt you provided, the app should now correctly extract:
- **Merchant**: WALMART
- **Items**: BREAD, GV PNT BUTTR, GV PARM 1602Z, GV CHNK CHKN, 12 CT NITRIL, FOLGERS, SC TWIST UP, EGGS
- **Subtotal**: $46.04
- **Tax**: $0.26 (at 7.000% rate)
- **Total**: $46.30
- **Payment Method**: DEBIT TEND
- **Date**: 11/06/11

## Files Modified

1. `app.py`: Updated OCR extraction, Gemini model config, and receipt parsing logic

## Dependencies
- numpy (for image processing) - already installed
- pillow/PIL (for image handling) - already installed
- pytesseract (for Tesseract OCR) - already installed
- google-genai (for Gemini API) - already installed

No new dependencies needed!
