"""
subprocess_wrapper.py: Wrapper for subprocess module to better handle errors and output
                       Additionally handles our Privileged Helper Tool
"""

import enum
import base64
import logging
import os
import shlex
import subprocess
import sys

from pathlib import Path


OCLP_PRIVILEGED_HELPER = "/Library/PrivilegedHelperTools/com.dortania.opencore-legacy-patcher.privileged-helper"


class PrivilegedHelperErrorCodes(enum.IntEnum):
    """
    Error codes for Privileged Helper Tool.

    Reference:
        payloads/Tools/PrivilegedHelperTool/main.m
    """
    OCLP_PHT_ERROR_MISSING_ARGUMENTS           = 160
    OCLP_PHT_ERROR_SET_UID_MISSING             = 161
    OCLP_PHT_ERROR_SET_UID_FAILED              = 162
    OCLP_PHT_ERROR_SELF_PATH_MISSING           = 163
    OCLP_PHT_ERROR_PARENT_PATH_MISSING         = 164
    OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING = 165
    OCLP_PHT_ERROR_INVALID_TEAM_ID             = 166
    OCLP_PHT_ERROR_INVALID_CERTIFICATES        = 167
    OCLP_PHT_ERROR_COMMAND_MISSING             = 168
    OCLP_PHT_ERROR_COMMAND_FAILED              = 169
    OCLP_PHT_ERROR_CATCH_ALL                   = 170


def run(*args, **kwargs) -> subprocess.CompletedProcess:
    """
    Basic subprocess.run wrapper.
    """
    return subprocess.run(*args, **kwargs)


_AUTHORIZATION_SCRIPT = """on run argv
    return do shell script (item 1 of argv) with administrator privileges without altering line endings
end run"""


def _authorization_shell(argv, combined=False):
    """Quote literal arguments and capture bytes in an exclusive private directory."""
    redirect = '2>&1' if combined else '2>"$work/stderr"'
    return '\n'.join([
        'umask 077',
        'work=$(/usr/bin/mktemp -d /private/tmp/oclp-authorized.XXXXXXXX) || exit 1',
        'trap \'/bin/rm -rf "$work"\' EXIT',
        'trap \'exit 1\' HUP INT TERM',
        ': >"$work/stderr"',
        f'{shlex.join(argv)} >"$work/stdout" {redirect}',
        'status=$?',
        'printf "OCLP_AUTH_V1:%s\\n" "$status"',
        '/usr/bin/base64 -i "$work/stdout" | /usr/bin/tr -d "\\n"',
        'printf "\\nOCLP_AUTH_STDERR\\n"',
        '/usr/bin/base64 -i "$work/stderr" | /usr/bin/tr -d "\\n"',
        'printf "\\n"',
    ])


def _emit(destination, data) -> None:
    """Write captured output to a console stream, file object, or file descriptor.

    Console streams may be absent in windowed applications, binary-only on the
    underlying buffer of a text wrapper, or plain text objects with no buffer.
    """
    if isinstance(destination, int):
        payload = data if isinstance(data, bytes) else data.encode("utf-8", errors="replace")
        while payload:
            payload = payload[os.write(destination, payload):]
        return
    if isinstance(data, bytes):
        binary = getattr(destination, "buffer", None)
        if binary is not None:
            binary.write(data)
        else:
            destination.write(data.decode("utf-8", errors="replace"))
    else:
        destination.write(data)
    destination.flush()


