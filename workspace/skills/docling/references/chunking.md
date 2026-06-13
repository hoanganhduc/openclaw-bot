# Docling chunking

Default OpenClaw recommendation:

- use hierarchical chunking first for lightweight structure-aware chunking
- switch to hybrid or token-aware chunking only when downstream embedding or token constraints justify it

Chunking should preserve:
- heading context
- page provenance when possible
- table and figure boundaries when relevant
