from flask import Flask, request, render_template, redirect, session, jsonify, url_for, send_file
import sqlite3
from flask_bcrypt import Bcrypt
from authlib.integrations.flask_client import OAuth
import os
import json
import hashlib
import re
from datetime import datetime
from PIL import Image
import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import numpy as np

import pytesseract
import google.genai as genai

# Configure pytesseract path for Windows
pytesseract.pytesseract.pytesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "CHANGE_THIS_TO_RANDOM_SECRET_KEY")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

bcrypt = Bcrypt(app)

# Configure Gemini API (expects GEMINI_API_KEY env var)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Chat-only API key (use provided key by default; can be overridden with CHAT_API_KEY env var)
CHAT_API_KEY = os.getenv("CHAT_API_KEY")
# Receipt extraction model - use gemini-2.5-flash (MUST be a valid Gemini model, not gpt-4o!)
RECEIPT_MODEL = os.getenv("RECEIPT_MODEL", "gemini-2.5-flash")


def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# Initialize DB schema
with get_db() as conn:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        password TEXT,
        is_admin INTEGER DEFAULT 0,
        last_login TEXT,
        active INTEGER DEFAULT 0
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS receipts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        merchant_name TEXT,
        total_amount REAL,
        tax_amount REAL,
        discount_amount REAL,
        currency TEXT,
        receipt_date TEXT,
        payment_method TEXT,
        receipt_number TEXT,
        vendor_address TEXT,
        image_path TEXT,
        raw_ocr_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS receipt_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id INTEGER NOT NULL,
        item_name TEXT,
        quantity REAL,
        unit_price REAL,
        total_price REAL,
        category TEXT,
        FOREIGN KEY(receipt_id) REFERENCES receipts(id)
    )
    """)
    conn.commit()

    # ensure columns exist for older databases
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()


def extract_text_from_image(image_bytes):
    """Extract text from image using pytesseract with receipt-optimized settings."""
    try:
        image = Image.open(io.BytesIO(image_bytes))

        # Preprocess image for better OCR on receipts
        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Upscale for better OCR (receipts are often small/blurry)
        width, height = image.size
        if width < 800 or height < 600:
            scale_factor = max(800 / width, 600 / height)
            new_size = (int(width * scale_factor), int(height * scale_factor))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        # Convert to grayscale for Tesseract
        image = image.convert('L')

        # Apply thresholding to improve contrast
        img_array = np.array(image)

        # Use adaptive thresholding for receipts
        # Receipts typically have dark text on light background
        threshold = 127
        img_array = np.where(img_array > threshold, 255, 0).astype(np.uint8)

        image = Image.fromarray(img_array)

        # Use optimized Tesseract config for receipt OCR
        # --psm 6: uniform block of text (receipts are usually single column)
        # --oem 3: use both legacy and LSTM OCR models
        config = '--psm 6 --oem 3'
        text = pytesseract.image_to_string(image, config=config)

        # Debug: print raw OCR text
        print(f"[DEBUG] Raw OCR Text:\n{text}\n{'='*50}")

        return text.strip()
    except Exception as e:
        print(f"[OCR Error] {str(e)}")
        return f"OCR Error: {str(e)}"


def process_receipt_fallback(ocr_text):
    """Simple regex-based fallback for receipt extraction when Gemini API is unavailable."""
    # Extract all prices from the text
    price_pattern = r'\$?(\d+\.\d{2})'
    prices = re.findall(price_pattern, ocr_text)
    prices = [float(p) for p in prices]

    if not prices:
        return {
            "merchant_name": "Unknown Merchant",
            "total_amount": 0,
            "tax_amount": 0,
            "discount_amount": 0,
            "currency": "USD",
            "receipt_date": "2024-01-01",
            "payment_method": "Unknown",
            "receipt_number": "N/A",
            "vendor_address": "N/A",
            "items": []
        }

    # Assume the largest price is the total
    total_amount = max(prices)

    # Extract merchant name (usually first non-empty line)
    lines = [line.strip() for line in ocr_text.split('\n') if line.strip()]
    merchant_name = lines[0] if lines else "Unknown Merchant"

    # Extract tax (look for 'tax' keyword near a price)
    tax_amount = 0
    tax_match = re.search(r'[Tt]ax[\s:]*\$?([\d.]+)', ocr_text)
    if tax_match:
        tax_amount = float(tax_match.group(1))

    # Extract date
    date_match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', ocr_text)
    receipt_date = f"{date_match.group(3)}-{date_match.group(1).zfill(2)}-{date_match.group(2).zfill(2)}" if date_match else "2024-01-01"

    # Better item extraction: look for lines that have both text and prices
    items = []
    lines = ocr_text.split('\n')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue

        # Skip header/footer lines
        if any(skip_word in line.upper() for skip_word in ['SUBTOTAL', 'TAX', 'TOTAL', 'THANK', 'DATE', 'TIME', 'TRANSACTION']):
            continue

        # Look for lines with prices
        price_match = re.search(r'\$?(\d+\.\d{2})', line)
        if price_match:
            price = float(price_match.group(1))

            # Extract item name (everything before the price)
            price_start = line.find(price_match.group(0))
            item_name = line[:price_start].strip()

            # Clean up item name
            if item_name and len(item_name) > 1:
                # Remove common prefixes that aren't part of item names
                item_name = re.sub(r'^\d+\s*', '', item_name)  # Remove leading numbers
                item_name = item_name.strip()

                if item_name:
                    items.append({
                        "item_name": item_name,
                        "quantity": 1,
                        "unit_price": price,
                        "total_price": price,
                        "category": "general"
                    })

    # If no items found, create some from prices (excluding total and tax)
    if not items and len(prices) > 1:
        # Sort prices, assume last is total, second to last might be tax
        sorted_prices = sorted(prices, reverse=True)
        item_prices = sorted_prices[1:]  # Exclude the largest (total)

        if tax_amount > 0 and tax_amount in item_prices:
            item_prices.remove(tax_amount)

        # Create generic items
        for i, price in enumerate(item_prices[:10]):  # Limit to 10 items
            items.append({
                "item_name": f"Item {i+1}",
                "quantity": 1,
                "unit_price": price,
                "total_price": price,
                "category": "general"
            })

    return {
        "merchant_name": merchant_name,
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "discount_amount": 0,
        "currency": "USD",
        "receipt_date": receipt_date,
        "payment_method": "Unknown",
        "receipt_number": "N/A",
        "vendor_address": "N/A",
        "items": items
    }


def process_receipt_with_gemini(ocr_text):
    """Call Gemini to extract structured JSON from OCR text. Fallback to regex if API fails."""
    if not GEMINI_API_KEY:
        return process_receipt_fallback(ocr_text)

    try:
        # Clean up OCR text first
        cleaned_text = re.sub(r'\n+', '\n', ocr_text)
        cleaned_text = re.sub(r' +', ' ', cleaned_text)
        cleaned_text = cleaned_text.strip()

        print(f"[DEBUG] Cleaned OCR Text for Gemini:\n{cleaned_text}\n{'='*50}")

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""You are an expert receipt OCR parser. Extract structured data from this OCR-scanned receipt text.

CRITICAL REQUIREMENT: Return ONLY valid JSON (no markdown, no code blocks, no explanation).
Start with {{ and end with }}.

Required JSON fields:
- merchant_name: Store name (usually at top of receipt)
- vendor_address: Full address if present, otherwise 'N/A'
- receipt_date: Date in YYYY-MM-DD format (find dates like 01/01/25, Jan 1 2025, etc)
- receipt_number: Transaction/receipt number if visible
- payment_method: DEBIT, CREDIT, CASH, or UNKNOWN
- currency: USD
- items: Array of ALL purchased items with fields: item_name, quantity, unit_price, total_price, category
- subtotal_amount: Sum of all item prices BEFORE tax
- tax_amount: Tax amount charged (0 if not found)
- discount_amount: Discount amount (0 if not found)
- total_amount: Final total INCLUDING tax

ITEM EXTRACTION RULES (MOST IMPORTANT):
1. Parse EVERY product line in the receipt - be thorough
2. Each item is typically one line with: product_name + quantity + price
3. If quantity is not visible, use quantity=1
4. Set unit_price and total_price to the same value for single-quantity items
5. Clean product names - remove leading item codes that aren't part of description
6. Categorize items: GROCERY, FOOD, BEVERAGE, HOUSEHOLD, HEALTH, OTHER
7. Example correct parsing: "BREAD 2.88 M" → item_name="BREAD", quantity=1, unit_price=2.88, total_price=2.88
8. Example: "GV PNT BUTR 007842370003F 1 3.84 N" → item_name="GV PNT BUTR", quantity=1, price=3.84

FINANCIAL PARSING:
1. SUBTOTAL = sum of all item prices
2. Find TAX line - it usually says "TAX" with an amount
3. TOTAL = SUBTOTAL + TAX - DISCOUNT
4. All price fields must be numbers (no $ symbols)
5. Do not invent prices - extract exact values from receipt

Receipt OCR text to parse:
{cleaned_text}

Return ONLY the JSON object:
"""

        client = genai.Client(api_key=GEMINI_API_KEY)
        chat = client.chats.create(model='gemini-1.5-flash')
        response = chat.send_message(prompt)
        json_str = response.text.strip()

        print(f"[DEBUG] Gemini Raw Response:\n{json_str}\n{'='*50}")

        # clean up markdown code blocks if present
        if json_str.startswith('```json'):
            json_str = json_str[7:]
        if json_str.startswith('```'):
            json_str = json_str[3:]
        if json_str.endswith('```'):
            json_str = json_str[:-3]

        json_str = json_str.strip()

        # Parse JSON
        data = json.loads(json_str)

        # Ensure required fields exist with proper defaults
        data.setdefault('merchant_name', 'Unknown Merchant')
        data.setdefault('total_amount', data.get('subtotal_amount', 0) + data.get('tax_amount', 0))
        data.setdefault('tax_amount', 0)
        data.setdefault('discount_amount', 0)
        data.setdefault('currency', 'USD')
        data.setdefault('receipt_date', '2024-01-01')
        data.setdefault('payment_method', 'Unknown')
        data.setdefault('receipt_number', 'N/A')
        data.setdefault('vendor_address', 'N/A')
        data.setdefault('items', [])

        # Validate and clean items
        cleaned_items = []
        for item in data.get('items', []):
            if isinstance(item, dict):
                cleaned_item = {
                    'item_name': str(item.get('item_name', '')).strip(),
                    'quantity': int(item.get('quantity', 1)),
                    'unit_price': float(item.get('unit_price', 0)),
                    'total_price': float(item.get('total_price', item.get('unit_price', 0))),
                    'category': str(item.get('category', 'OTHER'))
                }
                if cleaned_item['unit_price'] > 0:
                    cleaned_items.append(cleaned_item)

        data['items'] = cleaned_items

        # Recalculate totals to ensure consistency
        subtotal = sum(item['total_price'] for item in cleaned_items)
        data['subtotal_amount'] = round(subtotal, 2)
        data['total_amount'] = round(subtotal + data.get('tax_amount', 0) - data.get('discount_amount', 0), 2)

        print(f"[DEBUG] Final Parsed Data:\n{json.dumps(data, indent=2)}\n{'='*50}")

        return data

    except json.JSONDecodeError as e:
        print(f"[Fallback Mode] JSON parsing failed: {str(e)}. Using regex extraction instead.")
        print(f"[Invalid JSON] {json_str}")
        return process_receipt_fallback(ocr_text)
    except Exception as e:
        print(f"[Fallback Mode] Gemini API unavailable: {type(e).__name__}: {str(e)}. Using regex extraction instead.")
        return process_receipt_fallback(ocr_text)


