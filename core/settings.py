import sublime


class GitSavvySettings:
    def __init__(self, parent=None):
        self.parent = parent
        self.global_settings = sublime.load_settings("GitSavvy.sublime-settings")

    def get(self, key, default=None):
        window = sublime.active_window()
        view = window.active_view()

        if view:
            project_savvy_settings = view.settings().get("GitSavvy", {}) or {}
            if key in project_savvy_settings:
                return project_savvy_settings[key]

        project_data = window.project_data() or {}
        # get the settings directly from project_data
        project_savvy_settings = project_data.get("settings", {}).get("GitSavvy", {})
        if key in project_savvy_settings:
            return project_savvy_settings.get(key)

        # fallback location of settings
        project_savvy_settings = project_data.get("GitSavvy", {})
        if key in project_savvy_settings:
            return project_savvy_settings.get(key)

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
