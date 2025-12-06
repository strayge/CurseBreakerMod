import os
import io
import re
import httpx
import zipfile
from typing import Any
from pathlib import Path
from . import retry


class WoWInterfaceAddon:
    @retry()
    def __init__(self, url: str, checkcache: dict[str, Any], http: httpx.Client) -> None:
        project = re.findall(r'\d+', url)[0]
        self.http: httpx.Client = http
        if project in checkcache:
            self.payload: dict[str, Any] = checkcache[project]
        else:
            try:
                data = self.http.get(f'https://api.mmoui.com/v3/game/WOW/filedetails/{project}.json').json()
            except httpx.RequestError as e:
                raise RuntimeError(f'{url}\nWoWInterface API failed to respond.') from e
            if 'ERROR' in data:
                raise RuntimeError(f'{url}\nThis might be a temporary error or this project is not supported '
                                   f'by WoWInterface API.')
            else:
                self.payload = data[0]
        self.name: str = self.payload['UIName'].strip().strip('\u200b')
        self.downloadUrl: str = self.payload['UIDownload']
        self.changelogUrl: str = f'{url}#changelog'
        self.currentVersion: str = self.payload['UIVersion']
        self.uiVersion: str | None = None
        self.archive: zipfile.ZipFile = None
        self.directories: list[str] = []
        self.author: list[str] = [self.payload['UIAuthorName']]
        self.zipContent: bytes = b''

    @retry()
    def get_addon(self) -> None:
        self.zipContent = self.http.get(self.downloadUrl).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))
        for file in self.archive.namelist():
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path: Path) -> None:
        self.archive.extractall(path)
