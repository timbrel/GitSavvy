import doctest
import pkgutil

import GitSavvy


def load_tests(loader, tests, ignore):
    package_iterator = pkgutil.walk_packages(GitSavvy.__path__, 'GitSavvy.')

    for pkg_loader, module_name, is_pkg in package_iterator:

        if module_name.startswith('GitSavvy.tests'):
            continue

        module = pkg_loader.find_module(module_name).load_module(module_name)

        try:
            module_tests = doctest.DocTestSuite(module)
        except ValueError:
            continue

        tests.addTests(module_tests)

    return tests
