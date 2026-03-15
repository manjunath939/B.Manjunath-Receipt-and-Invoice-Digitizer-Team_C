#!/usr/bin/env python3
"""Test the correct google-genai API for vision content."""

import google.genai as genai
import base64
import os

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set")
    exit(1)

print("Testing google-genai vision API...")

client = genai.Client(api_key=API_KEY)

# Try to generate a simple text response first
print("\n[Test 1] Text generation")
try:
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents="Say 'Hello World'"
    )
    print(f"Success: {response.text[:100]}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")

# Try with vision
print("\n[Test 2] Vision API with image")
try:
    # Use a small test image for vision
    receipt_path = "static/receipts/04462c5f44421443f42428f0c0a500a630.png"
    
    if os.path.exists(receipt_path):
        with open(receipt_path, 'rb') as f:
            image_bytes = f.read()
        
        image_b64 = base64.standard_b64encode(image_bytes).decode()
        
        # Try with inline_data
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_b64
                            }
                        },
                        {
                            "text": "What is in this image? Be brief."
                        }
                    ]
                }
            ]
        )
        print(f"Vision Success: {response.text[:200]}")
    else:
        print("Receipt image not found, skipping vision test")
        
except Exception as e:
    print(f"Vision Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
