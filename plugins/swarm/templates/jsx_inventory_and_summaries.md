# Frontend JSX Inventory

Map a React frontend: list every JSX file, summarize each one, and compile an
architecture report. Three agents, one after the other.

## Inventory Frontend JSX Files
Assign to: any
Files: docs/jsx-inventory.md
Deps: []
Description: Recursively scan the frontend source directory (for example frontend/src/) and list every file ending in .jsx. Write the complete list of file paths to docs/jsx-inventory.md and commit it.

## Summarize Code Contents
Assign to: any
Files: docs/jsx-summaries.md
Deps: [T1]
Description: Read every file listed in docs/jsx-inventory.md. For each one write a concise summary: its purpose, the main components it renders, and how it handles state or core logic. Write the summaries to docs/jsx-summaries.md and commit it.

## Generate Final Report
Assign to: any
Files: docs/frontend-architecture.md
Deps: [T2]
Description: Compile the summaries in docs/jsx-summaries.md into one markdown report, docs/frontend-architecture.md, that gives an overview of the frontend architecture: related files grouped together, and how data and components flow through the application. Commit it.