def generate_receipt_pdf(receipt_data):
    """Generate a formatted PDF receipt."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.5*inch, leftMargin=0.5*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Heading1'],
        fontSize=24, textColor=colors.HexColor('#3366ff'),
        spaceAfter=30, alignment=1  # center
    )
    heading_style = ParagraphStyle(
        'CustomHeading', parent=styles['Heading2'],
        fontSize=12, textColor=colors.HexColor('#00c6ff'),
        spaceAfter=10
    )
    
    # Title
    elements.append(Paragraph("RECEIPT", title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    # Merchant info
    merchant = receipt_data.get('merchant_name', 'N/A')
    elements.append(Paragraph(f"<b>{merchant}</b>", heading_style))
    vendor_addr = receipt_data.get('vendor_address', 'N/A')
    elements.append(Paragraph(f"Address: {vendor_addr}", styles['Normal']))
    elements.append(Spacer(1, 0.2*inch))
    
    # Receipt details table
    receipt_details = [
        ['Date:', receipt_data.get('receipt_date', 'N/A')],
        ['Receipt #:', receipt_data.get('receipt_number', 'N/A')],
        ['Payment Method:', receipt_data.get('payment_method', 'N/A')],
    ]
    detail_table = Table(receipt_details, colWidths=[2*inch, 2.5*inch])
    detail_table.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Items table
    items_data = [['Item', 'Qty', 'Price', 'Total']]
    for item in receipt_data.get('items', []):
        items_data.append([
            item.get('item_name', ''),
            str(item.get('quantity', '')),
            f"${item.get('unit_price', 0):.2f}",
            f"${item.get('total_price', 0):.2f}"
        ])
    if len(items_data) == 1:
        items_data.append(['No items', '', '', ''])
    items_table = Table(items_data, colWidths=[2*inch, 0.8*inch, 1.2*inch, 1.2*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3366ff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 11),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')])
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Totals
    currency = receipt_data.get('currency', 'USD')
    totals_data = [
        ['Subtotal:', f"{currency} ${receipt_data.get('total_amount', 0) - receipt_data.get('tax_amount', 0):.2f}"],
        ['Tax:', f"{currency} ${receipt_data.get('tax_amount', 0):.2f}"],
        ['Discount:', f"{currency} -${receipt_data.get('discount_amount', 0):.2f}"],
        ['TOTAL:', f"{currency} ${receipt_data.get('total_amount', 0):.2f}"]
    ]
    totals_table = Table(totals_data, colWidths=[2*inch, 2.5*inch])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONT', (0, 0), (-1, -2), 'Helvetica', 10),
        ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#3366ff')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(totals_table)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer


    """Fallback receipt processing without API - extracts basic info from OCR text."""
    import re
    
    # Try to extract total amount (look for currency symbols and numbers)
    total_amount = 0.0
    amounts = re.findall(r'[\$₹€]?\s*(\d+\.?\d*)', ocr_text)
    if amounts:
        total_amount = float(amounts[-1])  # Usually the last amount is the total
    
    # Try to extract date
    dates = re.findall(r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', ocr_text)
    receipt_date = dates[0] if dates else datetime.now().strftime('%Y-%m-%d')
    
    # Basic structure
    data = {
        "merchant_name": "Merchant (OCR Only - No API)",
        "total_amount": total_amount,
        "tax_amount": round(total_amount * 0.1, 2),  # Estimate 10% tax
        "discount_amount": 0.0,
        "currency": "USD",
        "receipt_date": receipt_date,
        "payment_method": "Card",
        "receipt_number": "N/A",
        "vendor_address": "Not extracted",
        "items": [
            {
                "item_name": "Items from receipt",
                "quantity": 1,
                "unit_price": total_amount,
                "total_price": total_amount,
                "category": "General"
            }
        ]
    }
    
    return data


def generate_receipt_pdf(receipt_data):
    """Generate a formatted PDF receipt."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.5*inch, leftMargin=0.5*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Heading1'],
        fontSize=24, textColor=colors.HexColor('#3366ff'),
        spaceAfter=30, alignment=1  # center
    )
    heading_style = ParagraphStyle(
        'CustomHeading', parent=styles['Heading2'],
        fontSize=12, textColor=colors.HexColor('#00c6ff'),
        spaceAfter=10
    )
    
    # Title
    elements.append(Paragraph("RECEIPT", title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    # Merchant info
    merchant = receipt_data.get('merchant_name', 'N/A')
    elements.append(Paragraph(f"<b>{merchant}</b>", heading_style))
    vendor_addr = receipt_data.get('vendor_address', 'N/A')
    elements.append(Paragraph(f"Address: {vendor_addr}", styles['Normal']))
    elements.append(Spacer(1, 0.2*inch))
    
    # Receipt details table
    receipt_details = [
        ['Date:', receipt_data.get('receipt_date', 'N/A')],
        ['Receipt #:', receipt_data.get('receipt_number', 'N/A')],
        ['Payment Method:', receipt_data.get('payment_method', 'N/A')],
    ]
    detail_table = Table(receipt_details, colWidths=[2*inch, 2.5*inch])
    detail_table.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Items table
    items_data = [['Item', 'Qty', 'Price', 'Total']]
    for item in receipt_data.get('items', []):
        items_data.append([
            item.get('item_name', ''),
            str(item.get('quantity', '')),
            f"${item.get('unit_price', 0):.2f}",
            f"${item.get('total_price', 0):.2f}"
        ])
    items_table = Table(items_data, colWidths=[2*inch, 0.8*inch, 1.2*inch, 1.2*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3366ff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 11),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')])
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Totals
    currency = receipt_data.get('currency', 'USD')
    totals_data = [
        ['Subtotal:', f"{currency} ${receipt_data.get('total_amount', 0) - receipt_data.get('tax_amount', 0):.2f}"],
        ['Tax:', f"{currency} ${receipt_data.get('tax_amount', 0):.2f}"],
        ['Discount:', f"{currency} ${receipt_data.get('discount_amount', 0):.2f}"],
        ['TOTAL:', f"{currency} ${receipt_data.get('total_amount', 0):.2f}"]
    ]
    totals_table = Table(totals_data, colWidths=[2*inch, 2.5*inch])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONT', (0, 0), (-1, -2), 'Helvetica', 10),
        ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#3366ff')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(totals_table)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer


def save_receipt_to_db(user_id, receipt_data, image_path, raw_ocr):
    """Save receipt data and items to database."""
    try:
        conn = get_db()
        cursor = conn.execute(
            """
            INSERT INTO receipts(
                user_id, merchant_name, total_amount, tax_amount, discount_amount,
                currency, receipt_date, payment_method, receipt_number, vendor_address,
                image_path, raw_ocr_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                receipt_data.get("merchant_name", ""),
                receipt_data.get("total_amount", 0),
                receipt_data.get("tax_amount", 0),
                receipt_data.get("discount_amount", 0),
                receipt_data.get("currency", "USD"),
                receipt_data.get("receipt_date", ""),
                receipt_data.get("payment_method", ""),
                receipt_data.get("receipt_number", ""),
                receipt_data.get("vendor_address", ""),
                image_path,
                raw_ocr,
            ),
        )
        receipt_id = cursor.lastrowid

        for item in receipt_data.get("items", []):
            conn.execute(
                """
                INSERT INTO receipt_items(
                    receipt_id, item_name, quantity, unit_price, total_price, category
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item.get("item_name", ""),
                    item.get("quantity", 0),
                    item.get("unit_price", 0),
                    item.get("total_price", 0),
                    item.get("category", ""),
                ),
            )
        conn.commit()
        return receipt_id
    except Exception:
        return None


# ------------------- Routes ------------------- #


@app.route("/")
def home():
    return render_template("landing.html")


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


# --------- Admin Support Routes ---------- #
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    # GET serves the login page, POST handles authentication
    if request.method == "GET":
        # clear any previous admin flag so the link always shows login page
        session.pop("admin", None)
        return render_template("admin_login.html")
    data = request.json or {}
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (data.get("email"),)).fetchone()
    if user and user["is_admin"] == 1 and bcrypt.check_password_hash(user["password"], data.get("password")):
        session["user"] = user["email"]
        session["admin"] = True
        # update last login and mark active
        conn.execute("UPDATE users SET last_login=?, active=1 WHERE id=?", (datetime.now(), user["id"]))
        conn.commit()
        return {"msg": "admin"}
    return {"error": "invalid"}, 401


@app.route("/admin_dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/admin_login")
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]
    active_count = conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
    total_receipts = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    users = conn.execute(
        "SELECT id, email, is_admin, last_login, active, (SELECT COUNT(*) FROM receipts r WHERE r.user_id = users.id) as receipts FROM users"
    ).fetchall()
    return render_template("admin_dashboard.html", total_users=total_users,
                           admin_count=admin_count, active_count=active_count,
                           total_receipts=total_receipts,
                           users=[dict(u) for u in users])


@app.post("/api/toggle_admin")
def api_toggle_admin():
    if not session.get("admin"):
        return {"error": "unauthorized"}, 401
    data = request.json or {}
    email = data.get("email")
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        return {"error": "not found"}, 404
    new_status = 0 if user["is_admin"] else 1
    conn.execute("UPDATE users SET is_admin=? WHERE email=?", (new_status, email))
    conn.commit()
    return {"msg": "updated"}


@app.route("/register_page")
def register_page():
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email=?", (session["user"],)).fetchone()
    user_id = user["id"] if user else None

    receipts = conn.execute("SELECT * FROM receipts WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    receipts_data = [dict(r) for r in receipts]
    return render_template("dashboard.html", email=session["user"], receipts=receipts_data)


@app.route("/logout")
def logout():
    # mark user inactive when logging out
    if "user" in session:
        conn = get_db()
        conn.execute("UPDATE users SET active=0 WHERE email=?", (session.get("user"),))
        conn.commit()
    session.clear()
    return redirect("/")


@app.post("/register")
def register():
    data = request.json
    hashed = bcrypt.generate_password_hash(data["password"]).decode()
    try:
        conn = get_db()
        conn.execute("INSERT INTO users(email,password) VALUES(?,?)", (data["email"], hashed))
        conn.commit()
        return {"msg": "registered"}
    except sqlite3.IntegrityError:
        return {"error": "email exists"}, 400


@app.post("/login")
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (data["email"],)).fetchone()
    if user and user["password"] != "GOOGLE_USER" and bcrypt.check_password_hash(user["password"], data["password"]):
        session["user"] = user["email"]
        # update last_login and mark active
        conn.execute("UPDATE users SET last_login=?, active=1 WHERE id=?", (datetime.now(), user["id"]))
        conn.commit()
        return {"msg": "ok"}
    return {"error": "invalid"}, 401


# Upload endpoint
@app.post("/api/upload_receipt")
def upload_receipt():
    if "user" not in session:
        return {"error": "unauthorized"}, 401

    if "image" not in request.files:
        return {"error": "no image provided"}, 400

    file = request.files["image"]
    if file.filename == "":
        return {"error": "no file selected"}, 400

    try:
        image_bytes = file.read()

        ocr_text = extract_text_from_image(image_bytes)
        if isinstance(ocr_text, str) and ocr_text.startswith("OCR Error"):
            return {"error": ocr_text}, 400

        receipt_data = process_receipt_with_gemini(ocr_text)

        conn = get_db()
        user = conn.execute("SELECT id FROM users WHERE email=?", (session["user"],)).fetchone()
        user_id = user["id"]

        # Save image
        filename = hashlib.md5(image_bytes).hexdigest() + ".png"
        image_path = os.path.join("static", "receipts", filename)
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        with open(image_path, "wb") as f:
            f.write(image_bytes)

        receipt_id = save_receipt_to_db(user_id, receipt_data, image_path, ocr_text)
        if receipt_id:
            return {"msg": "receipt processed", "receipt_id": receipt_id, "data": receipt_data}, 201
        return {"error": "failed to save receipt"}, 500
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/api/receipt/<int:receipt_id>")
def get_receipt_detail(receipt_id):
    if "user" not in session:
        return {"error": "unauthorized"}, 401
    conn = get_db()
    receipt = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    if not receipt:
        return {"error": "receipt not found"}, 404
    items = conn.execute("SELECT * FROM receipt_items WHERE receipt_id=?", (receipt_id,)).fetchall()
    receipt_data = dict(receipt)
    receipt_data["items"] = [dict(i) for i in items]
    return receipt_data


@app.get("/api/receipts")
def get_user_receipts():
    if "user" not in session:
        return {"error": "unauthorized"}, 401
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email=?", (session["user"],)).fetchone()
    user_id = user["id"]
    receipts = conn.execute(
        "SELECT id, merchant_name, total_amount, tax_amount, discount_amount, currency, receipt_date, created_at FROM receipts WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in receipts]


@app.get("/api/admin_receipts")
def get_admin_receipts():
    if not session.get("admin"):
        return {"error": "unauthorized"}, 401
    conn = get_db()
    receipts = conn.execute(
        "SELECT r.id, r.merchant_name, r.total_amount, r.currency, r.receipt_date, r.created_at, u.email as user_email FROM receipts r JOIN users u ON r.user_id = u.id ORDER BY r.created_at DESC LIMIT 200"
    ).fetchall()
    return [dict(r) for r in receipts]


@app.get("/api/download_receipt/<int:receipt_id>")
def download_receipt(receipt_id):
    # allow either the receipt owner (user session) or an admin
    if ("user" not in session) and (not session.get("admin")):
        return {"error": "unauthorized"}, 401
    conn = get_db()
    receipt = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    if not receipt:
        return {"error": "receipt not found"}, 404
    items = conn.execute("SELECT * FROM receipt_items WHERE receipt_id=?", (receipt_id,)).fetchall()
    receipt_data = dict(receipt)
    receipt_data["items"] = [dict(i) for i in items]
    
    # Generate PDF
    pdf_buffer = generate_receipt_pdf(receipt_data)
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'receipt_{receipt_id}.pdf'
    )


@app.post("/api/chat")
def api_chat():
    if "user" not in session and not session.get("admin"):
        return {"error": "unauthorized"}, 401
    data = request.json or {}
    message = data.get("message", "")
    if not message:
        return {"error": "no message"}, 400
    # Use chat-specific API key (CHAT_API_KEY); fall back to a lightweight local reply if missing
    if not CHAT_API_KEY:
        fallback_reply = f"AI unavailable (no chat API key). Echo: {message}"
        return {"reply": fallback_reply}
    try:
        client = genai.Client(api_key=CHAT_API_KEY)
        # using gpt-4o-mini model (former gemini options are unsupported on v1beta)
        # try configured model; catch NOT_FOUND and return error message
        try:
            # Use gemini-2.5-flash specifically for chatbot as requested
            chat = client.chats.create(model="gemini-2.5-flash")
            res = chat.send_message(message)
        except Exception as e:
            return {"error": str(e)}, 500
        # Extract text from GenerateContentResponse
        reply = ''
        try:
            reply = getattr(res, 'text', '') or ''
        except Exception:
            reply = ''
        reply = reply.strip() if isinstance(reply, str) else ''
        if not reply:
            reply = "(no response from model)"
        print(f"[chat] prompt={message!r} reply={reply!r}")
        return {"reply": reply}
    except Exception as e:
        print(f"[chat error] {e}")
        return {"error": str(e)}, 500


@app.get("/api/admin_stats")
def api_admin_stats():
    if not session.get("admin"):
        return {"error": "unauthorized"}, 401
    conn = get_db()
    receipts = conn.execute("SELECT SUM(total_amount) as total_spent, COUNT(*) as receipt_count, AVG(total_amount) as avg_amount FROM receipts").fetchone()
    merchants = conn.execute(
        "SELECT merchant_name, COUNT(*) as count, SUM(total_amount) as total FROM receipts GROUP BY merchant_name ORDER BY total DESC LIMIT 10"
    ).fetchall()
    categories = conn.execute(
        "SELECT ri.category, SUM(ri.total_price) as total FROM receipt_items ri JOIN receipts r ON ri.receipt_id = r.id GROUP BY ri.category ORDER BY total DESC"
    ).fetchall()
    total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"] or 0
    admin_count = conn.execute("SELECT COUNT(*) as count FROM users WHERE is_admin=1").fetchone()["count"] or 0
    active_count = conn.execute("SELECT COUNT(*) as count FROM users WHERE active=1").fetchone()["count"] or 0
    total_spent = round(receipts["total_spent"] or 0, 2)
    # per-user totals (email, total, receipts)
    per_user = conn.execute(
        "SELECT u.email as email, COUNT(r.id) as receipt_count, COALESCE(SUM(r.total_amount),0) as total FROM users u LEFT JOIN receipts r ON r.user_id = u.id GROUP BY u.id ORDER BY total DESC LIMIT 50"
    ).fetchall()
    per_user_list = [{"email": dict(u)["email"], "receipt_count": dict(u)["receipt_count"], "total": round(dict(u)["total"], 2)} for u in per_user]
    return {
        "stats": {
            "total_users": total_users,
            "admin_count": admin_count,
            "active_count": active_count,
            "total_receipts": receipts["receipt_count"] or 0,
            "total_spent": total_spent,
            "avg_amount": round(receipts["avg_amount"] or 0, 2),
            "per_user": per_user_list,
        },
        "merchants": [{"merchant_name": dict(m)["merchant_name"], "total": round(dict(m)["total"], 2), "count": dict(m)["count"]} for m in merchants],
        "categories": [{"category": dict(c)["category"], "total": round(dict(c)["total"], 2)} for c in categories],
    }


@app.post("/api/admin_ocr")
def api_admin_ocr():
    if not session.get("admin"):
        return {"error": "unauthorized"}, 401
    if "image" not in request.files:
        return {"error": "no image"}, 400
    try:
        image_bytes = request.files["image"].read()
        ocr_text = extract_text_from_image(image_bytes)
        parsed = {}
        if GEMINI_API_KEY:
            try:
                parsed = process_receipt_with_gemini(ocr_text)
            except Exception:
                parsed = {}
        return {"ocr_text": ocr_text, "data": parsed}
    except Exception as e:
        return {"error": str(e)}, 500


@app.post("/api/download_temp_pdf")
def download_temp_pdf():
    payload = request.json or {}
    data = payload.get("data") or {}
    if not data:
        return {"error": "no data"}, 400
    try:
        pdf = generate_receipt_pdf(data)
        return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name='temp_receipt.pdf')
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/api/admin_analytics")
def get_admin_analytics():
    if not session.get("admin"):
        return {"error": "unauthorized"}, 401
    conn = get_db()

    # Top merchants (system-wide)
    merchants = conn.execute(
        "SELECT merchant_name, COUNT(*) as count, SUM(total_amount) as total FROM receipts GROUP BY merchant_name ORDER BY total DESC LIMIT 10"
    ).fetchall()

    # Categories breakdown (system-wide)
    categories = conn.execute(
        "SELECT ri.category, SUM(ri.total_price) as total FROM receipt_items ri GROUP BY ri.category ORDER BY total DESC"
    ).fetchall()

    return {
        "merchants": [{"merchant_name": dict(m)["merchant_name"], "total": round(dict(m)["total"], 2), "count": dict(m)["count"]} for m in merchants],
        "categories": [{"category": dict(c)["category"], "total": round(dict(c)["total"], 2)} for c in categories],
    }


