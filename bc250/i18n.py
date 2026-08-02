from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .messages import UserMessage

SUPPORTED_LANGUAGES = ("auto", "ko", "en", "ja", "zh-CN")
DEFAULT_SETTINGS = Path.home() / ".config/bc250-custom-pannel/settings.json"


def normalize_locale(value: str | None) -> str:
    normalized = (value or "").split(".", 1)[0].replace("-", "_")
    if normalized.startswith("ko"):
        return "ko"
    if normalized.startswith("ja"):
        return "ja"
    if normalized in {"zh_CN", "zh_SG", "zh"}:
        return "zh-CN"
    if normalized.startswith("en"):
        return "en"
    return "en"


def _read_choice(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return "auto"
        value = payload.get("language", "auto")
    except (OSError, ValueError, TypeError):
        return "auto"
    return value if value in SUPPORTED_LANGUAGES else "auto"


def _write_choice(path: Path, language: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"language": language}, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Translator:
    def __init__(
        self,
        locale_root: Path,
        settings_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.locale_root = Path(locale_root)
        self.settings_path = Path(settings_path or DEFAULT_SETTINGS)
        self.environ = dict(os.environ if environ is None else environ)
        self.catalogs = self._load_catalogs()
        self.missing_keys: set[str] = set()
        self.language = _read_choice(self.settings_path)
        self.effective_language = self._resolve(self.language)

    def _load_catalogs(self) -> dict[str, dict[str, str]]:
        catalogs: dict[str, dict[str, str]] = {}
        self.catalog_errors: list[str] = []
        for language in SUPPORTED_LANGUAGES[1:]:
            path = self.locale_root / f"{language}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in payload.items()
                ):
                    raise ValueError("catalog must contain string keys and values")
                catalogs[language] = payload
            except (OSError, ValueError, TypeError) as exc:
                catalogs[language] = {}
                self.catalog_errors.append(f"{path}: {exc}")
        return catalogs

    def _resolve(self, language: str) -> str:
        if language != "auto":
            return language
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            if self.environ.get(name):
                return normalize_locale(self.environ[name])
        return "en"

    def set_language(self, language: str, persist: bool = True) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(language)
        self.language = language
        self.effective_language = self._resolve(language)
        if persist:
            _write_choice(self.settings_path, language)

    def gettext(self, key: str, **params: object) -> str:
        if key not in self.catalogs.get(self.effective_language, {}) and key not in self.catalogs.get("en", {}):
            self.missing_keys.add(key)
        candidates = (
            self.catalogs.get(self.effective_language, {}).get(key),
            self.catalogs.get("en", {}).get(key),
            key,
        )
        for template in candidates:
            if not template:
                continue
            try:
                return template.format(**params)
            except (KeyError, ValueError):
                continue
        return key

    def render(self, message: UserMessage) -> str:
        return self.gettext(message.key, **dict(message.params))
