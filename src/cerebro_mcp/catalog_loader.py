from __future__ import annotations

from cerebro_mcp.artifact_loader import ArtifactLoader, local_artifact_candidates
from cerebro_mcp.config import settings


class CatalogLoader(ArtifactLoader):
    def __init__(self):
        super().__init__(
            url=settings.DBT_CATALOG_URL,
            path=settings.DBT_CATALOG_PATH,
            label="catalog artifact",
            path_resolver=lambda: local_artifact_candidates(
                "catalog.json",
                settings.DBT_CATALOG_PATH,
                settings.DBT_MANIFEST_PATH,
                settings.SEMANTIC_REGISTRY_PATH,
                settings.SEMANTIC_DOCS_INDEX_PATH,
            ),
        )


catalog = CatalogLoader()
