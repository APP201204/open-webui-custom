"""Unit tests for translation utility module."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from open_webui.utils.translation import (
    _cache_translation,
    _get_cached_translation,
    _get_text_hash,
    detect_language,
    restore_code_blocks,
    strip_code_blocks,
    translate_batch,
    translate_text,
)


class TestCodeBlockHandling:
    """Test code block stripping and restoration."""

    def test_strip_code_blocks_simple(self):
        text = "Here is some code:\n```python\nprint('hello')\n```\nAnd some more text."
        blocks, prose = strip_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == "```python\nprint('hello')\n```"
        assert "___CODE_BLOCK___" in prose
        assert "```" not in prose

    def test_strip_code_blocks_multiple(self):
        text = "First:\n```python\nx = 1\n```\nSecond:\n```javascript\ny = 2\n```"
        blocks, prose = strip_code_blocks(text)
        assert len(blocks) == 2
        assert prose.count("___CODE_BLOCK___") == 2

    def test_strip_code_blocks_none(self):
        text = "Just plain text with no code blocks."
        blocks, prose = strip_code_blocks(text)
        assert len(blocks) == 0
        assert prose == text

    def test_restore_code_blocks(self):
        text = "Here is ___CODE_BLOCK___ and more text."
        blocks = ["```python\nprint('hello')\n```"]
        restored = restore_code_blocks(text, blocks)
        assert "```python\nprint('hello')\n```" in restored
        assert "___CODE_BLOCK___" not in restored

    def test_restore_code_blocks_multiple(self):
        text = "First ___CODE_BLOCK___ second ___CODE_BLOCK___ end."
        blocks = ["```python\nx = 1\n```", "```javascript\ny = 2\n```"]
        restored = restore_code_blocks(text, blocks)
        assert restored.count("```") == 4  # 2 blocks * 2 backticks each
        assert "___CODE_BLOCK___" not in restored


class TestTextHash:
    """Test text hashing for cache keys."""

    def test_text_hash_consistent(self):
        text = "Hello world"
        hash1 = _get_text_hash(text)
        hash2 = _get_text_hash(text)
        assert hash1 == hash2

    def test_text_hash_different(self):
        hash1 = _get_text_hash("Hello")
        hash2 = _get_text_hash("World")
        assert hash1 != hash2


class TestCache:
    """Test translation caching."""

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        cache_key = "test_key"
        await _cache_translation(cache_key, "translated text")
        result = await _get_cached_translation(cache_key)
        assert result == "translated text"

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        result = await _get_cached_translation("nonexistent_key")
        assert result is None


class TestTranslateBatchFallback:
    """Test batch translation fallback on length mismatch."""

    @pytest.mark.asyncio
    async def test_batch_fallback_on_mismatch(self):
        """Test that batch translation falls back to per-string when length mismatches."""
        strings = ["Hello", "World", "Test"]

        # Mock the client to return wrong number of translations
        mock_response = MagicMock()
        mock_response.text = "1. Hola\n2. Mundo"  # Only 2 translations for 3 inputs

        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        # Mock detect_language to return 'en'
        with patch('open_webui.utils.translation.detect_language', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = 'en'

            # Mock translate_text for fallback
            with patch('open_webui.utils.translation.translate_text', new_callable=AsyncMock) as mock_translate:
                mock_translate.side_effect = lambda t, s, tgt, m: f"{t}_translated"

                with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
                    with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                        result = await translate_batch(strings, 'es')

                        # Should have fallen back to per-string translation
                        assert result == ["Hello_translated", "World_translated", "Test_translated"]
                        # Verify translate_text was called for each string
                        assert mock_translate.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_success(self):
        """Test successful batch translation when lengths match."""
        strings = ["Hello", "World"]

        mock_response = MagicMock()
        mock_response.text = "1. Hola\n2. Mundo"

        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
            with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                result = await translate_batch(strings, 'es')

                assert result == ["Hola", "Mundo"]

    @pytest.mark.asyncio
    async def test_batch_with_empty_strings(self):
        """Test batch translation with empty strings preserved."""
        strings = ["Hello", "", "World"]

        mock_response = MagicMock()
        mock_response.text = "1. Hola\n2. Mundo"

        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
            with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                result = await translate_batch(strings, 'es')

                assert result == ["Hola", "", "Mundo"]

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        """Test batch translation with empty list."""
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
            result = await translate_batch([], 'es')
            assert result == []

    @pytest.mark.asyncio
    async def test_batch_disabled_translation(self):
        """Test batch translation when feature is disabled."""
        strings = ["Hello", "World"]
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', False):
            result = await translate_batch(strings, 'es')
            assert result == strings


class TestDetectLanguage:
    """Test language detection."""

    @pytest.mark.asyncio
    async def test_detect_english(self):
        mock_response = MagicMock()
        mock_response.text = "en"

        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
            with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                result = await detect_language("Hello world")
                assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_empty_text(self):
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
            result = await detect_language("")
            assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_disabled(self):
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', False):
            result = await detect_language("Hello")
            assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_failure_fallback(self):
        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(side_effect=Exception("API error"))

        with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
            with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                result = await detect_language("Hello")
                assert result == "en"  # Fallback on error


class TestTranslateText:
    """Test single text translation."""

    @pytest.mark.asyncio
    async def test_translate_same_language(self):
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
            result = await translate_text("Hello", "en", "en")
            assert result == "Hello"

    @pytest.mark.asyncio
    async def test_translate_empty_text(self):
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
            result = await translate_text("", "en", "es")
            assert result == ""

    @pytest.mark.asyncio
    async def test_translate_disabled(self):
        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', False):
            result = await translate_text("Hello", "en", "es")
            assert result == "Hello"

    @pytest.mark.asyncio
    async def test_translate_with_cache_hit(self):
        """Test that cached translation is returned."""
        cache_key = f"trans:{_get_text_hash('Hello')}:en:es"
        await _cache_translation(cache_key, "Hola")

        with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
            result = await translate_text("Hello", "en", "es")
            assert result == "Hola"

    @pytest.mark.asyncio
    async def test_translate_failure_fallback(self):
        """Test that original text is returned on translation failure."""
        mock_client = MagicMock()
        mock_client.models.generate_content = AsyncMock(side_effect=Exception("API error"))

        with patch('open_webui.utils.translation.genai.Client', return_value=mock_client):
            with patch('open_webui.utils.translation.ENABLE_TRANSLATION', True):
                result = await translate_text("Hello", "en", "es")
                assert result == "Hello"  # Fallback on error
