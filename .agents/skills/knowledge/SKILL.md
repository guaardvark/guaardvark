---
name: knowledge
description: >-
  Answer from the user's own indexed documents with Guaardvark's local RAG, browse and
  read indexed files section by section, remember facts across sessions, and process or
  fetch new material. Use when the user asks what their documents say, wants a
  source-cited answer from their files, says "remember this", or asks to index a PDF,
  DOCX, spreadsheet, image or URL.
---

# Knowledge with Guaardvark (MCP tools)

Everything here is the `guaardvark` MCP server. All retrieval is local (Ollama embeddings,
hybrid search, cross-encoder reranking on the user's GPU).

## Find and read

| tool | use |
|---|---|
| `search_knowledge_base` | the question in plain words; returns passages with sources. Cite them. |
| `summarize_corpus` | what the whole knowledge base covers, before searching blind |
| `list_documents` | which documents are indexed and how many passages each has |
| `get_document_outline` | sections or pages of one document, in order |
| `read_document_section` | the actual text of one section or page, no search |
| `process_file` | extract content from a PDF, DOCX, CSV, Excel, image or other file the user names |
| `fetch_url` | title, description and main text of one web page |
| `web_search` | DuckDuckGo results (titles, snippets, URLs) when the answer is not local |

Pattern: `summarize_corpus` or `list_documents` → `search_knowledge_base` → read the exact
section with `get_document_outline` + `read_document_section` before quoting.

## Remember

- `save_memory`: a fact, preference or instruction to keep across sessions. Use when the user says
  "remember", or states a durable preference. One fact per call.
- `search_memory`: check before assuming; recalled memories are what was true when saved.
- `delete_memory` when the user says to forget something.

## Rules

- Quote the source (document name and section) for any claim that came from retrieval.
- If retrieval returns nothing, say so; do not fill the gap from general knowledge without
  labelling it.
- Indexing new folders happens in the Studio's Library page; the MCP server reads what is indexed.
