FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KPTNCOOK_HOME=/data

WORKDIR /app

ARG KPTNCOOK_VERSION

RUN test -n "$KPTNCOOK_VERSION" \
    && python -m pip install --no-cache-dir "kptncook==${KPTNCOOK_VERSION}" \
    && python -c "from importlib.metadata import version; open('/usr/share/kptncook-version', 'w').write(version('kptncook') + '\n')" \
    && test "$(python -c "from importlib.metadata import version; print(version('kptncook'))")" = "$KPTNCOOK_VERSION" \
    && python -c "from kptncook.api import KptnCookClient; from kptncook.tandoor import TandoorExporter; assert callable(getattr(KptnCookClient, 'list_favorites', None) or getattr(KptnCookClient, 'get_favorites', None)); assert callable(getattr(KptnCookClient, 'resolve_recipe_summaries', None)); assert callable(getattr(TandoorExporter, 'get_recipe_payload', None)); assert callable(getattr(TandoorExporter, 'get_cover_image_bytes', None))"
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001

VOLUME ["/data"]
LABEL org.opencontainers.image.title="KptnCook favorites to Tandoor sync"
LABEL org.opencontainers.image.description="One-way importer; resolved kptncook version is recorded in /usr/share/kptncook-version"
LABEL org.opencontainers.image.version="${KPTNCOOK_VERSION}"

ENTRYPOINT ["kptncook-tandoor-sync"]
