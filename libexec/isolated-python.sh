#!/bin/sh
# The Ansible interpreter setting accepts an executable path. Exclude all site hooks.
exec /usr/bin/python3 -I -S -B "$@"
