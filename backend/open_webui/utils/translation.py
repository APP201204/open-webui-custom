"""Translation utility module for multilingual support.

Provides language detection, text translation, and batch translation with caching.
Uses Gemini Flash (or configured model) for fast translation.
"""

import hashlib
import logging
import re
from typing import Optional

from aiocache import cached
from google import genai
from open_webui.env import ENABLE_TRANSLATION, GOOGLE_API_KEY, TRANSLATION_CACHE_TTL, TRANSLATION_MODEL_ID

log = logging.getLogger(__name__)

# Instruction to preserve numbers, dates, and proper nouns
PRESERVE_INSTRUCTION = (
    "Do NOT translate numbers, dates, or proper nouns (names of people, places, "
    "organizations, brands, etc.). Preserve them exactly as they appear in the original text."
)


def _get_text_hash(text: str) -> str:
    """Generate SHA-256 hash of text for cache key."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


async def detect_language(text: str) -> str:
    """Detect the language of the given text.

    Detection is performed on the full text, not per-chunk, for accuracy.

    Args:
        text: The text to detect language for.

    Returns:
        ISO 639-1 language code (e.g., 'en', 'hi', 'gu').
    """
    if not ENABLE_TRANSLATION:
        return 'en'

    if not text or not text.strip():
        return 'en'

    if not GOOGLE_API_KEY:
        log.warning("GOOGLE_API_KEY not set, defaulting to 'en'")
        return 'en'

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_content(
            model=TRANSLATION_MODEL_ID,
            contents=f"Detect the language of this text. Respond with only the ISO 639-1 language code (e.g., 'en', 'hi', 'gu'). Text: {text[:500]}",
        )
        detected = response.text.strip().lower()
        # Normalize common responses
        if detected in ['english', 'en']:
            return 'en'
        elif detected in ['hindi', 'hi']:
            return 'hi'
        elif detected in ['gujarati', 'gu']:
            return 'gu'
        return detected
    except Exception as e:
        log.warning(f"Language detection failed: {e}, defaulting to 'en'")
        return 'en'


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    model_id: str = TRANSLATION_MODEL_ID,
) -> str:
    """Translate text from source language to target language.

    Args:
        text: The text to translate.
        source_lang: Source language code (e.g., 'en', 'hi').
        target_lang: Target language code (e.g., 'en', 'hi').
        model_id: Model ID to use for translation.

    Returns:
        Translated text.
    """
    if not ENABLE_TRANSLATION:
        return text

    if not text or not text.strip():
        return text

    if source_lang == target_lang:
        return text

    if not GOOGLE_API_KEY:
        log.warning("GOOGLE_API_KEY not set, returning original text")
        return text

    # Check cache first
    cache_key = f"trans:{_get_text_hash(text)}:{source_lang}:{target_lang}"
    cached_result = await _get_cached_translation(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        prompt = f"""Translate the following text from {source_lang} to {target_lang}.

{PRESERVE_INSTRUCTION}

Text:
{text}

Translate only the text above, do not include any explanations or additional text."""

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
        )
        translated = response.text.strip()

        # Cache the result
        await _cache_translation(cache_key, translated)

        return translated
    except Exception as e:
        log.error(f"Translation failed: {e}")
        return text


async def translate_batch(
    strings: list[str],
    target_lang: str,
    model_id: str = TRANSLATION_MODEL_ID,
) -> list[str]:
    """Translate a batch of strings to target language in a single API call.

    Uses a numbered-list prompt to preserve order. If the returned list length
    doesn't match input length, falls back to translating strings one at a time.

    Args:
        strings: List of strings to translate.
        target_lang: Target language code (e.g., 'en', 'hi').
        model_id: Model ID to use for translation.

    Returns:
        List of translated strings in the same order as input.
    """
    if not ENABLE_TRANSLATION:
        return strings

    if not strings:
        return []

    # Filter out empty strings and track their positions
    non_empty_indices = [i for i, s in enumerate(strings) if s and s.strip()]
    non_empty_strings = [strings[i] for i in non_empty_indices]

    if not non_empty_strings:
        return strings

    if not GOOGLE_API_KEY:
        log.warning("GOOGLE_API_KEY not set, returning original strings")
        return strings

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)

        # Build numbered list prompt
        numbered_text = "\n".join(
            f"{i+1}. {s}" for i, s in enumerate(non_empty_strings)
        )

        prompt = f"""Translate each of the following numbered lines from their detected language to {target_lang}.

{PRESERVE_INSTRUCTION}

Return the translations as a numbered list in the same order, with each translation on its own line.
Do not include the original text in your response, only the translations.

Text to translate:
{numbered_text}"""

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
        )

        # Parse the response
        translated_lines = []
        for line in response.text.strip().split('\n'):
            # Remove numbering prefix (e.g., "1. ", "2. ")
            match = re.match(r'^\d+[\.\)]\s*(.+)$', line.strip())
            if match:
                translated_lines.append(match.group(1))
            elif line.strip():
                # Fallback: if no numbering, just take the line
                translated_lines.append(line.strip())

        # Validate length
        if len(translated_lines) == len(non_empty_strings):
            # Success: reconstruct full list with empty strings in original positions
            result = list(strings)
            for idx, trans in zip(non_empty_indices, translated_lines):
                result[idx] = trans
            return result
        else:
            log.warning(
                f"Batch translation length mismatch: expected {len(non_empty_strings)}, "
                f"got {len(translated_lines)}. Falling back to per-string translation."
            )
            raise ValueError("Length mismatch")

    except Exception as e:
        log.warning(f"Batch translation failed: {e}, falling back to per-string translation")
        # Fallback: translate each string individually
        result = []
        for s in strings:
            if not s or not s.strip():
                result.append(s)
            else:
                detected = await detect_language(s)
                translated = await translate_text(s, detected, target_lang, model_id)
                result.append(translated)
        return result


async def _get_cached_translation(cache_key: str) -> Optional[str]:
    """Retrieve translation from cache if available and not expired."""
    try:
        from aiocache import Cache
        cache = Cache(Cache.MEMORY)
        result = await cache.get(cache_key)
        return result
    except Exception as e:
        log.debug(f"Cache retrieval failed: {e}")
        return None


async def _cache_translation(cache_key: str, translated_text: str) -> None:
    """Cache translation result with TTL."""
    try:
        from aiocache import Cache
        cache = Cache(Cache.MEMORY)
        await cache.set(cache_key, translated_text, ttl=TRANSLATION_CACHE_TTL)
    except Exception as e:
        log.debug(f"Cache set failed: {e}")


def strip_code_blocks(text: str) -> tuple[list[str], str]:
    """Strip fenced code blocks from text, returning (blocks, prose_without_blocks).

    Args:
        text: Text that may contain fenced code blocks.

    Returns:
        Tuple of (list of code blocks, text with code blocks removed).
    """
    pattern = r'```[\s\S]*?```'
    blocks = re.findall(pattern, text)
    prose = re.sub(pattern, '___CODE_BLOCK___', text)
    return blocks, prose


def restore_code_blocks(text: str, blocks: list[str]) -> str:
    """Restore code blocks into text at placeholder positions.

    Args:
        text: Text with ___CODE_BLOCK___ placeholders.
        blocks: List of code blocks to restore.

    Returns:
        Text with code blocks restored.
    """
    for block in blocks:
        text = text.replace('___CODE_BLOCK___', block, 1)
    return text
