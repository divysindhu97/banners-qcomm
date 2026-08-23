from __future__ import annotations

import functools
import re

# OCR frequently drops the space between two words. Re-inserting it is safe for
# ordinary English but destructive for proper nouns, so splitting is guarded by a
# protected vocabulary and by a minimum length for every resulting part.
MIN_SPLIT_LENGTH = 6
MIN_PART_LENGTH = 3
SHORT_WORDS = frozenset(
    "a an as at be by do go he if in is it me my no of on or so to up us we".split()
)

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)")
_AMPERSAND = re.compile(r"(?<=\w)&|&(?=\w)")
# OCR also loses the space that follows punctuation, gluing the next word on.
_AFTER_PUNCT = re.compile(r"(?<=[,:;%])(?=[A-Za-z])")


@functools.lru_cache(maxsize=1)
def _splitter():
    import wordninja

    return wordninja.split


def _acceptable(parts: list[str]) -> bool:
    if len(parts) < 2:
        return False
    return all(len(part) >= MIN_PART_LENGTH or part.lower() in SHORT_WORDS for part in parts)


def _split_word(word: str, protected: frozenset[str]) -> str:
    core = word.strip(".,!?:;\"'()")
    if len(core) < MIN_SPLIT_LENGTH or not core.isalpha() or core.lower() in protected:
        return word
    parts = _splitter()(core)
    if not _acceptable(parts):
        return word
    # wordninja lower-cases; slice the original so capitalisation survives.
    rebuilt, cursor = [], 0
    for part in parts:
        rebuilt.append(core[cursor : cursor + len(part)])
        cursor += len(part)
    return word.replace(core, " ".join(rebuilt), 1)


def clean(text: str, protected: frozenset[str] = frozenset()) -> str:
    """Restore spacing that OCR dropped, without breaking brand names."""
    if not text:
        return text
    text = _AMPERSAND.sub(" & ", text)
    text = _AFTER_PUNCT.sub(" ", text)
    output: list[str] = []
    for token in text.split():
        if token.strip(".,!?:;\"'()").lower() in protected:
            output.append(token)
            continue
        token = _LETTER_DIGIT.sub(" ", token)
        token = _CAMEL.sub(" ", token)
        output.extend(_split_word(piece, protected) for piece in token.split())
    return re.sub(r"\s+", " ", " ".join(output)).strip()
