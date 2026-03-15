from app import extract_text_from_image
import glob

with open('ocr_inspect_output.txt', 'w', encoding='utf-8') as out:
    for path in glob.glob('static/receipts/*.png'):
        out.write('---- ' + path + '\n')
        with open(path, 'rb') as f:
            raw = f.read()
        text = extract_text_from_image(raw)
        out.write('\nOCR TEXT:\n' + text + '\n')
        lines = text.split('\n')
        for l in lines:
            if 'TOTAL' in l.upper() or 'SUBTOTAL' in l.upper():
                out.write('>> ' + l + '\n')
        prices = [p for p in text.split() if p.replace('$','').replace('.','').isdigit()]
        out.write('prices found: ' + str(prices) + '\n')
        out.write('\n')
    out.write('--- done\n')

