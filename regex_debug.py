import re
line = "MbBTO TENT 46.30"
match = re.search(r"\b(?:total|tent|t0tal)\b[^0-9]*(\d+[\.,]\d{2})", line, re.I)
print('match', match, 'group', match.group(1) if match else None)
