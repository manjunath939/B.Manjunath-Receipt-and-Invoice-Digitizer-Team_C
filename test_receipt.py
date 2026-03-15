#!/usr/bin/env python3
"""
Test script for receipt OCR and parsing functions.
"""
import os
import sys
import json
from PIL import Image
import io

# Add current directory to path so we can import app functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import extract_text_from_image, process_receipt_with_gemini

def test_ocr_with_sample_text():
    """Test OCR function with sample receipt text."""
    print("Testing OCR and Gemini parsing...")

    # Sample receipt text that might be extracted from OCR
    sample_receipt_text = """
    WALMART
    123 Main St
    Anytown, USA 12345

    Date: 01/15/2024
    Time: 2:30 PM
    Transaction: 123456789

    BREAD 2.88
    MILK 3.49
    EGGS 4.99
    APPLES 5.67
    BANANAS 2.34

    SUBTOTAL 19.37
    TAX 1.36
    TOTAL 20.73

    Thank you for shopping!
    """

    print(f"Sample OCR Text:\n{sample_receipt_text}\n{'='*50}")

    # Test Gemini processing
    result = process_receipt_with_gemini(sample_receipt_text)

    print(f"Parsed Result:\n{json.dumps(result, indent=2)}")

    # Additional test: simulate OCR output with leading asterisks
    noisy = "*\n* WALMART *\n" + sample_receipt_text
    print("\nTesting noisy OCR with asterisks")
    noisy_result = process_receipt_with_gemini(noisy)
    print(f"Noisy Parsed Result:\n{json.dumps(noisy_result, indent=2)}")
    return result

    return result

if __name__ == "__main__":
    test_ocr_with_sample_text()