import os
import io
import httpx
import shutil
import zipfile
from typing import Any
from pathlib import Path
from . import retry, APIAuth


class GitHubAddon:
    @retry()
    def __init__(
        self, url: str, checkcache: dict[str, Any], packagercache: dict[str, Any],
        clienttype: str, apikey: str, http: httpx.Client
    ) -> None:
        project = url.replace('https://github.com/', '')
        self.http: httpx.Client = http
        self.apiKey: str = apikey
        self.payloads: list[dict[str, Any]] = []
        self.packagerCache: dict[str, Any] = packagercache
        if project in checkcache:
            self.payload = checkcache[project]
        else:
            try:
                self.payload = self.http.get(f'https://api.github.com/repos/{project}/releases',
                                             auth=APIAuth('Bearer', self.apiKey))
            except httpx.RequestError as e:
                raise RuntimeError(f'{project}\nGitHub API failed to respond.') from e
            if self.payload.status_code == 401:
                raise RuntimeError(f'{project}\nIncorrect or expired GitHub API personal access token.')
            elif self.payload.status_code == 403:
                raise RuntimeError(f'{project}\nGitHub API rate limit exceeded. Try later or provide personal access '
                                   f'token.')
            elif self.payload.status_code == 404:
                raise RuntimeError(url)
            else:
                self.payload = self.payload.json()
        for release in self.payload:
            if release['assets'] and len(release['assets']) > 0 and not release['draft'] and not release['prerelease']:
                self.payloads.append(release)
                if len(self.payloads) > 14:
                    break
        if not self.payloads:
            raise RuntimeError(f'{url}\nThis integration supports only the projects that provide packaged'
                               f' releases.')
        self.name: str = project.split('/')[1]
        self.clientType: str = clienttype
        self.currentVersion: str | None = None
        self.uiVersion: str | None = None
        self.downloadUrl: str | None = None
        self.changelogUrl: str | None = None
        self.archive: zipfile.ZipFile | None = None
        self.metadata: dict[str, Any] | None = None
        self.directories: list[str] = []
        self.author: list[str] = [project.split('/')[0]]
        self.releaseDepth: int = 0
        self.zipContent: bytes = b''
        self.parse()

    def parse(self) -> None:
        if self.releaseDepth >= len(self.payloads):
            raise RuntimeError(f'{self.name}.\nFailed to find release for your client version.')
        self.currentVersion = self.payloads[self.releaseDepth]['tag_name'] or self.payloads[self.releaseDepth]['name']
        self.changelogUrl = self.payloads[self.releaseDepth]['html_url']
        self.parse_metadata()
        if self.metadata:
            self.get_latest_package()
        else:
            self.get_latest_package_nometa()

    def parse_metadata(self) -> None:
        for release in self.payloads[self.releaseDepth]['assets']:
            if release['name'] and release['name'] == 'release.json':
                if release['node_id'] in self.packagerCache:
                    self.metadata = self.packagerCache[release['node_id']]
                else:
                    self.metadata = self.http.get(release['url'], headers={'Accept': 'application/octet-stream'},
                                                  auth=APIAuth('Bearer', self.apiKey)).json()
                break
        else:
            self.metadata = None

    def get_latest_package(self) -> None:
        targetfile = None
        if self.clientType == 'retail':
            targetflavor = 'mainline'
        elif self.clientType == 'mop':
            targetflavor = 'mists'
        else:
            targetflavor = self.clientType
        for release in self.metadata['releases']:
            if not release['nolib']:
                for flavor in release['metadata']:
                    if flavor['flavor'] == targetflavor:
                        targetfile = release['filename']
                        if 'name' in release:
                            self.name = release['name']
                            self.currentVersion = release['version']
                        break
                if targetfile:
                    break
        if not targetfile:
            self.releaseDepth += 1
            self.parse()
        for release in self.payloads[self.releaseDepth]['assets']:
            if release['name'] and release['name'] == targetfile:
                self.downloadUrl = release['url']
                break
        if not self.downloadUrl:
            self.releaseDepth += 1
            self.parse()

    def get_latest_package_nometa(self) -> None:
        latest = None
        latestclassic = None
        latestmop = None
        for release in self.payloads[self.releaseDepth]['assets']:
            if release['name'] and release['name'].endswith('.zip') and '-nolib' not in release['name'] \
                    and release['content_type'] in ['application/x-zip-compressed', 'application/zip', 'raw']:
                if not latest and not release['name'].endswith(('-classic.zip', '-bc.zip', '-bcc.zip', '-wrath.zip',
                                                                '-cata.zip', '-mists.zip')):
                    latest = release['url']
                elif not latestclassic and release['name'].endswith('-classic.zip'):
                    latestclassic = release['url']
                elif not latestmop and release['name'].endswith('-mists.zip'):
                    latestmop = release['url']
        if (self.clientType == 'retail' and latest) \
                or (self.clientType == 'classic' and latest and not latestclassic) \
                or (self.clientType == 'mop' and latest and not latestmop):
            self.downloadUrl = latest
        elif self.clientType == 'classic' and latestclassic:
            self.downloadUrl = latestclassic
        elif self.clientType == 'mop' and latestmop:
            self.downloadUrl = latestmop
        else:
            self.releaseDepth += 1
            self.parse()

    @retry()
    def get_addon(self) -> None:
        self.zipContent = self.http.get(self.downloadUrl, headers={'Accept': 'application/octet-stream'},
                                        auth=APIAuth('Bearer', self.apiKey)).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))
        for file in self.archive.namelist():
            if file.lower().endswith('.toc') and '/' not in file:
                raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path: Path) -> None:
        self.archive.extractall(path)


