import os
import io
import re
import hashlib
import httpx
import zipfile
from typing import Any, final
from pathlib import Path
from datetime import datetime
from dateutil import parser
from dateutil.tz import tzutc
from json import JSONDecodeError
from . import retry, APIAuth
from .BaseProvider import BaseAddon, BaseAddonProvider, DetectedAddon


def parse_wagoaddons_error(code: int) -> None:
    """Parse WagoAddons API error codes and raise appropriate exceptions."""
    if code == 401:
        raise RuntimeError('Wago Addons API key is missing or incorrect.')
    elif code == 403:
        raise RuntimeError('Provided Wago Addons API key is expired. Please acquire a new one.')
    elif code == 423:
        raise RuntimeError('Provided Wago Addons API key is blocked. Please acquire a new one.')
    elif code in [429, 500, 502, 504]:
        raise RuntimeError('Temporary Wago Addons API issue. Please try later.')


def parse_wagoapp_payload(url: str, client_type: str | None, api_key: str, http: httpx.Client) -> str:
    """Parse wago-app:// protocol URLs and convert them to regular Wago Addons URLs."""
    if api_key == '':
        raise RuntimeError('This feature requires the Wago Addons API key.\n'
                           'It can be obtained here: https://addons.wago.io/patreon')
    projectid = url.replace('wago-app://addons/', '')
    payload = http.get(f'https://addons.wago.io/api/external/addons/{projectid}?game_version='
                       f'{client_type}', auth=APIAuth('Bearer', api_key))
    parse_wagoaddons_error(payload.status_code)
    payload = payload.json()
    return f'https://addons.wago.io/addons/{payload["slug"]}'


@final
class WagoAddonsAddon(BaseAddon):
    def __init__(
        self, url: str, checkcache: dict[str, Any], clienttype: str,
        clientversion: str, allowdev: int, apikey: str, http: httpx.Client
    ) -> None:
        super().__init__()
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
            self.payload = self._get_metadata(project, url)
        self.name = self.payload['display_name'].strip().strip('\u200b')
        self.allowDev: int = allowdev
        self.author = self.payload['authors']
        self.get_current_version()

    @retry()
    def _get_metadata(self, project: str, url: str) -> dict[str, Any]:
        if self.apiKey == '':
            raise RuntimeError(f'{url}\nThe Wago Addons API key is missing. '
                                f'It can be obtained here: https://addons.wago.io/patreon')
        try:
            response = self.http.get(f'https://addons.wago.io/api/external/addons/{project}?game_version='
                                            f'{self.clientType}', auth=APIAuth('Bearer', self.apiKey))
        except httpx.RequestError as e:
            raise RuntimeError(f'{url}\nWago Addons API failed to respond.') from e
        if response.status_code == 401:
            raise RuntimeError(f'{url}\nWago Addons API key is missing or incorrect.')
        if response.status_code == 403:
            raise RuntimeError(f'{url}\nProvided Wago Addons API key is expired. Please acquire a new one.')
        if response.status_code == 404:
            raise RuntimeError(f'{url}\nThis might be a temporary issue with Wago Addons API or the project was '
                                f'removed/renamed. In this case, uninstall it (and reinstall if it still exists) '
                                f'to fix this issue.')
        if response.status_code == 423:
            raise RuntimeError(f'{url}\nProvided Wago Addons API key is blocked. Please acquire a new one.')
        if response.status_code in [429, 500, 502, 504]:
            raise RuntimeError(f'{url}\nTemporary Wago Addons API issue. Please try later.')

        try:
            return response.json()
        except (StopIteration, JSONDecodeError) as e:
            raise RuntimeError(f'{url}\nThis might be a temporary issue with Wago Addons API.') from e

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
        self.uiVersion = patches
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
        if self.archive:
            self.archive.extractall(path)


