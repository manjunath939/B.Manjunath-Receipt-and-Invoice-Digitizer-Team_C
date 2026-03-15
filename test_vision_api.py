#!/usr/bin/env python3
"""Test script for Gemini Vision API receipt extraction."""

import os
import sys
import json
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Set up environment
os.environ.setdefault("FLASK_ENV", "development")

# Import after path setup
from app import app, process_receipt_with_vision_api, process_receipt_fallback, extract_text_from_image

def test_vision_api_with_receipt(receipt_path):
    """Test Vision API on a real receipt image."""
    print(f"\n{'='*60}")
    print(f"Testing Vision API with receipt: {receipt_path}")
    print(f"{'='*60}\n")
    
    if not os.path.exists(receipt_path):
        print(f"ERROR: Receipt file not found: {receipt_path}")
        return False
    
    # Read receipt image
    with open(receipt_path, 'rb') as f:
        image_bytes = f.read()
    
    print(f"Image size: {len(image_bytes)} bytes")
    print(f"File format: {receipt_path.split('.')[-1].upper()}")
    
    # Test Vision API
    print("\n[1] Testing Gemini Vision API...")
    print("-" * 40)
    receipt_data = process_receipt_with_vision_api(image_bytes)
    
    if receipt_data:
        print("✓ Vision API SUCCESS")
        print(f"\nExtracted Data:")
        print(f"  Merchant: {receipt_data.get('merchant_name', 'N/A')}")
        print(f"  Items: {len(receipt_data.get('items', []))}")
        print(f"  Subtotal: ${receipt_data.get('subtotal_amount', 0):.2f}")
        print(f"  Tax: ${receipt_data.get('tax_amount', 0):.2f}")
        print(f"  Total: ${receipt_data.get('total_amount', 0):.2f}")
        
        if receipt_data.get('items'):
            print(f"\n  Items detected:")
            for i, item in enumerate(receipt_data.get('items', [])[:5], 1):
                print(f"    {i}. {item.get('item_name', 'Unknown')} x{item.get('quantity', 0)} @ ${item.get('unit_price', 0):.2f}")
            if len(receipt_data.get('items', [])) > 5:
                print(f"    ... and {len(receipt_data.get('items', [])) - 5} more items")
        
        print(f"\nFull JSON Response:\n{json.dumps(receipt_data, indent=2)}")
        return True
    else:
        print("✗ Vision API FAILED - falling back to Tesseract")
        
        # Try fallback
        print("\n[2] Testing Tesseract OCR fallback...")
        print("-" * 40)
        ocr_text = extract_text_from_image(image_bytes)
        print(f"OCR Text (first 300 chars):\n{ocr_text[:300]}...\n")
        
        receipt_data = process_receipt_fallback(ocr_text)
        print(f"Fallback extraction result:")
        print(f"  Merchant: {receipt_data.get('merchant_name', 'N/A')}")
        print(f"  Total: ${receipt_data.get('total_amount', 0):.2f}")
        print(f"  Items: {len(receipt_data.get('items', []))}")
        return False


def main():
    """Find and test receipts."""
    receipt_dir = Path("static/receipts")
    
    if not receipt_dir.exists():
        print("ERROR: Receipt directory not found!")
        return
    
    receipts = list(receipt_dir.glob("*.png")) + list(receipt_dir.glob("*.jpg"))
    
    if not receipts:
        print("No receipt images found in static/receipts/")
        print("Please upload a receipt first or check the directory.")
        return
    
    print(f"Found {len(receipts)} receipt image(s)")
    
    # Test first receipt
    test_receipt = str(receipts[0])
    success = test_vision_api_with_receipt(test_receipt)
    
    if success:
        print("\n" + "="*60)
        print("✓ Vision API is working correctly!")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("✗ Vision API extraction failed, using fallback")
        print("="*60)


if __name__ == "__main__":
    with app.app_context():
        main()
