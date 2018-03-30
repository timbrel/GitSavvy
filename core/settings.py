import sublime


class GitSavvySettings:
    def __init__(self, parent=None):
        self.parent = parent
        self.global_settings = sublime.load_settings("GitSavvy.sublime-settings")

    def get(self, key, default=None):
        return self.global_settings.get(key, default)

    def set(self, key, value):
        self.global_settings.set(key, value)


class SettingsMixin:
    _savvy_settings = None

    @property
    def savvy_settings(self):
        if not self._savvy_settings:
            self._savvy_settings = GitSavvySettings(self)
        return self._savvy_settings