def run_as_root(*args, **kwargs) -> subprocess.CompletedProcess:
    """Use macOS administrator authorization, or execute directly when already root."""
    if len(args) != 1 or isinstance(args[0], (str, bytes)) or not args[0]:
        raise ValueError("Expected a nonempty argument list")
    argv = [os.fspath(value) for value in args[0]]
    if any(not isinstance(value, str) or '\x00' in value for value in argv):
        raise ValueError("Arguments must be strings without NUL bytes")
    if not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file():
        raise FileNotFoundError(f"Absolute executable not found: {argv[0]}")
    if os.geteuid() == 0:
        return subprocess.run(argv, **kwargs)

    supported = {"capture_output", "stdout", "stderr", "text", "universal_newlines", "encoding", "errors", "check", "timeout"}
    if kwargs.keys() - supported:
        raise ValueError(f"Unsupported authorization options: {sorted(kwargs.keys() - supported)}")
    capture = kwargs.get("capture_output", False)
    if capture and ("stdout" in kwargs or "stderr" in kwargs):
        raise ValueError("stdout/stderr cannot be used with capture_output")
    stdout = subprocess.PIPE if capture else kwargs.get("stdout")
    stderr = subprocess.PIPE if capture else kwargs.get("stderr")
    if stdout not in (None, subprocess.PIPE, subprocess.DEVNULL) or stderr not in (None, subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT):
        raise ValueError("Authorization supports inherited, captured, or discarded output")
    process = subprocess.run(
        ["/usr/bin/osascript", "-e", _AUTHORIZATION_SCRIPT, "--", _authorization_shell(argv, stderr == subprocess.STDOUT)],
        capture_output=True, timeout=kwargs.get("timeout"),
    )
    status, out, err = process.returncode, process.stdout, process.stderr
    if status and stderr == subprocess.STDOUT:
        out, err = out + err, b''
    if status == 0:
        try:
            header, data = out.replace(b'\r', b'\n').split(b'\n', 1)
            if not header.startswith(b'OCLP_AUTH_V1:'):
                raise ValueError("Missing authorization result header")
            status = int(header.split(b':', 1)[1])
            if not 0 <= status <= 255:
                raise ValueError("Invalid command status")
            encoded_out, encoded_err = data.split(b'\nOCLP_AUTH_STDERR\n')
            out = base64.b64decode(b''.join(encoded_out.split()), validate=True)
            err = base64.b64decode(b''.join(encoded_err.split()), validate=True)
        except (ValueError, TypeError) as error:
            status = 1
            message = f"Invalid authorization response: {error}".encode()
            if stderr == subprocess.STDOUT:
                out, err = b"\n".join(part for part in (out, message) if part), b''
            else:
                err = message
    text_mode = kwargs.get("text", kwargs.get("universal_newlines", False)) or kwargs.get("encoding") or kwargs.get("errors")
    if text_mode:
        encoding = kwargs.get("encoding") or "utf-8"
        errors = kwargs.get("errors") or "strict"
        out, err = out.decode(encoding, errors), err.decode(encoding, errors)
    for target, data, stream in ((stdout, out, sys.stdout), (stderr, err, sys.stderr)):
        if not data:
            continue
        destination = stream if target is None else target
        if destination is None or destination in (subprocess.PIPE, subprocess.DEVNULL):
            continue
        _emit(destination, data)
    result = subprocess.CompletedProcess(argv, status, out if stdout == subprocess.PIPE else None, err if stderr == subprocess.PIPE else None)
    if kwargs.get("check") and status:
        raise subprocess.CalledProcessError(status, argv, output=result.stdout, stderr=result.stderr)
    return result


def verify(process_result: subprocess.CompletedProcess) -> None:
    """
    Verify process result and raise exception if failed.
    """
    if process_result.returncode == 0:
        return

    log(process_result)

    raise Exception(f"Process failed with exit code {process_result.returncode}")


def run_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess and verify result.

    Asserts on failure.
    """
    verify(run(*args, **kwargs))


def run_as_root_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess as root and verify result.

    Asserts on failure.
    """
    verify(run_as_root(*args, **kwargs))


def log(process: subprocess.CompletedProcess) -> None:
    """
    Display subprocess error output in formatted string.
    """
    for line in generate_log(process).split("\n"):
        logging.error(line)


def generate_log(process: subprocess.CompletedProcess) -> str:
    """
    Display subprocess error output in formatted string.
    Note this function is still used for zero return code errors, since
    some software don't ever return non-zero regardless of success.

    Format:

        Command: <command>
        Return Code: <return code>
        Standard Output:
            <standard output line 1>
            <standard output line 2>
            ...
        Standard Error:
            <standard error line 1>
            <standard error line 2>
            ...
    """
    output = "Subprocess failed.\n"
    output += f"    Command: {process.args}\n"
    output += f"    Return Code: {process.returncode}\n"
    _returned_error = __resolve_privileged_helper_errors(process.returncode)
    if _returned_error:
        output += f"        Likely Enum: {_returned_error}\n"
    output += f"    Standard Output:\n"
    if process.stdout:
        output += __format_output(process.stdout)
    else:
        output += "        None\n"
    output += f"    Standard Error:\n"
    if process.stderr:
        output += __format_output(process.stderr)
    else:
        output += "        None\n"

    return output


def __resolve_privileged_helper_errors(return_code: int) -> str:
    """
    Attempt to resolve Privileged Helper Tool error codes.
    """
    if return_code not in [error_code.value for error_code in PrivilegedHelperErrorCodes]:
        return None

    return PrivilegedHelperErrorCodes(return_code).name


def __format_output(output: str | bytes) -> str:
    """
    Format output.
    """
    if not output:
        # Shouldn't happen, but just in case
        return "        None\n"

    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    _result = "\n".join([f"        {line}" for line in output.split("\n") if line not in ["", "\n"]])
    if not _result.endswith("\n"):
        _result += "\n"

    return _result
