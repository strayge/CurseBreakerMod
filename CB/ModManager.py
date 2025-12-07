import shutil
import tempfile
import zipfile
from typing import Any
from pathlib import Path
from difflib import unified_diff


CLEAN_BACKUP_DIR = 'WTF/CurseBreakerClean'


class ModManager:
    def __init__(self, core: Any) -> None:
        self.core: Any = core

    def create_mod(self, addon_name: str, mod_name: str) -> int:
        """Create mod from current addon modifications"""
        # 1. Validate addon exists and is installed
        addon = self.core.check_if_installed(addon_name)
        if not addon:
            raise RuntimeError(f"Addon '{addon_name}' is not installed.")

        # 2. Check if clean ZIP exists
        clean_zip = Path(f'{CLEAN_BACKUP_DIR}/{addon_name}/{addon["Version"]}.zip')
        if not clean_zip.exists():
            raise RuntimeError(
                f"No clean version found for {addon_name} v{addon['Version']}.\n"
                f"Run 'force_update {addon_name}' to reinstall cleanly first."
            )

        # 3. Create single temp directory with subdirectories
        temp_base = Path(tempfile.mkdtemp(prefix='cb_mod_'))

        try:
            # 4. Extract clean version to temp_base/clean
            clean_path = temp_base / 'clean'
            clean_path.mkdir()
            with zipfile.ZipFile(clean_zip, 'r') as zf:
                zf.extractall(clean_path)

            # 5. Copy clean to expected (copytree creates expected_path for us)
            expected_path = temp_base / 'expected'
            shutil.copytree(clean_path, expected_path)

            # Apply existing enabled mods to expected
            enabled_mods = self._get_enabled_mods(addon_name)
            if enabled_mods:
                for _mod_name, mod_data in enabled_mods:
                    for file_path, patch_content in mod_data['patches'].items():
                        target_file = expected_path / file_path
                        if target_file.exists():
                            try:
                                self._apply_patch(target_file, patch_content)
                            except Exception:
                                pass  # Silently skip failed patches

            # 6. Generate diffs between expected and current
            current_path = Path('Interface/AddOns')
            patches = {}

            for directory in addon['Directories']:
                dir_path = current_path / directory
                if not dir_path.exists():
                    continue

                for file_path in self._get_lua_xml_files(dir_path):
                    rel_path = file_path.relative_to(current_path)
                    expected_file = expected_path / rel_path

                    if expected_file.exists():
                        diff = self._generate_diff(expected_file, file_path, addon['Version'])
                        if diff:
                            patches[str(rel_path)] = diff

            # 7. Validate we found changes
            if not patches:
                raise RuntimeError("No modifications detected. Edit files before creating mod.")

        finally:
            # 8. Always cleanup temp directory
            shutil.rmtree(temp_base, ignore_errors=True)

        # 8. Save mod to config
        if addon_name not in self.core.config['Mods']:
            self.core.config['Mods'][addon_name] = {}

        if mod_name in self.core.config['Mods'][addon_name]:
            raise RuntimeError(f"Mod '{mod_name}' already exists. Delete it first or choose different name.")

        self.core.config['Mods'][addon_name][mod_name] = {
            'enabled': True,
            'priority': self._get_next_priority(addon_name),
            'baseVersion': addon['Version'],
            'patches': patches
        }

        self.core.save_config()
        return len(patches)

    def _get_enabled_mods(self, addon_name: str) -> list[tuple[str, dict[str, Any]]]:
        """Get enabled mods for an addon, sorted by priority"""
        if addon_name not in self.core.config['Mods']:
            return []

        enabled_mods = [
            (name, data) for name, data in self.core.config['Mods'][addon_name].items()
            if data.get('enabled', False)
        ]
        enabled_mods.sort(key=lambda x: x[1].get('priority', 999))
        return enabled_mods

    def _generate_diff(self, expected_file: Path, current_file: Path, version: str) -> str | None:
        """Generate unified diff between two files"""
        try:
            with open(expected_file, encoding='utf-8', errors='ignore') as f:
                expected_lines = f.readlines()
            with open(current_file, encoding='utf-8', errors='ignore') as f:
                current_lines = f.readlines()

            diff_lines = list(unified_diff(
                expected_lines,
                current_lines,
                fromfile=f'{expected_file.name} (base v{version})',
                tofile=f'{current_file.name} (modded)',
                lineterm=''
            ))

            return '\n'.join(diff_lines) if diff_lines else None
        except Exception:
            return None

    def _apply_patch(self, target_file: Path, patch_content: str) -> None:
        """Apply unified diff patch to file"""
        # Simple patch application - parse and apply unified diff
        with open(target_file, encoding='utf-8', errors='ignore') as f:
            original_lines = f.readlines()

        # Parse patch
        patch_lines = patch_content.split('\n')
        hunks: list[dict[str, Any]] = []
        current_hunk: dict[str, Any] | None = None

        for line in patch_lines:
            if line.startswith('@@'):
                if current_hunk:
                    hunks.append(current_hunk)
                # Parse hunk header: @@ -start,count +start,count @@
                parts = line.split('@@')[1].strip().split()
                old_start = int(parts[0].split(',')[0][1:])
                new_start = int(parts[1].split(',')[0][1:])
                current_hunk = {
                    'old_start': old_start,
                    'new_start': new_start,
                    'lines': []
                }
            elif current_hunk is not None and line.startswith(('+', '-', ' ')):
                current_hunk['lines'].append(line)

        if current_hunk:
            hunks.append(current_hunk)

        # Apply hunks in reverse order to maintain line numbers
        result_lines = original_lines.copy()

        for hunk in reversed(hunks):
            old_line_idx = hunk['old_start'] - 1
            new_lines: list[str] = []
            lines_to_delete = 0

            for line in hunk['lines']:
                if line.startswith('+'):
                    new_lines.append(line[1:] + '\n' if not line[1:].endswith('\n') else line[1:])
                elif line.startswith('-'):
                    lines_to_delete += 1
                elif line.startswith(' '):
                    new_lines.append(line[1:] + '\n' if not line[1:].endswith('\n') else line[1:])

            # Remove old lines and insert new lines
            del result_lines[old_line_idx:old_line_idx + lines_to_delete]
            for i, new_line in enumerate(new_lines):
                result_lines.insert(old_line_idx + i, new_line)

        # Write patched content
        with open(target_file, 'w', encoding='utf-8', errors='ignore') as f:
            f.writelines(result_lines)

    def _get_lua_xml_files(self, directory: Path) -> Any:
        """Get all .lua and .xml files recursively"""
        for ext in ['**/*.lua', '**/*.xml']:
            yield from directory.glob(ext)

    def _get_next_priority(self, addon_name: str) -> int:
        """Get next available priority number"""
        if addon_name not in self.core.config['Mods']:
            return 1
        priorities = [m.get('priority', 1) for m in self.core.config['Mods'][addon_name].values()]
        return max(priorities, default=0) + 1

    def reapply_all_mods(self, addon_name: str) -> list[tuple[str, str]]:
        """Reapply all enabled mods for an addon"""
        addon = self.core.check_if_installed(addon_name)
        if not addon:
            return []

        # Get enabled mods sorted by priority
        enabled_mods = self._get_enabled_mods(addon_name)
        if not enabled_mods:
            return []

        failed_mods: list[tuple[str, str]] = []
        current_path = Path('Interface/AddOns')

        for mod_name, mod_data in enabled_mods:
            try:
                for file_path, patch_content in mod_data['patches'].items():
                    target_file = current_path / file_path
                    if target_file.exists():
                        self._apply_patch(target_file, patch_content)
            except Exception as e:
                # Disable failed mod
                mod_data['enabled'] = False
                failed_mods.append((mod_name, str(e)))

        if failed_mods:
            self.core.save_config()

        return failed_mods
