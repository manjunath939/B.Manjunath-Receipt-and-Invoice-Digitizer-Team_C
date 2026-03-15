#!/usr/bin/env python3
"""Send a receipt image straight to the revised upload_receipt endpoint."""

import requests
import os

# Ensure the Flask server is running on localhost:5000
URL = "http://127.0.0.1:5000/api/upload_receipt"

# choose one of the existing receipt images
receipt_path = "static/receipts/04462c5f44421443f42428f0a500a631.png"

if not os.path.exists(receipt_path):
    print("Receipt image not found, please upload first.")
    exit(1)

with open(receipt_path, 'rb') as f:
    files = {'image': (os.path.basename(receipt_path), f, 'image/png')}
    # optional override key via param
    data = {'api_key': os.getenv('GEMINI_API_KEY')}
    resp = requests.post(URL, files=files, data=data)
    try:
        print('Status:', resp.status_code)
        print(resp.json())
    except Exception:
        print('Response text:', resp.text)
