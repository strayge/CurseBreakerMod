import os
import io
import json
import httpx
import shutil
import zipfile
import concurrent.futures
from typing import Any, final
from pathlib import Path
from . import retry, APIAuth
from .BaseProvider import BaseAddon, BaseAddonProvider


@final
class GitHubAddon(BaseAddon):
    @retry()
    def __init__(
        self, url: str, checkcache: dict[str, Any], packagercache: dict[str, Any],
        clienttype: str, apikey: str, http: httpx.Client
    ) -> None:
        super().__init__()
        project = url.replace('https://github.com/', '')
        self.http: httpx.Client = http
        self.apiKey: str = apikey
        self.payloads: list[dict[str, Any]] = []
        self.packagerCache: dict[str, Any] = packagercache
        if project in checkcache:
            self.payload = checkcache[project]
        else:
            self.payload = self._get_metadata(project, url)

        for release in self.payload:
            assets: list[dict[str, Any]] = release.get('assets', [])
            is_draft = release.get('draft')
            is_prerelease = release.get('prerelease')
            if assets and len(assets) > 0 and not is_draft and not is_prerelease:
                self.payloads.append(release)
                if len(self.payloads) > 14:
                    break
        if not self.payloads:
            raise RuntimeError(f'{url}\nThis integration supports only the projects that provide packaged'
                               f' releases.')
        self.name = project.split('/')[1]
        self.clientType: str = clienttype
        self.metadata: dict[str, Any] | None = None
        self.author = [project.split('/')[0]]
        self.releaseDepth: int = 0
        self.parse()

    @retry()
    def _get_metadata(self, project: str, url: str) -> list[dict[str, Any]]:
        try:
            response = self.http.get(f'https://api.github.com/repos/{project}/releases',
                                            auth=APIAuth('Bearer', self.apiKey))
        except httpx.RequestError as e:
            raise RuntimeError(f'{project}\nGitHub API failed to respond.') from e
        if response.status_code == 401:
            raise RuntimeError(f'{project}\nIncorrect or expired GitHub API personal access token.')
        if response.status_code == 403:
            raise RuntimeError(f'{project}\nGitHub API rate limit exceeded. Try later or provide personal access '
                                f'token.')
        if response.status_code == 404:
            raise RuntimeError(url)
        return response.json()

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
        if self.archive:
            self.archive.extractall(path)


@final
class GitHubAddonRaw(BaseAddon):
    def __init__(self, addon: dict[str, Any], apikey: str, http: httpx.Client) -> None:
        super().__init__()
        repository = addon['Repository']
        self.http: httpx.Client = http
        self.apiKey: str = apikey
        self.branch: str = addon['Branch']
        self.name: str = addon['Name']
        self.payload: dict[str, Any] = self._get_metadata(repository, self.branch)

        self.shorthPath: str = repository.split('/')[1]
        if self.name in ['ElvUI', 'Tukui']:
            self.downloadUrl = f'https://api.tukui.org/v1/download/dev/{self.name.lower()}/{self.branch}'
        else:
            self.downloadUrl = f'https://github.com/{repository}/archive/refs/heads/{self.branch}.zip'

        self.changelogUrl: str = f'https://github.com/{repository}/commits/{self.branch}'
        self.currentVersion: str = self.payload['commit']['sha'][:7]
        self.directories: list[str] = addon['Directories']
        self.author: list[str] = addon['Authors']

    @retry()
    def _get_metadata(self, repository: str, branch: str) -> dict[str, Any]:
        try:
            response = self.http.get(f'https://api.github.com/repos/{repository}/branches/{branch}',
                                         auth=APIAuth('Bearer', self.apiKey))
        except httpx.RequestError as e:
            raise RuntimeError(f'{self.name}\nGitHub API failed to respond.') from e
        if response.status_code == 401:
            raise RuntimeError(f'{self.name}\nIncorrect or expired GitHub API personal access token.')
        if response.status_code == 403:
            raise RuntimeError(f'{self.name}\nGitHub API rate limit exceeded. Try later or provide personal access '
                               f'token.')
        if response.status_code == 404:
            raise RuntimeError(f'{self.name}\nTarget branch don\'t exist.')
        payload = response.json()
        return payload

    @retry()
    def get_addon(self) -> None:
        if not self.downloadUrl:
            raise RuntimeError(f'{self.name}\nDownload URL not available.')
        self.zipContent = self.http.get(self.downloadUrl).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))

    def install(self, path: Path) -> None:
        if not self.archive:
            return
        for directory in self.directories:
            shutil.rmtree(path / directory, ignore_errors=True)
        for file in self.archive.infolist():
            file.filename = file.filename.replace(f'{self.shorthPath}-{self.branch}/', '')
            if any(f in file.filename for f in self.directories):
                self.archive.extract(file, path)


