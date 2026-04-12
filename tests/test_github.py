from unittesting import DeferrableTestCase

from GitSavvy.github import github
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


class TestGitHubRemoteParsing(DeferrableTestCase):
    def tearDown(self):
        unstub()
        github.remote_to_url.cache_clear()
        github._resolve_ssh_hostname.cache_clear()

    @p.expand([
        ("git://github.com/timbrel/GitSavvy.git", "https://github.com/timbrel/GitSavvy"),
        ("https://github.com/timbrel/GitSavvy.git", "https://github.com/timbrel/GitSavvy"),
        ("https://github.com/timbrel/GitSavvy", "https://github.com/timbrel/GitSavvy"),
    ])
    def test_remote_to_url_non_ssh_remotes(self, remote_url, expected):
        self.assertEqual(expected, github.remote_to_url(remote_url))

    def test_remote_to_url_resolves_host_alias_for_scp_like_ssh_remote(self):
        when(github)._resolve_ssh_hostname("my-github").thenReturn("github.com")

        actual = github.remote_to_url("git@my-github:me/my-project.git")

        self.assertEqual("https://github.com/me/my-project", actual)

    def test_remote_to_url_resolves_host_alias_for_ssh_scheme_remote(self):
        when(github)._resolve_ssh_hostname("my-github").thenReturn("github.com")

        actual = github.remote_to_url("ssh://git@my-github/me/my-project.git")

        self.assertEqual("https://github.com/me/my-project", actual)

    def test_read_ssh_config_hostname(self):
        when(github.subprocess).check_output(
            ["ssh", "-G", "my-github"],
            stderr=github.subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            startupinfo=github.STARTUPINFO,
        ).thenReturn("host my-github\nhostname github.com\n")

        actual = github._read_ssh_config_hostname("my-github")

        self.assertEqual("github.com", actual)

    def test_resolve_ssh_hostname_falls_back_to_input_value(self):
        when(github)._read_ssh_config_hostname("my-github").thenReturn(None)

        actual = github._resolve_ssh_hostname("my-github")

        self.assertEqual("my-github", actual)
