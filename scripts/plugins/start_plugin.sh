#!/bin/bash
# Start a plugin by name: scripts/plugins/start_plugin.sh <plugin> [--direct]
PROG=start_plugin.sh ACTION=start exec bash "$(dirname "${BASH_SOURCE[0]}")/_plugin_action.sh" "$@"