class GitHubAddonRaw:
    @retry()
    def __init__(self, addon: dict[str, Any], apikey: str, http: httpx.Client) -> None:
        repository = addon['Repository']
        self.http: httpx.Client = http
        self.apiKey: str = apikey
        self.branch: str = addon['Branch']
        self.name: str = addon['Name']
        try:
            self.payload = self.http.get(f'https://api.github.com/repos/{repository}/branches/{self.branch}',
                                         auth=APIAuth('Bearer', self.apiKey))
        except httpx.RequestError as e:
            raise RuntimeError(f'{self.name}\nGitHub API failed to respond.') from e
        if self.payload.status_code == 401:
            raise RuntimeError(f'{self.name}\nIncorrect or expired GitHub API personal access token.')
        elif self.payload.status_code == 403:
            raise RuntimeError(f'{self.name}\nGitHub API rate limit exceeded. Try later or provide personal access '
                               f'token.')
        elif self.payload.status_code == 404:
            raise RuntimeError(f'{self.name}\nTarget branch don\'t exist.')
        else:
            self.payload = self.payload.json()
        self.shorthPath: str = repository.split('/')[1]
        if self.name in ['ElvUI', 'Tukui']:
            self.downloadUrl = f'https://api.tukui.org/v1/download/dev/{self.name.lower()}/{self.branch}'
        else:
            self.downloadUrl = f'https://github.com/{repository}/archive/refs/heads/{self.branch}.zip'

        self.changelogUrl: str = f'https://github.com/{repository}/commits/{self.branch}'
        self.currentVersion: str = self.payload['commit']['sha'][:7]
        self.uiVersion: str | None = None
        self.archive: zipfile.ZipFile | None = None
        self.directories: list[str] = addon['Directories']
        self.author: list[str] = addon['Authors']
        self.zipContent: bytes = b''

    @retry()
    def get_addon(self) -> None:
        self.zipContent = self.http.get(self.downloadUrl).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))

    def install(self, path: Path) -> None:
        for directory in self.directories:
            shutil.rmtree(path / directory, ignore_errors=True)
        for file in self.archive.infolist():
            file.filename = file.filename.replace(f'{self.shorthPath}-{self.branch}/', '')
            if any(f in file.filename for f in self.directories):
                self.archive.extract(file, path)
