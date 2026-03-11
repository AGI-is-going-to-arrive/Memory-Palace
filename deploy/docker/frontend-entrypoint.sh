#!/usr/bin/env sh
set -eu

template_path="/etc/nginx/templates/default.conf.template"
target_path="/etc/nginx/conf.d/default.conf"

escaped_mcp_api_key="$(printf '%s' "${MCP_API_KEY:-}" | sed 's/[\\"]/\\&/g')"
export MCP_API_KEY_NGINX_ESCAPED="${escaped_mcp_api_key}"

envsubst '${MCP_API_KEY_NGINX_ESCAPED}' < "${template_path}" > "${target_path}"
nginx -t

exec nginx -g 'daemon off;'
