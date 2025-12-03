import os
import io
import httpx
import zipfile
from . import retry


CF_API_KEY = '$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm'
GAME_VERSION_TYPE_MAP = {'retail': 517, 'classic': 67408, 'mop': 79434}


class CurseForgeAddon:
    @retry()
    def __init__(self, url, checkcache, clienttype, allowdev, http):
        slug = url.split('/')[-1]
        self.http = http
        self.clientType = clienttype
        self.allowDev = allowdev

        if slug in checkcache:
            self.payload = checkcache[slug]
        else:
            try:
                response = self.http.get(f'https://api.curseforge.com/v1/mods/search?gameId=1&slug={slug}',
                                        headers={'x-api-key': CF_API_KEY}, timeout=15)
            except httpx.RequestError as e:
                raise RuntimeError(f'{url}\nCurseForge API failed to respond.') from e

            try:
                data = response.json()['data']
            except (StopIteration, KeyError) as e:
                raise RuntimeError(f'{url}\nFailed to parse CurseForge API response.') from e

            if not data:
                raise RuntimeError(f'{url}\nAddon not found on CurseForge.')

            self.payload = data[0]

        self.name = self.payload['name'].strip().strip('\u200b')
        self.changelogUrl = self.payload['links']['websiteUrl']
        self.author = [author['name'] for author in self.payload['authors']]
        self.downloadUrl = None
        self.currentVersion = None
        self.uiVersion = None
        self.archive = None
        self.directories = []
        self.get_current_version()

    def get_current_version(self):
        game_version_type_id = GAME_VERSION_TYPE_MAP.get(self.clientType)
        if not game_version_type_id:
            raise RuntimeError(f'{self.name}.\nUnsupported client type: {self.clientType}')

        # Filter files by game version type and release type
        compatible_files = []
        max_release_type = self.allowDev + 1  # 0->1 (stable), 1->2 (stable+beta), 2->3 (all)

        for file_index in self.payload['latestFilesIndexes']:
            if (file_index['gameVersionTypeId'] == game_version_type_id and
                file_index['releaseType'] <= max_release_type):
                compatible_files.append(file_index)

        if not compatible_files:
            raise RuntimeError(f'{self.name}.\nFailed to find release for your client version.')

        # Select file with highest fileId (most recent)
        selected_file_index = max(compatible_files, key=lambda x: x['fileId'])
        selected_file_id = selected_file_index['fileId']

        # Find the full file data in latestFiles
        selected_file = None
        for file_data in self.payload['latestFiles']:
            if file_data['id'] == selected_file_id:
                selected_file = file_data
                break

        if not selected_file:
            raise RuntimeError(f'{self.name}.\nFailed to find file data for selected release.')

        self.downloadUrl = selected_file['downloadUrl']
        self.currentVersion = selected_file['displayName']
        self.uiVersion = selected_file_index['gameVersion']

    @retry()
    def get_addon(self):
        self.archive = zipfile.ZipFile(io.BytesIO(self.http.get(self.downloadUrl).content))
        for file in self.archive.namelist():
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path):
        self.archive.extractall(path)
