#!/usr/bin/env python3
"""Test to find the correct API pattern for google-genai 1.64.0"""

import google.genai as genai
import os

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set")
    exit(1)

print(f"genai version: {genai.version.__version__}")
print(f"API_KEY: {API_KEY[:20]}...")

# Try different patterns
print("\n" + "="*60)
print("Testing API patterns...")
print("="*60)

# Pattern 1: Try Client + generate_content directly
print("\n[Pattern 1] Client().models.generate_content()")
try:
    client = genai.Client(api_key=API_KEY)
    # Try to find the right API
    print(f"Client type: {type(client)}")
    print(f"Client attrs: {[x for x in dir(client) if not x.startswith('_')][:10]}")
    
    # Try with models
    models = client.models.list()
    print(f"Available models: {[m.name for m in models.models[:3]]}")
    
except Exception as e:
    print(f"Error: {e}")

# Pattern 2: Check if there's a synchronous API
print("\n[Pattern 2] Check genai attributes")
try:
    if hasattr(genai, 'GenerativeModel'):
        print("genai.GenerativeModel exists")
    else:
        print("genai.GenerativeModel does NOT exist")
    
    if hasattr(genai, 'configure'):
        print("genai.configure exists")
    else:
        print("genai.configure does NOT exist")
    
    if hasattr(genai, 'Client'):
        print("genai.Client exists")
        client = genai.Client(api_key=API_KEY)
        print(f"  Client methods: {[x for x in dir(client) if not x.startswith('_')]}")
        
except Exception as e:
    print(f"Error: {e}")
