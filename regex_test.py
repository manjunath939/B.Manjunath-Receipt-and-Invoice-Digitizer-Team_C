import re

tests = ['; SUBTUTAL 46 04', 'MbBTO TENT 46 30', '3 84N', '46 30 other']
for t in tests:
    fixed = re.sub(r'(?<=\d)\s+(?=\d{2}\b)', '.', t)
    print(repr(t), '->', repr(fixed))
