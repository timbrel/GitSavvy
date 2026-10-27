# Package Testing

We try to cover the most crucial functionality with unit tests using
[UnitTesting](https://github.com/SublimeText/UnitTesting). Tests run inside
Sublime Text, either headlessly in Docker or in your editor.

## Run the tests headlessly (Docker)

Install [UnitTesting via Package Control](https://packagecontrol.io/packages/UnitTesting).
The package includes the `docker/ut-run-tests` launcher (on Windows,
`docker/ut-run-tests.cmd`). You need Docker running and Python available to
the launcher. Optionally add its directory to `PATH` or create an alias so
you can invoke it as `ut-run-tests`, as in the examples below.

From the GitSavvy repository root:

```sh
# Run all categories: unit tests, syntax tests, and syntax compatibility checks.
ut-run-tests .

# Run one concrete test file (path relative to the repository root).
ut-run-tests . --file tests/test_app_state.py
```

If the launcher is not on `PATH`, invoke
`<Packages>/UnitTesting/docker/ut-run-tests` instead (on Windows, use
`ut-run-tests.cmd`). The launcher builds the local Docker image when needed,
runs Sublime Text headlessly using UnitTesting's CI entrypoints, and caches
its setup in a Docker volume for faster reruns. Categories without matching
resources are skipped successfully.

Use `ut-run-tests --help` for current options. Use `--refresh-image` to
rebuild the image, `--refresh-cache` to recreate the cached Sublime setup,
or `--refresh` for both. See `docker/README.md` in the installed UnitTesting
package for setup details.

## Run the tests in Sublime Text

[Clone GitSavvy from source](../README.md#less-simple) and install
[UnitTesting via Package Control](https://packagecontrol.io/packages/UnitTesting).
Open the `GitSavvy` directory and run `UnitTesting: Test Current Project`.

CI runs unit tests on Linux and Windows, plus syntax tests on Linux; see the
[test workflow](../.github/workflows/test.yml).

## Some details about DeferrableTestCase

[DeferrableTestCase][1] is used to write the test cases. They are executed by
the [DeferringTextTestRunner][2] and the runner expects not only regular test
functions, but also generators. If the test function is a generator, it does
the following

- if the yielded object is a callable, the runner will evaluate the
  [callable][3] and check its returned value. If the result is `True`, the
  runner continues the generator, if not, the runner will wait until the
  condition is met.

- If the yielded object is an integer, say `x`, then it will [continue][4] the
  generator after `x` ms.

- Otherwise, the `yield` statement will always wait for 10 ms.
  
[1]: https://github.com/SublimeText/UnitTesting/blob/master/unittesting/core/py38/case.py
[2]: https://github.com/SublimeText/UnitTesting/blob/master/unittesting/core/py38/runner.py
[3]: https://github.com/SublimeText/UnitTesting#deferred-testing
[4]: https://github.com/SublimeText/UnitTesting#deferred-testing
