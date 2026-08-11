"""Local, private retrieval-augmented generation support.

The package must remain importable without optional inference or vector-index
dependencies. Runtime modules import those dependencies only when RAG is
enabled and constructed.
"""

from nanobot.rag.config import RagConfig

__all__ = ["RagConfig"]
