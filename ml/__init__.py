"""ML tree — nightly artifact build + CV attribute extraction (spec §2.2).

Physically separate from the serving app. The pipeline NEVER imports app/; the
serving app NEVER imports ml.pipeline / ml.vision. The only interface between
the two trees is the version-stamped artifact files on disk (ml/artifacts/).
Both trees may import the shared, framework-free core/ package.
"""
