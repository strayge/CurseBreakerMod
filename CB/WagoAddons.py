import os
import io
import httpx
import zipfile
from typing import Any
from pathlib import Path
from datetime import datetime
from dateutil import parser
from dateutil.tz import tzutc
from json import JSONDecodeError
from . import retry, APIAuth


class WagoAddonsAddon:
    @retry()
    def __init__(
        self, url: str, checkcache: dict[str, Any], clienttype: str,
        clientversion: str, allowdev: int, apikey: str, http: httpx.Client
    ) -> None:
        project = url.replace('https://addons.wago.io/addons/', '')
        self.http: httpx.Client = http
        self.apiKey: str = apikey
        self.clientType: str = clienttype
        self.clientVersion: str = clientversion
        if project in checkcache:
            self.payload = checkcache[project]
            self.payload['display_name'] = self.payload['name']
            self.payload['recent_release'] = self.payload['recent_releases']
        else:
            if self.apiKey == '':
                raise RuntimeError(f'{url}\nThe Wago Addons API key is missing. '
                                   f'It can be obtained here: https://addons.wago.io/patreon')
            try:
                self.payload = self.http.get(f'https://addons.wago.io/api/external/addons/{project}?game_version='
                                             f'{self.clientType}', auth=APIAuth('Bearer', self.apiKey))
            except httpx.RequestError as e:
                raise RuntimeError(f'{url}\nWago Addons API failed to respond.') from e
            if self.payload.status_code == 401:
                raise RuntimeError(f'{url}\nWago Addons API key is missing or incorrect.')
            elif self.payload.status_code == 403:
                raise RuntimeError(f'{url}\nProvided Wago Addons API key is expired. Please acquire a new one.')
            elif self.payload.status_code == 404:
                raise RuntimeError(f'{url}\nThis might be a temporary issue with Wago Addons API or the project was '
                                   f'removed/renamed. In this case, uninstall it (and reinstall if it still exists) '
                                   f'to fix this issue.')
            elif self.payload.status_code == 423:
                raise RuntimeError(f'{url}\nProvided Wago Addons API key is blocked. Please acquire a new one.')
            elif self.payload.status_code in [429, 500, 502, 504]:
                raise RuntimeError(f'{url}\nTemporary Wago Addons API issue. Please try later.')
            else:
                try:
                    self.payload = self.payload.json()
                except (StopIteration, JSONDecodeError) as e:
                    raise RuntimeError(f'{url}\nThis might be a temporary issue with Wago Addons API.') from e
        self.name: str = self.payload['display_name'].strip().strip('\u200b')
        self.allowDev: int = allowdev
        self.downloadUrl: str | None = None
        self.changelogUrl: str | None = None
        self.currentVersion: str | None = None
        self.uiVersion: str | None = None
        self.archive: zipfile.ZipFile | None = None
        self.directories: list[str] = []
        self.author: list[str] = self.payload['authors']
        self.zipContent: bytes = b''
        self.get_current_version()

    def get_current_version(self) -> None:
        if len(self.payload['recent_release']) == 0:
            raise RuntimeError(f'{self.name}.\nFailed to find release for your client version.')

        empty = datetime(1, 1, 1, 0, 1, tzinfo=tzutc())
        release = {'stable': datetime(1, 1, 1, 0, 0, tzinfo=tzutc()),
                   'beta': datetime(1, 1, 1, 0, 0, tzinfo=tzutc()),
                   'alpha': datetime(1, 1, 1, 0, 0, tzinfo=tzutc())}
        for channel in ['stable', 'beta', 'alpha']:
            if channel in self.payload['recent_release']:
                release[channel] = parser.isoparse(self.payload['recent_release'][channel]['created_at'])
        if self.allowDev == 1:
            release.pop('alpha', None)
        elif self.allowDev == 0:
            if release['stable'] == empty:
                release.pop('stable', None)
                if release['beta'] != empty:
                    release.pop('alpha', None)
                elif release['alpha'] != empty:
                    release.pop('beta', None)
                else:
                    release.pop('alpha', None)
                    release.pop('beta', None)
            else:
                release.pop('alpha', None)
                release.pop('beta', None)
        if not release:
            raise RuntimeError(f'{self.name}.\nFailed to find release for your client version.')
        release = self.payload['recent_release'][max(release, key=release.get)]

        self.downloadUrl = release['download_link'] if 'download_link' in release else release['link']
        patches = release['supported_patches'] if 'supported_patches' in release \
            else release[f'supported_{self.clientType}_patches']
        self.uiVersion = self.clientVersion if self.clientVersion in patches else patches[0]
        self.changelogUrl = f'{self.payload["website_url"]}/versions'
        self.currentVersion = release['label']

    @retry()
    def get_addon(self) -> None:
        self.zipContent = self.http.get(self.downloadUrl, auth=APIAuth('Bearer', self.apiKey)).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))
        for file in self.archive.namelist():
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path: Path) -> None:
        self.archive.extractall(path)
