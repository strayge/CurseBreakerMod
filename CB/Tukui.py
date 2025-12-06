import os
import io
import zipfile
from typing import Any
from pathlib import Path
import httpx
from . import retry


class TukuiAddon:
    @retry()
    def __init__(self, slug: str, checkcache: list[dict[str, Any]], clientversion: str, http: httpx.Client) -> None:
        for addon in checkcache:
            if addon['slug'] == slug:
                self.payload: dict[str, Any] = addon
                break
        else:
            raise RuntimeError(f'{slug}\nProject not found.')
        self.http: httpx.Client = http
        self.name: str = self.payload['name'].strip().strip('\u200b')
        self.downloadUrl: str = self.payload['url']
        self.currentVersion: str = self.payload['version']
        self.uiVersion: str = clientversion if clientversion in self.payload['patch'] else self.payload['patch'][0]
        self.archive: zipfile.ZipFile | None = None
        self.directories: list[str] = self.payload['directories']
        self.author: list[str] = [self.payload['author']]
        self.changelogUrl: str = self.payload['changelog_url']
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
