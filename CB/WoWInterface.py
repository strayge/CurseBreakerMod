import os
import io
import re
import httpx
import zipfile
from typing import Any, final
from pathlib import Path
from . import retry
from .BaseProvider import BaseAddon, BaseAddonProvider


@final
class WoWInterfaceAddon(BaseAddon):
    def __init__(self, url: str, checkcache: dict[str, Any], http: httpx.Client) -> None:
        super().__init__()
        project = re.findall(r'\d+', url)[0]
        self.http: httpx.Client = http
        if project in checkcache:
            self.payload: dict[str, Any] = checkcache[project]
        else:
            self.payload = self._get_metadata(project, url)
        self.name: str = self.payload['UIName'].strip().strip('\u200b')
        self.downloadUrl: str = self.payload['UIDownload']
        self.changelogUrl: str = f'{url}#changelog'
        self.currentVersion: str = self.payload['UIVersion']
        self.author: list[str] = [self.payload['UIAuthorName']]

    @retry()
    def _get_metadata(self, project: str, url: str) -> dict[str, Any]:
        try:
            data = self.http.get(f'https://api.mmoui.com/v3/game/WOW/filedetails/{project}.json').json()
        except httpx.RequestError as e:
            raise RuntimeError(f'{url}\nWoWInterface API failed to respond.') from e
        if 'ERROR' in data:
            raise RuntimeError(f'{url}\nThis might be a temporary error or this project is not supported '
                                f'by WoWInterface API.')
        return data[0]

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


class WoWInterfaceProvider(BaseAddonProvider):
    """Provider for WoWInterface addons."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]):
        super().__init__(http, config, master_config)
        self.name: str = "WoWI"
        self.prefix: str = "wowi"

    def is_addon_url(self, url: str) -> bool:
        return url.startswith('https://www.wowinterface.com/downloads/')

    def convert_url_to_id(self, url: str) -> str:
        """Extract file ID from WoWInterface URL."""
        match = re.findall(r'\d+', url)
        return match[0] if match else ""

    def convert_id_to_url(self, identifier: str) -> str:
        """Build WoWInterface URL from file ID."""
        return f'https://www.wowinterface.com/downloads/info{identifier}.html'

    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        return WoWInterfaceAddon(url, self.cache, self.http)

    def bulk_check(self, addon_urls: list[str], client_type: str) -> None:
        """Bulk check for updates from WoWInterface API."""
        ids = [self.convert_url_to_id(url) for url in addon_urls]
        if not ids:
            return

        try:
            payload = self.http.get(f'https://api.mmoui.com/v3/game/WOW/filedetails/{",".join(ids)}.json',
                                    timeout=15).json()
            if 'ERROR' not in payload:
                for addon in payload:
                    self.cache[str(addon['UID'])] = addon
        except Exception:
            pass