class GitHubProvider(BaseAddonProvider):
    """Provider for GitHub releases."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]):
        super().__init__(http, config, master_config)
        self.name: str = "GitHub"
        self.prefix: str = "gh"
        self.githubPackagerCache: dict[str, Any] = {}

    def is_addon_url(self, url: str) -> bool:
        return url.startswith('https://github.com/')

    def convert_url_to_id(self, url: str) -> str:
        return url.replace('https://github.com/', '')

    def convert_id_to_url(self, identifier: str) -> str:
        return f'https://github.com/{identifier}'

    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        return GitHubAddon(
            url,
            self.cache,
            self.githubPackagerCache,
            client_type,
            self.config['GHAPIKey'],
            self.http
        )

    def bulk_check(self, addon_urls: list[str], client_type: str) -> None:
        """Bulk check for updates from GitHub API."""
        if not self.config['GHAPIKey']:
            return

        ids = [self.convert_url_to_id(url) for url in addon_urls]

        # Build GraphQL query
        query = ('{\n  "query": "{ search( type: REPOSITORY query: \\"' + f'repo:{" repo:".join(ids)}' + ' fork:true\\"'
                 ' first: 100 ) { nodes { ... on Repository { nameWithOwner releases(first: 15) { nodes { tag_name: tag'
                 'Name name html_url: url draft: isDraft prerelease: isPrerelease assets: releaseAssets(first: 100) { n'
                 'odes { node_id: id name content_type: contentType url } } } } } } }}"\n'
                 '}')

        try:
            payload = self.http.post('https://api.github.com/graphql',
                                   json=json.loads(query),
                                   auth=APIAuth('Bearer', self.config['GHAPIKey']),
                                   timeout=15)
            if payload.status_code != 200:
                return

            payload = payload.json()
            packager_cache: dict[str, str] = {}

            for addon in payload['data']['search']['nodes']:
                self.cache[addon['nameWithOwner']] = addon['releases']['nodes']

            for addon in self.cache:
                for i in range(len(self.cache[addon])):
                    self.cache[addon][i]['assets'] = self.cache[addon][i]['assets']['nodes']
                for release in self.cache[addon]:
                    if not release['draft'] and not release['prerelease']:
                        for asset in release['assets']:
                            if asset['name'] == 'release.json':
                                packager_cache[asset['node_id']] = asset['url']
                                break
                        break

            # Fetch packager metadata
            with concurrent.futures.ThreadPoolExecutor() as executor:
                workers: list[concurrent.futures.Future[tuple[str, Any]]] = []
                for node_id, url in packager_cache.items():
                    workers.append(executor.submit(self._bulk_check_worker, node_id, url))
                for future in concurrent.futures.as_completed(workers):
                    try:
                        node_id, response = future.result()
                        self.githubPackagerCache[node_id] = response
                    except (httpx.RequestError, json.JSONDecodeError):
                        pass
        except Exception:
            pass

    def _bulk_check_worker(self, node_id: str, url: str) -> tuple[str, Any]:
        """Worker function for fetching packager metadata."""
        return node_id, self.http.get(url, headers={'Accept': 'application/octet-stream'},
                                      auth=APIAuth('Bearer', self.config['GHAPIKey'])).json()
