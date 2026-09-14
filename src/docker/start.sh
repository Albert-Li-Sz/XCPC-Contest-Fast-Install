#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
: "${DOMJUDGE_API_URL:?missing API URL}"
: "${JUDGEDAEMON_USERNAME:?missing API user}"
: "${JUDGEDAEMON_PASSWORD_FILE:?missing password file}"
: "${DAEMON_ID:?missing CPU id}"
: "${RUN_USER_UID_GID:?missing unique sandbox UID/GID}"
[[ "$DAEMON_ID" =~ ^[0-9]+$ && "$RUN_USER_UID_GID" =~ ^[0-9]+$ ]] || exit 2
[[ "$DOMJUDGE_API_URL" == http*/api/ && "$DOMJUDGE_API_URL" != *[[:space:]]* ]] || exit 2
[[ "$JUDGEDAEMON_USERNAME" != *[[:space:]]* ]] || exit 2
secret="$(cat "$JUDGEDAEMON_PASSWORD_FILE")"
[[ -n "$secret" && "$secret" != *[[:space:]]* ]] || exit 2
printf 'default %s %s %s\n' "$DOMJUDGE_API_URL" "$JUDGEDAEMON_USERNAME" "$secret" \
    > /opt/domjudge/judgehost/etc/restapi.secret
unset secret
chown domjudge:domjudge /opt/domjudge/judgehost/etc/restapi.secret
chmod 0600 /opt/domjudge/judgehost/etc/restapi.secret
if [[ -f /run/xcpc-secrets/api-ca.crt ]]; then
    install -m 0644 /run/xcpc-secrets/api-ca.crt /usr/local/share/ca-certificates/xcpc-api.crt
    update-ca-certificates
fi
if [[ -n "${CONTAINER_TIMEZONE:-}" ]]; then
    [[ -f "/usr/share/zoneinfo/$CONTAINER_TIMEZONE" ]] || exit 2
    ln -snf "/usr/share/zoneinfo/$CONTAINER_TIMEZONE" /etc/localtime
fi
if ! getent group domjudge-run >/dev/null; then
    groupadd -g "$RUN_USER_UID_GID" domjudge-run
fi
if ! id "domjudge-run-$DAEMON_ID" >/dev/null 2>&1; then
    useradd -u "$RUN_USER_UID_GID" -N -d /nonexistent -g domjudge-run -s /bin/false "domjudge-run-$DAEMON_ID"
fi
[[ "$(id -u "domjudge-run-$DAEMON_ID")" == "$RUN_USER_UID_GID" ]] || exit 2
[[ "$(getent group domjudge-run | cut -d: -f3)" == "$RUN_USER_UID_GID" ]] || exit 2
/opt/domjudge/judgehost/bin/create_cgroups
cp /etc/resolv.conf /chroot/domjudge/etc/resolv.conf
exec sudo -u domjudge /opt/domjudge/judgehost/bin/judgedaemon -n "$DAEMON_ID"
