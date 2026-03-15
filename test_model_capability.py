#!/usr/bin/env python3
"""Check model capabilities."""

import google.genai as genai
import os

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set")
    exit(1)

print("Checking model capabilities...")

client = genai.Client(api_key=API_KEY)

try:
    # Check gemini-2.5-flash capabilities
    models = client.models.list()
    
    for model in models:
        if 'gemini-2.5-flash' in model.name and 'image' not in model.name:
            print(f"\nModel: {model.name}")
            print(f"Display: {model.display_name}")
            if hasattr(model, 'supported_generation_methods'):
                methods = model.supported_generation_methods
                print(f"Supported methods: {methods}")
                for method in methods or []:
                    print(f"  - {method}")
            break
            
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# Now try a simple text generation
print("\n" + "="*60)
print("Testing text generation with correct model name...")
print("="*60)

try:
    response = client.models.generate_content(
        model="models/gemini-2.5-flash",
        contents="Say 'API is working!'"
    )
    print(f"✓ Text generation works!")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"✗ Error: {type(e).__name__}: {e}")
