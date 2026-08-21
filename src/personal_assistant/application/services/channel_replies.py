"""Channel-specific audio and media reply helpers."""

from __future__ import annotations

from typing import Protocol


class _CatalogAccess(Protocol):
    def _text(self, key: str) -> str: ...


class ChannelRepliesMixin:
    """Channel-specific audio and media reply helpers."""

    def telegram_audio_missing_file_id(self: _CatalogAccess) -> str:
        return self._text("telegram_audio_missing_file_id")

    def telegram_transcription_not_configured(self: _CatalogAccess) -> str:
        return self._text("telegram_transcription_not_configured")

    def telegram_token_missing_for_audio(self: _CatalogAccess) -> str:
        return self._text("telegram_token_missing_for_audio")

    def telegram_audio_too_large(self: _CatalogAccess) -> str:
        return self._text("telegram_audio_too_large")

    def telegram_audio_download_too_large(self: _CatalogAccess) -> str:
        return self._text("telegram_audio_download_too_large")

    def telegram_file_path_missing(self: _CatalogAccess) -> str:
        return self._text("telegram_file_path_missing")

    def telegram_transcription_failed(self: _CatalogAccess) -> str:
        return self._text("telegram_transcription_failed")

    def whatsapp_audio_missing_file_id(self: _CatalogAccess) -> str:
        return self._text("whatsapp_audio_missing_file_id")

    def whatsapp_transcription_not_configured(self: _CatalogAccess) -> str:
        return self._text("whatsapp_transcription_not_configured")

    def whatsapp_audio_too_large(self: _CatalogAccess) -> str:
        return self._text("whatsapp_audio_too_large")

    def whatsapp_audio_download_too_large(self: _CatalogAccess) -> str:
        return self._text("whatsapp_audio_download_too_large")

    def whatsapp_media_download_failed(self: _CatalogAccess) -> str:
        return self._text("whatsapp_media_download_failed")

    def whatsapp_transcription_failed(self: _CatalogAccess) -> str:
        return self._text("whatsapp_transcription_failed")

    def whatsapp_media_unsupported(self: _CatalogAccess) -> str:
        return self._text("whatsapp_media_unsupported")
