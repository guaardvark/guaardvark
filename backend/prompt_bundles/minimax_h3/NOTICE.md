# MiniMax H3 prompt bundle

Data the H3 prompt compiler (`backend/services/h3_prompt_compiler.py`) and the
Video Generator's prompt presets read.

## What is here and where it came from

- `presets.json` — prompt presets written for Guaardvark in the H3 prompt
  structure. Original work, licensed with the rest of this repository.
- `gallery_index.json` — titles, categories, formats and source links for the
  community prompt gallery at
  https://github.com/Anil-matcha/awesome-minimax-h3-prompts (documentation and
  prompt text there: CC BY 4.0, attribution "Anil-matcha MiniMax H3 Prompt Lab").
  The prompt text itself is not copied: many entries are third-party prompts the
  gallery collected from social posts, and its own rights note says to keep
  media only with permission. The index links to each entry so a person can
  read the prompt at its source.
- `languages.json` — the eleven dialogue languages the model card lists.

## The prompt format

The compiler reproduces the structure MiniMax documents in its prompt-writing
skill, https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
(read at commit d21241f0a4b3acbb34c97dae47fa417b7065e438). That repository
publishes no license for the guide text, so none of it is reproduced here; the
section names, tags, timing notation and ordering rules are format facts and
are implemented in code. Read the upstream guide for the full rationale and
worked examples.
