FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install without a pinned version so every uncached image build checks PyPI.
RUN python -m pip install --no-cache-dir --upgrade kptncook \
    && python -c "from importlib.metadata import version; open('/usr/share/kptncook-version', 'w').write(version('kptncook') + '\n')" \
    && python -c "from kptncook.api import KptnCookClient; from kptncook.tandoor import TandoorExporter; assert callable(getattr(KptnCookClient, 'get_favorites', None)); assert callable(getattr(KptnCookClient, 'resolve_recipe_summaries', None)); assert callable(getattr(TandoorExporter, 'get_recipe_payload', None)); assert callable(getattr(TandoorExporter, 'get_cover_image_bytes', None))"
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001

VOLUME ["/data"]
LABEL org.opencontainers.image.title="KptnCook favorites to Tandoor sync"
LABEL org.opencontainers.image.description="One-way importer; resolved kptncook version is recorded in /usr/share/kptncook-version"

ENTRYPOINT ["kptncook-tandoor-sync"]
