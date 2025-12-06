import os
import io
import zipfile
from typing import Any, final
from pathlib import Path
import httpx
from . import retry
from .BaseProvider import BaseAddon, BaseAddonProvider, DetectedAddon


@final
class TukuiAddon(BaseAddon):
    def __init__(self, slug: str, checkcache: list[dict[str, Any]], clientversion: str, http: httpx.Client) -> None:
        super().__init__()
        for addon in checkcache:
            if addon['slug'] == slug:
                self.payload: dict[str, Any] = addon
                break
        else:
            raise RuntimeError(f'{slug}\nProject not found.')
        self.http: httpx.Client = http
        self.name = self.payload['name'].strip().strip('\u200b')
        self.downloadUrl = self.payload['url']
        self.currentVersion = self.payload['version']
        self.uiVersion = clientversion if clientversion in self.payload['patch'] else self.payload['patch'][0]
        self.directories = self.payload['directories']
        self.author = [self.payload['author']]
        self.changelogUrl = self.payload['changelog_url']

    @retry()
    def get_addon(self) -> None:
        if not self.downloadUrl:
            raise RuntimeError(f'{self.name}\nDownload URL not available.')
        self.zipContent = self.http.get(self.downloadUrl).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))
        for file in self.archive.namelist():
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path: Path) -> None:
        if self.archive:
            self.archive.extractall(path)


class TukuiProvider(BaseAddonProvider):
    """Provider for Tukui/ElvUI addons."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]):
        super().__init__(http, config, master_config)
        self.name: str = "Tukui"
        self.prefix: str = ""  # No prefix - uses keywords directly
        self.tukuiCache: list[dict[str, Any]] | None = None

    def is_addon_url(self, url: str) -> bool:
        return url.lower() in ['elvui', 'tukui']

    def convert_url_to_id(self, url: str) -> str:
        return url.lower()

    def convert_id_to_url(self, identifier: str) -> str:
        return identifier.lower()

    def get_website_url(self, url: str) -> str | None:
        if url.lower().startswith('elvui'):
            return 'https://www.tukui.org/download.php?ui=elvui'
        elif url.lower().startswith('tukui'):
            return 'https://www.tukui.org/download.php?ui=tukui'
        return None

    def url_to_shorthand(self, url: str) -> str:
        # No transformation needed - already using keywords
        return url.lower()

    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        self._ensure_cache()
        return TukuiAddon(
            url.lower(),
            self.tukuiCache,
            kwargs.get('client_version', ''),
            self.http
        )

    @retry()
    def _ensure_cache(self) -> None:
        """Ensure Tukui cache is loaded."""
        if not self.tukuiCache:
            self.tukuiCache = self.http.get('https://api.tukui.org/v1/addons').json()

    def bulk_check(self, addon_urls: list[str], client_type: str) -> None:
        """Ensure cache is populated for bulk operations."""
        self._ensure_cache()

    def scan(self, addon_dirs: list[str], path: Path, client_type: str) -> list[DetectedAddon]:
        """Scan for ElvUI/Tukui installations."""
        detected: list[DetectedAddon] = []

        for special in ['ElvUI', 'Tukui']:
            if special in addon_dirs:
                detected.append(DetectedAddon(
                    name=special,
                    url=special.lower(),
                    directories=[special],
                    is_installed=False  # Will be determined by Core
                ))

        return detected
