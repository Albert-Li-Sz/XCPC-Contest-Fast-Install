# The official registry has no 9.0.1 tag as of this installer release.
# Reuse its verified chroot/toolchains, and install the exact 9.0.1 judgehost.
FROM domjudge/judgehost@sha256:4c01f07e49023bcadd92255786372ec4c5fb5335bec5c9366f07b1fdddb28567
USER root
LABEL org.xcpc.managed="true" org.xcpc.domjudge-version="9.0.1"
COPY domjudge-9.0.1.tar.gz /tmp/domjudge-9.0.1.tar.gz
RUN echo '3d46b31c296581833ac9f8e9b249d929fc28710e834b1cec68723791a2dceb97  /tmp/domjudge-9.0.1.tar.gz' | sha256sum -c - \
    && apt-get update \
    && apt-get install --no-install-recommends -y build-essential pkg-config libcgroup-dev ca-certificates \
    && tar -xzf /tmp/domjudge-9.0.1.tar.gz -C /tmp \
    && cd /tmp/domjudge-9.0.1 \
    && ./configure --prefix=/opt/domjudge --disable-domserver-build --disable-doc-build \
       --with-domjudge-user=domjudge --with-runuser=domjudge-run --with-rungroup=domjudge-run \
    && make -j2 judgehost \
    && make install-judgehost \
    && cp /opt/domjudge/judgehost/etc/sudoers-domjudge /etc/sudoers.d/sudoers-domjudge \
    && chmod 0440 /etc/sudoers.d/sudoers-domjudge \
    && visudo -cf /etc/sudoers.d/sudoers-domjudge \
    && /opt/domjudge/judgehost/bin/runguard --version | grep -F '9.0.1/' \
    && rm -rf /tmp/domjudge-9.0.1 /tmp/domjudge-9.0.1.tar.gz /var/lib/apt/lists/*
COPY start.sh /usr/local/sbin/xcpc-judgehost-start
RUN chmod 0755 /usr/local/sbin/xcpc-judgehost-start
ENV JUDGEDAEMON_PASSWORD="" DOMJUDGE_API_URL="" JUDGEDAEMON_PASSWORD_FILE="/run/xcpc-secrets/password"
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/local/sbin/xcpc-judgehost-start"]
