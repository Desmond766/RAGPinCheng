"""Evaluation harness for the RAG pipeline.

Submodules:
- types     EvalItem dataclass + Kind literal
- io        load/save JSONL
- sample    weighted sampling from parents.sqlite by kind
- metrics   Recall@k, MRR (retrieval-level, parent-id based)
"""
