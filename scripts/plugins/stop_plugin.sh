#!/bin/bash
# Stop a plugin by name: scripts/plugins/stop_plugin.sh <plugin> [--direct]
PROG=stop_plugin.sh ACTION=stop exec bash "$(dirname "${BASH_SOURCE[0]}")/_plugin_action.sh" "$@"
