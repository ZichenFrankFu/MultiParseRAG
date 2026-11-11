# Use a checksum-verified official release package; legacy public images may be unavailable.
FROM debian:bookworm-slim AS download
ARG TARGETARCH
ARG MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z
ARG MINIO_PACKAGE_VERSION=20250907161309.0.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /download
RUN set -eu; \
    case "$TARGETARCH" in amd64|arm64) ;; *) echo "Supported architectures: amd64, arm64"; exit 1;; esac; \
    package="minio_${MINIO_PACKAGE_VERSION}_${TARGETARCH}.deb"; \
    base="https://github.com/minio/minio/releases/download/${MINIO_RELEASE}"; \
    curl --fail --location --retry 3 "$base/$package" -o "$package"; \
    curl --fail --location --retry 3 "$base/$package.sha256sum" -o "$package.sha256sum"; \
    sha256sum --check "$package.sha256sum"; \
    dpkg-deb -x "$package" /extracted

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=download /extracted/usr/local/bin/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