@app.get("/api/dashboard_stats")
def get_dashboard_stats():
    if "user" not in session:
        return {"error": "unauthorized"}, 401
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email=?", (session["user"],)).fetchone()
    user_id = user["id"]

    # Basic stats
    receipts = conn.execute(
        "SELECT SUM(total_amount) as total_spent, COUNT(*) as receipt_count, SUM(tax_amount) as total_tax, AVG(total_amount) as avg_amount FROM receipts WHERE user_id=?",
        (user_id,),
    ).fetchone()

    # Top merchants
    merchants = conn.execute(
        "SELECT merchant_name, COUNT(*) as count, SUM(total_amount) as total FROM receipts WHERE user_id=? GROUP BY merchant_name ORDER BY total DESC LIMIT 10",
        (user_id,),
    ).fetchall()

    # Categories breakdown
    categories = conn.execute(
        "SELECT ri.category, SUM(ri.total_price) as total FROM receipt_items ri JOIN receipts r ON ri.receipt_id = r.id WHERE r.user_id=? GROUP BY ri.category ORDER BY total DESC",
        (user_id,),
    ).fetchall()

    # Payment method distribution
    payment_methods = conn.execute(
        "SELECT payment_method, COUNT(*) as count, SUM(total_amount) as total FROM receipts WHERE user_id=? GROUP BY payment_method ORDER BY total DESC",
        (user_id,),
    ).fetchall()

    # Monthly spending trend (last 12 months)
    monthly_data = conn.execute("""
        SELECT strftime('%Y-%m', receipt_date) as month, SUM(total_amount) as total, COUNT(*) as count
        FROM receipts WHERE user_id=? AND receipt_date IS NOT NULL
        GROUP BY strftime('%Y-%m', receipt_date)
        ORDER BY month DESC LIMIT 12
    """, (user_id,)).fetchall()
    monthly_data = list(reversed([dict(m) for m in monthly_data]))

    # Top items by frequency
    top_items = conn.execute("""
        SELECT item_name, COUNT(*) as frequency, SUM(total_price) as total_spent
        FROM receipt_items ri JOIN receipts r ON ri.receipt_id = r.id
        WHERE r.user_id=?
        GROUP BY item_name ORDER BY frequency DESC LIMIT 8
    """, (user_id,)).fetchall()

    # build human-readable summaries
    summary = {}
    if monthly_data:
        total_recent = sum([m.get("total", 0) for m in monthly_data])
        summary["monthly"] = f"Over the last {len(monthly_data)} months you've spent ${total_recent:.2f}."
    if merchants:
        first = dict(merchants[0])
        summary["merchants"] = f"Your top merchant is {first['merchant_name']} with ${first['total']:.2f} spent."
    if categories:
        first = dict(categories[0])
        summary["categories"] = f"Most of your spending falls under {first['category']}: ${first['total']:.2f}."
    if payment_methods:
        first = dict(payment_methods[0])
        summary["payment_methods"] = f"You primarily pay using {first['payment_method']}."
    if top_items:
        first = dict(top_items[0])
        summary["top_items"] = f"Your most frequent item is {first['item_name']} ({first['frequency']} times)."

    return {
        "stats": {
            "total_spent": round(receipts["total_spent"] or 0, 2),
            "receipt_count": receipts["receipt_count"] or 0,
            "total_tax": round(receipts["total_tax"] or 0, 2),
            "avg_amount": round(receipts["avg_amount"] or 0, 2),
        },
        "merchants": [{"merchant_name": dict(m)["merchant_name"], "total": round(dict(m)["total"], 2), "count": dict(m)["count"]} for m in merchants],
        "categories": [{"category": dict(c)["category"], "total": round(dict(c)["total"], 2)} for c in categories],
        "payment_methods": [{"payment_method": dict(p)["payment_method"], "total": round(dict(p)["total"], 2), "count": dict(p)["count"]} for p in payment_methods],
        "monthly_trend": monthly_data,
        "top_items": [{"item_name": dict(i)["item_name"], "frequency": dict(i)["frequency"], "total_spent": round(dict(i)["total_spent"], 2)} for i in top_items],
        "summary": summary,
    }


if __name__ == "__main__":
    app.run(debug=True)
