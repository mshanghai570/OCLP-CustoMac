#!/bin/zsh --no-rcs
# ------------------------------------------------------
# Privileged Helper Tool Installer (retired)
# ------------------------------------------------------
# The setuid broker no longer exists. Root work goes
# through macOS administrator authorization instead,
# so there is nothing to install and no SUID bit to set.
# ------------------------------------------------------

print -u2 "The privileged helper is retired and cannot be installed."
print -u2 "Reinstall OpenCore-Patcher.pkg; it escalates with macOS administrator authorization."
exit 1
