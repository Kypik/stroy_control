import re

CYRILLIC_TO_LATIN = str.maketrans({
    'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'Н': 'H', 'К': 'K', 'М': 'M',
    'О': 'O', 'Р': 'P', 'Т': 'T', 'Х': 'X', 'У': 'Y', 'Д': 'D',
    'а': 'a', 'в': 'b', 'с': 'c', 'е': 'e', 'н': 'h', 'к': 'k', 'м': 'm',
    'о': 'o', 'р': 'p', 'т': 't', 'х': 'x', 'у': 'y', 'д': 'd',
})
LATIN_TO_CYRILLIC = str.maketrans({
    'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'H': 'Н', 'K': 'К', 'M': 'М',
    'O': 'О', 'P': 'Р', 'T': 'Т', 'X': 'Х', 'Y': 'У', 'D': 'Д',
    'a': 'а', 'b': 'в', 'c': 'с', 'e': 'е', 'h': 'н', 'k': 'к', 'm': 'м',
    'o': 'о', 'p': 'р', 't': 'т', 'x': 'х', 'y': 'у', 'd': 'д',
})


def variants(number):
    original = re.sub(r'\s+', ' ', (number or '').strip())
    normalized_separators = re.sub(r'\s*-\s*', '-', re.sub(r'[_–—−]', '-', original))
    result = []
    for base in (original, normalized_separators):
        for candidate in (base, base.translate(LATIN_TO_CYRILLIC), base.translate(CYRILLIC_TO_LATIN)):
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def numeric_tail(number):
    groups = re.findall(r'\d+', number or '')
    if not groups:
        return ''
    if len(groups) > 1 and len(groups[-1]) <= 2:
        return '/'.join(groups[-2:])
    return groups[-1]
