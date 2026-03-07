#!/usr/bin/env sh
set -eu

template_path="/etc/nginx/templates/default.conf.template"
target_path="/etc/nginx/conf.d/default.conf"

envsubst '${MCP_API_KEY}' < "${template_path}" > "${target_path}"

exec nginx -g 'daemon off;'
