# Retired desktop application

The PyInstaller/pywebview desktop application was retired from active
development. This directory preserves its launcher, runtime module, UI bridge,
tests, build and smoke scripts, release workflow, and operating guide as a
historical reference.

The archive is intentionally excluded from the maintained pytest path and from
`make check`. Its former `make desktop`, `make bundle`, and `make test-desktop`
entry points and the `desktop` dependency extra were removed. The archived
scripts retain their original repository-relative assumptions and are not a
supported build from this location.
