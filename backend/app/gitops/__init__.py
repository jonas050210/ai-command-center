"""Git/GitHub integration (P7) — REAL and wired.

Local git operations run as audited argv subprocesses pinned to repos
inside the workspace sandbox; mutations require the ``git:operate``
capability. GitHub REST uses a PAT from the encrypted credentials store.
See routers/git.py for the API surface.
"""
