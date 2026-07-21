"""Serving application (spec §2.1). MVC: controllers → services → repositories.

Reads ml/artifacts/ (via app.services.artifact_store) and nothing else from
ml/. Never imports ml.pipeline / ml.vision. May import shared core/.
"""