class WagoAddonsHasher:
    def __init__(self, directory: Path) -> None:
        self.directory: Path = directory
        self.filesToHash: list[Path] = []
        self.filesToParse: list[Path] = []
        self.hashes: list[str] = []
        self.parse()

    def parse_file(self, target: list[Path]):
        for f in target:
            if f.is_file():
                self.filesToHash.append(f)
                if not f.name.lower().endswith('.lua'):
                    with open(f, encoding='utf-8', errors='ignore') as g:
                        newfilestoparse = None
                        data = g.read()
                        if f.name.lower().endswith('.toc'):
                            data = re.sub(r'\s*#.*$', '', data, flags=re.I | re.M)
                            newfilestoparse = re.findall(r'^\s*((?:(?<!\.\.).)+\.(?:xml|lua))\s*$', data,
                                                         flags=re.I | re.M)
                        elif f.name.lower().endswith('.xml'):
                            data = re.sub(r'<!--.*?-->', '', data, flags=re.I | re.S)
                            newfilestoparse = re.findall(r"<(?:Include|Script)\s+file=[\"']((?:(?<!\.\.).)+)[\"']\s*/>",
                                                         data, flags=re.I)
                        if newfilestoparse and len(newfilestoparse) > 0:
                            newfilestoparse = [Path(f.parent, element) for element in newfilestoparse]
                            self.parse_file(newfilestoparse)

    def parse(self) -> None:
        for f in list(self.directory.glob('*')):
            if f.name.lower().endswith('.toc'):
                self.filesToParse.append(f)
            elif f.name.lower() == 'bindings.xml':
                self.filesToHash.append(f)
        self.parse_file(self.filesToParse)
        self.filesToHash = list(dict.fromkeys(self.filesToHash))
        for f in self.filesToHash:
            with open(f, 'rb') as g:
                self.hashes.append(hashlib.md5(g.read()).hexdigest())
        self.hashes.sort()

    def get_hash(self) -> str:
        return hashlib.md5(''.join(self.hashes).encode('utf-8')).hexdigest()


class WagoAddonsProvider(BaseAddonProvider):
    """Provider for Wago Addons."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]):
        super().__init__(http, config, master_config)
        self.name: str = "Wago"
        self.prefix: str = "wa"
        self.wagoIdCache: dict[str, Any] | None = None

    def is_addon_url(self, url: str) -> bool:
        return url.startswith('https://addons.wago.io/addons/')

    def convert_url_to_id(self, url: str) -> str:
        return url.replace('https://addons.wago.io/addons/', '')

    def convert_id_to_url(self, identifier: str) -> str:
        return f'https://addons.wago.io/addons/{identifier}'

    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        return WagoAddonsAddon(
            url,
            self.cache,
            client_type,
            kwargs.get('client_version', ''),
            kwargs.get('dev_level', 0),
            self.config['WAAAPIKey'],
            self.http
        )

    def bulk_check(self, addon_urls: list[str], client_type: str) -> None:
        """Bulk check for updates from Wago Addons API."""
        if not self.config['WAAAPIKey']:
            return

        # Extract slugs from URLs
        ids = [{'slug': self.convert_url_to_id(url), 'id': ''} for url in addon_urls]

        # Get ID cache
        if not self.wagoIdCache:
            try:
                response = self.http.get(
                    f'https://addons.wago.io/api/data/slugs?game_version={client_type}',
                    timeout=15
                )
                parse_wagoaddons_error(response.status_code)
                self.wagoIdCache = response.json()
            except Exception:
                return

        # Map slugs to IDs
        if self.wagoIdCache:
            for addon in ids:
                if addon['slug'] in self.wagoIdCache.get('addons', {}):
                    addon['id'] = self.wagoIdCache['addons'][addon['slug']]['id']

        # Fetch recent releases
        try:
            payload = self.http.post(
                f'https://addons.wago.io/api/external/addons/_recents?game_version={client_type}',
                json={'addons': [addon["id"] for addon in ids if addon["id"] != ""]},
                auth=APIAuth('Bearer', self.config['WAAAPIKey']),
                timeout=15
            )
            parse_wagoaddons_error(payload.status_code)
            payload = payload.json()

            # Populate cache
            for addonid in payload.get('addons', {}):
                for addon in ids:
                    if addon['id'] == addonid:
                        self.cache[addon['slug']] = payload['addons'][addonid]
                        break
        except Exception:
            pass

    def scan(self, addon_dirs: list[str], path: Path, client_type: str) -> list[DetectedAddon]:
        """Scan directories for Wago Addons using hash matching."""
        if not self.config['WAAAPIKey']:
            return []

        detected: list[DetectedAddon] = []
        wago_input: list[dict[str, str]] = []

        for directory in addon_dirs:
            directoryhash = WagoAddonsHasher(path / directory)
            wago_input.append({'name': directory, 'hash': directoryhash.get_hash()})

        if not wago_input:
            return []

        try:
            payload = self.http.post(
                f'https://addons.wago.io/api/external/addons/_match?game_version={client_type}',
                json={'addons': wago_input},
                auth=APIAuth('Bearer', self.config['WAAAPIKey'])
            )
            parse_wagoaddons_error(payload.status_code)
            payload = payload.json()

            for addon in payload.get('addons', []):
                detected.append(DetectedAddon(
                    name=addon['name'],
                    url=f'wa:{addon["website_url"].split("/")[-1]}',
                    directories=[addon['name']],  # TODO: Get actual directories from match
                    is_installed=False  # Will be determined by Core
                ))
        except Exception:
            pass

        return detected
