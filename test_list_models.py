#!/usr/bin/env python3
"""Check available models and their capabilities."""

import google.genai as genai
import os

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set")
    exit(1)

print("Checking available models...")

client = genai.Client(api_key=API_KEY)

try:
    # List available models
    models = client.models.list()
    
    print(f"\nFound {len(list(models))} models:")
    
    # List again since it's an iterator
    models = client.models.list()
    for i, model in enumerate(models):
        print(f"\n{i+1}. {model.name}")
        if hasattr(model, 'supported_generation_methods'):
            print(f"   Methods: {model.supported_generation_methods}")
        if hasattr(model, 'display_name'):
            print(f"   Display: {model.display_name}")
        if i >= 5:  # Just show first few
            print("\n... (more models available)")
            break
            
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
