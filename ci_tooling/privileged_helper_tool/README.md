# OpenCore Legacy Patcher Privileged Helper Tool (retired)

`com.dortania.opencore-legacy-patcher.privileged-helper` is retired.

The helper used to be a setuid broker: the main application sent arguments to it,
the helper inspected signing metadata of its parent process, and it executed the
resulting command as root. That design cannot authenticate a caller correctly
(on-disk signing dictionaries are not signature validation), and personal builds
are unsigned anyway, so the broker was removed rather than repaired.

## Current behavior

* `main.m` only prints a retirement message and exits with status `170`. It has
  no Security framework linkage, no signing checks, and no command execution.
* The checked-in binary is the compiled stub, present so an installation that
  still has the old path fails closed instead of running stale code.
* Packages never ship the helper and never set a setuid bit; the payload
  contract (`ci_tooling/build_modules/payload_contract.py`) rejects both.
* Uninstall and preinstall scripts remove a helper installed by older releases.

## Privilege escalation today

Root work goes through macOS administrator authorization instead of a broker:
`opencore_legacy_patcher/support/subprocess_wrapper.py::run_as_root` quotes the
argument list, runs it via `osascript ... with administrator privileges`, and
parses a framed status/stdout/stderr response. There is no setuid artifact to
install, sign, or keep in sync with the application.

## Building

`make` compiles the stub so the directory keeps building in isolation:

```
make release
```

There is no debug configuration: no build of this source can execute commands.
