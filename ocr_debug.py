from app import extract_text_from_image

path = 'static/receipts/3fbba2f59cfe7fdd50982a76135f8414.png'
print('Processing', path)
with open(path, 'rb') as f:
    raw = f.read()
text = extract_text_from_image(raw)
print('--- OCR TEXT ---\n', text)

# show lines containing SUBTOTAL or TOTAL
for l in text.split('\n'):
    if 'SUBTOTAL' in l.upper() or 'TOTAL' in l.upper():
        print('>> raw line:', repr(l))

# show extracted prices via regex to check our new fix
import re
prices = re.findall(r'\$?(\d+[\.,]\d{2})', text)
print('prices regex matched', prices)

# run fallback parser on cleaned text to see output
result = None
from app import process_receipt_fallback
result = process_receipt_fallback(text)
print('fallback result:', result)
